CREATE OR REPLACE TABLE `${DATASET}.geo_places` (
  name STRING,
  loc GEOGRAPHY
);
INSERT INTO `${DATASET}.geo_places` (name, loc) VALUES
  ('a', ST_GEOGPOINT(-122.4, 37.8)),
  ('b', ST_GEOGPOINT(0.0, 0.0));
