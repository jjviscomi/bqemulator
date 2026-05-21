DECLARE threshold INT64 DEFAULT 5;
SELECT n
FROM `${DATASET}`.numbers
WHERE n > threshold
ORDER BY n
