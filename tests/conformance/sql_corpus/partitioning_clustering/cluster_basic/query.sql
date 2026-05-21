SELECT region, COUNT(*) AS c FROM `${DATASET}.clustered_t` GROUP BY region ORDER BY region
