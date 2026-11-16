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
