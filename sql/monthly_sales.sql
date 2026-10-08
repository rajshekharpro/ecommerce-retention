-- Completed order value in USD. Refunds/cancellations are excluded, not netted.
WITH RECURSIVE months(month) AS (
  SELECT DATE(MIN(signup_date), 'start of month') FROM customers
  UNION ALL
  SELECT DATE(month, '+1 month') FROM months
  WHERE DATE(month, '+1 month') < DATE(:observation_end, 'start of month')
), totals AS (
  SELECT STRFTIME('%Y-%m-01',order_date) AS month,
         ROUND(SUM(amount),2) AS revenue, COUNT(*) AS orders,
         COUNT(DISTINCT customer_id) AS active_customers
  FROM orders
  WHERE status = 'completed' AND order_date < DATE(:observation_end,'start of month')
  GROUP BY 1
)
SELECT m.month, COALESCE(t.revenue,0) AS revenue, COALESCE(t.orders,0) AS orders,
       COALESCE(t.active_customers,0) AS active_customers
FROM months m LEFT JOIN totals t USING(month)
WHERE m.month < DATE(:observation_end,'start of month')
ORDER BY m.month;
