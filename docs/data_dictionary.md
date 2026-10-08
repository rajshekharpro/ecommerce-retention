# Data dictionary

Dates use `YYYY-MM-DD` in UTC. All monetary values are fictional USD. All keys are strings, with one row per customer or order in the raw files.

## Raw inputs

| File | Field | Meaning |
| --- | --- | --- |
| customers.csv | customer_id | Unique customer key |
| customers.csv | signup_date | First signup date |
| customers.csv | acquisition_channel | Organic, Paid Search, Social, Referral or Email in the demo |
| customers.csv | country | Customer market; four fictional market distributions in the demo |
| orders.csv | order_id | Unique order key |
| orders.csv | customer_id | Existing customer foreign key |
| orders.csv | order_date | Order date, on or after signup |
| orders.csv | amount | Nonnegative completed-order value when status is completed |
| orders.csv | status | completed, cancelled or refunded |
| orders.csv | category | Fictional product category |

## Snapshot features

One row per eligible customer per `snapshot_date`. Only completed purchases strictly before the snapshot are used.

| Field | Definition |
| --- | --- |
| tenure_days | Snapshot minus signup date |
| recency_days | Snapshot minus most recent completed order date |
| orders_30d / orders_90d / orders_180d | Completed orders in the respective trailing window |
| revenue_90d / revenue_180d | Sum of completed order amounts in that window |
| lifetime_orders / lifetime_revenue | Completed orders / completed order value before the snapshot |
| avg_order_value | Lifetime completed revenue divided by lifetime completed orders |
| avg_gap_days | Average gap between successive completed orders before snapshot; null if only one order |
| gap_std_days | Sample standard deviation of gaps; null if fewer than two gaps |
| days_to_first_order | First completed order minus signup date |
| order_trend | orders_30d / ((orders_90d - orders_30d) / 2 + 1); stabilized recent vs preceding activity |
| churned | 1 if no completed purchase in the next 90 days, otherwise 0; absent for production scoring |

Same-day orders are separate orders and can have zero-day gaps. Missing gap statistics are handled within the fitted model pipeline. Identifiers, signup timestamps, snapshot dates and targets are never input features. Channel and country are categorical features.

## Export tables and grains

| Export | Grain | Aggregation guidance |
| --- | --- | --- |
| customer_scores.csv | One eligible customer at latest scoring date | Count customers; sum revenue exposure; average probability |
| segment_summary.csv | One behavioral segment | Sum customer counts and revenue exposure; probability is an already-computed mean |
| monthly_sales.csv | One fully observed calendar month | Sum revenue/orders; monthly unique customers cannot be summed as unique across months |
| sales_forecast.csv | One month, actual or future | Use revenue for actuals, forecast/lower/upper for future; do not add actual and forecast measures together |
| cohorts.csv | Signup cohort month and months elapsed | Rate is retained_customers / cohort_size; do not sum precomputed rates or duplicated cohort sizes across elapsed months |
| feature_importance.csv | One model input | Relative predictive importance from validation; not causality |

`customer_scores.csv` also includes a churn probability, risk band, behavioral segment, reason and recommended action. `expected_90d_revenue` is a historical run-rate proxy (`revenue_180d / 2`), and `revenue_at_risk` is that proxy times inactivity probability. The field name does not imply a separately fitted customer revenue forecast.

For portfolio retention at one elapsed month, sum retained counts and cohort sizes **only across cohorts with that elapsed month observed**, then divide. For any specific cohort-month cell use its single rate. All-time cohorts and sales use the entire raw population; customer-risk filters apply to the current scored population.
