CREATE OR REPLACE TABLE `${DATASET}.t_opts_pf`
(dt DATE, value INT64)
PARTITION BY dt
OPTIONS(require_partition_filter=true);
