DECLARE total INT64 DEFAULT 0;
DECLARE i INT64 DEFAULT 1;
WHILE i <= 5 DO
  SET total = total + i;
  SET i = i + 1;
END WHILE;
SELECT total AS sum_1_to_5
