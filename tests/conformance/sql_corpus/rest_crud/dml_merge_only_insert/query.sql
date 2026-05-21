MERGE INTO `${DATASET}.orders` AS t
USING (SELECT 999 AS order_id, NUMERIC '99' AS a, 'Zelda' AS c) AS s
ON t.order_id = s.order_id
WHEN NOT MATCHED THEN INSERT (order_id, customer, amount) VALUES (s.order_id, s.c, s.a);
SELECT order_id, customer FROM `${DATASET}.orders` WHERE order_id = 999
