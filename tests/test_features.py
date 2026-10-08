import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from retention.features import build_features, cohort_retention, monthly_sales, NUMERIC_FEATURES
from retention.data import validate_data, generate_demo
from retention.pipeline import make_database, verify_sql


@pytest.fixture
def small_data():
    customers = pd.DataFrame([
        ["A", "2023-01-01", "Organic", "Canada"],
        ["B", "2024-02-01", "Referral", "Canada"],
        ["C", "2023-01-01", "Social", "Canada"],
        ["D", "2023-01-01", "Organic", "Canada"]],
        columns=["customer_id", "signup_date", "acquisition_channel", "country"])
    orders = pd.DataFrame([
        ["1", "A", "2023-10-04", 10, "completed", "Home"], # 180-day boundary at Apr 1
        ["2", "A", "2024-01-02", 20, "completed", "Home"], # 90-day boundary
        ["3", "A", "2024-03-02", 30, "completed", "Home"], # 30-day boundary
        ["4", "A", "2024-03-31", 900, "cancelled", "Home"],
        ["5", "A", "2024-04-01", 40, "completed", "Home"], # outcome only
        ["6", "B", "2024-03-30", 50, "completed", "Home"], # new user excluded
        ["7", "C", "2023-10-03", 60, "completed", "Home"], # 181 days -> dormant excluded
        ["8", "D", "2024-03-01", 70, "completed", "Home"],
        ["9", "D", "2024-06-30", 80, "completed", "Home"]], # at horizon end -> not label purchase
        columns=["order_id", "customer_id", "order_date", "amount", "status", "category"])
    return validate_data(customers, orders, "2024-07-01")


def test_feature_boundaries_and_eligibility(small_data):
    users, orders = small_data
    f = build_features(users, orders, "2024-04-01", "2024-07-01").set_index("customer_id")
    assert set(f.index) == {"A", "D"}
    assert f.loc["A", ["orders_30d", "orders_90d", "orders_180d"]].tolist() == [1, 2, 3]
    assert f.loc["A", "revenue_180d"] == 60
    assert f.loc["A", "recency_days"] == 30
    assert f.loc["A", "churned"] == 0
    assert f.loc["D", "churned"] == 1
    assert np.isnan(f.loc["D", "avg_gap_days"])


def test_future_changes_cannot_change_features(small_data):
    users, orders = small_data
    before = build_features(users, orders, "2024-04-01", "2024-07-01")
    altered = orders.copy()
    altered.loc[altered.order_date.ge("2024-04-01"), "amount"] = 1000000
    altered.loc[altered.order_date.ge("2024-04-01"), "status"] = "refunded"
    after = build_features(users, altered, "2024-04-01", "2024-07-01")
    pd.testing.assert_frame_equal(before.drop(columns="churned"), after.drop(columns="churned"))
    assert before.churned.tolist() != after.churned.tolist()


def test_censored_outcomes_rejected(small_data):
    users, orders = small_data
    with pytest.raises(ValueError, match="full 90-day"):
        build_features(users, orders, "2024-06-01", "2024-07-01")
    assert "churned" not in build_features(users, orders, "2024-07-01", "2024-07-01", False)


def test_sql_matches_pandas(small_data, tmp_path):
    users, orders = small_data
    path = tmp_path / "retention.sqlite"
    make_database(users, orders, path)
    current = build_features(users, orders, "2024-07-01", "2024-07-01", False)
    cohorts = cohort_retention(users, orders, "2024-07-01")
    sales = monthly_sales(users, orders, "2024-07-01")
    result = verify_sql(path, current, cohorts, "2024-07-01", sales)
    assert result["sql_pandas_match"]
    with sqlite3.connect(path) as db:
        labels = pd.read_sql_query((Path(__file__).parents[1] / "sql/churn_labels.sql").read_text(), db,
                                   params={"snapshot_date": "2024-04-01", "observation_end": "2024-07-01"})
    features = build_features(users, orders, "2024-04-01", "2024-07-01")
    pd.testing.assert_frame_equal(labels, features[["customer_id", "snapshot_date", "churned"]], check_dtype=False)


def test_cohorts_omit_future_and_partial_months(small_data):
    users, orders = small_data
    cohorts = cohort_retention(users, orders, "2024-06-15")
    assert cohorts.retention_rate.between(0, 1).all()
    assert cohorts.loc[cohorts.cohort_month.eq("2024-02-01"), "month_index"].tolist() == [0, 1, 2, 3]
    sales = monthly_sales(users, orders, "2024-06-15")
    assert sales.month.max() == pd.Timestamp("2024-05-01")
    assert sales.revenue.sum() == 280


@pytest.mark.parametrize("problem", ["duplicate", "orphan", "negative", "signup", "end"])
def test_input_errors_are_actionable(small_data, problem):
    users, orders = [f.copy() for f in small_data]
    if problem == "duplicate":
        orders.loc[1, "order_id"] = orders.loc[0, "order_id"]
    elif problem == "orphan":
        orders.loc[0, "customer_id"] = "MISSING"
    elif problem == "negative":
        orders.loc[0, "amount"] = -1
    elif problem == "signup":
        orders.loc[0, "order_date"] = pd.Timestamp("2022-01-01")
    else:
        orders.loc[0, "order_date"] = pd.Timestamp("2024-07-01")
    with pytest.raises(ValueError):
        validate_data(users, orders, "2024-07-01")


def test_demo_is_reproducible(tmp_path):
    a, b = generate_demo(tmp_path / "first", 100, 7)
    c, d = generate_demo(tmp_path / "second", 100, 7)
    pd.testing.assert_frame_equal(a, c)
    pd.testing.assert_frame_equal(b, d)
    validate_data(a, b, "2026-10-01")
