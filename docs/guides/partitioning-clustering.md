# Partitioning and clustering

Status: shipped.

## Partitioning

Three partitioning schemes, all fully supported:

- **Time-unit partitioning**: HOUR / DAY / MONTH / YEAR on a DATE /
  TIMESTAMP / DATETIME column.
- **Ingestion-time partitioning**: partition by server write time
  (exposed via `_PARTITIONTIME` / `_PARTITIONDATE`).
- **Integer-range partitioning**: fixed-width buckets over an INT64
  column.

Partition pruning is applied in the SQL rewriter via WHERE-clause
inspection.

## Clustering

Up to four clustering columns, physically realized via DuckDB's SORT at
write time. Clustering is a storage optimization — queries produce
identical results regardless; only scan behavior changes.

## Require partition filter

Tables with `require_partition_filter = true` reject queries that cannot
be pruned to a subset of partitions; the error surfaces as a BigQuery
`invalidQuery` response with the canonical "Cannot query over table
without a filter that can be used for partition elimination" message.
