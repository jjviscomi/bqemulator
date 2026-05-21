# Streaming inserts

Status: shipped — both `tabledata.insertAll` and the Storage Write API.

The legacy REST method `tabledata.insertAll` and the modern gRPC Storage
Write API are both supported. Prefer Storage Write for new code — it is
faster, cheaper in production, and offers exactly-once semantics via
offset tracking.

See [storage-api.md](storage-api.md) for the Storage Write deep dive.

```python
errors = client.insert_rows_json(
    "my-project.sales.orders",
    [{"id": 1, "amount": "12.50"}],
)
assert not errors
```
