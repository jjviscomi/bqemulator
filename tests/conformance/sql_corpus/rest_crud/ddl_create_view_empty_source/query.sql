CREATE OR REPLACE VIEW `${DATASET}.v_empty` AS
  SELECT id, v FROM `${DATASET}.empty_base`;
SELECT COUNT(*) AS n FROM `${DATASET}.v_empty`
