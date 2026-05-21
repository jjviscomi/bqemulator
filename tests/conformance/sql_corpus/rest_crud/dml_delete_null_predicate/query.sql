DELETE FROM `${DATASET}.items` WHERE label IS NULL;
SELECT id, label FROM `${DATASET}.items` ORDER BY id
