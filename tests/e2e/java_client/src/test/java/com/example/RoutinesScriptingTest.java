package com.example;

import com.google.cloud.NoCredentials;
import com.google.cloud.bigquery.BigQuery;
import com.google.cloud.bigquery.BigQueryOptions;
import com.google.cloud.bigquery.Dataset;
import com.google.cloud.bigquery.DatasetId;
import com.google.cloud.bigquery.DatasetInfo;
import com.google.cloud.bigquery.FieldValueList;
import com.google.cloud.bigquery.QueryJobConfiguration;
import com.google.cloud.bigquery.RoutineArgument;
import com.google.cloud.bigquery.RoutineId;
import com.google.cloud.bigquery.RoutineInfo;
import com.google.cloud.bigquery.StandardSQLDataType;
import com.google.cloud.bigquery.StandardSQLTypeName;
import com.google.cloud.bigquery.TableResult;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * E2E: Phase 6 routines + scripting via the google-cloud-bigquery Java client.
 */
class RoutinesScriptingTest {
    private static final String REST_URL = System.getenv("BQEMU_REST_URL") != null
            ? System.getenv("BQEMU_REST_URL")
            : "http://localhost:9050";
    private static final String PROJECT = "e2e-java-routines_scripting";
    private static final String DATASET = "routines_scripting_java_ds";

    private BigQuery client;

    @BeforeEach
    void setUp() {
        client = BigQueryOptions.newBuilder()
                .setProjectId(PROJECT)
                .setHost(REST_URL)
                .setCredentials(NoCredentials.getInstance())
                .build()
                .getService();
        try {
            client.delete(DatasetId.of(PROJECT, DATASET),
                    BigQuery.DatasetDeleteOption.deleteContents());
        } catch (Exception ignore) {
            // absent is fine
        }
        client.create(DatasetInfo.newBuilder(DATASET).setLocation("US").build());
    }

    @AfterEach
    void tearDown() {
        try {
            client.delete(DatasetId.of(PROJECT, DATASET),
                    BigQuery.DatasetDeleteOption.deleteContents());
        } catch (Exception ignore) {
            // fine
        }
    }

    @Test
    void testShipCriterionScript() throws Exception {
        createRoutine("sql_inc", "SCALAR_FUNCTION", "SQL",
                "x + 1",
                List.of(RoutineArgument.newBuilder()
                        .setName("x")
                        .setDataType(StandardSQLDataType.newBuilder(StandardSQLTypeName.INT64).build())
                        .build()),
                StandardSQLDataType.newBuilder(StandardSQLTypeName.INT64).build());

        createRoutine("js_double", "SCALAR_FUNCTION", "JAVASCRIPT",
                "return x * 2;",
                List.of(RoutineArgument.newBuilder()
                        .setName("x")
                        .setDataType(StandardSQLDataType.newBuilder(StandardSQLTypeName.INT64).build())
                        .build()),
                StandardSQLDataType.newBuilder(StandardSQLTypeName.INT64).build());

        createRoutine("one_to_n", "TABLE_VALUED_FUNCTION", "SQL",
                "SELECT i AS value FROM UNNEST(GENERATE_ARRAY(1, n)) AS i",
                List.of(RoutineArgument.newBuilder()
                        .setName("n")
                        .setDataType(StandardSQLDataType.newBuilder(StandardSQLTypeName.INT64).build())
                        .build()),
                null);

        String script = String.format("""
DECLARE n INT64 DEFAULT 3;
DECLARE total INT64 DEFAULT 0;
BEGIN
  FOR row IN (SELECT value FROM %s.one_to_n(n)) DO
    SET total = total + %s.js_double(%s.sql_inc(row.value));
  END FOR;
EXCEPTION WHEN ERROR THEN
  SET total = -1;
END;
IF total > 0 THEN
  SELECT total AS answer;
ELSE
  SELECT -1 AS answer;
END IF;
""", DATASET, DATASET, DATASET);

        TableResult result = client.query(
                QueryJobConfiguration.newBuilder(script).setUseLegacySql(false).build());
        FieldValueList row = result.iterateAll().iterator().next();
        assertEquals(18L, row.get("answer").getLongValue());
    }

    @Test
    void testScriptedCreateSchemaIsListed() throws Exception {
        // A single-statement CREATE SCHEMA takes the executor fast path; the
        // trailing SELECT tips this job into the scripting interpreter, whose
        // DDL-sync hook must register the dataset so it surfaces via
        // datasets.list and datasets.get.
        String scriptedDs = "scripted_created_schema_java_ds";
        try {
            client.delete(DatasetId.of(PROJECT, scriptedDs),
                    BigQuery.DatasetDeleteOption.deleteContents());
        } catch (Exception ignore) {
            // absent is fine
        }
        try {
            String script = "CREATE SCHEMA `" + scriptedDs + "`;\nSELECT 1 AS n;";
            client.query(QueryJobConfiguration.newBuilder(script).setUseLegacySql(false).build());

            boolean found = false;
            for (Dataset ds : client.listDatasets(PROJECT).iterateAll()) {
                if (scriptedDs.equals(ds.getDatasetId().getDataset())) {
                    found = true;
                    break;
                }
            }
            assertTrue(found,
                    "dataset " + scriptedDs + " absent from datasets.list after scripted CREATE SCHEMA");

            assertNotNull(client.getDataset(DatasetId.of(PROJECT, scriptedDs)),
                    "datasets.get returned null for " + scriptedDs);
        } finally {
            try {
                client.delete(DatasetId.of(PROJECT, scriptedDs),
                        BigQuery.DatasetDeleteOption.deleteContents());
            } catch (Exception ignore) {
                // fine
            }
        }
    }

    @Test
    void testScriptEndingInDdlReturnsEmpty() throws Exception {
        // Last-statement-wins: a trailing DDL has no result set, so the prior
        // SELECT's rows must not leak into the script result.
        String ds = "script_result_ddl_last_java";
        try {
            client.delete(DatasetId.of(PROJECT, ds), BigQuery.DatasetDeleteOption.deleteContents());
        } catch (Exception ignore) {
            // absent is fine
        }
        client.create(DatasetInfo.newBuilder(ds).setLocation("US").build());
        try {
            String script = "SELECT 1 AS a;\nCREATE TABLE `" + PROJECT + "." + ds + ".trailing` (id INT64)";
            TableResult result = client.query(
                    QueryJobConfiguration.newBuilder(script).setUseLegacySql(false).build());
            assertEquals(0L, result.getTotalRows());
        } finally {
            try {
                client.delete(DatasetId.of(PROJECT, ds), BigQuery.DatasetDeleteOption.deleteContents());
            } catch (Exception ignore) {
                // fine
            }
        }
    }

    private void createRoutine(
            String routineId,
            String type,
            String language,
            String body,
            List<RoutineArgument> args,
            StandardSQLDataType returnType) {
        RoutineInfo.Builder builder = RoutineInfo
                .newBuilder(RoutineId.of(PROJECT, DATASET, routineId))
                .setRoutineType(type)
                .setLanguage(language)
                .setBody(body)
                .setArguments(args);
        if (returnType != null) {
            builder.setReturnType(returnType);
        }
        client.create(builder.build());
    }
}
