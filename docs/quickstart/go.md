# Go quickstart

```bash
go get cloud.google.com/go/bigquery
```

```go
package main

import (
    "context"
    "fmt"
    "log"

    "cloud.google.com/go/bigquery"
    "google.golang.org/api/option"
)

func main() {
    ctx := context.Background()
    client, err := bigquery.NewClient(ctx, "my-project",
        option.WithEndpoint("http://localhost:9050"),
        option.WithoutAuthentication(),
    )
    if err != nil {
        log.Fatal(err)
    }
    defer client.Close()

    if err := client.Dataset("sales").Create(ctx, &bigquery.DatasetMetadata{}); err != nil {
        log.Fatal(err)
    }

    it, err := client.Query("SELECT COUNT(*) AS n FROM sales.orders").Read(ctx)
    if err != nil {
        log.Fatal(err)
    }
    var row []bigquery.Value
    _ = it.Next(&row)
    fmt.Println(row)
}
```

All operations supported by the REST backend work against bqemulator.
See the [compatibility matrix](../reference/compatibility-matrix.md).
