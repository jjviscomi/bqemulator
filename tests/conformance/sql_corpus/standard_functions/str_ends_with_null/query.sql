SELECT ENDS_WITH(CAST(NULL AS STRING), 'foo') AS r_value_null, ENDS_WITH('foobar', CAST(NULL AS STRING)) AS r_suffix_null
