MERGE INTO `${DATASET}.orders` AS t
USING (SELECT 1 AS order_id, NUMERIC '999.00' AS new_amount) AS s
ON t.order_id = s.order_id
WHEN MATCHED THEN UPDATE SET amount = s.new_amount
WHEN NOT MATCHED THEN INSERT (order_id, customer, amount, order_date)
  VALUES (s.order_id, 'NEW', s.new_amount, DATE '2024-01-20');

SELECT order_id, amount FROM `${DATASET}.orders` WHERE order_id = 1
