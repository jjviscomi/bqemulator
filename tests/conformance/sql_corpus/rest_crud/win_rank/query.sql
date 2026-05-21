SELECT order_id, customer,
       RANK() OVER (ORDER BY amount DESC) AS r
FROM `${DATASET}.orders`
ORDER BY order_id
