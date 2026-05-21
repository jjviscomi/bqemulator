CREATE SNAPSHOT TABLE `${DATASET}.events_snap`
CLONE `${DATASET}.events`;

SELECT id, amount FROM `${DATASET}.events_snap` WHERE action = 'click' ORDER BY id
