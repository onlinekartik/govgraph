from __future__ import annotations

import builtins
import json
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
import pytest
from mcp import Client

import govgraph as gg
from govgraph.mcp import build_server
from govgraph.samples import asset

F1002 = gg.node_id("DQFailure", {"failure_id": "F-1002"})


@pytest.fixture
def kit(memory_store: gg.MemoryStore) -> gg.Toolkit:
    return gg.Toolkit(memory_store.reader())


def with_client(kit: gg.Toolkit, body: Callable[[Client], Awaitable[Any]]) -> Any:
    async def main() -> Any:
        async with Client(build_server(kit), raise_exceptions=True) as client:
            return await body(client)

    return anyio.run(main)


def test_server_lists_the_tools_with_their_schemas(kit: gg.Toolkit) -> None:
    listed = with_client(kit, lambda client: client.list_tools()).tools

    assert [tool.name for tool in listed] == [s["name"] for s in kit.schemas()]
    for tool, schema in zip(listed, kit.schemas(), strict=True):
        assert tool.description == schema["description"]
        assert tool.input_schema == schema["input_schema"]
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False


def test_server_identifies_itself(kit: gg.Toolkit) -> None:
    async def body(client: Client) -> Any:
        return client.server_info, client.instructions

    info, instructions = with_client(kit, body)
    assert info.name == "govgraph"
    assert info.version == gg.__version__
    assert "truncated" in instructions


def test_calling_a_tool_returns_the_same_result_as_the_toolkit(kit: gg.Toolkit) -> None:
    result = with_client(kit, lambda c: c.call_tool("explain_failure", {"failure_id": F1002}))

    expected = kit.explain_failure(F1002).to_dict()
    assert result.is_error is not True
    payload = json.loads(result.content[0].text)
    assert result.structured_content == payload
    assert {k: v for k, v in payload.items() if k != "elapsed_ms"} == {
        k: v for k, v in expected.items() if k != "elapsed_ms"
    }
    assert payload["claims"][0]["citations"] == [F1002]


def test_defaults_apply_when_optional_arguments_are_omitted(kit: gg.Toolkit) -> None:
    result = with_client(
        kit, lambda c: c.call_tool("impact_of", {"entity_id": asset("raw.payments").id})
    )
    assert result.structured_content["limits"]["depth"] == 2


@pytest.mark.parametrize(
    ("name", "arguments", "code"),
    [
        ("get_asset_context", {"asset_id": "nope"}, "invalid_input"),
        ("get_asset_context", {"asset_id": asset("no.such").id}, "not_found"),
        ("impact_of", {"entity_id": F1002, "depth": 4}, "invalid_input"),
        ("impact_of", {"entity_id": F1002, "depth": 2.0}, "invalid_input"),
        ("run_sql", {"query": "SELECT 1"}, "unknown_tool"),
        ("find_by_term", None, "invalid_input"),
    ],
)
def test_tool_errors_come_back_as_error_results(
    kit: gg.Toolkit, name: str, arguments: dict[str, Any] | None, code: str
) -> None:
    result = with_client(kit, lambda c: c.call_tool(name, arguments))
    assert result.is_error is True
    assert json.loads(result.content[0].text)["error"]["code"] == code
    assert result.structured_content["error"]["code"] == code


def test_unexpected_failures_do_not_leak_details(
    kit: gg.Toolkit, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(name: str, arguments: Any) -> gg.ToolResult:
        raise RuntimeError("INTERNAL-DETAIL that must stay inside the server")

    monkeypatch.setattr(kit, "call", explode)
    result = with_client(kit, lambda c: c.call_tool("find_by_term", {"term": "x"}))

    assert result.is_error is True
    assert result.structured_content == {
        "error": {"code": "internal_error", "message": "the tool failed unexpectedly"}
    }
    assert "INTERNAL-DETAIL" not in result.content[0].text


def test_missing_mcp_package_is_explained(kit: gg.Toolkit, monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def without_mcp(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "mcp":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_mcp)
    with pytest.raises(gg.GovGraphError, match=r"pip install 'govgraph\[mcp\]'"):
        build_server(kit)
