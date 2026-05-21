CREATE TEMP FUNCTION js_double_each(xs ARRAY<INT64>) RETURNS ARRAY<INT64> LANGUAGE js AS "return xs.map(x => x * 2);";
SELECT js_double_each([1, 2, 3]) AS doubled
