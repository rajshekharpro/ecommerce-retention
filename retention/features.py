"""Point-in-time Pandas features, completed-month cohorts, and sales summaries."""
import numpy as np
import pandas as pd

NUMERIC_FEATURES = ["tenure_days", "recency_days", "orders_30d", "orders_90d", "orders_180d",
                    "revenue_90d", "revenue_180d", "lifetime_orders", "lifetime_revenue",
                    "avg_order_value", "avg_gap_days", "gap_std_days", "days_to_first_order", "order_trend"]
CATEGORICAL_FEATURES = ["acquisition_channel", "country"]


def build_features(customers, orders, snapshot_date, observation_end, include_label=True):
    snapshot, end = pd.Timestamp(snapshot_date), pd.Timestamp(observation_end)
    horizon_end = snapshot + pd.Timedelta(days=90)
    if snapshot > end:
        raise ValueError("Snapshot cannot follow observation end")
    if include_label and horizon_end > end:
        raise ValueError("A full 90-day outcome window is required; use include_label=False for scoring")
    users = customers.copy()
    users["signup_date"] = pd.to_datetime(users.signup_date)
    purchases = orders.copy()
    purchases["order_date"] = pd.to_datetime(purchases.order_date)
    completed = purchases.loc[purchases.status.eq("completed")]
    history = completed.loc[completed.order_date.lt(snapshot)].sort_values(["customer_id", "order_date", "order_id"])
    lifetime = history.groupby("customer_id").agg(
        first_order=("order_date", "min"), last_order=("order_date", "max"),
        lifetime_orders=("order_id", "count"), lifetime_revenue=("amount", "sum"), avg_order_value=("amount", "mean"))
    base = users.set_index("customer_id").join(lifetime)
    base["tenure_days"] = (snapshot - base.signup_date).dt.days
    base["recency_days"] = (snapshot - base.last_order).dt.days
    base["days_to_first_order"] = (base.first_order - base.signup_date).dt.days
    for window in [30, 90, 180]:
        recent = history.loc[history.order_date.ge(snapshot - pd.Timedelta(days=window))]
        agg = recent.groupby("customer_id").agg(n=("order_id", "count"), amount=("amount", "sum"))
        base[f"orders_{window}d"] = agg.n.reindex(base.index, fill_value=0).astype(int)
        if window != 30:
            base[f"revenue_{window}d"] = agg.amount.reindex(base.index, fill_value=0.0)
    gaps = history.assign(gap=history.groupby("customer_id").order_date.diff().dt.total_seconds() / 86400)
    gap_stats = gaps.groupby("customer_id").gap.agg(avg_gap_days="mean", gap_std_days="std")
    base = base.join(gap_stats)
    base["order_trend"] = base.orders_30d / ((base.orders_90d - base.orders_30d) / 2 + 1)
    base = base.loc[base.tenure_days.ge(90) & base.orders_180d.gt(0)].copy()
    base["snapshot_date"] = snapshot.strftime("%Y-%m-%d")
    if include_label:
        future_buyers = completed.loc[completed.order_date.ge(snapshot) & completed.order_date.lt(horizon_end), "customer_id"]
        base["churned"] = (~base.index.isin(future_buyers)).astype(int)
    columns = ["snapshot_date", "signup_date"] + CATEGORICAL_FEATURES + NUMERIC_FEATURES
    if include_label:
        columns.append("churned")
    return base[columns].reset_index()


def monthly_sales(customers, orders, observation_end):
    end = pd.Timestamp(observation_end)
    # Only fully observed calendar months belong in time-series analysis.
    end_month = end.to_period("M").start_time
    completed = orders.loc[orders.status.eq("completed") & orders.order_date.lt(end_month)].copy()
    completed["month"] = completed.order_date.dt.to_period("M").dt.to_timestamp()
    months = pd.date_range(customers.signup_date.min().to_period("M").start_time,
                           end_month - pd.offsets.MonthBegin(1), freq="MS")
    result = completed.groupby("month").agg(revenue=("amount", "sum"), orders=("order_id", "count"),
                                               active_customers=("customer_id", "nunique")).reindex(months, fill_value=0)
    result.index.name = "month"
    result["revenue"] = result.revenue.round(2)
    return result.reset_index()


def cohort_retention(customers, orders, observation_end):
    end_month = pd.Timestamp(observation_end).to_period("M")
    users = customers.assign(cohort_month=customers.signup_date.dt.to_period("M"))
    sizes = users.groupby("cohort_month").customer_id.nunique()
    completed = orders.loc[orders.status.eq("completed")].copy()
    completed["purchase_month"] = completed.order_date.dt.to_period("M")
    completed = completed.loc[completed.purchase_month.lt(end_month)]
    joined = completed.merge(users[["customer_id", "cohort_month"]], on="customer_id", validate="many_to_one")
    counts = joined.groupby(["cohort_month", "purchase_month"]).customer_id.nunique()
    rows = []
    for cohort, size in sizes.items():
        for index, month in enumerate(pd.period_range(cohort, end_month - 1, freq="M")):
            retained = int(counts.get((cohort, month), 0))
            rows.append({"cohort_month": str(cohort.start_time.date()), "month_index": index,
                         "cohort_size": int(size), "retained_customers": retained, "retention_rate": retained / size})
    return pd.DataFrame(rows)
