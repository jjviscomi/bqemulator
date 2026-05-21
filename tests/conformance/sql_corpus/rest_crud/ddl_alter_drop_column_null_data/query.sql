ALTER TABLE `${DATASET}.t_with_null_col` DROP COLUMN drop_me;
SELECT id, keep_me FROM `${DATASET}.t_with_null_col` ORDER BY id
