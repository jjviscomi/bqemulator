package com.example;

import com.google.cloud.NoCredentials;
import com.google.cloud.bigquery.BigQuery;
import com.google.cloud.bigquery.BigQueryOptions;
import com.google.cloud.bigquery.DatasetId;
import com.google.cloud.bigquery.DatasetInfo;
import com.google.cloud.bigquery.Field;
import com.google.cloud.bigquery.FieldValueList;
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

import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * E2E: G4 INFORMATION_SCHEMA virtual views via the
 * google-cloud-bigquery Java client. Verifies the canonical
 * TABLES + COLUMNS surfaces dbt/Looker emit constantly.
 */
class G4InformationSchemaTest {
    private static final String REST_URL = System.getenv("BQEMU_REST_URL") != null
            ? System.getenv("BQEMU_REST_URL")
            : "http://localhost:9050";
    private static final String PROJECT = "e2e-g4-java";
    private static final String DATASET = "g4_java_ds";

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
    }

    @AfterEach
    void tearDown() {
        try {
            client.delete(DatasetId.of(PROJECT, DATASET),
                    BigQuery.DatasetDeleteOption.deleteContents());
        } catch (Exception ignore) {
            // best-effort cleanup
        }
    }

    @Test
    void informationSchemaTablesListsBaseTables() throws Exception {
        client.create(DatasetInfo.newBuilder(DATASET).setLocation("US").build());
        for (String name : new String[]{"orders", "customers"}) {
            client.create(TableInfo.of(
                    TableId.of(PROJECT, DATASET, name),
                    StandardTableDefinition.of(Schema.of(
                            Field.of("id", LegacySQLTypeName.INTEGER)))));
        }

        String sql = String.format(
                "SELECT table_name FROM `%s.%s`.INFORMATION_SCHEMA.TABLES "
                        + "WHERE table_type = 'BASE TABLE' ORDER BY table_name",
                PROJECT, DATASET);
        TableResult result = client.query(QueryJobConfiguration.newBuilder(sql).build());
        List<String> names = new ArrayList<>();
        for (FieldValueList row : result.iterateAll()) {
            names.add(row.get("table_name").getStringValue());
        }
        assertEquals(List.of("customers", "orders"), names);
    }

    @Test
    void informationSchemaColumnsOrderedByOrdinalPosition() throws Exception {
        client.create(DatasetInfo.newBuilder(DATASET).setLocation("US").build());
        client.create(TableInfo.of(
                TableId.of(PROJECT, DATASET, "events"),
                StandardTableDefinition.of(Schema.of(
                        Field.newBuilder("id", LegacySQLTypeName.INTEGER)
                                .setMode(Field.Mode.REQUIRED).build(),
                        Field.of("ts", LegacySQLTypeName.TIMESTAMP),
                        Field.of("payload", LegacySQLTypeName.STRING)))));

        String sql = String.format(
                "SELECT column_name, data_type FROM `%s.%s`.INFORMATION_SCHEMA.COLUMNS "
                        + "WHERE table_name = 'events' ORDER BY ordinal_position",
                PROJECT, DATASET);
        TableResult result = client.query(QueryJobConfiguration.newBuilder(sql).build());
        List<String> names = new ArrayList<>();
        List<String> types = new ArrayList<>();
        for (FieldValueList row : result.iterateAll()) {
            names.add(row.get("column_name").getStringValue());
            types.add(row.get("data_type").getStringValue());
        }
        assertEquals(List.of("id", "ts", "payload"), names);
        assertEquals(List.of("INT64", "TIMESTAMP", "STRING"), types);
    }
}
