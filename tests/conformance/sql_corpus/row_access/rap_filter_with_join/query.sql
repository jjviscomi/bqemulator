SELECT o.id, c.name, o.country, o.amount
FROM `${DATASET}.orders` AS o
JOIN `${DATASET}.customers` AS c USING (customer_id)
ORDER BY o.id
