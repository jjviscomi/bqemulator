CREATE OR REPLACE TABLE `${DATASET}.events` (
  user_id INT64,
  event_type STRING,
  ts TIMESTAMP,
  amount NUMERIC
);
INSERT INTO `${DATASET}.events` (user_id, event_type, ts, amount) VALUES
  (1, "view",      TIMESTAMP "2024-01-15 09:00:00+00", NUMERIC "0.00"),
  (1, "click",     TIMESTAMP "2024-01-15 09:05:00+00", NUMERIC "0.00"),
  (1, "purchase",  TIMESTAMP "2024-01-15 09:10:00+00", NUMERIC "50.00"),
  (2, "view",      TIMESTAMP "2024-01-15 10:00:00+00", NUMERIC "0.00"),
  (2, "view",      TIMESTAMP "2024-01-15 10:01:00+00", NUMERIC "0.00"),
  (2, "click",     TIMESTAMP "2024-01-15 10:10:00+00", NUMERIC "0.00"),
  (2, "purchase",  TIMESTAMP "2024-01-15 10:20:00+00", NUMERIC "75.00"),
  (3, "view",      TIMESTAMP "2024-01-15 11:00:00+00", NUMERIC "0.00"),
  (3, "click",     TIMESTAMP "2024-01-15 11:30:00+00", NUMERIC "0.00"),
  (4, "purchase",  TIMESTAMP "2024-01-16 08:00:00+00", NUMERIC "120.00"),
  (5, "view",      TIMESTAMP "2024-01-16 09:00:00+00", NUMERIC "0.00"),
  (5, "purchase",  TIMESTAMP "2024-01-16 09:05:00+00", NUMERIC "30.00"),
  (5, "purchase",  TIMESTAMP "2024-01-16 09:10:00+00", NUMERIC "45.00");
