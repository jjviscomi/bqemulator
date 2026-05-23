{{ config(materialized='table') }}

SELECT
    customer_id,
    customer_name,
    email,
    signup_date,
    EXTRACT(MONTH FROM signup_date) AS signup_month,
    EXTRACT(YEAR FROM signup_date) AS signup_year
FROM {{ ref('stg_customers') }}
