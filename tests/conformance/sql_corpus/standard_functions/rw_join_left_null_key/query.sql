SELECT l.id AS l_id, l.label, r.id AS r_id, r.descr
FROM `${DATASET}.left_t` l
LEFT JOIN `${DATASET}.right_t` r
  ON l.id = r.id
ORDER BY l.label NULLS LAST, l.id NULLS LAST
