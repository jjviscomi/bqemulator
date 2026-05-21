CREATE OR REPLACE TABLE `${DATASET}.sales_clone` CLONE `${DATASET}.sales`;
INSERT INTO `${DATASET}.sales_clone` VALUES ("JP", 600), ("US", 500), ("EU", 50);
SELECT country, SUM(amount) AS total, COUNT(*) AS n
FROM `${DATASET}.sales_clone`
GROUP BY country
ORDER BY country
