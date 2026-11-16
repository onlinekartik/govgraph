from __future__ import annotations

import json
import re

import pytest

import govgraph as gg
from govgraph.export import EXPORT_MAX_DEPTH, export_json
from govgraph.samples import asset
from govgraph.viewer import render_viewer
from tests.conftest import Backend

F1002 = gg.node_id("DQFailure", {"failure_id": "F-1002"})


def test_export_of_depth_zero_is_just_the_roots(loaded: Backend) -> None:
    graph = export_json(loaded.reader(), [F1002], depth=0)
    assert graph["ontology"] == {"name": "core", "version": "1.0.0"}
    assert graph["roots"] == [F1002]
    assert [n["id"] for n in graph["nodes"]] == [F1002]
    assert graph["nodes"][0]["label"] == "F-1002"
    assert graph["edges"] == []
    assert graph["truncated"] is False


def test_export_follows_edges_in_both_directions(loaded: Backend) -> None:
    graph = export_json(loaded.reader(), [asset("marts.order_facts.order_total").id], depth=1)
    labels = {n["label"] for n in graph["nodes"]}
    assert labels == {
        "marts.order_facts.order_total",
        "Order Total",
        "Recognised Revenue",
        "Order total is not negative",
        "staging.payments.amount",
        "reporting.daily_revenue.revenue",
        "F-1002",
    }
    node_ids = {n["id"] for n in graph["nodes"]}
    assert len(graph["edges"]) == 6
    for edge in graph["edges"]:
        assert {edge["source"], edge["target"]} <= node_ids
        assert set(edge) == {"id", "type", "source", "target", "label", "properties"}
    derives = next(e for e in graph["edges"] if e["type"] == "DERIVES_FROM")
    assert derives["label"] == "derives from"
    assert derives["properties"]["lineage_id"].startswith("LineageEdge:")
    json.dumps(graph)


def test_export_grows_with_depth_and_accepts_several_roots(memory_store: gg.MemoryStore) -> None:
    reader = memory_store.reader()
    sizes = [len(export_json(reader, [F1002], depth=d)["nodes"]) for d in range(4)]
    assert sizes == sorted(sizes)
    assert sizes[0] == 1
    assert sizes[3] == 19
    two = export_json(reader, [F1002, asset("raw.customers").id], depth=0)
    assert len(two["nodes"]) == 2
    assert len(two["roots"]) == 2


def test_export_is_bounded(memory_store: gg.MemoryStore) -> None:
    graph = export_json(
        memory_store.reader(), [F1002], depth=EXPORT_MAX_DEPTH, limits=gg.Limits(max_rows=10)
    )
    assert graph["truncated"] is True
    assert len(graph["nodes"]) + len(graph["edges"]) <= 10
    node_ids = {n["id"] for n in graph["nodes"]}
    assert all({e["source"], e["target"]} <= node_ids for e in graph["edges"])


def test_export_rejects_bad_requests(memory_store: gg.MemoryStore) -> None:
    reader = memory_store.reader()
    with pytest.raises(ValueError, match="depth must be between 0 and 5"):
        export_json(reader, [F1002], depth=6)
    with pytest.raises(ValueError, match="depth must be"):
        export_json(reader, [F1002], depth=-1)
    with pytest.raises(gg.ToolError, match="not_found"):
        export_json(reader, [asset("no.such").id])
    with pytest.raises(gg.ToolError, match="invalid_input"):
        export_json(reader, ["garbage"])


def test_viewer_embeds_the_export(memory_store: gg.MemoryStore) -> None:
    graph = export_json(memory_store.reader(), [F1002], depth=1)
    page = render_viewer(graph)

    assert page.startswith("<!doctype html>")
    assert "__GOVGRAPH_" not in page
    assert "const EMBEDDED_GRAPH = {" in page
    embedded = re.search(r"const EMBEDDED_GRAPH = (\{.*\});\n", page)
    assert embedded is not None
    assert json.loads(embedded.group(1)) == graph


def test_viewer_without_data_asks_for_a_file() -> None:
    page = render_viewer()
    assert "const EMBEDDED_GRAPH = null;" in page
    assert 'type="file"' in page


def test_viewer_is_self_contained() -> None:
    page = render_viewer()
    assert "default-src 'none'" in page
    assert page.count("<script") == 1
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", page)
    assert "innerHTML" not in page
    assert "eval(" not in page
    # The only URL in the page is the SVG namespace, which is a name and is never fetched.
    assert set(re.findall(r"https?://[^\s\"']+", page)) == {"http://www.w3.org/2000/svg"}


def test_viewer_data_cannot_break_out_of_its_script_element() -> None:
    hostile = "</script><script>alert(1)</script><!-- \u2028 & -->"
    graph = {"nodes": [{"id": "a", "type": "T", "label": hostile}], "edges": []}
    page = render_viewer(graph)

    assert page.count("</script>") == 1
    assert "<script>alert(1)" not in page
    assert "\u2028" not in page
    embedded = re.search(r"const EMBEDDED_GRAPH = (\{.*\});\n", page)
    assert embedded is not None
    assert json.loads(embedded.group(1)) == graph
