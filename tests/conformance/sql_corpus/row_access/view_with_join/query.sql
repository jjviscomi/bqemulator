CREATE OR REPLACE VIEW `${DATASET}.v_join`
AS SELECT a.id, a.s, b.s AS s2
FROM `${DATASET}.t` AS a
JOIN `${DATASET}.t` AS b ON b.id = a.id + 1;

SELECT id, s, s2 FROM `${DATASET}.v_join` ORDER BY id
