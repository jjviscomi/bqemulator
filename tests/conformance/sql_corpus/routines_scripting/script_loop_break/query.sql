DECLARE counter INT64 DEFAULT 0;
LOOP
  SET counter = counter + 1;
  IF counter >= 3 THEN BREAK; END IF;
END LOOP;
SELECT counter AS final
