CREATE OR REPLACE TABLE `${DATASET}.sessions` (
  user_id STRING,
  region STRING,
  active RANGE<DATE>
);

INSERT INTO `${DATASET}.sessions` (user_id, region, active) VALUES
  ("alice", "NORTH", RANGE<DATE> "[2024-01-01, 2024-01-03)"),
  ("alice", "NORTH", RANGE<DATE> "[2024-01-03, 2024-01-05)"),
  ("alice", "SOUTH", RANGE<DATE> "[2024-01-04, 2024-01-06)"),
  ("bob",   "NORTH", RANGE<DATE> "[2024-01-02, 2024-01-04)"),
  ("bob",   "NORTH", RANGE<DATE> "[2024-01-10, 2024-01-12)");
