CREATE TABLE FUNCTION `${DATASET}`.people_with_full_name(min_id INT64)
  AS (
    SELECT id, STRUCT(first_name AS first, last_name AS last) AS name
    FROM `${DATASET}`.people
    WHERE id >= min_id
  );
SELECT id, name FROM `${DATASET}`.people_with_full_name(2) ORDER BY id
