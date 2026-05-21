DECLARE product INT64 DEFAULT 1;
FOR row IN (SELECT v FROM UNNEST([2, 3, 5]) AS v ORDER BY v) DO
  SET product = product * row.v;
END FOR;
SELECT product AS product_2_3_5
