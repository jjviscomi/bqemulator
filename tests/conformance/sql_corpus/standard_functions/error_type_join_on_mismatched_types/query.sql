SELECT a.k AS sk, b.k AS ik
FROM `${DATASET}.t_str` a
JOIN `${DATASET}.t_int` b ON a.k = b.k
