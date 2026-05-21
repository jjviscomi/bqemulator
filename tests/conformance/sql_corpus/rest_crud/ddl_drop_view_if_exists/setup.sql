CREATE OR REPLACE TABLE `${DATASET}.base_t` (id INT64);
INSERT INTO `${DATASET}.base_t` (id) VALUES (10);
DROP VIEW IF EXISTS `${DATASET}.never_existed_view`;
DROP VIEW IF EXISTS `${DATASET}.also_never_existed`;
