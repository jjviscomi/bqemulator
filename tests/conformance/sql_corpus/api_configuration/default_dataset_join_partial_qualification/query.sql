SELECT u.name, SUM(o.amount) AS total
FROM users AS u
JOIN orders AS o USING (user_id)
GROUP BY u.name
ORDER BY u.name
