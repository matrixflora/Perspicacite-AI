"""Unit tests for cancel_task MCP tool + registry integration."""
import json
import pytest

from perspicacite.mcp.server import cancel_task
from perspicacite.rag import cancellation as cr


@pytest.fixture(autouse=True)
async def _reset():
    await cr.reset_for_tests()
    yield
    await cr.reset_for_tests()


@pytest.mark.asyncio
async def test_cancel_task_marks_registry():
    out = await cancel_task("task-123")
    data = json.loads(out)
    assert data["ok"] is True
    assert data["task_id"] == "task-123"
    assert cr.is_cancelled("task-123") is True


@pytest.mark.asyncio
async def test_cancel_task_empty_id_rejected():
    out = await cancel_task("")
    data = json.loads(out)
    assert data["ok"] is False
    assert "missing" in data["error"]


@pytest.mark.asyncio
async def test_cancel_task_idempotent():
    await cancel_task("x")
    await cancel_task("x")
    assert cr.is_cancelled("x") is True
