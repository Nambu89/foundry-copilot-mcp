"""Bridge #1 — expose a Microsoft Foundry agent as a tool inside GitHub Copilot Chat.

This is the smallest useful piece of the whole project, and the one people usually get wrong.

The idea: you already built an agent in Microsoft Foundry (a *prompt agent*: instructions,
a model deployment, maybe some OpenAPI tools). It works in the Foundry playground. Now you want
your team to talk to it from where they already live — VS Code, Copilot Chat — without asking
them to open a portal, and without rebuilding the agent's logic on the client.

The bridge is three calls:

    project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential())
    client  = project.get_openai_client(agent_name=AGENT_NAME)  # pre-bound to your agent
    reply   = client.responses.create(input=prompt).output_text

`get_openai_client(agent_name=...)` returns an OpenAI-shaped client that is already wired to
that agent, so you do NOT pass a model or re-send the system prompt: the agent's instructions,
tools and model deployment all live server-side in Foundry. Change the agent's prompt in the
portal and every client picks it up on the next call, with no redeploy.

Authentication is `DefaultAzureCredential`, which means the *user's own* identity (their
`az login`). That matters more than it looks: the agent runs under the permissions of whoever
is asking, so anything it reaches downstream honours their access, not a shared service
principal's. See docs/01-foundry-bridge.md.

Verified against azure-ai-projects 2.3.0.
"""

from __future__ import annotations

import os
from typing import Any

# Read once at import time so a missing setting fails loudly at startup, not mid-conversation.
PROJECT_ENDPOINT = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "").strip()
AGENT_NAME = os.environ.get("FOUNDRY_AGENT_NAME", "").strip()

_client: Any | None = None


class FoundryNotConfigured(RuntimeError):
    """Raised when the Foundry settings are missing, with a message that says what to do."""


def _require_config() -> None:
    missing = [name for name, value in
               (("FOUNDRY_PROJECT_ENDPOINT", PROJECT_ENDPOINT), ("FOUNDRY_AGENT_NAME", AGENT_NAME))
               if not value]
    if missing:
        raise FoundryNotConfigured(
            f"Missing environment variable(s): {', '.join(missing)}. "
            "Set them in .vscode/mcp.json (see .vscode/mcp.json.sample). "
            "FOUNDRY_PROJECT_ENDPOINT looks like "
            "https://<resource>.services.ai.azure.com/api/projects/<project>"
        )


def get_client() -> Any:
    """Return the OpenAI-shaped client bound to the Foundry agent, creating it on first use.

    Cached: building the credential chain is slow and every tool call would otherwise pay for it.
    """
    global _client
    if _client is not None:
        return _client

    _require_config()

    # Imported lazily so that `--selftest` and the unit tests can run without the Azure SDK
    # installed, and so a missing dependency surfaces as a clear message instead of an import
    # error at startup.
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential()
    try:
        project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)
        _client = project.get_openai_client(agent_name=AGENT_NAME)
    except ValueError as exc:
        # Gotcha worth knowing about: the documented prompt-agent quickstart does not mention
        # `allow_preview`, but some SDK builds share a code path with hosted agents and raise a
        # ValueError demanding it. We try the documented way first and only opt into preview if
        # the SDK explicitly asks — never assume the flag is needed.
        if "allow_preview" not in str(exc):
            raise
        project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential,
                                  allow_preview=True)
        _client = project.get_openai_client(agent_name=AGENT_NAME)
    return _client


def ask_agent(prompt: str) -> str:
    """Send one prompt to the Foundry agent and return its text answer.

    Single turn: no conversation state is kept here. If you need multi-turn memory, Foundry has
    a `conversations` API — but for a Copilot Chat tool, single turn is usually what you want,
    because Copilot itself is already holding the conversation with the user.
    """
    client = get_client()
    response = client.responses.create(input=prompt)
    return response.output_text or "(the agent returned no text)"
