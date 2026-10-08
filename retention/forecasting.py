"""Monthly revenue forecasting with rolling-origin comparison and honest intervals."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge


FORECAST_HORIZON = 6
MIN_TRAIN_MONTHS = 24


def _calendar_features(indices: np.ndarray) -> np.ndarray:
    indices = np.asarray(indices, dtype=float)
    return np.column_stack([
        indices / 12,
        np.sin(2 * np.pi * indices / 12), np.cos(2 * np.pi * indices / 12),
        np.sin(4 * np.pi * indices / 12), np.cos(4 * np.pi * indices / 12),
    ])


def predict_revenue(history: np.ndarray, horizon: int, model_name: str) -> np.ndarray:
    """Predict from the supplied history only; never reads future actual values."""
    history = np.asarray(history, dtype=float)
    if len(history) < 12 or horizon < 1:
        raise ValueError("Forecasting requires at least 12 months and a positive horizon")
    if not np.isfinite(history).all() or (history < 0).any():
        raise ValueError("Monthly revenue must be finite and nonnegative")
    if model_name in ("seasonal_naive", "seasonal_naive_drift"):
        annual_drift = 0.0
        if model_name == "seasonal_naive_drift":
            if len(history) < 24:
                raise ValueError("Seasonal drift requires at least 24 months")
            annual_drift = float(history[-12:].mean() - history[-24:-12].mean())
        # Recursion is valid for horizons longer than a single seasonal period.
        series = list(history)
        for _ in range(horizon):
            series.append(max(0.0, series[-12] + annual_drift))
        return np.asarray(series[-horizon:])
    if model_name == "ridge_seasonal_trend":
        model = Ridge(alpha=1.0)
        model.fit(_calendar_features(np.arange(len(history))), np.log1p(history))
        predicted_log = model.predict(_calendar_features(np.arange(len(history), len(history) + horizon)))
        return np.maximum(np.expm1(predicted_log), 0)
    raise ValueError(f"Unknown forecast model: {model_name}")


def _error_metrics(actual: np.ndarray, predicted: np.ndarray) -> dict:
    errors = np.asarray(actual) - np.asarray(predicted)
    denominator = float(np.abs(actual).sum())
    return {
        "mae": float(np.mean(np.abs(errors))),
        "rmse": float(np.sqrt(np.mean(errors ** 2))),
        "wape": float(np.abs(errors).sum() / denominator) if denominator else None,
        "n_predictions": int(len(errors)),
    }


def run_forecast(monthly_sales: pd.DataFrame, output_dir: str | Path):
    output_dir = Path(output_dir)
    (output_dir / "tableau").mkdir(parents=True, exist_ok=True)
    (output_dir / "reports").mkdir(parents=True, exist_ok=True)
    if not {"month", "revenue"}.issubset(monthly_sales.columns):
        raise ValueError("monthly_sales must contain month and revenue")
    frame = monthly_sales[["month", "revenue"]].copy()
    frame["month"] = pd.to_datetime(frame["month"], utc=True).dt.tz_localize(None)
    frame["revenue"] = pd.to_numeric(frame["revenue"], errors="raise").astype(float)
    frame = frame.sort_values("month").reset_index(drop=True)
    if frame["month"].isna().any() or frame["month"].duplicated().any():
        raise ValueError("Each observed calendar month must occur exactly once")
    if len(frame) < MIN_TRAIN_MONTHS + FORECAST_HORIZON:
        raise ValueError("Provide at least 30 complete monthly observations for rolling evaluation")
    expected_months = pd.date_range(frame["month"].iloc[0], periods=len(frame), freq="MS")
    if not pd.DatetimeIndex(frame["month"]).equals(expected_months):
        raise ValueError("month must be consecutive first-of-month dates; explicitly fill zero-sales months")
    y = frame["revenue"].to_numpy()
    if not np.isfinite(y).all() or (y < 0).any():
        raise ValueError("Monthly revenue must be finite and nonnegative")
    records = []
    model_names = ("seasonal_naive", "seasonal_naive_drift", "ridge_seasonal_trend")
    for origin in range(MIN_TRAIN_MONTHS, len(frame)):
        available_horizon = min(FORECAST_HORIZON, len(frame) - origin)
        for model_name in model_names:
            predictions = predict_revenue(y[:origin], available_horizon, model_name)
            for offset, predicted in enumerate(predictions):
                records.append({
                    "model": model_name, "origin": str(frame["month"].iloc[origin - 1].date()),
                    "month": str(frame["month"].iloc[origin + offset].date()),
                    "horizon": offset + 1, "actual": float(y[origin + offset]),
                    "prediction": float(predicted), "residual": float(y[origin + offset] - predicted),
                })
    backtest = pd.DataFrame(records)
    candidate_metrics = {}
    for model_name in model_names:
        subset = backtest.loc[backtest["model"] == model_name]
        candidate_metrics[model_name] = _error_metrics(subset["actual"].to_numpy(), subset["prediction"].to_numpy())
    # MAE and pooled WAPE rank these models identically on the same target observations.
    selected_model = min(candidate_metrics, key=lambda name: candidate_metrics[name]["mae"])
    selected_backtest = backtest.loc[backtest["model"] == selected_model]
    point_forecast = predict_revenue(y, FORECAST_HORIZON, selected_model)
    lower, upper, horizon_metrics = [], [], {}
    for horizon, forecast in enumerate(point_forecast, start=1):
        subset = selected_backtest.loc[selected_backtest["horizon"] == horizon]
        residuals = subset["residual"].to_numpy()
        # Ensure the point estimate remains inside the interval even with biased residuals.
        low_residual, high_residual = np.quantile(residuals, [0.1, 0.9])
        lower.append(float(max(0, forecast + min(0, low_residual))))
        upper.append(float(forecast + max(0, high_residual)))
        horizon_metrics[str(horizon)] = {
            **_error_metrics(subset["actual"].to_numpy(), subset["prediction"].to_numpy()),
            "residual_q10": float(low_residual), "residual_q90": float(high_residual),
        }
    future_months = pd.date_range(frame["month"].iloc[-1] + pd.offsets.MonthBegin(1), periods=FORECAST_HORIZON, freq="MS")
    actual = pd.DataFrame({
        "month": frame["month"].dt.strftime("%Y-%m-%d"), "revenue": y,
        "forecast": np.nan, "lower": np.nan, "upper": np.nan, "series": "Actual",
    })
    future = pd.DataFrame({
        "month": future_months.strftime("%Y-%m-%d"), "revenue": np.nan,
        "forecast": point_forecast, "lower": lower, "upper": upper, "series": "Forecast",
    })
    result = pd.concat([actual, future], ignore_index=True)
    metrics = {
        "selected_model": selected_model,
        "selection_metric": "Pooled rolling-origin MAE over observed horizons 1 to 6",
        "rolling_origin_cv": {
            "initial_training_months": MIN_TRAIN_MONTHS, "max_horizon_months": FORECAST_HORIZON,
            "origins": len(frame) - MIN_TRAIN_MONTHS,
            "first_origin": str(frame["month"].iloc[MIN_TRAIN_MONTHS - 1].date()),
            "last_origin": str(frame["month"].iloc[-2].date()),
            "note": "Expanding history; future actuals are unavailable to each fit. Recent origins contribute only observed horizons. Model selection and reported errors share these folds; there is no independent forecast test set.",
        },
        "candidates": candidate_metrics,
        "baseline": candidate_metrics["seasonal_naive"],
        "selected": candidate_metrics[selected_model],
        "horizon_metrics": horizon_metrics,
        "forecast_months": FORECAST_HORIZON,
        "forecast_revenue_total": float(point_forecast.sum()),
        "intervals": {
            "nominal_coverage": 0.8,
            "method": "Per-horizon rolling residual 10th/90th percentiles, widened to contain point forecast and clipped at zero",
            "limitation": "Approximate empirical ranges from limited, overlapping folds used for model selection; no guaranteed coverage or formal confidence level. Do not sum bounds into a total-revenue interval.",
        },
        "observed_months": len(frame), "last_observed_month": str(frame["month"].iloc[-1].date()),
    }
    result.to_csv(output_dir / "tableau" / "sales_forecast.csv", index=False)
    backtest.to_csv(output_dir / "reports" / "forecast_backtest.csv", index=False)
    (output_dir / "reports" / "forecast_metrics.json").write_text(json.dumps(metrics, indent=2, allow_nan=False), encoding="utf-8")
    return result, metrics
