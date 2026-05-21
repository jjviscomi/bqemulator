SELECT
  n.n_name,
  ROUND(SUM(l.l_extendedprice * (1 - l.l_discount)), 4) AS revenue
FROM `${DATASET}.customer` AS c
JOIN `${DATASET}.orders` AS o ON c.c_custkey = o.o_custkey
JOIN `${DATASET}.lineitem` AS l ON l.l_orderkey = o.o_orderkey
JOIN `${DATASET}.nation` AS n ON c.c_nationkey = n.n_nationkey
JOIN `${DATASET}.region` AS r ON n.n_regionkey = r.r_regionkey
WHERE r.r_name = 'AMERICA'
  AND o.o_orderdate >= DATE '1995-01-01'
  AND o.o_orderdate < DATE '1996-01-01'
GROUP BY n.n_name
ORDER BY revenue DESC
