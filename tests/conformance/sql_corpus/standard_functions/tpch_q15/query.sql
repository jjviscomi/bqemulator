WITH revenue AS (
  SELECT
    l.l_suppkey AS supplier_no,
    SUM(l.l_extendedprice * (1 - l.l_discount)) AS total_revenue
  FROM `${DATASET}.lineitem` AS l
  WHERE l.l_shipdate >= DATE '1996-01-01'
    AND l.l_shipdate <  DATE '1996-04-01'
  GROUP BY l.l_suppkey
)
SELECT
  s.s_suppkey,
  s.s_name,
  s.s_address,
  s.s_phone,
  ROUND(r.total_revenue, 4) AS total_revenue
FROM `${DATASET}.supplier` AS s
JOIN revenue AS r ON s.s_suppkey = r.supplier_no
WHERE r.total_revenue = (SELECT MAX(total_revenue) FROM revenue)
ORDER BY s.s_suppkey
