"""Checks for chronological isolation, feature exclusion, and score accounting."""

import json

import joblib
import numpy as np
import pandas as pd
import pytest

from retention.modeling import (
    NUMERIC_FEATURES, PREDICTORS, _validate_split_maturity,
    choose_threshold, run_modeling, score_customers,
)


def synthetic_features(seed=17):
    rng = np.random.default_rng(seed)
    frames = []
    for date in ["2024-01-01", "2025-10-01", "2026-01-01", "2026-04-01", "2026-07-01", "2026-10-01"]:
        n = 130
        recency = rng.uniform(0, 179, n)
        revenue = rng.lognormal(5.1, 0.6, n)
        orders90 = np.maximum(0, np.round((100 - recency) / 20)).astype(int)
        probability = 1 / (1 + np.exp(-(recency - 100) / 25))
        frame = pd.DataFrame({
            "customer_id": [f"C{i:04}" for i in range(n)], "snapshot_date": date,
            "signup_date": "2023-01-01", "acquisition_channel": rng.choice(["Search", "Email"], n),
            "country": rng.choice(["US", "GB"], n), "tenure_days": rng.integers(90, 1000, n),
            "recency_days": recency, "orders_30d": (recency < 30).astype(int),
            "orders_90d": orders90, "orders_180d": orders90 + 1,
            "revenue_90d": revenue * 0.5, "revenue_180d": revenue,
            "lifetime_orders": orders90 + 6, "lifetime_revenue": revenue * 3,
            "avg_order_value": revenue / (orders90 + 1), "avg_gap_days": np.where(orders90 == 0, np.nan, 28),
            "gap_std_days": np.nan, "days_to_first_order": rng.integers(0, 20, n),
            "order_trend": rng.normal(0, 0.5, n), "churned": rng.binomial(1, probability),
        })
        frames.append(frame)
    labeled = pd.concat(frames[:-1], ignore_index=True)
    current = frames[-1].drop(columns="churned")
    current.loc[0, "acquisition_channel"] = "Unseen production channel"
    return labeled, current


@pytest.fixture(scope="module")
def fitted_artifacts(tmp_path_factory):
    root = tmp_path_factory.mktemp("model")
    features, current = synthetic_features()
    scores, metrics = run_modeling(features, current, root)
    return root, features, current, scores, metrics


def test_pipeline_uses_training_only_and_excludes_identifiers(fitted_artifacts):
    root, features, _, _, metrics = fitted_artifacts
    frozen_model = joblib.load(root / "models" / "evaluation_model.joblib")
    imputer = frozen_model.named_steps["preprocess"].named_transformers_["numeric"].named_steps["imputer"]
    train = features.loc[features["snapshot_date"] <= "2025-10-01"]
    expected_medians = train[NUMERIC_FEATURES].median().fillna(0).to_numpy()
    np.testing.assert_allclose(imputer.statistics_, expected_medians)
    assert set(frozen_model.feature_names_in_) == set(PREDICTORS)
    assert {"customer_id", "snapshot_date", "signup_date", "churned"}.isdisjoint(PREDICTORS)
    assert metrics["split_counts"] == {"train": 260, "validation": 130, "test": 130}
    assert metrics["production_refit"]["rows"] == len(features)
    assert metrics["test"]["roc_auc"] > 0.7


def test_scoring_is_complete_and_revenue_is_a_proxy(fitted_artifacts):
    root, _, current, scores, metrics = fitted_artifacts
    assert len(scores) == len(current)
    assert scores["churn_probability"].between(0, 1).all()
    np.testing.assert_allclose(scores["revenue_at_risk"], current["revenue_180d"] / 2 * scores["churn_probability"])
    high = metrics["risk_thresholds"]["high"]
    assert scores.loc[scores["churn_probability"] >= high, "risk_band"].eq("High").all()
    assert not scores[["segment", "reason", "recommended_action"]].isna().any().any()
    assert set(scores["risk_band"]) <= {"Low", "Medium", "High"}
    assert (root / "tableau" / "segment_summary.csv").exists()
    assert len(pd.read_csv(root / "tableau" / "feature_importance.csv")) == len(PREDICTORS)
    assert json.loads((root / "reports" / "metrics.json").read_text())["selected_model"] == metrics["selected_model"]
    baseline = metrics["baseline_test"]
    assert baseline["roc_auc"] == 0.5
    assert baseline["average_precision"] == pytest.approx(baseline["churn_rate"])


def test_test_labels_do_not_affect_model_selection_or_threshold(fitted_artifacts, tmp_path):
    _, features, current, _, first_metrics = fitted_artifacts
    changed = features.copy()
    heldout = changed["snapshot_date"].eq("2026-04-01")
    changed.loc[heldout, "churned"] = 1 - changed.loc[heldout, "churned"]
    _, metrics = run_modeling(changed, current, tmp_path)
    assert metrics["selected_model"] == first_metrics["selected_model"]
    assert metrics["risk_thresholds"] == first_metrics["risk_thresholds"]
    assert metrics["validation"] == first_metrics["validation"]
    assert metrics["test"]["roc_auc"] == pytest.approx(1 - first_metrics["test"]["roc_auc"])


def test_unmatured_labels_are_rejected(tmp_path):
    features, current = synthetic_features()
    features.loc[0, "snapshot_date"] = "2026-09-01"
    with pytest.raises(ValueError, match="outcome extends"):
        run_modeling(features, current, tmp_path)


def test_threshold_is_maximum_f2():
    from sklearn.metrics import fbeta_score
    y = np.array([0, 0, 0, 1, 1, 1])
    probability = np.array([0.1, 0.2, 0.6, 0.35, 0.7, 0.9])
    selected = choose_threshold(y, probability)
    expected = max(fbeta_score(y, probability >= t, beta=2) for t in np.unique(probability))
    assert fbeta_score(y, probability >= selected, beta=2) == pytest.approx(expected)


@pytest.mark.parametrize("dates", [
    ["2025-11-01", "2026-01-01", "2026-04-01"],
    ["2025-10-01", "2026-01-15", "2026-04-01"],
])
def test_cross_split_outcome_overlap_is_rejected(dates):
    dates = pd.Series(pd.to_datetime(dates))
    masks = {name: dates.index == index for index, name in enumerate(["train", "validation", "test"])}
    with pytest.raises(ValueError, match="outcome window overlaps"):
        _validate_split_maturity(dates, masks)


def test_declining_reason_compares_equal_length_purchase_rates():
    class FixedModel:
        def predict_proba(self, frame):
            return np.tile([0.3, 0.7], (len(frame), 1))

    _, current = synthetic_features()
    current = current.head(3).copy()
    current["orders_30d"] = [1, 2, 0]
    current["orders_90d"] = [5, 6, 1]
    current["recency_days"] = 10
    current["tenure_days"] = 500
    current["order_trend"] = [1 / 3, 2 / 3, 0]
    scores = score_customers(FixedModel(), current, 0.5)
    assert scores.iloc[0]["reason"] == "Purchase frequency declined"
    assert scores.iloc[1]["reason"] != "Purchase frequency declined"  # Equal monthly rate.
    assert scores.iloc[2]["reason"] != "Purchase frequency declined"  # Too little prior evidence.
