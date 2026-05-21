CREATE OR REPLACE TABLE `${DATASET}.windows` (
  emp_id STRING,
  duration RANGE<DATE>
);

INSERT INTO `${DATASET}.windows` (emp_id, duration) VALUES
  ("e1", RANGE<DATE> "[2024-01-01, 2024-01-03)"),
  ("e1", RANGE<DATE> "[2024-01-03, 2024-01-05)"),
  ("e1", RANGE<DATE> "[2024-01-04, 2024-01-07)"),
  ("e1", RANGE<DATE> "[2024-01-20, 2024-01-22)");
