SELECT JSON_EXTRACT_ARRAY('{"users": [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]}', '$.users') AS v
