SELECT
  cntrycode,
  COUNT(*) AS numcust,
  ROUND(SUM(c_acctbal), 4) AS totacctbal
FROM (
  SELECT
    SUBSTR(c.c_phone, 1, 2) AS cntrycode,
    c.c_acctbal AS c_acctbal
  FROM `${DATASET}.customer` AS c
  WHERE SUBSTR(c.c_phone, 1, 2) IN ('13','31','23','29','30','18','17')
    AND c.c_acctbal > (
      SELECT AVG(c2.c_acctbal)
      FROM `${DATASET}.customer` AS c2
      WHERE c2.c_acctbal > 0.00
        AND SUBSTR(c2.c_phone, 1, 2) IN ('13','31','23','29','30','18','17')
    )
    AND NOT EXISTS (
      SELECT 1
      FROM `${DATASET}.orders` AS o
      WHERE o.o_custkey = c.c_custkey
    )
) AS custsale
GROUP BY cntrycode
ORDER BY cntrycode
