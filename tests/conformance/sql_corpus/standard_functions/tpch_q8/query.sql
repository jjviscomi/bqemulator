SELECT
  o_year,
  ROUND(
    SUM(CASE WHEN nation = 'BRAZIL' THEN volume ELSE 0 END) / SUM(volume),
    4
  ) AS mkt_share
FROM (
  SELECT
    EXTRACT(YEAR FROM o.o_orderdate) AS o_year,
    l.l_extendedprice * (1 - l.l_discount) AS volume,
    n2.n_name AS nation
  FROM `${DATASET}.part` AS p
  JOIN `${DATASET}.lineitem` AS l ON p.p_partkey = l.l_partkey
  JOIN `${DATASET}.supplier` AS s ON s.s_suppkey = l.l_suppkey
  JOIN `${DATASET}.orders` AS o   ON l.l_orderkey = o.o_orderkey
  JOIN `${DATASET}.customer` AS c ON o.o_custkey = c.c_custkey
  JOIN `${DATASET}.nation` AS n1  ON c.c_nationkey = n1.n_nationkey
  JOIN `${DATASET}.region` AS r   ON n1.n_regionkey = r.r_regionkey
  JOIN `${DATASET}.nation` AS n2  ON s.s_nationkey = n2.n_nationkey
  WHERE r.r_name = 'AMERICA'
    AND o.o_orderdate BETWEEN DATE '1995-01-01' AND DATE '1996-12-31'
    AND p.p_type = 'ECONOMY ANODIZED STEEL'
) AS all_nations
GROUP BY o_year
ORDER BY o_year
