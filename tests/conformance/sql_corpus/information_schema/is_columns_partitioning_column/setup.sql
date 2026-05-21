CREATE OR REPLACE TABLE `${DATASET}.daily_metrics` (
  dt DATE,
  metric_name STRING,
  value FLOAT64
)
PARTITION BY dt;
