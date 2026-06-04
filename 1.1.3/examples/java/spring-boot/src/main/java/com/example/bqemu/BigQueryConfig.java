package com.example.bqemu;

import com.google.cloud.NoCredentials;
import com.google.cloud.bigquery.BigQuery;
import com.google.cloud.bigquery.BigQueryOptions;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class BigQueryConfig {

    @Value("${bqemulator.project}")
    private String projectId;

    @Value("${bqemulator.endpoint:}")
    private String emulatorEndpoint;

    @Bean
    public BigQuery bigQuery() {
        BigQueryOptions.Builder builder = BigQueryOptions.newBuilder()
                .setProjectId(projectId);

        if (emulatorEndpoint != null && !emulatorEndpoint.isBlank()) {
            // Emulator path: skip ADC, use the override host.
            builder.setCredentials(NoCredentials.getInstance())
                    .setHost(emulatorEndpoint);
        }

        return builder.build().getService();
    }
}
