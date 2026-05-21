CREATE TABLE FUNCTION `${DATASET}`.of_kind(k STRING)
  AS (SELECT id, kind, score FROM `${DATASET}`.events WHERE kind = k);
CREATE TABLE FUNCTION `${DATASET}`.kind_a_above(min_score INT64)
  AS (SELECT id, score FROM `${DATASET}`.of_kind('A') WHERE score >= min_score);
SELECT id, score FROM `${DATASET}`.kind_a_above(20) ORDER BY id
