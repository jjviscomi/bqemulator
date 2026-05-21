MERGE INTO `${DATASET}.orders` AS t
USING (SELECT 1 AS order_id) AS s
ON t.order_id = s.order_id
WHEN MATCHED THEN DELETE;
SELECT COUNT(*) AS n FROM `${DATASET}.orders` WHERE order_id = 1
