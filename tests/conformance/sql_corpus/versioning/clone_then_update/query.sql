CREATE OR REPLACE TABLE `${DATASET}.c` CLONE `${DATASET}.t`;
UPDATE `${DATASET}.c` SET label = "X" WHERE id = 2;
SELECT t.id, t.label AS src_label, c.label AS clone_label
FROM `${DATASET}.t` t JOIN `${DATASET}.c` c USING (id)
ORDER BY t.id
