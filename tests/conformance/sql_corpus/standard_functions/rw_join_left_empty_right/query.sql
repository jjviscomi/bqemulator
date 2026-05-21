SELECT l.id AS l_id, l.label, r.id AS r_id, r.descr
FROM `${DATASET}.left_t2` l
LEFT JOIN `${DATASET}.right_t2` r
  ON l.id = r.id
ORDER BY l.id
