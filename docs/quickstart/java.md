# Java quickstart

Maven:

```xml
<dependency>
  <groupId>com.google.cloud</groupId>
  <artifactId>google-cloud-bigquery</artifactId>
  <version>2.40.0</version>
</dependency>
```

```java
import com.google.auth.oauth2.GoogleCredentials;
import com.google.cloud.NoCredentials;
import com.google.cloud.bigquery.*;

public class Quickstart {
    public static void main(String[] args) throws Exception {
        BigQuery bigquery = BigQueryOptions.newBuilder()
            .setProjectId("my-project")
            .setHost("http://localhost:9050")
            .setCredentials(NoCredentials.getInstance())
            .build()
            .getService();

        bigquery.create(DatasetInfo.newBuilder("sales").build());

        TableResult result = bigquery.query(QueryJobConfiguration.of(
            "SELECT COUNT(*) AS n FROM sales.orders"));
        result.iterateAll().forEach(row -> System.out.println(row.get("n")));
    }
}
```

All operations supported by the REST backend work against bqemulator.
See the [compatibility matrix](../reference/compatibility-matrix.md).
