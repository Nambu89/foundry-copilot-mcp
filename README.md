# foundry-copilot-mcp

Bring a **Microsoft Foundry** agent — and your **Power BI** semantic models — into **GitHub Copilot Chat**, over MCP.

Your agent already works in the Foundry playground. Your analysts already live in VS Code. This is the ~400 lines of glue in between, written to be read: three bridges, each one small enough to hold in your head.

```text
   VS Code / Copilot Chat  (the user is already here)
            │  MCP over stdio
            ▼
   ┌────────────────────────────────┐
   │      this server               │
   ├────────────────────────────────┤
   │ 1. Foundry bridge  ────────────┼──►  your agent in Microsoft Foundry
   │ 2. MCP client      ────────────┼──►  Microsoft's Power BI modeling MCP server
   │ 3. Fabric REST     ────────────┼──►  Fabric API (GUID → display name)
   └────────────────────────────────┘
```

Everything runs as **the signed-in user**, never a service principal. That is a design decision, not an omission — see [Security](#security).

## The three bridges

### 1. A Foundry agent as a Copilot tool

The whole bridge, minus error handling:

```python
project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential())
client  = project.get_openai_client(agent_name=AGENT_NAME)   # pre-bound to your agent
reply   = client.responses.create(input=prompt).output_text
```

No model name, no system prompt on the client. The instructions, tools and model deployment stay server-side in Foundry, so you change the agent in the portal and every user picks it up on their next message — no redeploy, no version drift across machines.

→ [docs/01-foundry-bridge.md](docs/01-foundry-bridge.md)

### 2. An MCP server that is also an MCP client

Plenty of people write MCP servers. Far fewer write a client — and that is where the interesting architecture lives. This server launches **Microsoft's official Power BI modeling MCP server** as a child process, speaks JSON-RPC to it over stdio, and re-exposes the result as one opinionated tool.

You get to add policy at the boundary: the child is launched `--read-only` by default, so no chain of LLM calls can quietly mutate a production model.

`StdioMcpClient` is generic — point it at any MCP server, not just this one.

→ [docs/02-mcp-calls-mcp.md](docs/02-mcp-calls-mcp.md)

### 3. Fabric GUIDs → names

The modeling server connects **by name**. What the user has in front of them is a Fabric URL full of GUIDs. So we resolve them against the Fabric REST API instead of asking people to go hunting for display names.

→ [docs/03-fabric-rest.md](docs/03-fabric-rest.md)

## Tools exposed to Copilot

| Tool | What it does | Writes? |
|---|---|---|
| `ask_agent_tool` | Puts a question to your Foundry agent | no |
| `inspect_model` | Connects to a semantic model and summarises it | no |
| `show_measure` | Shows the current DAX of one measure | no |
| `update_measure_dax` | Rewrites a measure | **yes** — needs `confirm=True` |
| `resolve_fabric_ids` | Turns the GUIDs in a Fabric URL into names | no |

## Getting started

Requirements: Python 3.11+, VS Code with GitHub Copilot, `az login` done. Node.js only if you want the Power BI tools (it runs the official server via `npx`).

```bash
git clone https://github.com/Nambu89/foundry-copilot-mcp
cd foundry-copilot-mcp
python -m venv .venv
.venv/Scripts/activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

cp .vscode/mcp.json.sample .vscode/mcp.json   # then fill in your endpoint and agent name
```

Check the Foundry connection before you trust it — and definitely before you demo it:

```bash
python -m foundry_mcp.server --selftest "Hello, who are you?"
```

Then open VS Code, switch Copilot Chat to **Agent mode**, and the tools show up. Ask it something like *"ask the agent what it can do"* or *"inspect the model in samples/sample-model"*.

No Foundry project yet? The Power BI and TMDL parts work standalone, and `samples/sample-model` is a small semantic model you can point them at.

## Security

Four decisions worth copying into whatever you build:

**The user's identity, not a service principal.** `DefaultAzureCredential` and the modeling server's interactive sign-in mean every read runs as the person asking. Row-level security therefore applies. Wire a service principal in and RLS is silently bypassed — users see rows they should not, with no error to warn you. That is the quietest data leak on this list.

**Read-only by default.** The one writing tool (`update_measure_dax`) needs an explicit `confirm=True`, and shows the current and proposed DAX first so a human can compare them.

**Validation stays with the engine.** Measure updates go through the official server's `measure_operations/Update`, so the tabular engine rejects invalid DAX. Hand-patching TMDL text would happily write a broken model.

**No secrets in the repo.** Configuration is environment variables in `.vscode/mcp.json`, which is gitignored. Only `.vscode/mcp.json.sample` is tracked.

## Tests

```bash
pytest
```

21 tests, no network, no Azure, no Node.js — including fake MCP servers that exercise the real JSON-RPC framing, the notification handling and the timeout.

## Talk

This repo backs a talk on connecting Microsoft Foundry agents to Copilot Chat over MCP. Slides and video will be linked here after the conference.

## Licence

MIT — see [LICENSE](LICENSE).
