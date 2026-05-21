CREATE TEMP TABLE t AS SELECT 1 AS id, 'session-bound' AS shape;
SELECT id, shape FROM t ORDER BY id;
