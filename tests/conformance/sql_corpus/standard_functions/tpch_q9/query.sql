SELECT
  nation,
  o_year,
  ROUND(SUM(amount), 4) AS sum_profit
FROM (
  SELECT
    n.n_name AS nation,
    EXTRACT(YEAR FROM o.o_orderdate) AS o_year,
    l.l_extendedprice * (1 - l.l_discount) - ps.ps_supplycost * l.l_quantity AS amount
  FROM `${DATASET}.part` AS p
  JOIN `${DATASET}.lineitem` AS l ON p.p_partkey = l.l_partkey
  JOIN `${DATASET}.supplier` AS s ON s.s_suppkey = l.l_suppkey
  JOIN `${DATASET}.partsupp` AS ps
    ON ps.ps_suppkey = l.l_suppkey
   AND ps.ps_partkey = l.l_partkey
  JOIN `${DATASET}.orders` AS o   ON o.o_orderkey = l.l_orderkey
  JOIN `${DATASET}.nation` AS n   ON s.s_nationkey = n.n_nationkey
  WHERE p.p_name LIKE '%green%'
) AS profit
GROUP BY nation, o_year
ORDER BY nation, o_year DESC
