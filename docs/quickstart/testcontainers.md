# Testcontainers quickstart

Use the published Docker image as a Testcontainers-managed dependency
from any language.

## Python

```python
from bqemulator.testing import BigQueryEmulatorContainer

with BigQueryEmulatorContainer() as emu:
    rest_url = emu.get_rest_url()
    grpc_endpoint = emu.get_grpc_endpoint()
    # ... use a BigQuery client pointed at rest_url ...
```

## Java

```java
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.wait.strategy.Wait;

GenericContainer<?> emu = new GenericContainer<>("ghcr.io/jjviscomi/bqemulator:latest")
    .withExposedPorts(9050, 9060)
    .waitingFor(Wait.forLogMessage(".*rest\\.listen.*", 1));
emu.start();
String restUrl = "http://" + emu.getHost() + ":" + emu.getMappedPort(9050);
```

## Go

```go
req := testcontainers.ContainerRequest{
    Image:        "ghcr.io/jjviscomi/bqemulator:latest",
    ExposedPorts: []string{"9050/tcp", "9060/tcp"},
    WaitingFor:   wait.ForLog("rest.listen"),
}
```

## Node.js

```typescript
import { GenericContainer, Wait } from "testcontainers";

const emu = await new GenericContainer("ghcr.io/jjviscomi/bqemulator:latest")
  .withExposedPorts(9050, 9060)
  .withWaitStrategy(Wait.forLogMessage(/rest\.listen/))
  .start();
```
