CREATE TABLE `${DATASET}`.orders (id INT64, qty INT64);
INSERT INTO `${DATASET}`.orders (id, qty) VALUES (1, 10), (2, 20), (3, 30);
CREATE TABLE `${DATASET}`.products (id INT64, name STRING);
INSERT INTO `${DATASET}`.products (id, name) VALUES (1, 'widget'), (2, 'gadget'), (3, 'gizmo');
