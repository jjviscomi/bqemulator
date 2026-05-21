CREATE OR REPLACE TABLE `${DATASET}.avro_split` (
  id INT64,
  kind STRING
);

INSERT INTO `${DATASET}.avro_split`
SELECT id, IF(MOD(id, 2) = 0, 'even', 'odd')
FROM UNNEST(GENERATE_ARRAY(1, 8)) AS id;
