"""End-to-end build: raw data -> SQL/Pandas -> models -> dashboard exports."""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import re
import sqlite3
import numpy as np
import pandas as pd
from .data import OBSERVATION_END, generate_demo, load_data
from .features import build_features, cohort_retention, monthly_sales

ROOT = Path(__file__).resolve().parents[1]


def make_database(customers, orders, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Rebuild only this project's named generated database.
    if path.exists():
        path.unlink()
    with sqlite3.connect(path) as db:
        db.executescript((ROOT / "sql/schema.sql").read_text(encoding="utf-8"))
        for name, frame, date_col in [("customers", customers, "signup_date"), ("orders", orders, "order_date")]:
            data = frame.copy()
            data[date_col] = data[date_col].dt.strftime("%Y-%m-%d")
            data.to_sql(name, db, if_exists="append", index=False)


def verify_sql(db_path, current, cohorts, observation_end, sales=None):
    with sqlite3.connect(db_path) as db:
        sql_features = pd.read_sql_query((ROOT / "sql/lifecycle_features.sql").read_text(), db,
                                         params={"snapshot_date": observation_end})
        sql_cohorts = pd.read_sql_query((ROOT / "sql/cohort_retention.sql").read_text(), db,
                                        params={"observation_end": observation_end})
        sql_sales = pd.read_sql_query((ROOT / "sql/monthly_sales.sql").read_text(), db,
                                      params={"observation_end": observation_end}, parse_dates=["month"])
    columns = [x for x in sql_features.columns if x not in ["customer_id", "snapshot_date"]]
    py = current.sort_values("customer_id").reset_index(drop=True)
    if py.customer_id.tolist() != sql_features.customer_id.tolist():
        raise AssertionError("SQL/Pandas feature populations differ")
    np.testing.assert_allclose(py[columns].to_numpy(float), sql_features[columns].to_numpy(float), rtol=1e-9, atol=1e-6)
    pd.testing.assert_frame_equal(cohorts.reset_index(drop=True), sql_cohorts.reset_index(drop=True),
                                  check_dtype=False, check_exact=False, atol=1e-10)
    if sales is not None:
        pd.testing.assert_frame_equal(sales.reset_index(drop=True), sql_sales.reset_index(drop=True),
                                      check_dtype=False, check_exact=False, atol=1e-6)
    return {"feature_rows": len(py), "feature_columns": len(columns), "cohort_rows": len(cohorts),
            "sales_months": len(sql_sales), "sql_pandas_match": True}


def records(frame):
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def run(raw_dir=ROOT / "data/raw", output_dir=ROOT / "artifacts", observation_end=OBSERVATION_END):
    from .modeling import run_modeling
    from .forecasting import run_forecast
    raw_dir, output_dir = Path(raw_dir), Path(output_dir)
    for folder in ["tableau", "reports", "models", "features"]:
        (output_dir / folder).mkdir(parents=True, exist_ok=True)
    customers, orders = load_data(raw_dir, observation_end)
    print(f"Validated {len(customers):,} customers and {len(orders):,} orders", flush=True)
    metadata_path = raw_dir / "metadata.json"
    source_meta = json.loads(metadata_path.read_text()) if metadata_path.exists() else {"synthetic": False, "currency": "USD"}
    db_path = output_dir / "retention.sqlite"
    make_database(customers, orders, db_path)
    snapshots = pd.date_range("2024-01-01", "2026-07-01", freq="QS")
    frames = [build_features(customers, orders, d, observation_end) for d in snapshots
              if d + pd.Timedelta(days=90) <= pd.Timestamp(observation_end)]
    if not frames:
        raise ValueError("No fully observed training snapshots; this project expects 2023-2026 demo history")
    features = pd.concat(frames, ignore_index=True)
    current = build_features(customers, orders, observation_end, observation_end, include_label=False)
    features.to_csv(output_dir / "features/labeled_snapshots.csv", index=False)
    current.to_csv(output_dir / "features/current_features.csv", index=False)
    cohorts = cohort_retention(customers, orders, observation_end)
    sales = monthly_sales(customers, orders, observation_end)
    validation = verify_sql(db_path, current, cohorts, observation_end, sales)
    (output_dir / "reports/data_validation.json").write_text(json.dumps(validation, indent=2))
    print(f"Verified SQL/Pandas agreement for {len(current):,} current customers", flush=True)
    cohorts.to_csv(output_dir / "tableau/cohorts.csv", index=False)
    sales.to_csv(output_dir / "tableau/monthly_sales.csv", index=False, date_format="%Y-%m-%d")
    scored, metrics = run_modeling(features, current, output_dir)
    metrics["synthetic_data"] = source_meta.get("synthetic", False)
    (output_dir / "reports/metrics.json").write_text(json.dumps(metrics, indent=2, allow_nan=False), encoding="utf-8")
    scored.to_csv(output_dir / "tableau/customer_scores.csv", index=False, date_format="%Y-%m-%d")
    segments = scored.groupby("segment", as_index=False).agg(customers=("customer_id", "count"),
        avg_churn_probability=("churn_probability", "mean"), revenue_at_risk=("revenue_at_risk", "sum"))
    segments.to_csv(output_dir / "tableau/segment_summary.csv", index=False)
    forecast, forecast_metrics = run_forecast(sales, output_dir)
    forecast_metrics["synthetic_data"] = source_meta.get("synthetic", False)
    (output_dir / "reports/forecast_metrics.json").write_text(json.dumps(forecast_metrics, indent=2, allow_nan=False), encoding="utf-8")
    importance_path = output_dir / "tableau/feature_importance.csv"
    importance = pd.read_csv(importance_path) if importance_path.exists() else pd.DataFrame(columns=["feature", "importance"])
    payload = {
        "meta": {"title": "E-Commerce Customer Retention & Churn Prediction", "currency": source_meta.get("currency", "USD"),
                 "synthetic": source_meta.get("synthetic", False), "observation_end": observation_end,
                 "score_date": observation_end, "customer_count": len(customers), "order_count": len(orders)},
        "customers": records(scored), "monthly_sales": records(sales), "forecast": records(forecast),
        "cohorts": records(cohorts), "feature_importance": records(importance),
        "metrics": metrics, "forecast_metrics": forecast_metrics}
    # JSON null represents missing measurements. Escape HTML-significant characters for safe embedding.
    serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e")
    (ROOT / "dashboard").mkdir(exist_ok=True)
    (ROOT / "dashboard/data.js").write_text("window.RETENTION_DATA=" + serialized + ";\n", encoding="utf-8")
    # A content hash prevents a local browser cache from showing results from a previous run.
    dashboard_index = ROOT / "dashboard/index.html"
    if dashboard_index.exists():
        revision = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]
        markup = re.sub(r'src="data\.js(?:\?[^\"]*)?"', f'src="data.js?v={revision}"',
                        dashboard_index.read_text(encoding="utf-8"))
        dashboard_index.write_text(markup, encoding="utf-8")
    (output_dir / "reports/dashboard_data.json").write_text(serialized, encoding="utf-8")
    print("Saved model, forecasts, Tableau tables and offline dashboard data", flush=True)
    builder = ROOT / "scripts/build_tableau.py"
    if builder.exists():
        spec = importlib.util.spec_from_file_location("build_tableau", builder)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.build_tableau(ROOT, output_dir)
        print("Built Tableau workbook and packaged workbook", flush=True)
    return payload


def main():
    parser = argparse.ArgumentParser(description="E-commerce retention analytics project")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="Generate fictional data and run the complete analytics pipeline")
    demo.add_argument("--customers", type=int, default=5000)
    demo.add_argument("--seed", type=int, default=42)
    analyze = sub.add_parser("run", help="Analyze existing customers.csv and orders.csv")
    analyze.add_argument("--raw-dir", type=Path, default=ROOT / "data/raw")
    analyze.add_argument("--observation-end", default=OBSERVATION_END)
    for command in [demo, analyze]:
        command.add_argument("--output-dir", type=Path, default=ROOT / "artifacts")
    args = parser.parse_args()
    if args.command == "demo":
        raw_dir = ROOT / "data/raw"
        generate_demo(raw_dir, n_customers=args.customers, seed=args.seed)
        run(raw_dir, args.output_dir)
    else:
        run(args.raw_dir, args.output_dir, args.observation_end)


if __name__ == "__main__":
    main()
