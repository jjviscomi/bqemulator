CREATE OR REPLACE TABLE `${DATASET}.events` (
  org STRING,
  user_id STRING,
  region STRING,
  active RANGE<DATE>
);

INSERT INTO `${DATASET}.events` (org, user_id, region, active) VALUES
  ("acme", "alice", "NORTH", RANGE<DATE> "[2024-01-01, 2024-01-03)"),
  ("acme", "alice", "NORTH", RANGE<DATE> "[2024-01-03, 2024-01-05)"),
  ("acme", "alice", "SOUTH", RANGE<DATE> "[2024-01-04, 2024-01-06)"),
  ("acme", "bob",   "NORTH", RANGE<DATE> "[2024-01-02, 2024-01-04)");
