SELECT l.id AS left_id, l.val, r.id AS right_id, r.tag
FROM `${DATASET}.left_t` AS l
RIGHT JOIN `${DATASET}.right_t` AS r ON l.id = r.id
ORDER BY r.id, l.id NULLS FIRST
