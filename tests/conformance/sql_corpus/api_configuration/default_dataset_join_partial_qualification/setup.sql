CREATE OR REPLACE TABLE `${DATASET}`.users (user_id INT64, name STRING);
CREATE OR REPLACE TABLE `${DATASET}`.orders (order_id INT64, user_id INT64, amount INT64);
INSERT INTO `${DATASET}`.users (user_id, name) VALUES (1, 'alice'), (2, 'bob');
INSERT INTO `${DATASET}`.orders (order_id, user_id, amount) VALUES (100, 1, 50), (101, 2, 75), (102, 1, 25);
