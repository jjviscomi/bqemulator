package com.example.bqemu;

import com.google.cloud.bigquery.BigQuery;
import com.google.cloud.bigquery.DatasetInfo;
import com.google.cloud.bigquery.Field;
import com.google.cloud.bigquery.InsertAllRequest;
import com.google.cloud.bigquery.LegacySQLTypeName;
import com.google.cloud.bigquery.Schema;
import com.google.cloud.bigquery.StandardTableDefinition;
import com.google.cloud.bigquery.TableId;
import com.google.cloud.bigquery.TableInfo;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.wait.strategy.Wait;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.utility.DockerImageName;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest
@Testcontainers
class CustomerRepositoryIT {

    static final String IMAGE = System.getenv()
            .getOrDefault("BQEMU_IMAGE", "ghcr.io/jjviscomi/bqemulator:dev");

    @Container
    static GenericContainer<?> EMULATOR =
            new GenericContainer<>(DockerImageName.parse(IMAGE))
                    .withEnv("BQEMU_REST_HOST", "0.0.0.0")
                    .withEnv("BQEMU_GRPC_HOST", "0.0.0.0")
                    .withEnv("BQEMU_ADMIN_ENABLED", "1")
                    .withExposedPorts(9050, 9060)
                    .waitingFor(Wait.forHttp("/healthz").forPort(9050));

    @DynamicPropertySource
    static void bqemuEndpoint(DynamicPropertyRegistry registry) {
        String host = EMULATOR.getHost();
        Integer port = EMULATOR.getMappedPort(9050);
        registry.add("bqemulator.endpoint", () -> "http://" + host + ":" + port);
    }

    @Autowired
    BigQuery bigQuery;

    @Autowired
    CustomerRepository repository;

    @BeforeAll
    static void noop() {
        // Container start is handled by @Testcontainers; nothing else needed here.
    }

    @Test
    void readsSeededCustomers() throws Exception {
        bigQuery.create(DatasetInfo.newBuilder("spring_demo").setLocation("US").build());
        TableId tableId = TableId.of("spring_demo", "customers");
        Schema schema = Schema.of(
                Field.of("id", LegacySQLTypeName.INTEGER),
                Field.of("name", LegacySQLTypeName.STRING)
        );
        bigQuery.create(TableInfo.newBuilder(tableId, StandardTableDefinition.of(schema)).build());

        bigQuery.insertAll(InsertAllRequest.newBuilder(tableId)
                .addRow(Map.of("id", 1L, "name", "Alice"))
                .addRow(Map.of("id", 2L, "name", "Bob"))
                .addRow(Map.of("id", 3L, "name", "Carol"))
                .build());

        List<CustomerRepository.Customer> rows = repository.findAll();
        assertThat(rows).extracting(CustomerRepository.Customer::id).containsExactly(1L, 2L, 3L);
        assertThat(rows).extracting(CustomerRepository.Customer::name)
                .containsExactly("Alice", "Bob", "Carol");
    }
}
