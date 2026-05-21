# Apache Spark integration

Status: shipped (runnable example in `docs/examples/spark/pyspark-storage-api/`).

The [spark-bigquery-connector](https://github.com/GoogleCloudDataproc/spark-bigquery-connector)
uses the Storage Read API for high-throughput reads. Point it at the
emulator:

```python
spark.conf.set("viewsEnabled", "true")
df = (
    spark.read
        .format("bigquery")
        .option("project", "test-project")
        .option("parentProject", "test-project")
        .option("endpoint", "localhost:9060")   # gRPC
        .option("useAvroLogicalTypes", "true")
        .load("sales.orders")
)
```

Storage Write API sink writes are equivalently configured. See the
example project for a complete Spark job.
