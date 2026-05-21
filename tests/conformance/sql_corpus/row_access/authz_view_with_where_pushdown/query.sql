SELECT id, country, amount
FROM `${PROJECT}.${DATASET_ID}_views.all_orders`
WHERE country = 'EU'
ORDER BY id
