"""Forecast checks exercise the baseline, horizon alignment, and temporal isolation."""

import numpy as np
import pandas as pd
import pytest

from retention.forecasting import predict_revenue, run_forecast


def monthly_frame(revenues):
    return pd.DataFrame({
        "month": pd.date_range("2023-01-01", periods=len(revenues), freq="MS"),
        "revenue": revenues, "orders": 100, "active_customers": 80,
    })


def test_perfect_seasonality_selects_baseline_and_preserves_actuals(tmp_path):
    y = 1000 + 200 * np.sin(2 * np.pi * np.arange(45) / 12)
    # Tiling avoids floating point period differences and gives an exactly known baseline.
    y = np.resize(y[:12], 45)
    result, metrics = run_forecast(monthly_frame(y), tmp_path)
    assert metrics["selected_model"] == "seasonal_naive"
    assert metrics["baseline"]["mae"] == pytest.approx(0)
    assert metrics["rolling_origin_cv"]["origins"] == 21
    assert metrics["horizon_metrics"]["6"]["n_predictions"] == 16
    actual = result.loc[result["series"] == "Actual"]
    future = result.loc[result["series"] == "Forecast"]
    np.testing.assert_allclose(actual["revenue"], y)
    assert actual[["forecast", "lower", "upper"]].isna().all().all()
    assert len(future) == 6 and future["revenue"].isna().all()
    assert future["month"].tolist() == ["2026-10-01", "2026-11-01", "2026-12-01", "2027-01-01", "2027-02-01", "2027-03-01"]
    assert future["lower"].ge(0).all()
    assert future["lower"].le(future["forecast"]).all()
    assert future["upper"].ge(future["forecast"]).all()


def test_growth_and_seasonality_benefit_from_trend_model(tmp_path):
    months = np.arange(45)
    y = np.expm1(7 + 0.025 * months + 0.20 * np.sin(2 * np.pi * months / 12))
    _, metrics = run_forecast(monthly_frame(y), tmp_path)
    assert metrics["selected_model"] == "ridge_seasonal_trend"
    assert metrics["selected"]["mae"] < metrics["baseline"]["mae"] * 0.3


def test_forecast_origin_does_not_use_future_values(tmp_path):
    y = np.exp(6 + 0.01 * np.arange(36))
    frame = monthly_frame(y)
    run_forecast(frame, tmp_path / "first")
    frame.loc[frame.index[-1], "revenue"] *= 10
    run_forecast(frame, tmp_path / "second")
    first = pd.read_csv(tmp_path / "first" / "reports" / "forecast_backtest.csv")
    second = pd.read_csv(tmp_path / "second" / "reports" / "forecast_backtest.csv")
    # The modified final actual is never training history for any evaluated origin.
    np.testing.assert_allclose(first["prediction"], second["prediction"])
    assert not np.array_equal(first["actual"], second["actual"])


def test_missing_months_and_incomplete_history_are_rejected(tmp_path):
    frame = monthly_frame(np.ones(36) * 100)
    with pytest.raises(ValueError, match="consecutive"):
        run_forecast(frame.drop(index=20), tmp_path)
    with pytest.raises(ValueError, match="at least 30"):
        run_forecast(frame.head(24), tmp_path)


def test_seasonal_recursion_and_zero_revenue(tmp_path):
    y = np.arange(1, 25, dtype=float)
    result = predict_revenue(y, 18, "seasonal_naive")
    np.testing.assert_allclose(result, np.resize(y[-12:], 18))
    _, metrics = run_forecast(monthly_frame(np.zeros(36)), tmp_path)
    assert metrics["selected"]["wape"] is None
    assert metrics["selected"]["mae"] == 0


def test_additive_drift_recovers_linear_seasonal_growth(tmp_path):
    months = np.arange(45)
    y = 1000 + 20 * months + 100 * np.sin(2 * np.pi * months / 12)
    result, metrics = run_forecast(monthly_frame(y), tmp_path)
    assert metrics["selected_model"] == "seasonal_naive_drift"
    assert metrics["selected"]["mae"] == pytest.approx(0, abs=1e-10)
    future_months = np.arange(45, 51)
    expected = 1000 + 20 * future_months + 100 * np.sin(2 * np.pi * future_months / 12)
    np.testing.assert_allclose(result.loc[result["series"] == "Forecast", "forecast"], expected)
    # Historical revenue that is falling faster than its remaining value cannot predict negatives.
    assert (predict_revenue(np.r_[np.full(12, 100), np.full(12, 10)], 6, "seasonal_naive_drift") == 0).all()
