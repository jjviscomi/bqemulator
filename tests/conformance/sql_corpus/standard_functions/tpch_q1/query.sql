SELECT
  l_returnflag, l_linestatus,
  SUM(l_quantity) AS sum_qty,
  SUM(l_extendedprice) AS sum_base_price,
  ROUND(SUM(l_extendedprice * (1 - l_discount)), 4) AS sum_disc_price,
  ROUND(SUM(l_extendedprice * (1 - l_discount) * (1 + l_tax)), 4) AS sum_charge,
  ROUND(AVG(l_quantity), 4) AS avg_qty,
  ROUND(AVG(l_extendedprice), 4) AS avg_price,
  ROUND(AVG(l_discount), 4) AS avg_disc,
  COUNT(*) AS count_order
FROM `${DATASET}.lineitem`
WHERE l_shipdate <= DATE '1995-12-31'
GROUP BY l_returnflag, l_linestatus
ORDER BY l_returnflag, l_linestatus
