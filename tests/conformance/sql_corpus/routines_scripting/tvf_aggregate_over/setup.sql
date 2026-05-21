CREATE TABLE `${DATASET}`.measurements (region STRING, value INT64);
INSERT INTO `${DATASET}`.measurements (region, value) VALUES
  ('east', 10), ('east', 20), ('east', 30),
  ('west', 5), ('west', 15);
