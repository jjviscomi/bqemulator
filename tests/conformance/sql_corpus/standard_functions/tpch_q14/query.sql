SELECT
  ROUND(
    100.00 * SUM(CASE
                   WHEN p.p_type LIKE 'PROMO%'
                   THEN l.l_extendedprice * (1 - l.l_discount)
                   ELSE 0
                 END)
           / SUM(l.l_extendedprice * (1 - l.l_discount)),
    4
  ) AS promo_revenue
FROM `${DATASET}.lineitem` AS l
JOIN `${DATASET}.part` AS p ON l.l_partkey = p.p_partkey
WHERE l.l_shipdate >= DATE '1995-09-01'
  AND l.l_shipdate <  DATE '1995-10-01'
