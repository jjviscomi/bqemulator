-- TPC-DS Q41 setup — item with EXISTS subquery containing GROUP BY HAVING COUNT.
-- Spec params: i_manufact_id BETWEEN 738 AND 738+40, multi-arm OR-disjunction
-- over (i_category, i_color, i_units, i_size) combinations.

CREATE OR REPLACE TABLE `${DATASET}.item` (
  i_item_sk INT64, i_manufact_id INT64, i_manufact STRING,
  i_product_name STRING, i_category STRING,
  i_color STRING, i_units STRING, i_size STRING
);
INSERT INTO `${DATASET}.item` VALUES
  -- manuf 750 with various (cat,color,unit,size) combos
  (1, 750, "Manuf750",  "Widget Alpha",   "Women",  "almond",  "Ounce", "medium"),
  (2, 750, "Manuf750",  "Widget Bravo",   "Women",  "indian",  "Pound", "medium"),
  -- multiple rows of i_product_name "Widget Alpha" via manuf 750 (qualifies COUNT > 0 EXISTS)
  (3, 750, "Manuf750a", "Widget Alpha",   "Men",    "khaki",   "Pound", "extra large"),
  (4, 750, "Manuf750",  "Widget Charlie", "Music",  "antique", "Pound", "large"),
  -- manuf 760 (out of i_manufact_id range filter, BETWEEN 738+40 = 778)
  (5, 760, "Manuf760",  "Widget Delta",   "Music",  "antique", "Pound", "large"),
  -- manuf 740: too few i_product_name dupes (does not satisfy HAVING > 0)
  (6, 740, "Manuf740",  "Solo Item",      "Women",  "almond",  "Ounce", "medium"),
  -- manuf 745: also has dupe widget
  (7, 745, "Manuf745",  "Widget Alpha",   "Women",  "indian",  "Pound", "medium"),
  (8, 745, "Manuf745",  "Widget Alpha",   "Women",  "almond",  "Ounce", "medium");
