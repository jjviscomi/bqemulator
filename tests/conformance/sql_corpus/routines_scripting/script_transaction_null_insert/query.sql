BEGIN TRANSACTION;
INSERT INTO `${DATASET}.tx_target` (id, label) VALUES (1, "a"), (2, NULL), (3, "c");
COMMIT TRANSACTION;
SELECT id, label FROM `${DATASET}.tx_target` ORDER BY id
