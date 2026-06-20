{{ config(materialized='view') }}

SELECT
    id AS customer_id,
    name AS customer_name,
    email,
    CAST(signup_date AS DATE) AS signup_date
FROM {{ ref('customers') }}
