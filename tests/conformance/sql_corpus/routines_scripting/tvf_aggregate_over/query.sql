CREATE TABLE FUNCTION `${DATASET}`.in_region(r STRING)
  AS (SELECT region, value FROM `${DATASET}`.measurements WHERE region = r);
SELECT region, SUM(value) AS total
FROM `${DATASET}`.in_region('east')
GROUP BY region
