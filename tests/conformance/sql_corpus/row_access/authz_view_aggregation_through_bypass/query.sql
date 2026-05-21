SELECT country, row_count, total
FROM `${PROJECT}.${DATASET_ID}_views.public_summary`
ORDER BY country
