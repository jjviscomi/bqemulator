SELECT
  ARRAY(SELECT DISTINCT user_id FROM `${DATASET}.events` ORDER BY user_id) AS users
