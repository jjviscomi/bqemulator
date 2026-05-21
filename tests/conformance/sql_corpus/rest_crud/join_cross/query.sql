SELECT o.order_id, c.region
FROM `${DATASET}.orders` AS o
CROSS JOIN (SELECT 'NORTH' AS region UNION ALL SELECT 'SOUTH') AS c
ORDER BY o.order_id, c.region
