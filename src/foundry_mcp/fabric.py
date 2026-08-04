"""Bridge #3 — the bit of the Fabric REST API you actually need: turning GUIDs into names.

A small problem with a annoying cause. The official Power BI modeling server connects to a
semantic model **by name**. But when a user is looking at a model in Fabric, what they have in
front of them is the URL:

    https://app.fabric.microsoft.com/groups/<workspace-id>/... /<model-id>

Two GUIDs, no names. Telling people "open the settings blade and copy the display name" is how
you lose them. So we resolve GUID → display name ourselves against the Fabric REST API, and then
hand the *name* to the modeling server.

    GET https://api.fabric.microsoft.com/v1/workspaces/{id}            -> displayName
    GET https://api.fabric.microsoft.com/v1/workspaces/{id}/items/{id} -> displayName

Cross-tenant gotcha, learned the hard way: `DefaultAzureCredential` uses whatever tenant your
`az login` is currently in. If the workspace lives in a *different* tenant, this returns a
confusing 404 "workspace not found" — the workspace exists, you are just asking the wrong
directory. Set FABRIC_TENANT_ID to that tenant. Connecting by name never has this problem,
because the modeling server opens a browser and resolves the tenant itself.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

FABRIC_API = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
# Only needed when the workspace lives outside the tenant of your current `az login`.
FABRIC_TENANT_ID = os.environ.get("FABRIC_TENANT_ID", "").strip()

_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                      r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class FabricError(RuntimeError):
    """A Fabric REST call failed, with an actionable message where we can give one."""


def is_guid(value: str) -> bool:
    """True if the string is a GUID, i.e. an id rather than a display name."""
    return bool(_GUID_RE.match((value or "").strip()))


def _token() -> str:
    """Acquire a Fabric access token for the current user."""
    from azure.identity import AzureCliCredential, DefaultAzureCredential, InteractiveBrowserCredential

    if FABRIC_TENANT_ID:
        # Explicit tenant: try the CLI first (silent), fall back to a browser prompt.
        try:
            return AzureCliCredential(tenant_id=FABRIC_TENANT_ID).get_token(FABRIC_SCOPE).token
        except Exception:  # noqa: BLE001 - not signed in to that tenant yet; prompt instead
            return InteractiveBrowserCredential(
                tenant_id=FABRIC_TENANT_ID).get_token(FABRIC_SCOPE).token
    return DefaultAzureCredential().get_token(FABRIC_SCOPE).token


def _get(path: str, token: str) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(f"{FABRIC_API}{path}",
                                     headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8")
            return response.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"error": body[:400]}


def resolve_names(workspace: str, model: str) -> tuple[str, str]:
    """Return `(workspace_name, model_name)`, resolving whichever argument is a GUID.

    Anything that is already a name is passed straight through, so callers can mix and match.
    """
    if not is_guid(workspace) and not is_guid(model):
        return workspace, model

    token = _token()
    workspace_name, model_name = workspace, model

    if is_guid(workspace):
        status, body = _get(f"/workspaces/{workspace}", token)
        if status == 404:
            raise FabricError(
                f"workspace {workspace} not found. If it belongs to another tenant, set "
                "FABRIC_TENANT_ID to that tenant — or just pass the workspace name instead, "
                "which sidesteps tenant resolution entirely."
            )
        if status >= 400:
            raise FabricError(f"could not resolve the workspace ({status}): "
                              f"{json.dumps(body)[:300]}")
        workspace_name = body.get("displayName") or workspace

    if is_guid(model):
        status, body = _get(f"/workspaces/{workspace}/items/{model}", token)
        if status >= 400:
            raise FabricError(f"could not resolve the semantic model ({status}): "
                              f"{json.dumps(body)[:300]}")
        model_name = body.get("displayName") or model

    return workspace_name, model_name


def list_semantic_models(workspace: str) -> list[dict[str, str]]:
    """List the semantic models in a workspace, as `[{"id":..., "name":...}]`.

    Useful right after a failed connection: showing the user what *does* exist beats telling
    them the name they typed was wrong.
    """
    token = _token()
    status, body = _get(f"/workspaces/{workspace}/semanticModels", token)
    if status >= 400:
        raise FabricError(f"could not list semantic models ({status}): {json.dumps(body)[:300]}")
    return [{"id": item.get("id", ""), "name": item.get("displayName", "")}
            for item in body.get("value", [])]
