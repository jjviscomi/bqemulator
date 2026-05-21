SELECT
  s_store_name,
  SUM(ss_net_profit) AS net
FROM `${DATASET}.store_sales` AS store_sales,
     `${DATASET}.date_dim` AS date_dim,
     `${DATASET}.store` AS store,
     (
       SELECT ca_zip
       FROM (
         SELECT SUBSTR(ca_zip, 1, 5) AS ca_zip
         FROM `${DATASET}.customer_address`
         WHERE SUBSTR(ca_zip, 1, 5) IN (
           '24128','76232','65084','87816','83926','77556','20548',
           '26231','43848','15126','91137','61265','98294','25782',
           '17920','18426','98235','40081','84093','28577',
           '55565','17183','54601','67897','22752','41001','96425',
           '47770','85042','29405','12305','62856','89996','25223',
           '57834','62878','22685','39279','86539','64479','15890',
           '15823','25960','78366','40005','56600','30203','75694',
           '67592','10044'
         )
       ) A1
     ) A2
WHERE ss_store_sk = s_store_sk
  AND ss_sold_date_sk = d_date_sk
  AND d_qoy = 2 AND d_year = 1998
  AND (SUBSTR(s_zip, 1, 2) = SUBSTR(A2.ca_zip, 1, 2))
GROUP BY s_store_name
ORDER BY s_store_name
LIMIT 100
