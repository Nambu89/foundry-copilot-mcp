"""Power BI bridge — read a semantic model through Microsoft's official modeling MCP server.

This is `StdioMcpClient` put to work. Microsoft ships an MCP server that can connect to a Power
BI semantic model — a local Power BI Desktop instance, a PBIP folder on disk, or a model hosted
in Fabric — and export it as TMDL (the text format of a tabular model).

We wrap it because the raw server is deliberately low-level: connect, then export, then parse.
Our users say "look at this model" once.

Two deliberate constraints:

* **Read-only by default.** The child process is launched with `--read-only`. Writing to a
  production semantic model should be a separate, explicit decision — not something an agent can
  reach by accident while answering a question.
* **The user's own sign-in.** The official server authenticates interactively in the browser, so
  every read happens as the person asking. Row-level security therefore applies. If you wire a
  service principal in here instead, RLS is bypassed and users can see rows they should not —
  quietly, with no error to alert you.

Verified against @microsoft/powerbi-modeling-mcp (beta) and the TMDL format.
"""

from __future__ import annotations

import os
import re

from foundry_mcp.mcp_client import McpError, StdioMcpClient, resource_texts

# The official server, pinned read-only. Override only if you know why.
PBI_MCP_COMMAND = os.environ.get(
    "PBI_MCP_COMMAND", "npx -y @microsoft/powerbi-modeling-mcp --read-only")


def connect_request(*, workspace: str = "", model: str = "", folder: str = "") -> dict:
    """Build the connection request for the official server.

    Either a Fabric workspace + semantic model name, or a local PBIP/`.SemanticModel` folder.
    """
    if folder:
        return {"operation": "ConnectFolder", "folderPath": folder}
    if workspace and model:
        return {"operation": "ConnectFabric", "workspaceName": workspace,
                "semanticModelName": model}
    raise ValueError("provide either folder=..., or both workspace=... and model=...")


def export_tmdl(connection: dict, *, timeout: float = 300.0) -> str:
    """Connect to a semantic model and return its full TMDL definition.

    `maxReturnCharacters: -1` matters: without it the server truncates large models, and you get
    a definition that parses fine but is silently incomplete — the worst kind of wrong.
    """
    with StdioMcpClient(PBI_MCP_COMMAND, timeout=timeout) as mcp:
        mcp.initialize()
        mcp.call_tool("connection_operations", {"request": connection})
        result = mcp.call_tool("model_operations", {"request": {
            "operation": "ExportTMDL",
            "tmdlExportOptions": {
                "serializationOptions": {"includeChildren": True},
                "maxReturnCharacters": -1,
            },
        }})
    payloads = resource_texts(result)
    if not payloads:
        raise McpError("the export returned no TMDL payload")
    return payloads[0]


# --- Reading measures out of TMDL ---------------------------------------------------------------
#
# TMDL writes a measure in one of two shapes, and real models contain both:
#
#     measure 'Total Sales' = SUM(Sales[Amount])          -- inline, single line
#
#     measure 'Margin %' =                                 -- block, indented body
#             ```
#             DIVIDE([Profit], [Total Sales])
#             ```
#
# Regex is enough to *read* them. It would not be enough to rewrite them safely, which is why
# writing goes back through the official server instead of patching text (see `update_measure`).

_TABLE_RE = re.compile(r"^table[^\S\r\n]+(?:'([^']+)'|(\S+))", re.MULTILINE)
# Every gap here is HORIZONTAL whitespace ([^\S\r\n]), never plain \s. With re.MULTILINE, `\s*`
# happily crosses the newline, so `measure 'X' =` followed by a fenced body would capture the
# ``` line as the expression — quietly turning every block measure into garbage.
_MEASURE_RE = re.compile(
    r"^[^\S\r\n]*measure[^\S\r\n]+(?:'([^']+)'|([^\s=]+))[^\S\r\n]*=[^\S\r\n]*(.*)$",
    re.MULTILINE)


def _table_at(tmdl: str, position: int) -> str:
    """Name of the table whose block contains `position` (measures are nested under a table)."""
    table = ""
    for match in _TABLE_RE.finditer(tmdl):
        if match.start() > position:
            break
        table = match.group(1) or match.group(2) or ""
    return table


def parse_measures(tmdl: str) -> dict[str, dict[str, str]]:
    """Return `{measure_name: {"table": ..., "expression": ...}}` for every measure in the model."""
    measures: dict[str, dict[str, str]] = {}
    lines = tmdl.splitlines()
    # Offset of the start of each line, so a regex match can be mapped back to its line index.
    offsets, running = [], 0
    for line in lines:
        offsets.append(running)
        running += len(line) + 1

    for match in _MEASURE_RE.finditer(tmdl):
        name = (match.group(1) or match.group(2) or "").strip()
        if not name:
            continue
        expression = (match.group(3) or "").strip()
        if not expression or expression.startswith("```"):
            # Block form: the body sits between the ``` fences that follow.
            line_index = next((i for i, off in enumerate(offsets) if off >= match.start()), 0)
            body, inside = [], False
            for line in lines[line_index + 1:]:
                stripped = line.strip()
                if stripped.startswith("```"):
                    if inside:
                        break
                    inside = True
                    continue
                if inside:
                    body.append(line.strip())
                elif stripped and not stripped.startswith("///"):
                    break  # next property, not an expression body
            expression = "\n".join(body).strip()
        measures[name] = {"table": _table_at(tmdl, match.start()), "expression": expression}
    return measures


def normalise_measure_reference(reference: str) -> str:
    """Accept `Table[Measure]`, `[Measure]` or `Measure` and return the bare measure name.

    Analysis tools tend to report findings in the qualified form, while people type the short
    one. Normalising here means the caller can pass through whatever they have.
    """
    reference = (reference or "").strip()
    match = re.search(r"\[([^\]]+)\]\s*$", reference)
    return (match.group(1) if match else reference).strip()


def update_measure(connection: dict, *, table: str, name: str, expression: str,
                   timeout: float = 180.0) -> str:
    """Write a new DAX expression for one measure. **Mutates the model — call deliberately.**

    Deliberately not a text patch of the TMDL: the change goes through the official server's
    `measure_operations/Update`, so the tabular engine validates the DAX and rejects anything
    invalid. Hand-editing TMDL would happily write a broken model.

    Requires the downstream server to run *without* `--read-only`, and the user to hold write
    access (Contributor plus a read/write XMLA endpoint). Expect a permission error otherwise —
    that error is the safety net working, not a bug.
    """
    command = PBI_MCP_COMMAND.replace("--read-only", "").strip()
    with StdioMcpClient(command, timeout=timeout) as mcp:
        mcp.initialize()
        mcp.call_tool("connection_operations", {"request": connection})
        result = mcp.call_tool("measure_operations", {"request": {
            "operation": "Update",
            "definitions": [{"tableName": table, "name": name, "expression": expression}],
        }})
    from foundry_mcp.mcp_client import text_content
    return text_content(result) or "measure updated"
