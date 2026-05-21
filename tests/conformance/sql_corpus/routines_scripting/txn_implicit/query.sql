INSERT INTO `${DATASET}`.ledger (id, amount) VALUES (1, 100);
INSERT INTO `${DATASET}`.ledger (id, amount) VALUES (2, 200);
SELECT id, amount FROM `${DATASET}`.ledger ORDER BY id
