CREATE OR REPLACE TABLE `${DATASET}.events` (
  user_id STRING,
  duration RANGE<DATE>
);

INSERT INTO `${DATASET}.events` (user_id, duration) VALUES
  ("alice", RANGE<DATE> "[2024-01-01, 2024-01-03)"),
  ("alice", RANGE<DATE> "[2024-01-03, 2024-01-05)"),
  ("alice", RANGE<DATE> "[2024-01-10, 2024-01-12)");
