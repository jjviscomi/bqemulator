SELECT
  region,
  channel,
  GROUPING(region) AS gr_region,
  GROUPING(channel) AS gr_channel,
  SUM(n) AS total
FROM `${DATASET}.events`
GROUP BY CUBE(region, channel)
ORDER BY gr_region, gr_channel, region, channel
