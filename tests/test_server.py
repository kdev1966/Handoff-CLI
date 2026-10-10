import pytest
from mcp import Client
from mcp_types import TextContent

from ai_handoff.context_file import context_path
from ai_handoff.project import Project
from ai_handoff.server import mcp
from ai_handoff.store import Store

pytestmark = pytest.mark.anyio


def text_of(result) -> str:
    return "\n".join(block.text for block in result.content if isinstance(block, TextContent))


async def test_exposes_three_annotated_tools() -> None:
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert "memory_get" in (client.instructions or "")
    assert set(tools) == {"memory_get", "memory_save", "memory_history"}
    assert tools["memory_get"].annotations.read_only_hint is True
    assert tools["memory_history"].annotations.read_only_hint is True
    assert tools["memory_save"].annotations.destructive_hint is False


async def test_save_then_get(project: Project) -> None:
    async with Client(mcp) as client:
        empty = await client.call_tool("memory_get", {"project_path": str(project.root)})
        assert "No memory saved yet" in text_of(empty)

        saved = await client.call_tool(
            "memory_save",
            {
                "project_path": str(project.root),
                "summary": "Auth module done; api_key=abcdef123456 used in tests",
                "next_actions": "Write the README",
                "stack": "Python 3.12, FastAPI",
                "agent": "claude-code",
            },
        )
        assert not saved.is_error
        assert "1 secret-looking value" in text_of(saved)

        result = text_of(await client.call_tool("memory_get", {"project_path": str(project.root)}))
    assert "Auth module done" in result
    assert "abcdef123456" not in result
    assert "claude-code" in result
    assert context_path(project).exists()


async def test_history(project: Project) -> None:
    async with Client(mcp) as client:
        for step in ("one", "two"):
            await client.call_tool(
                "memory_save", {"project_path": str(project.root), "summary": step, "next_actions": ""}
            )
        result = text_of(
            await client.call_tool("memory_history", {"project_path": str(project.root), "limit": 5})
        )
    assert result.index("two") < result.index("one")


@pytest.mark.parametrize("bad_path", ["relative/path", "/definitely/not/here"])
async def test_invalid_path_is_a_tool_error(bad_path: str) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("memory_get", {"project_path": bad_path})
    assert result.is_error


async def test_validation_error_is_a_tool_error(project: Project) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "memory_save", {"project_path": str(project.root), "summary": "  ", "next_actions": ""}
        )
    assert result.is_error
    assert "summary" in text_of(result)


async def test_paused_project_is_not_exposed(project: Project) -> None:
    with Store() as store:
        store.save(project, summary="secret plan", next_actions="")
        store.set_paused(project, True)
    async with Client(mcp) as client:
        read = await client.call_tool("memory_get", {"project_path": str(project.root)})
        write = await client.call_tool(
            "memory_save", {"project_path": str(project.root), "summary": "x", "next_actions": ""}
        )
    assert "paused" in text_of(read)
    assert "secret plan" not in text_of(read)
    assert write.is_error


async def test_save_warns_about_empty_handoffs(project: Project) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "memory_save", {"project_path": str(project.root), "summary": "SUMMARY", "next_actions": ""}
        )
    text = text_of(result)
    assert not result.is_error
    assert "summary is very short" in text
    assert "no next actions" in text
