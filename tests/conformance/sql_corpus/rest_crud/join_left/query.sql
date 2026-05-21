SELECT o.order_id, c.region
FROM `${DATASET}.orders` AS o
LEFT JOIN `${DATASET}.customers` AS c ON o.customer = c.customer
ORDER BY o.order_id
