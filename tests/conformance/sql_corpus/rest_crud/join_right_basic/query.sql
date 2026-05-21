SELECT o.order_id, c.customer, c.region
FROM `${DATASET}.orders` AS o
RIGHT JOIN `${DATASET}.customers` AS c ON o.customer = c.customer
ORDER BY c.customer, o.order_id NULLS FIRST
