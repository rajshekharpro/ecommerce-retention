-- Forward-looking targets only; never join this outcome into pre-snapshot features.
-- Bind :snapshot_date and :observation_end (exclusive); require a fully mature label window.
SELECT c.customer_id, :snapshot_date AS snapshot_date,
       CASE WHEN EXISTS (
         SELECT 1 FROM orders f WHERE f.customer_id=c.customer_id AND f.status='completed'
           AND f.order_date >= :snapshot_date AND f.order_date < DATE(:snapshot_date,'+90 days')
       ) THEN 0 ELSE 1 END AS churned
FROM customers c
WHERE DATE(:snapshot_date,'+90 days') <= :observation_end
  AND JULIANDAY(:snapshot_date)-JULIANDAY(c.signup_date) >= 90
  AND EXISTS (
    SELECT 1 FROM orders h WHERE h.customer_id=c.customer_id AND h.status='completed'
      AND h.order_date >= DATE(:snapshot_date,'-180 days') AND h.order_date < :snapshot_date
  )
ORDER BY c.customer_id;
