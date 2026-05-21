CREATE OR REPLACE VIEW `${DATASET}.v_with_nulls` AS
  SELECT id, name, note FROM `${DATASET}.source_data`;
SELECT id, name, note FROM `${DATASET}.v_with_nulls` ORDER BY id
