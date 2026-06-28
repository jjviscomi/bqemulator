package com.example.bqemu;

import com.google.cloud.bigquery.BigQuery;
import com.google.cloud.bigquery.FieldValueList;
import com.google.cloud.bigquery.QueryJobConfiguration;
import com.google.cloud.bigquery.TableResult;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Repository;

import java.util.ArrayList;
import java.util.List;

@Repository
public class CustomerRepository {

    private final BigQuery bigQuery;
    private final String project;
    private final String dataset;

    public CustomerRepository(
            BigQuery bigQuery,
            @Value("${bqemulator.project}") String project,
            @Value("${bqemulator.dataset}") String dataset
    ) {
        this.bigQuery = bigQuery;
        this.project = project;
        this.dataset = dataset;
    }

    public List<Customer> findAll() throws InterruptedException {
        String sql = "SELECT id, name FROM `" + project + "." + dataset + ".customers` ORDER BY id";
        TableResult result = bigQuery.query(
                QueryJobConfiguration.newBuilder(sql).setUseLegacySql(false).build()
        );
        List<Customer> rows = new ArrayList<>();
        for (FieldValueList row : result.iterateAll()) {
            rows.add(new Customer(
                    row.get("id").getLongValue(),
                    row.get("name").getStringValue()
            ));
        }
        return rows;
    }

    public record Customer(long id, String name) {}
}
