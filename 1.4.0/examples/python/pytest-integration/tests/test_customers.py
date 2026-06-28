"""Integration tests for the /customers route against bqemulator."""

from __future__ import annotations


def test_healthz(app_client) -> None:
    response = app_client.get("/healthz")
    assert response.status_code == 200
    assert response.data == b"ok"


def test_list_customers_returns_three_rows(app_client) -> None:
    response = app_client.get("/customers")
    assert response.status_code == 200
    rows = response.get_json()
    assert [row["id"] for row in rows] == [1, 2, 3]
    assert [row["name"] for row in rows] == ["Alice", "Bob", "Carol"]


def test_list_customers_returns_json_content_type(app_client) -> None:
    response = app_client.get("/customers")
    assert response.content_type.startswith("application/json")
