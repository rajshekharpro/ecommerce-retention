-- Signup-month cohorts; month 0 is purchasing signup customers / all signup customers.
-- Bind :observation_end (exclusive). Future or partially observed months are omitted.
WITH RECURSIVE months(month) AS (
  SELECT DATE(MIN(signup_date), 'start of month') FROM customers
  UNION ALL
  SELECT DATE(month, '+1 month') FROM months
  WHERE DATE(month, '+1 month') < DATE(:observation_end, 'start of month')
), cohorts AS (
  SELECT STRFTIME('%Y-%m-01', signup_date) AS cohort_month, COUNT(*) AS cohort_size
  FROM customers GROUP BY 1
), activity AS (
  SELECT STRFTIME('%Y-%m-01', c.signup_date) AS cohort_month,
         STRFTIME('%Y-%m-01', o.order_date) AS purchase_month,
         COUNT(DISTINCT o.customer_id) AS retained_customers
  FROM orders o JOIN customers c USING(customer_id)
  WHERE o.status = 'completed' AND o.order_date < DATE(:observation_end, 'start of month')
  GROUP BY 1,2
)
SELECT c.cohort_month,
  (CAST(STRFTIME('%Y',m.month) AS INTEGER)-CAST(STRFTIME('%Y',c.cohort_month) AS INTEGER))*12
    + CAST(STRFTIME('%m',m.month) AS INTEGER)-CAST(STRFTIME('%m',c.cohort_month) AS INTEGER) AS month_index,
  c.cohort_size, COALESCE(a.retained_customers,0) AS retained_customers,
  1.0 * COALESCE(a.retained_customers,0) / c.cohort_size AS retention_rate
FROM cohorts c JOIN months m ON m.month >= c.cohort_month
LEFT JOIN activity a ON a.cohort_month = c.cohort_month AND a.purchase_month = m.month
WHERE m.month < DATE(:observation_end,'start of month')
ORDER BY c.cohort_month,month_index;
