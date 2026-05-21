MERGE INTO `${DATASET}.orders` AS t
USING (SELECT 1 AS order_id, NUMERIC '777' AS a) AS s
ON t.order_id = s.order_id
WHEN MATCHED THEN UPDATE SET amount = s.a;
SELECT amount FROM `${DATASET}.orders` WHERE order_id = 1
