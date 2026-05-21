SELECT
  ROUND(SUM(l_extendedprice * l_discount), 4) AS revenue
FROM `${DATASET}.lineitem`
WHERE l_shipdate >= DATE '1995-01-01'
  AND l_shipdate < DATE '1996-01-01'
  AND l_discount BETWEEN NUMERIC '0.02' AND NUMERIC '0.09'
  AND l_quantity < NUMERIC '20'
