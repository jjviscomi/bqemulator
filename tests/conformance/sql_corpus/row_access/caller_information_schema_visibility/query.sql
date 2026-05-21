SELECT policy_name, table_name, grantees, filter_predicate
FROM `${DATASET}.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES`
ORDER BY policy_name
