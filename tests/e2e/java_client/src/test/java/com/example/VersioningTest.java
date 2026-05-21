package com.example;

import com.google.cloud.NoCredentials;
import com.google.cloud.bigquery.BigQuery;
import com.google.cloud.bigquery.BigQueryOptions;
import com.google.cloud.bigquery.DatasetId;
import com.google.cloud.bigquery.DatasetInfo;
import com.google.cloud.bigquery.FieldValueList;
import com.google.cloud.bigquery.QueryJobConfiguration;
import com.google.cloud.bigquery.TableResult;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.time.format.DateTimeFormatter;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * E2E: Phase 7 versioning (time travel, snapshots, clones, materialized
 * views) via the google-cloud-bigquery Java client.
 */
class VersioningTest {
    private static final String REST_URL = System.getenv("BQEMU_REST_URL") != null
            ? System.getenv("BQEMU_REST_URL")
            : "http://localhost:9050";
    private static final String PROJECT = "e2e-java-versioning";
    private static final String DATASET = "versioning_java_ds";

    private BigQuery client;

    @BeforeEach
    void setUp() throws Exception {
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
        runQuery("CREATE TABLE `" + PROJECT + "." + DATASET + ".orders` (id INT64, country STRING, amount INT64)");
    }

    @AfterEach
    void tearDown() {
        try {
            client.delete(DatasetId.of(PROJECT, DATASET),
                    BigQuery.DatasetDeleteOption.deleteContents());
        } catch (Exception ignore) {
        }
    }

    @Test
    void testTimeTravelReturnsHistoricalRows() throws Exception {
        runQuery("INSERT INTO `" + PROJECT + "." + DATASET + ".orders` VALUES (1, 'US', 10), (2, 'US', 20)");

        Thread.sleep(50);
        String boundary = DateTimeFormatter
                .ofPattern("yyyy-MM-dd HH:mm:ss.SSSSSS")
                .withZone(ZoneOffset.UTC)
                .format(Instant.now());
        Thread.sleep(50);

        runQuery("INSERT INTO `" + PROJECT + "." + DATASET + ".orders` VALUES (3, 'CA', 30)");

        TableResult historical = runQuery(
                "SELECT id FROM `" + PROJECT + "." + DATASET + ".orders` "
                        + "FOR SYSTEM_TIME AS OF TIMESTAMP '" + boundary + "' ORDER BY id");
        List<Long> ids = new ArrayList<>();
        for (FieldValueList row : historical.iterateAll()) {
            ids.add(row.get(0).getLongValue());
        }
        assertEquals(List.of(1L, 2L), ids);
    }

    @Test
    void testCreateSnapshotTableCapturesPointInTimeCopy() throws Exception {
        runQuery("INSERT INTO `" + PROJECT + "." + DATASET + ".orders` VALUES (1, 'US', 10)");
        runQuery("CREATE SNAPSHOT TABLE `" + PROJECT + "." + DATASET + ".orders_snap` "
                + "CLONE `" + PROJECT + "." + DATASET + ".orders`");
        runQuery("INSERT INTO `" + PROJECT + "." + DATASET + ".orders` VALUES (2, 'CA', 20)");

        TableResult snap = runQuery(
                "SELECT id FROM `" + PROJECT + "." + DATASET + ".orders_snap` ORDER BY id");
        List<Long> ids = new ArrayList<>();
        for (FieldValueList row : snap.iterateAll()) {
            ids.add(row.get(0).getLongValue());
        }
        assertEquals(List.of(1L), ids);
    }

    @Test
    void testCreateTableCloneDivergesIndependently() throws Exception {
        runQuery("INSERT INTO `" + PROJECT + "." + DATASET + ".orders` VALUES (1, 'US', 10)");
        runQuery("CREATE TABLE `" + PROJECT + "." + DATASET + ".workcopy` "
                + "CLONE `" + PROJECT + "." + DATASET + ".orders`");
        runQuery("INSERT INTO `" + PROJECT + "." + DATASET + ".workcopy` VALUES (99, 'NZ', 999)");

        TableResult clone = runQuery(
                "SELECT id FROM `" + PROJECT + "." + DATASET + ".workcopy` WHERE id = 99");
        TableResult src = runQuery(
                "SELECT id FROM `" + PROJECT + "." + DATASET + ".orders` WHERE id = 99");
        assertEquals(1, clone.getTotalRows());
        assertEquals(0, src.getTotalRows());
    }

    @Test
    void testMaterializedViewAutoRefreshes() throws Exception {
        runQuery("INSERT INTO `" + PROJECT + "." + DATASET + ".orders` VALUES (1, 'US', 10)");
        runQuery("CREATE MATERIALIZED VIEW `" + PROJECT + "." + DATASET + ".country_totals` AS "
                + "SELECT country, SUM(amount) AS total "
                + "FROM `" + PROJECT + "." + DATASET + ".orders` GROUP BY country");

        TableResult before = runQuery(
                "SELECT total FROM `" + PROJECT + "." + DATASET + ".country_totals` WHERE country = 'US'");
        long beforeTotal = before.iterateAll().iterator().next().get(0).getLongValue();

        runQuery("INSERT INTO `" + PROJECT + "." + DATASET + ".orders` VALUES (2, 'US', 90)");

        TableResult after = runQuery(
                "SELECT total FROM `" + PROJECT + "." + DATASET + ".country_totals` WHERE country = 'US'");
        long afterTotal = after.iterateAll().iterator().next().get(0).getLongValue();

        assertTrue(afterTotal > beforeTotal,
                "MV should auto-refresh: before=" + beforeTotal + " after=" + afterTotal);
    }

    private TableResult runQuery(String sql) throws Exception {
        QueryJobConfiguration cfg = QueryJobConfiguration.newBuilder(sql)
                .setUseLegacySql(false)
                .build();
        return client.query(cfg);
    }
}
