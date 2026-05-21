SELECT
  supp_nation,
  cust_nation,
  l_year,
  ROUND(SUM(volume), 4) AS revenue
FROM (
  SELECT
    n1.n_name AS supp_nation,
    n2.n_name AS cust_nation,
    EXTRACT(YEAR FROM l.l_shipdate) AS l_year,
    l.l_extendedprice * (1 - l.l_discount) AS volume
  FROM `${DATASET}.supplier` AS s
  JOIN `${DATASET}.lineitem` AS l ON s.s_suppkey = l.l_suppkey
  JOIN `${DATASET}.orders` AS o   ON o.o_orderkey = l.l_orderkey
  JOIN `${DATASET}.customer` AS c ON c.c_custkey = o.o_custkey
  JOIN `${DATASET}.nation` AS n1  ON s.s_nationkey = n1.n_nationkey
  JOIN `${DATASET}.nation` AS n2  ON c.c_nationkey = n2.n_nationkey
  WHERE (
      (n1.n_name = 'FRANCE'  AND n2.n_name = 'GERMANY')
    OR
      (n1.n_name = 'GERMANY' AND n2.n_name = 'FRANCE')
    )
    AND l.l_shipdate BETWEEN DATE '1995-01-01' AND DATE '1996-12-31'
) AS shipping
GROUP BY supp_nation, cust_nation, l_year
ORDER BY supp_nation, cust_nation, l_year
