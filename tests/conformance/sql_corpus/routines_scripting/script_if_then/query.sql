DECLARE x INT64 DEFAULT 10;
DECLARE label STRING;
IF x > 5 THEN SET label = 'big';
ELSE SET label = 'small';
END IF;
SELECT label
