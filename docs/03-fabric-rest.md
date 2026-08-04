# Bridge 3 — Fabric GUIDs into names

The smallest bridge, and the one that decides whether people keep using your tool.

## The mismatch

Microsoft's modeling server connects to a semantic model **by display name**:

```json
{"operation": "ConnectFabric", "workspaceName": "Finance", "semanticModelName": "Sales"}
```

What the user has in front of them is the URL they are already looking at:

```text
https://app.fabric.microsoft.com/groups/3f2a.../semanticmodels/8b41...
```

Two GUIDs, no names anywhere. "Open the settings blade and copy the display name" is a small ask that, in practice, is where people give up. Paste-the-URL has to work.

## Resolving it

```text
GET https://api.fabric.microsoft.com/v1/workspaces/{id}            -> displayName
GET https://api.fabric.microsoft.com/v1/workspaces/{id}/items/{id} -> displayName
```

Scope: `https://api.fabric.microsoft.com/.default`.

`resolve_names()` passes through anything that is already a name, so callers can mix a workspace name with a model GUID without caring.

## The cross-tenant 404

The one that costs an afternoon.

`DefaultAzureCredential` uses whichever tenant your `az login` currently points at. Ask about a workspace in a *different* tenant and Fabric answers:

```json
{"error": "workspace not found"}
```

Which is true, from where you asked. The workspace exists; you queried the wrong directory. Nothing in the message says so.

Two ways out:

```bash
# tell it which tenant
FABRIC_TENANT_ID=<the workspace's tenant>
```

or **connect by name instead**. Names never hit this path: the modeling server opens a browser and resolves the tenant itself. Worth remembering when someone reports the GUID route is broken — the answer is usually "use the name" rather than a fix.

`_token()` handles the explicit-tenant case by trying `AzureCliCredential(tenant_id=...)` first, silently, and only falling back to an interactive browser prompt if you are not signed in to that tenant yet. Prompting first would be rude in a tool that might be called mid-conversation.

## Failing usefully

A 404 here gets an error that names the actual fix:

```python
raise FabricError(
    f"workspace {workspace} not found. If it belongs to another tenant, set "
    "FABRIC_TENANT_ID to that tenant — or just pass the workspace name instead, "
    "which sidesteps tenant resolution entirely."
)
```

That string ends up in a chat window, in front of someone who has never heard of tenant resolution. "Workspace not found" would send them looking for a workspace that is right there.

`list_semantic_models()` exists for the same reason: after a failed connection, showing what *does* exist in the workspace beats telling the user their name was wrong.

## What is deliberately missing

No publishing, no deployment pipelines, no item creation. They belong to a different talk, and two Fabric REST notes are worth knowing if you go there:

- **Creation is asynchronous**: a `202` with no body means "queued", not "created".
- **Fabric allows duplicate display names.** Create the same model twice and you get two items with one name — after which connecting *by name* fails with "there are several…". Check existence before creating, and verify by listing afterwards, never by HTTP status alone.
