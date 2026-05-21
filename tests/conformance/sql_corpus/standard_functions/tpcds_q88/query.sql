SELECT *
FROM
  (SELECT COUNT(*) AS h8_30_to_9
   FROM `${DATASET}.store_sales` AS store_sales,
        `${DATASET}.household_demographics` AS household_demographics,
        `${DATASET}.time_dim` AS time_dim,
        `${DATASET}.store` AS store
   WHERE ss_sold_time_sk = time_dim.t_time_sk
     AND ss_hdemo_sk = household_demographics.hd_demo_sk
     AND ss_store_sk = s_store_sk
     AND time_dim.t_hour = 8
     AND time_dim.t_minute >= 30
     AND ((household_demographics.hd_dep_count = 4 AND household_demographics.hd_vehicle_count <= 4 + 2)
          OR (household_demographics.hd_dep_count = 2 AND household_demographics.hd_vehicle_count <= 2 + 2)
          OR (household_demographics.hd_dep_count = 0 AND household_demographics.hd_vehicle_count <= 0 + 2))
     AND store.s_store_name = 'ese') s1,
  (SELECT COUNT(*) AS h9_to_9_30
   FROM `${DATASET}.store_sales` AS store_sales,
        `${DATASET}.household_demographics` AS household_demographics,
        `${DATASET}.time_dim` AS time_dim,
        `${DATASET}.store` AS store
   WHERE ss_sold_time_sk = time_dim.t_time_sk
     AND ss_hdemo_sk = household_demographics.hd_demo_sk
     AND ss_store_sk = s_store_sk
     AND time_dim.t_hour = 9
     AND time_dim.t_minute < 30
     AND ((household_demographics.hd_dep_count = 4 AND household_demographics.hd_vehicle_count <= 4 + 2)
          OR (household_demographics.hd_dep_count = 2 AND household_demographics.hd_vehicle_count <= 2 + 2)
          OR (household_demographics.hd_dep_count = 0 AND household_demographics.hd_vehicle_count <= 0 + 2))
     AND store.s_store_name = 'ese') s2,
  (SELECT COUNT(*) AS h9_30_to_10
   FROM `${DATASET}.store_sales` AS store_sales,
        `${DATASET}.household_demographics` AS household_demographics,
        `${DATASET}.time_dim` AS time_dim,
        `${DATASET}.store` AS store
   WHERE ss_sold_time_sk = time_dim.t_time_sk
     AND ss_hdemo_sk = household_demographics.hd_demo_sk
     AND ss_store_sk = s_store_sk
     AND time_dim.t_hour = 9
     AND time_dim.t_minute >= 30
     AND ((household_demographics.hd_dep_count = 4 AND household_demographics.hd_vehicle_count <= 4 + 2)
          OR (household_demographics.hd_dep_count = 2 AND household_demographics.hd_vehicle_count <= 2 + 2)
          OR (household_demographics.hd_dep_count = 0 AND household_demographics.hd_vehicle_count <= 0 + 2))
     AND store.s_store_name = 'ese') s3,
  (SELECT COUNT(*) AS h10_to_10_30
   FROM `${DATASET}.store_sales` AS store_sales,
        `${DATASET}.household_demographics` AS household_demographics,
        `${DATASET}.time_dim` AS time_dim,
        `${DATASET}.store` AS store
   WHERE ss_sold_time_sk = time_dim.t_time_sk
     AND ss_hdemo_sk = household_demographics.hd_demo_sk
     AND ss_store_sk = s_store_sk
     AND time_dim.t_hour = 10
     AND time_dim.t_minute < 30
     AND ((household_demographics.hd_dep_count = 4 AND household_demographics.hd_vehicle_count <= 4 + 2)
          OR (household_demographics.hd_dep_count = 2 AND household_demographics.hd_vehicle_count <= 2 + 2)
          OR (household_demographics.hd_dep_count = 0 AND household_demographics.hd_vehicle_count <= 0 + 2))
     AND store.s_store_name = 'ese') s4,
  (SELECT COUNT(*) AS h10_30_to_11
   FROM `${DATASET}.store_sales` AS store_sales,
        `${DATASET}.household_demographics` AS household_demographics,
        `${DATASET}.time_dim` AS time_dim,
        `${DATASET}.store` AS store
   WHERE ss_sold_time_sk = time_dim.t_time_sk
     AND ss_hdemo_sk = household_demographics.hd_demo_sk
     AND ss_store_sk = s_store_sk
     AND time_dim.t_hour = 10
     AND time_dim.t_minute >= 30
     AND ((household_demographics.hd_dep_count = 4 AND household_demographics.hd_vehicle_count <= 4 + 2)
          OR (household_demographics.hd_dep_count = 2 AND household_demographics.hd_vehicle_count <= 2 + 2)
          OR (household_demographics.hd_dep_count = 0 AND household_demographics.hd_vehicle_count <= 0 + 2))
     AND store.s_store_name = 'ese') s5,
  (SELECT COUNT(*) AS h11_to_11_30
   FROM `${DATASET}.store_sales` AS store_sales,
        `${DATASET}.household_demographics` AS household_demographics,
        `${DATASET}.time_dim` AS time_dim,
        `${DATASET}.store` AS store
   WHERE ss_sold_time_sk = time_dim.t_time_sk
     AND ss_hdemo_sk = household_demographics.hd_demo_sk
     AND ss_store_sk = s_store_sk
     AND time_dim.t_hour = 11
     AND time_dim.t_minute < 30
     AND ((household_demographics.hd_dep_count = 4 AND household_demographics.hd_vehicle_count <= 4 + 2)
          OR (household_demographics.hd_dep_count = 2 AND household_demographics.hd_vehicle_count <= 2 + 2)
          OR (household_demographics.hd_dep_count = 0 AND household_demographics.hd_vehicle_count <= 0 + 2))
     AND store.s_store_name = 'ese') s6,
  (SELECT COUNT(*) AS h11_30_to_12
   FROM `${DATASET}.store_sales` AS store_sales,
        `${DATASET}.household_demographics` AS household_demographics,
        `${DATASET}.time_dim` AS time_dim,
        `${DATASET}.store` AS store
   WHERE ss_sold_time_sk = time_dim.t_time_sk
     AND ss_hdemo_sk = household_demographics.hd_demo_sk
     AND ss_store_sk = s_store_sk
     AND time_dim.t_hour = 11
     AND time_dim.t_minute >= 30
     AND ((household_demographics.hd_dep_count = 4 AND household_demographics.hd_vehicle_count <= 4 + 2)
          OR (household_demographics.hd_dep_count = 2 AND household_demographics.hd_vehicle_count <= 2 + 2)
          OR (household_demographics.hd_dep_count = 0 AND household_demographics.hd_vehicle_count <= 0 + 2))
     AND store.s_store_name = 'ese') s7,
  (SELECT COUNT(*) AS h12_to_12_30
   FROM `${DATASET}.store_sales` AS store_sales,
        `${DATASET}.household_demographics` AS household_demographics,
        `${DATASET}.time_dim` AS time_dim,
        `${DATASET}.store` AS store
   WHERE ss_sold_time_sk = time_dim.t_time_sk
     AND ss_hdemo_sk = household_demographics.hd_demo_sk
     AND ss_store_sk = s_store_sk
     AND time_dim.t_hour = 12
     AND time_dim.t_minute < 30
     AND ((household_demographics.hd_dep_count = 4 AND household_demographics.hd_vehicle_count <= 4 + 2)
          OR (household_demographics.hd_dep_count = 2 AND household_demographics.hd_vehicle_count <= 2 + 2)
          OR (household_demographics.hd_dep_count = 0 AND household_demographics.hd_vehicle_count <= 0 + 2))
     AND store.s_store_name = 'ese') s8
