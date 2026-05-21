MERGE INTO `${DATASET}.orders` AS t
USING (SELECT 1 AS order_id, NUMERIC '999' AS new_amount
       UNION ALL
       SELECT 100, NUMERIC '50') AS s
ON t.order_id = s.order_id
WHEN MATCHED THEN UPDATE SET amount = s.new_amount
WHEN NOT MATCHED THEN INSERT (order_id, customer, amount) VALUES (s.order_id, 'NEW', s.new_amount);
SELECT order_id, amount FROM `${DATASET}.orders` WHERE order_id IN (1, 100) ORDER BY order_id
