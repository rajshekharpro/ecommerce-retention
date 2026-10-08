"""Temporal churn evaluation, production refit, and actionable customer exports."""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    fbeta_score,
    log_loss,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


SEED = 42
NUMERIC_FEATURES = [
    "tenure_days", "recency_days", "orders_30d", "orders_90d", "orders_180d",
    "revenue_90d", "revenue_180d", "lifetime_orders", "lifetime_revenue",
    "avg_order_value", "avg_gap_days", "gap_std_days", "days_to_first_order",
    "order_trend",
]
CATEGORICAL_FEATURES = ["acquisition_channel", "country"]
PREDICTORS = NUMERIC_FEATURES + CATEGORICAL_FEATURES
TRAIN_END = pd.Timestamp("2025-10-01")
VALIDATION_DATE = pd.Timestamp("2026-01-01")
TEST_DATE = pd.Timestamp("2026-04-01")


def _predictors(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(set(PREDICTORS) - set(frame.columns))
    if missing:
        raise ValueError(f"Missing model features: {missing}")
    result = frame[PREDICTORS].copy()
    for name in NUMERIC_FEATURES:
        result[name] = pd.to_numeric(result[name], errors="raise").astype(float)
        if np.isinf(result[name].to_numpy()).any():
            raise ValueError(f"Feature {name} contains infinite values")
    for name in CATEGORICAL_FEATURES:
        result[name] = result[name].fillna("Unknown").astype(str)
    return result


def build_pipeline(model_name: str) -> Pipeline:
    """Create an unfitted pipeline; every learned transform is fit within a split."""
    numeric = Pipeline([
        ("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)),
        ("scale", StandardScaler()),
    ])
    preprocess = ColumnTransformer([
        ("numeric", numeric, NUMERIC_FEATURES),
        ("categorical", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL_FEATURES),
    ])
    if model_name == "logistic_regression":
        classifier = LogisticRegression(C=0.5, max_iter=1500, random_state=SEED)
    elif model_name == "hist_gradient_boosting":
        classifier = HistGradientBoostingClassifier(
            learning_rate=0.07, max_iter=160, max_leaf_nodes=15,
            min_samples_leaf=30, l2_regularization=2.0, early_stopping=False,
            random_state=SEED,
        )
    else:
        raise ValueError(f"Unknown model: {model_name}")
    return Pipeline([("preprocess", preprocess), ("classifier", classifier)])


def choose_threshold(y_true: np.ndarray, probabilities: np.ndarray) -> float:
    """Maximize validation F2, favoring recall; ties choose the higher threshold."""
    precision, recall, thresholds = precision_recall_curve(y_true, probabilities)
    scores = np.divide(
        5 * precision[:-1] * recall[:-1],
        4 * precision[:-1] + recall[:-1],
        out=np.zeros_like(thresholds),
        where=(4 * precision[:-1] + recall[:-1]) > 0,
    )
    if len(thresholds) == 0:
        return 0.5
    best = np.flatnonzero(np.isclose(scores, scores.max(), rtol=1e-10, atol=1e-12))[-1]
    return float(thresholds[best])


def _metrics(y_true: np.ndarray, probabilities: np.ndarray, threshold: float) -> dict:
    predicted = probabilities >= threshold
    both_classes = len(np.unique(y_true)) == 2
    return {
        "n": int(len(y_true)),
        "churn_rate": float(np.mean(y_true)),
        "roc_auc": float(roc_auc_score(y_true, probabilities)) if both_classes else None,
        "average_precision": float(average_precision_score(y_true, probabilities)) if np.any(y_true) else 0.0,
        "brier": float(brier_score_loss(y_true, probabilities)),
        "log_loss": float(log_loss(y_true, probabilities, labels=[0, 1])),
        "threshold": float(threshold),
        "precision": float(precision_score(y_true, predicted, zero_division=0)),
        "recall": float(recall_score(y_true, predicted, zero_division=0)),
        "f2": float(fbeta_score(y_true, predicted, beta=2, zero_division=0)),
        "confusion_matrix": confusion_matrix(y_true, predicted, labels=[0, 1]).tolist(),
    }


def _top_decile_lift(y_true: np.ndarray, probabilities: np.ndarray) -> float | None:
    prevalence = float(np.mean(y_true))
    if prevalence == 0:
        return None
    count = max(1, int(np.ceil(len(y_true) * 0.1)))
    order = np.argsort(-probabilities, kind="stable")[:count]
    return float(np.mean(y_true[order]) / prevalence)


def _validate_split_maturity(dates: pd.Series, masks: dict) -> None:
    """Explicitly reject outcomes that cross an evaluation boundary."""
    for earlier, later in (("train", "validation"), ("validation", "test")):
        last_outcome_end = dates.loc[masks[earlier]].max() + pd.Timedelta(days=90)
        next_snapshot = dates.loc[masks[later]].min()
        if last_outcome_end > next_snapshot:
            raise ValueError(f"The {earlier} outcome window overlaps the {later} snapshot")


def score_customers(model: Pipeline, current: pd.DataFrame, high_threshold: float) -> pd.DataFrame:
    """Produce transparent risk bands, heuristic segments, and revenue exposure."""
    result = current.copy()
    probabilities = model.predict_proba(_predictors(current))[:, 1]
    result["churn_probability"] = probabilities
    result["risk_band"] = np.select(
        [probabilities >= high_threshold, probabilities >= high_threshold / 2],
        ["High", "Medium"], default="Low",
    )
    # The value boundary is a descriptive production-segmentation rule, not a model input.
    value_boundary = float(current["revenue_180d"].quantile(0.75))
    valuable = (current["revenue_180d"] >= value_boundary) & (current["revenue_180d"] > 0)
    high = result["risk_band"].eq("High")
    dormant = current["orders_90d"].eq(0)
    new = current["tenure_days"].le(180)
    prior_60d_orders = current["orders_90d"] - current["orders_30d"]
    declining = prior_60d_orders.ge(2) & current["orders_30d"].lt(prior_60d_orders / 2)
    result["segment"] = np.select(
        [high & valuable, high & dormant, high, new, valuable],
        ["At-risk high value", "Dormant buyers", "At-risk active", "Developing customers", "Loyal high value"],
        default="Steady customers",
    )
    result["reason"] = np.select(
        [dormant, declining, current["recency_days"].gt(60), new],
        ["No completed purchase in the past 90 days", "Purchase frequency declined", "More than 60 days since last purchase", "Early customer lifecycle"],
        default="Recent purchase pattern and customer history",
    )
    actions = {
        "At-risk high value": "Test personal outreach with a relevant product recommendation",
        "Dormant buyers": "Test a win-back message; use a randomized holdout",
        "At-risk active": "Test a timely replenishment reminder or tailored offer",
        "Developing customers": "Offer onboarding guidance and a second-purchase recommendation",
        "Loyal high value": "Offer loyalty recognition and early product access",
        "Steady customers": "Maintain relevant lifecycle messaging and monitor engagement",
    }
    result["recommended_action"] = result["segment"].map(actions)
    result["expected_90d_revenue"] = current["revenue_180d"].clip(lower=0) / 2
    result["revenue_at_risk"] = result["expected_90d_revenue"] * probabilities
    return result


def run_modeling(features: pd.DataFrame, current: pd.DataFrame, output_dir: str | Path):
    """Compare on validation, evaluate once on test, then refit all mature labels."""
    output_dir = Path(output_dir)
    for directory in ("models", "reports", "tableau"):
        (output_dir / directory).mkdir(parents=True, exist_ok=True)
    if features.empty or current.empty:
        raise ValueError("Labeled and current customer features must both be nonempty")
    if "churned" not in features or not features["churned"].isin([0, 1]).all():
        raise ValueError("Every labeled row must have a binary, non-missing churned label")
    dates = pd.to_datetime(features["snapshot_date"], utc=True).dt.tz_localize(None)
    current_dates = pd.to_datetime(current["snapshot_date"], utc=True).dt.tz_localize(None)
    if current_dates.nunique() != 1:
        raise ValueError("Production scores require exactly one snapshot_date")
    if features.duplicated(["customer_id", "snapshot_date"]).any() or current["customer_id"].duplicated().any():
        raise ValueError("Customer/snapshot observations must be unique")
    if dates.isna().any() or current_dates.isna().any():
        raise ValueError("snapshot_date cannot be missing")
    if (dates + pd.Timedelta(days=90) > current_dates.iloc[0]).any():
        raise ValueError("A labeled outcome extends past the production observation boundary")
    masks = {
        "train": dates <= TRAIN_END,
        "validation": dates == VALIDATION_DATE,
        "test": dates == TEST_DATE,
    }
    for split, mask in masks.items():
        if not mask.any():
            raise ValueError(f"Missing {split} observations for the configured temporal split")
        if features.loc[mask, "churned"].nunique() != 2:
            raise ValueError(f"The {split} split needs both churn classes")
    _validate_split_maturity(dates, masks)
    x = _predictors(features)
    y = features["churned"].to_numpy(dtype=int)
    x_train, y_train = x.loc[masks["train"]], y[masks["train"]]
    x_val, y_val = x.loc[masks["validation"]], y[masks["validation"]]
    x_test, y_test = x.loc[masks["test"]], y[masks["test"]]
    fitted, candidate_metrics = {}, {}
    for name in ("logistic_regression", "hist_gradient_boosting"):
        candidate = build_pipeline(name)
        candidate.fit(x_train, y_train)
        val_prob = candidate.predict_proba(x_val)[:, 1]
        candidate_metrics[name] = _metrics(y_val, val_prob, 0.5)
        fitted[name] = candidate
    selected_name = max(
        candidate_metrics,
        key=lambda name: (candidate_metrics[name]["average_precision"], -candidate_metrics[name]["log_loss"]),
    )
    evaluation_model = fitted[selected_name]
    val_prob = evaluation_model.predict_proba(x_val)[:, 1]
    high_threshold = choose_threshold(y_val, val_prob)
    # The held-out test is touched only after the model and operating threshold are fixed.
    test_prob = evaluation_model.predict_proba(x_test)[:, 1]
    baseline_prob = np.full(len(y_test), y_train.mean())
    importance = permutation_importance(
        evaluation_model, x_val, y_val, scoring="average_precision", n_repeats=3,
        random_state=SEED, max_samples=min(4000, len(x_val)), n_jobs=1,
    )
    importance_frame = pd.DataFrame({
        "feature": PREDICTORS,
        "importance": importance.importances_mean,
        "importance_std": importance.importances_std,
    }).sort_values("importance", ascending=False)
    metrics = {
        "selected_model": selected_name,
        "selection_metric": "Maximum validation average precision; log loss breaks ties",
        "label_definition": "No completed order in the next 90 calendar days",
        "eligible_population": "At least 90 days tenure and a completed purchase in the preceding 180 days",
        "split_policy": {
            "train_end": str(TRAIN_END.date()), "validation": str(VALIDATION_DATE.date()),
            "test": str(TEST_DATE.date()), "outcome_days": 90,
            "description": "Chronological customer panels; customers can recur across snapshots. No future outcome crosses a split boundary.",
        },
        "split_counts": {name: int(mask.sum()) for name, mask in masks.items()},
        "candidates": candidate_metrics,
        "validation": _metrics(y_val, val_prob, high_threshold),
        "test": _metrics(y_test, test_prob, high_threshold),
        "baseline_test": _metrics(y_test, baseline_prob, high_threshold),
        "top_decile_lift": _top_decile_lift(y_test, test_prob),
        "risk_thresholds": {
            "high": high_threshold, "medium": high_threshold / 2,
            "selection": "High maximizes validation F2; Medium starts at half the High threshold",
        },
        "production_refit": {
            "rows": int(len(features)), "last_snapshot": str(dates.max().date()),
            "score_date": str(current_dates.iloc[0].date()),
            "note": "Production refit uses all matured labels; test metrics describe the separate frozen evaluation model",
        },
        "revenue_definition": "Prior 180-day completed revenue / 2 × churn probability; exposure proxy, not causal revenue loss",
        "importance_definition": "Validation permutation decrease in average precision; 3 repeats, up to 4000 rows",
    }
    joblib.dump(evaluation_model, output_dir / "models" / "evaluation_model.joblib")
    production_model = build_pipeline(selected_name)
    production_model.fit(x, y)
    joblib.dump(production_model, output_dir / "models" / "production_model.joblib")
    scored = score_customers(production_model, current, high_threshold)
    scored.to_csv(output_dir / "tableau" / "customer_scores.csv", index=False)
    importance_frame.to_csv(output_dir / "tableau" / "feature_importance.csv", index=False)
    scored.groupby("segment", as_index=False).agg(
        customers=("customer_id", "size"), avg_churn_probability=("churn_probability", "mean"),
        revenue_at_risk=("revenue_at_risk", "sum"),
    ).to_csv(output_dir / "tableau" / "segment_summary.csv", index=False)
    for split, probabilities in (("validation", val_prob), ("test", test_prob)):
        export = features.loc[masks[split], ["customer_id", "snapshot_date", "churned"]].copy()
        export["churn_probability"] = probabilities
        export["predicted_churn"] = (probabilities >= high_threshold).astype(int)
        export.to_csv(output_dir / "reports" / f"{split}_predictions.csv", index=False)
    (output_dir / "reports" / "metrics.json").write_text(json.dumps(metrics, indent=2, allow_nan=False), encoding="utf-8")
    (output_dir / "models" / "feature_schema.json").write_text(json.dumps({
        "numeric": NUMERIC_FEATURES, "categorical": CATEGORICAL_FEATURES,
        "excluded": ["customer_id", "snapshot_date", "signup_date", "churned"],
        "risk_thresholds": metrics["risk_thresholds"],
    }, indent=2), encoding="utf-8")
    return scored, metrics
