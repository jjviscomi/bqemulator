CREATE OR REPLACE TABLE `${DATASET}.clustered_t`
CLUSTER BY region
AS
  SELECT "north" AS region, 1 AS n UNION ALL
  SELECT "south", 2 UNION ALL
  SELECT "north", 3;
