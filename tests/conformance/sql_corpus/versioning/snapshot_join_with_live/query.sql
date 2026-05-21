CREATE SNAPSHOT TABLE `${DATASET}.users_snap`
CLONE `${DATASET}.users`;

SELECT s.name, a.action
FROM `${DATASET}.users_snap` s
JOIN `${DATASET}.activity` a USING (user_id)
ORDER BY s.user_id, a.action
