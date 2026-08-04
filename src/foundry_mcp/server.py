"""The MCP server itself — what Copilot Chat actually talks to.

Five tools, one per idea:

    ask_agent          -> Foundry agent answers, inside Copilot Chat        (bridge 1)
    inspect_model      -> read a semantic model via Microsoft's MCP server  (bridge 2)
    show_measure       -> the current DAX of one measure                    (bridge 2)
    update_measure_dax -> write a corrected measure  [opt-in, destructive]  (bridge 2)
    resolve_fabric_ids -> GUIDs from a Fabric URL -> display names          (bridge 3)

Two conventions that matter more than they look, both learned from production use:

**Tool docstrings are prompt engineering.** Copilot picks tools by reading these descriptions.
Vague docstring, wrong tool. Say what it does, when to use it, and — importantly — when *not* to.

**Return prose, not raw JSON.** The output goes to a language model that will paraphrase it to a
human. Dumping JSON invites the model to invent a summary. Formatted text with the real numbers
in it gets repeated accurately.
"""

from __future__ import annotations

import argparse
import sys

from mcp.server.fastmcp import FastMCP

from foundry_mcp import fabric, pbi_bridge
from foundry_mcp.foundry import FoundryNotConfigured, ask_agent

mcp = FastMCP("foundry-copilot-mcp")

# Cache of the last connection per model label, so show_measure/update_measure_dax do not force
# the user to re-specify where the model lives on every call.
_last_connection: dict[str, dict] = {}
_last_tmdl: dict[str, str] = {}


def _fail(exc: Exception) -> str:
    """Turn an exception into something a chat user can act on.

    Never re-raise into Copilot: an MCP tool that throws shows up as an opaque failure, and the
    model tends to answer "something went wrong" and stop. A sentence saying what to fix keeps
    the conversation moving.
    """
    return f"Could not complete the request: {exc}"


@mcp.tool()
def ask_agent_tool(prompt: str) -> str:
    """Ask the Microsoft Foundry agent a question and return its answer.

    Use this for anything the agent is specialised in — its instructions, knowledge and tools all
    live server-side in Foundry, so you do not need to know how it works. Pass the user's request
    in plain language.

    Do NOT use this to read a Power BI model: `inspect_model` does that directly and faster.
    """
    try:
        return ask_agent(prompt)
    except FoundryNotConfigured as exc:
        return f"Foundry is not configured: {exc}"
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


@mcp.tool()
def inspect_model(workspace: str = "", model: str = "", folder: str = "") -> str:
    """Connect to a Power BI semantic model and summarise what is in it.

    Accepts either a Fabric workspace and semantic model (by display name OR by the GUIDs from
    the Fabric URL), or the path to a local PBIP / `.SemanticModel` folder.

    Read-only: it never modifies the model. Authentication is the user's own, so their
    permissions and row-level security apply. Run this before `show_measure`.
    """
    try:
        label = model or folder or "model"
        if workspace and model and (fabric.is_guid(workspace) or fabric.is_guid(model)):
            workspace, model = fabric.resolve_names(workspace, model)
        connection = pbi_bridge.connect_request(workspace=workspace, model=model, folder=folder)
        tmdl = pbi_bridge.export_tmdl(connection)
        _last_connection[label] = connection
        _last_tmdl[label] = tmdl

        measures = pbi_bridge.parse_measures(tmdl)
        tables = sorted({m["table"] for m in measures.values() if m["table"]})
        lines = [
            f"Connected to '{model or folder}'.",
            f"- {len(measures)} measures across {len(tables)} tables holding measures.",
            f"- TMDL definition: {len(tmdl.splitlines())} lines.",
        ]
        if measures:
            sample = ", ".join(sorted(measures)[:8])
            lines.append(f"- For example: {sample}{'...' if len(measures) > 8 else ''}")
        lines.append(f"Use show_measure with model_label='{label}' to see any measure's DAX.")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


@mcp.tool()
def show_measure(measure: str, model_label: str = "") -> str:
    """Show the current DAX expression of one measure, and the table it belongs to.

    Call `inspect_model` first. Accepts `Table[Measure]`, `[Measure]` or just the measure name,
    so you can paste a reference straight from an analysis report.
    """
    try:
        label = model_label or (next(iter(_last_tmdl), "") if _last_tmdl else "")
        tmdl = _last_tmdl.get(label)
        if not tmdl:
            return ("No model has been read yet in this session. Call inspect_model first "
                    "(the DAX is read from the exported definition).")
        name = pbi_bridge.normalise_measure_reference(measure)
        measures = pbi_bridge.parse_measures(tmdl)
        found = measures.get(name)
        if found is None:
            close = [m for m in measures if name.lower() in m.lower()][:5]
            hint = f" Did you mean: {', '.join(close)}?" if close else ""
            return f"No measure named '{name}' in this model.{hint}"
        return (f"Measure '{name}' (table {found['table'] or 'unknown'}):\n\n"
                f"```dax\n{found['expression']}\n```")
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


@mcp.tool()
def update_measure_dax(measure: str, new_expression: str, model_label: str = "",
                       confirm: bool = False) -> str:
    """Write a new DAX expression for a measure. MODIFIES THE MODEL.

    Read-only is the default everywhere else in this server; this is the one exception, and it is
    opt-in twice over: the caller must pass `confirm=True`, and the user needs write access
    (Contributor plus a read/write XMLA endpoint). Always show the user the current expression
    with `show_measure` and get their agreement before calling this.

    The tabular engine validates the DAX, so an invalid expression is rejected rather than saved.
    """
    try:
        label = model_label or (next(iter(_last_tmdl), "") if _last_tmdl else "")
        tmdl = _last_tmdl.get(label)
        connection = _last_connection.get(label)
        if not tmdl or not connection:
            return "Call inspect_model first: I need to know which model to write to."
        name = pbi_bridge.normalise_measure_reference(measure)
        found = pbi_bridge.parse_measures(tmdl).get(name)
        if found is None:
            return f"No measure named '{name}' in this model. Nothing was changed."
        if not confirm:
            return (f"This would replace the definition of '{name}' in table "
                    f"'{found['table']}':\n\n"
                    f"Current:\n```dax\n{found['expression']}\n```\n\n"
                    f"Proposed:\n```dax\n{new_expression}\n```\n\n"
                    "Nothing has been changed. Ask the user to confirm, then call again with "
                    "confirm=True.")
        result = pbi_bridge.update_measure(connection, table=found["table"], name=name,
                                           expression=new_expression)
        return f"Measure '{name}' updated. {result}"
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


@mcp.tool()
def resolve_fabric_ids(workspace_id: str, model_id: str = "") -> str:
    """Turn the GUIDs in a Fabric URL into the display names the tools need.

    Use when the user pastes a Fabric Studio link instead of typing names.
    """
    try:
        workspace_name, model_name = fabric.resolve_names(workspace_id, model_id)
        return (f"Workspace: {workspace_name}\n"
                f"Semantic model: {model_name or '(not requested)'}")
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


def _selftest(prompt: str) -> int:
    """Check the Foundry connection without going through VS Code. Run before demoing."""
    try:
        print(ask_agent(prompt))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"selftest failed: {exc}", file=sys.stderr)
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP server bridging Microsoft Foundry, "
                                                 "Power BI and GitHub Copilot Chat.")
    parser.add_argument("--selftest", metavar="PROMPT",
                        help="send one prompt to the Foundry agent and exit")
    args = parser.parse_args()
    if args.selftest:
        raise SystemExit(_selftest(args.selftest))
    mcp.run()


if __name__ == "__main__":
    main()
