"""Tests for the MCP client — the bit that makes this server a client of another server.

Driven against fake MCP servers (small Python scripts written to disk) rather than mocks, so the
JSON-RPC framing, the notification handling and the timeout are all exercised for real. They run
anywhere: no network, no Azure, no Node.js.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from foundry_mcp.mcp_client import McpError, StdioMcpClient, resource_texts, text_content

# A fake server is a loop over stdin that answers JSON-RPC. `handle(msg)` returns the result dict.
_TEMPLATE = '''
import sys, json

def handle(msg):
{handler}

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    if "id" not in msg:      # notification: never answer one
        continue
    for out in handle(msg):
        sys.stdout.write(json.dumps(out) if isinstance(out, dict) else out)
        sys.stdout.write("\\n")
        sys.stdout.flush()
'''


def _server(tmp_path: Path, handler: str, name: str = "fake_server.py") -> str:
    """Write a fake MCP server to disk and return the command that runs it."""
    script = tmp_path / name
    script.write_text(_TEMPLATE.format(handler=textwrap.indent(textwrap.dedent(handler), "    ")),
                      encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


ECHO = '''
if msg["method"] == "initialize":
    return [{"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18"}}]
if msg["method"] == "tools/list":
    return [{"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [{"name": "echo"}]}}]
args = msg["params"].get("arguments", {})
return [{"jsonrpc": "2.0", "id": msg["id"],
         "result": {"content": [{"type": "text", "text": json.dumps(args)}]}}]
'''


def test_handshake_and_tool_call(tmp_path):
    with StdioMcpClient(_server(tmp_path, ECHO), timeout=30) as mcp:
        mcp.initialize()
        assert [t["name"] for t in mcp.list_tools()] == ["echo"]
        result = mcp.call_tool("echo", {"hello": "world"})
        assert json.loads(text_content(result)) == {"hello": "world"}


NOISY = '''
return [
    "starting up, not JSON at all",
    {"jsonrpc": "2.0", "method": "notifications/message",
     "params": {"level": "info", "data": "working"}},
    {"jsonrpc": "2.0", "id": msg["id"], "result": {"content": [{"type": "text", "text": "done"}]}},
]
'''


def test_skips_banners_and_notifications_before_the_answer(tmp_path):
    """Real servers log before answering. Taking the next line would read the wrong message."""
    with StdioMcpClient(_server(tmp_path, NOISY), timeout=30) as mcp:
        mcp.initialize()
        assert text_content(mcp.call_tool("anything", {})) == "done"


FAILING = '''
if msg["method"] == "initialize":
    return [{"jsonrpc": "2.0", "id": msg["id"], "result": {}}]
return [{"jsonrpc": "2.0", "id": msg["id"],
         "result": {"isError": True,
                    "content": [{"type": "text", "text": "model not found"}]}}]
'''


def test_tool_failure_is_raised_even_though_the_transport_succeeded(tmp_path):
    """MCP reports tool failures as a *successful* response carrying isError — easy to miss."""
    with StdioMcpClient(_server(tmp_path, FAILING), timeout=30) as mcp:
        mcp.initialize()
        with pytest.raises(McpError, match="model not found"):
            mcp.call_tool("connection_operations", {"request": {}})


SILENT = '''
import time
time.sleep(30)
return []
'''


def test_a_server_that_never_answers_times_out_instead_of_hanging(tmp_path):
    """Without the timeout this blocks forever, which is what a misconfigured server does."""
    with StdioMcpClient(_server(tmp_path, SILENT), timeout=2) as mcp:
        with pytest.raises(McpError, match="did not answer"):
            mcp.initialize()


PROTOCOL_ERROR = '''
if msg["method"] == "initialize":
    return [{"jsonrpc": "2.0", "id": msg["id"], "result": {}}]
return [{"jsonrpc": "2.0", "id": msg["id"],
         "error": {"code": -32601, "message": "method not found"}}]
'''


def test_jsonrpc_error_is_surfaced(tmp_path):
    with StdioMcpClient(_server(tmp_path, PROTOCOL_ERROR), timeout=30) as mcp:
        mcp.initialize()
        with pytest.raises(McpError, match="method not found"):
            mcp.call_tool("nope", {})


def test_resource_texts_extracts_embedded_payloads():
    """Large documents (an exported model, say) come back as embedded resources, not plain text."""
    result = {"content": [
        {"type": "text", "text": "here you go"},
        {"type": "resource", "resource": {"text": "table Sales\n\tmeasure X = 1"}},
    ]}
    assert resource_texts(result) == ["table Sales\n\tmeasure X = 1"]
    assert text_content(result) == "here you go"


def test_client_used_outside_its_context_manager_fails_clearly():
    with pytest.raises(McpError, match="context manager"):
        StdioMcpClient("does-not-matter").call_tool("x", {})
