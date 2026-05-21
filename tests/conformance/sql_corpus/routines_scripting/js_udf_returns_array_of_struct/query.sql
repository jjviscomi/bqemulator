CREATE TEMP FUNCTION js_make_pairs(n INT64)
  RETURNS ARRAY<STRUCT<i INT64, label STRING>>
  LANGUAGE js
  AS "var out = []; for (var i = 0; i < n; i++) { out.push({i: i, label: 'item-' + i}); } return out;";
SELECT js_make_pairs(3) AS pairs
