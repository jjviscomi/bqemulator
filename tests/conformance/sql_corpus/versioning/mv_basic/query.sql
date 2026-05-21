CREATE MATERIALIZED VIEW `${DATASET}.mv_summary`
AS SELECT label, COUNT(*) AS n FROM `${DATASET}.source_table` GROUP BY label;

SELECT label, n FROM `${DATASET}.mv_summary` ORDER BY label
