-- SQLite. Bind :snapshot_date as YYYY-MM-DD. Features use strictly earlier orders.
-- Eligible: >=90 days since signup and a completed purchase in the prior 180 days.
WITH history AS (
    SELECT * FROM orders
    WHERE status = 'completed' AND order_date < :snapshot_date
), aggregate_history AS (
    SELECT customer_id, MIN(order_date) AS first_order, MAX(order_date) AS last_order,
        COUNT(*) AS lifetime_orders, SUM(amount) AS lifetime_revenue, AVG(amount) AS avg_order_value,
        SUM(CASE WHEN order_date >= DATE(:snapshot_date, '-30 days') THEN 1 ELSE 0 END) AS orders_30d,
        SUM(CASE WHEN order_date >= DATE(:snapshot_date, '-90 days') THEN 1 ELSE 0 END) AS orders_90d,
        SUM(CASE WHEN order_date >= DATE(:snapshot_date, '-180 days') THEN 1 ELSE 0 END) AS orders_180d,
        SUM(CASE WHEN order_date >= DATE(:snapshot_date, '-90 days') THEN amount ELSE 0 END) AS revenue_90d,
        SUM(CASE WHEN order_date >= DATE(:snapshot_date, '-180 days') THEN amount ELSE 0 END) AS revenue_180d
    FROM history GROUP BY customer_id
)
SELECT c.customer_id, :snapshot_date AS snapshot_date,
    CAST(JULIANDAY(:snapshot_date) - JULIANDAY(c.signup_date) AS INTEGER) AS tenure_days,
    CAST(JULIANDAY(:snapshot_date) - JULIANDAY(a.last_order) AS INTEGER) AS recency_days,
    a.orders_30d, a.orders_90d, a.orders_180d, a.revenue_90d, a.revenue_180d,
    a.lifetime_orders, a.lifetime_revenue, a.avg_order_value,
    CAST(JULIANDAY(a.first_order) - JULIANDAY(c.signup_date) AS INTEGER) AS days_to_first_order,
    1.0 * a.orders_30d / ((a.orders_90d - a.orders_30d) / 2.0 + 1) AS order_trend
FROM customers c JOIN aggregate_history a USING(customer_id)
WHERE JULIANDAY(:snapshot_date) - JULIANDAY(c.signup_date) >= 90
  AND a.orders_180d > 0
ORDER BY customer_id;
