WITH t AS (SELECT STRUCT(1 AS id, 'a' AS label) AS s UNION ALL SELECT STRUCT(2, 'b'))
SELECT s.id, s.label FROM t WHERE s.id > 1
