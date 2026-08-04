"""Bridge #2 — a minimal MCP *client*, so your MCP server can call another MCP server.

Everyone writes MCP servers. Far fewer write an MCP client, and that is where the interesting
architecture lives: your server can stand in front of somebody else's server, add its own
policy, and expose the result as a single tool.

Why you would want that:

* Vendor servers are general-purpose; yours is opinionated. Microsoft's Power BI modeling server
  exposes low-level operations. Your users want "connect and analyse this model" — one call.
* You can enforce policy at the boundary. This client launches the downstream server read-only
  by default, so no chain of LLM calls can end up mutating a production model by accident.
* Auth stays with the user. The downstream server does its own interactive sign-in, which means
  the user's own permissions (and row-level security) apply. A service principal would silently
  bypass RLS — that is a real, and quiet, data-leak class of bug.

The protocol is JSON-RPC 2.0 over stdio, newline-delimited. Three things bite you:

1. You must `initialize` and then send the `notifications/initialized` notification before any
   `tools/call`. Notifications have no `id` and get no reply — don't wait for one.
2. The server may emit unrelated notifications (logs, progress) *before* the response you want,
   so match on the request `id`; never assume the next line is your answer.
3. A crashed child writes nothing and you block forever. Every read here is bounded by a timeout.

Verified against the MCP stdio transport, protocol revision 2025-06-18.
"""

from __future__ import annotations

import json
import subprocess
import threading
from queue import Empty, Queue
from typing import Any

# Protocol revision this client speaks. Servers negotiate down if they support an older one.
PROTOCOL_VERSION = "2025-06-18"
DEFAULT_TIMEOUT = 180.0


class McpError(RuntimeError):
    """The downstream MCP server failed, or never answered."""


class StdioMcpClient:
    """Talks JSON-RPC to an MCP server running as a child process over stdio.

    Use as a context manager so the child is always reaped, even if a tool call raises:

        with StdioMcpClient("npx -y some-mcp-server") as mcp:
            mcp.initialize()
            result = mcp.call_tool("some_tool", {"arg": "value"})
    """

    def __init__(self, command: str, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._command = command
        self._timeout = timeout
        self._proc: subprocess.Popen[str] | None = None
        self._next_id = 0
        self._lines: Queue[str] = Queue()
        self._reader: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------------------------

    def __enter__(self) -> StdioMcpClient:
        self._proc = subprocess.Popen(
            self._command, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
        )
        # Read stdout on a background thread. Reading inline would block forever if the child
        # dies without writing, which is exactly what a misconfigured server does.
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            self._proc.wait(timeout=10)
        except Exception:  # noqa: BLE001 - best-effort cleanup; never mask the original error
            self._proc.kill()

    def _pump(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            self._lines.put(line)

    # -- protocol ----------------------------------------------------------------------------

    def _send(self, method: str, params: dict, *, notification: bool = False) -> dict | None:
        if self._proc is None or self._proc.stdin is None:
            raise McpError("client used outside its context manager")
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            self._next_id += 1
            message["id"] = self._next_id
        self._proc.stdin.write(json.dumps(message) + "\n")
        self._proc.stdin.flush()
        if notification:
            return None  # notifications get no reply — returning here is the whole point
        return self._await_response(self._next_id)

    def _await_response(self, request_id: int) -> dict:
        """Read until the reply with our id shows up, skipping notifications and log noise."""
        while True:
            try:
                line = self._lines.get(timeout=self._timeout)
            except Empty:
                raise McpError(
                    f"the MCP server did not answer request {request_id} within "
                    f"{self._timeout:.0f}s (command: {self._command})"
                ) from None
            line = line.strip()
            if not line.startswith("{"):
                continue  # banners and stray output are not protocol messages
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == request_id:
                if "error" in message:
                    raise McpError(f"MCP error: {json.dumps(message['error'])[:400]}")
                return message

    def initialize(self, *, client_name: str = "foundry-copilot-mcp") -> dict:
        """Perform the handshake. Must be called before any tool call."""
        reply = self._send("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": client_name, "version": "1.0"},
        })
        # The spec requires this notification; skipping it leaves some servers refusing calls.
        self._send("notifications/initialized", {}, notification=True)
        return reply or {}

    def list_tools(self) -> list[dict]:
        """Ask the downstream server what it can do. Handy when its docs are thin."""
        reply = self._send("tools/list", {}) or {}
        return (reply.get("result") or {}).get("tools") or []

    def call_tool(self, name: str, arguments: dict) -> dict:
        """Call a tool and return its raw `result`.

        Note the MCP quirk: a failing tool call is a *successful* JSON-RPC response carrying
        `isError: true`. Checking only for transport errors will silently swallow real failures.
        """
        reply = self._send("tools/call", {"name": name, "arguments": arguments}) or {}
        result = reply.get("result") or {}
        if result.get("isError"):
            text = ""
            for item in result.get("content") or []:
                if item.get("type") == "text":
                    text = item.get("text", "")
                    break
            raise McpError(f"tool '{name}' failed: {text[:400]}")
        return result


def text_content(result: dict) -> str:
    """Concatenate the text parts of an MCP tool result."""
    return "\n".join(item.get("text", "") for item in (result.get("content") or [])
                     if item.get("type") == "text")


def resource_texts(result: dict) -> list[str]:
    """Extract embedded resource payloads (how large documents come back, e.g. an exported model)."""
    out: list[str] = []
    for item in result.get("content") or []:
        if item.get("type") == "resource":
            text = (item.get("resource") or {}).get("text")
            if text:
                out.append(text)
    return out
