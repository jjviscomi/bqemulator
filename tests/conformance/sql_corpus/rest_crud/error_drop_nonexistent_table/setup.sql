-- Dataset is provisioned by the runner; the table referenced by the
-- DROP statement in query.sql never exists, so it is expected to fail
-- (no IF EXISTS clause).
SELECT 1 AS placeholder;
