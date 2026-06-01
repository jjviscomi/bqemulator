package com.example;

import com.google.cloud.NoCredentials;
import com.google.cloud.bigquery.BigQuery;
import com.google.cloud.bigquery.BigQueryOptions;
import com.google.cloud.bigquery.DatasetInfo;
import com.google.cloud.bigquery.Field;
import com.google.cloud.bigquery.FieldValueList;
import com.google.cloud.bigquery.InsertAllRequest;
import com.google.cloud.bigquery.LegacySQLTypeName;
import com.google.cloud.bigquery.QueryJobConfiguration;
import com.google.cloud.bigquery.Schema;
import com.google.cloud.bigquery.StandardTableDefinition;
import com.google.cloud.bigquery.TableId;
import com.google.cloud.bigquery.TableInfo;
import com.google.cloud.bigquery.TableResult;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;

/**
 * E2E: Phase 1 REST CRUD + query via the google-cloud-bigquery Java client.
 */
class RestCrudTest {
    private static final String REST_URL = System.getenv("BQEMU_REST_URL") != null
            ? System.getenv("BQEMU_REST_URL")
            : "http://localhost:9050";
    private static final String PROJECT = "e2e-java";
    private static final String DATASET = "e2e_java";
    private static final String TABLE = "customers";

    private BigQuery client;

    @BeforeEach
    void setUp() {
        client = BigQueryOptions.newBuilder()
                .setProjectId(PROJECT)
                .setHost(REST_URL)
                .setCredentials(NoCredentials.getInstance())
                .build()
                .getService();
        // Clean any leftover state.
        try {
            client.delete(com.google.cloud.bigquery.DatasetId.of(PROJECT, DATASET),
                    BigQuery.DatasetDeleteOption.deleteContents());
        } catch (Exception ignore) {
            // absent is fine
        }
    }

    @AfterEach
    void tearDown() {
        try {
            client.delete(com.google.cloud.bigquery.DatasetId.of(PROJECT, DATASET),
                    BigQuery.DatasetDeleteOption.deleteContents());
        } catch (Exception ignore) {
            // best-effort cleanup
        }
    }

    @Test
    void datasetTableInsertQueryRoundTrip() throws Exception {
        client.create(DatasetInfo.newBuilder(DATASET).setLocation("US").build());

        TableId tableId = TableId.of(PROJECT, DATASET, TABLE);
        Schema schema = Schema.of(
                Field.newBuilder("id", LegacySQLTypeName.INTEGER)
                        .setMode(Field.Mode.REQUIRED).build(),
                Field.newBuilder("name", LegacySQLTypeName.STRING).build()
        );
        client.create(TableInfo.of(tableId, StandardTableDefinition.of(schema)));

        Map<String, Object> row1 = new HashMap<>();
        row1.put("id", 1);
        row1.put("name", "Alice");
        Map<String, Object> row2 = new HashMap<>();
        row2.put("id", 2);
        row2.put("name", "Bob");
        InsertAllRequest insertAll = InsertAllRequest.newBuilder(tableId)
                .addRow(row1)
                .addRow(row2)
                .build();
        client.insertAll(insertAll);

        String sql = String.format(
                "SELECT COUNT(*) AS n FROM `%s.%s.%s`",
                PROJECT, DATASET, TABLE);
        QueryJobConfiguration cfg = QueryJobConfiguration.newBuilder(sql).build();
        TableResult result = client.query(cfg);
        List<FieldValueList> rows = java.util.stream.StreamSupport
                .stream(result.iterateAll().spliterator(), false)
                .toList();
        assertEquals(1, rows.size());
        assertEquals(2L, rows.get(0).get("n").getLongValue());
    }

    @Test
    void dropTableViaQueryRemovesFromCatalog() throws Exception {
        client.create(DatasetInfo.newBuilder(DATASET).setLocation("US").build());
        TableId tableId = TableId.of(PROJECT, DATASET, TABLE);
        client.create(TableInfo.of(tableId, StandardTableDefinition.of(
                Schema.of(Field.newBuilder("id", LegacySQLTypeName.INTEGER).build()))));

        // Visible before the drop.
        assertNotNull(client.getTable(tableId));

        String sql = String.format("DROP TABLE `%s.%s.%s`", PROJECT, DATASET, TABLE);
        client.query(QueryJobConfiguration.newBuilder(sql).build());

        // Gone from tables.get — the Java client returns null for a missing table.
        assertNull(client.getTable(tableId));
    }
}
