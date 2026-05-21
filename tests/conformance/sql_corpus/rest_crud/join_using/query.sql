SELECT o.order_id, c.region
FROM `${DATASET}.orders` AS o
JOIN `${DATASET}.customers` AS c USING (customer)
ORDER BY o.order_id
