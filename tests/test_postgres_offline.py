"""PostgreSQL code paths that need no database."""

from __future__ import annotations

import builtins
from typing import Any

import pytest

import govgraph as gg
from govgraph.store import postgres

UNREACHABLE = "postgresql://nobody@127.0.0.1:1/nothing?connect_timeout=2"


def test_connection_failure_is_a_store_error(ontology: gg.Ontology) -> None:
    store = gg.PostgresStore(ontology, UNREACHABLE)
    with pytest.raises(gg.StoreError) as caught:
        store.create_schema()
    assert str(caught.value) == "could not connect to the database"
    with pytest.raises(gg.StoreError, match="could not connect"):
        store.reader().get_nodes(["DataAsset:" + "0" * 32])


def test_connections_get_a_timeout_unless_the_dsn_sets_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import psycopg

    seen: list[dict[str, Any]] = []

    def fake_connect(**options: Any) -> None:
        seen.append(options)
        raise psycopg.OperationalError("refused")

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    for dsn in ("postgresql://u@db/meta", "postgresql://u@db/meta?connect_timeout=3"):
        with pytest.raises(gg.StoreError, match="could not connect"):
            postgres._connect(dsn)
    assert seen[0]["connect_timeout"] == postgres.CONNECT_TIMEOUT_S
    assert seen[1]["connect_timeout"] == "3"
    assert seen[0]["host"] == "db"


def test_nothing_is_sent_when_there_is_nothing_to_do(ontology: gg.Ontology) -> None:
    store = gg.PostgresStore(ontology, UNREACHABLE)
    assert store.upsert() == (0, 0)
    assert store.existing_node_ids(["garbage", "Ghost:" + "0" * 32]) == set()
    reader = store.reader()
    assert reader.get_nodes(["garbage"]) == []
    assert reader.ontology is ontology
    reader.close()


def test_invalid_records_fail_before_connecting(ontology: gg.Ontology) -> None:
    store = gg.PostgresStore(ontology, UNREACHABLE)
    with pytest.raises(gg.RecordError):
        store.upsert([gg.NodeRecord("Owner", {"identifier": "a"}, {"age": 3})])


def test_missing_driver_is_explained(
    monkeypatch: pytest.MonkeyPatch, ontology: gg.Ontology
) -> None:
    real_import = builtins.__import__

    def without_psycopg(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "psycopg":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_psycopg)
    with pytest.raises(gg.StoreError, match=r"pip install 'govgraph\[postgres\]'"):
        postgres._driver()
    with pytest.raises(gg.StoreError, match="needs psycopg"):
        gg.PostgresStore(ontology, UNREACHABLE).create_schema()


def test_unsafe_schema_name_is_refused(ontology: gg.Ontology) -> None:
    with pytest.raises(gg.StoreError, match="unsafe SQL identifier"):
        gg.PostgresStore(ontology, UNREACHABLE, schema='g"; DROP SCHEMA public; --')
