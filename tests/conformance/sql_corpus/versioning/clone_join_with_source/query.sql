CREATE OR REPLACE TABLE `${DATASET}.c` CLONE `${DATASET}.t`;
INSERT INTO `${DATASET}.c` VALUES (4, "d"), (5, "e");
SELECT
  COALESCE(t.id, c.id) AS id,
  t.label AS source_label,
  c.label AS clone_label
FROM `${DATASET}.t` t FULL OUTER JOIN `${DATASET}.c` c USING (id)
ORDER BY id
