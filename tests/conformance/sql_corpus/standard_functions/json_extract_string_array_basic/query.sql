SELECT JSON_EXTRACT_STRING_ARRAY('{"tags": ["a", "b", "c"]}', '$.tags') AS v
