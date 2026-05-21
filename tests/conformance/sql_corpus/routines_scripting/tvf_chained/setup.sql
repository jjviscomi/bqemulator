CREATE TABLE `${DATASET}`.events (id INT64, kind STRING, score INT64);
INSERT INTO `${DATASET}`.events (id, kind, score) VALUES
  (1, 'A', 10), (2, 'A', 20), (3, 'A', 30),
  (4, 'B', 40), (5, 'B', 50);
