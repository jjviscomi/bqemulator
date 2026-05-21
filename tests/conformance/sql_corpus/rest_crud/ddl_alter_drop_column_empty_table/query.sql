ALTER TABLE `${DATASET}.empty_alter_target` DROP COLUMN drop_me;
SELECT COUNT(*) AS n FROM `${DATASET}.empty_alter_target`
