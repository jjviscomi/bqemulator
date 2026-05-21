SELECT c.id AS c_id, a.val AS a_val, b.val AS b_val
FROM `${DATASET}.t_a` AS a
LEFT JOIN `${DATASET}.t_b` AS b ON a.id = b.id
RIGHT JOIN `${DATASET}.t_c` AS c ON COALESCE(a.id, b.id) = c.id
ORDER BY c.id
