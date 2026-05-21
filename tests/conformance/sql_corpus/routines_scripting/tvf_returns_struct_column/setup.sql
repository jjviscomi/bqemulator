CREATE TABLE `${DATASET}`.people (id INT64, first_name STRING, last_name STRING);
INSERT INTO `${DATASET}`.people (id, first_name, last_name) VALUES
  (1, 'Ada', 'Lovelace'),
  (2, 'Alan', 'Turing'),
  (3, 'Grace', 'Hopper');
