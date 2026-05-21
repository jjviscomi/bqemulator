SELECT id, country, amount
FROM `${DATASET}.orders`
WHERE amount > (SELECT AVG(amount) FROM `${DATASET}.orders`)
ORDER BY id
