CREATE OR REPLACE TABLE `${DATASET}.base_t` (id INT64, val STRING);
INSERT INTO `${DATASET}.base_t` (id, val) VALUES (1, 'a'), (2, 'b');

CREATE OR REPLACE VIEW `${DATASET}.v_recreate` AS
  SELECT id FROM `${DATASET}.base_t`;
DROP VIEW `${DATASET}.v_recreate`;
CREATE OR REPLACE VIEW `${DATASET}.v_recreate` AS
  SELECT val FROM `${DATASET}.base_t`;
