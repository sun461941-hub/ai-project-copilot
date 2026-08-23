# Multi-interface gateway

AI Project Copilot keeps the Agent Skill as a first-class interface and adds three adapters over the same deterministic helpers:

```text
                    AI Project Copilot
                           |
                     Core Engine
                           |
          +----------------+----------------+
          |                |                |
        Skill             MCP              REST
          |                |                |
   ChatGPT/Codex     Claude/Cursor     apps / agents
                           |
                          CLI
```

The adapters do **not** create a second implementation of repository analysis, review, security, release, or eval logic. `scripts/project_copilot_core.py` builds fixed `argv` calls to the existing helpers, executes them without a shell, bounds captured output, and normalizes the result.

## Trust boundary

Treat every adapter as a capability boundary.

- Do not expose arbitrary shell or executable parameters.
- Keep merge, publish, deploy, delete, permission changes, and repository writes outside the gateway unless a separate explicitly reviewed write protocol is added later.
- REST binds to `127.0.0.1` and rejects every plaintext non-loopback bind. For remote access, keep the process on loopback and place an authenticated TLS reverse proxy in front.
- REST rejects requests carrying a browser `Origin` header; it is intended for trusted programs/agents, not arbitrary web pages.
- MCP and REST restrict caller-supplied repository/file paths to configured `--allow-root` directories. If no root is supplied, the current working directory is the only allowed root.
- Generic helper subprocesses receive a small runtime environment allowlist and do not inherit common cloud/model/repository credentials.
- Helper stdout/stderr is continuously drained through bounded in-memory buffers; excess bytes are discarded, so a noisy tool cannot force unbounded RAM or temporary-disk capture.
- Timeouts terminate the process group/tree where the platform permits it.
- REST sockets have a per-connection timeout and the server bounds simultaneous request threads; this is still a preview adapter rather than a hardened multi-tenant edge service.

## Capability contract

The preview exposes these stable names:

| Capability | Purpose | Main helper(s) |
|---|---|---|
| `route` | Route a broad task into capability lanes | `workflow_router.py` |
| `analyze_repository` | Build task-focused repository context | `repo_context.py` |
| `review_changes` | Prioritize PR/diff risk | `change_risk.py` |
| `scan_security` | Supply-chain + MCP configuration checks | `supply_chain_guard.py`, `mcp_config_audit.py` |
| `release_readiness` | SemVer/release intelligence | `release_intel.py` |
| `maintainer_triage` | Reviewable issue pre-triage | `maintainer_triage.py` |
| `run_evals` | Bundled deterministic/structural Skill evals | `run_skill_evals.py` |
| `copilot_run` | Route one natural-language goal and execute available read-only deterministic stages | composition of the above |

`copilot_run` is intentionally conservative. It does not invent missing release versions, issue fixtures, or write permissions. Unsupported model-driven lanes are returned as planned/skipped stages rather than silently fabricated.

## 1. Keep using the Skill

No migration is required for existing Skill users. Continue invoking repository-level work naturally. The Skill may use the same underlying deterministic helpers directly or through the core adapter.

## 2. Local CLI

List capabilities:

```bash
python scripts/project_copilot.py capabilities
```

Analyze a repository:

```bash
python scripts/project_copilot.py analyze /path/to/repo \
  --task "review the authentication change"
```

Run security checks:

```bash
python scripts/project_copilot.py security /path/to/repo
```

Run a goal through the router and available deterministic stages:

```bash
python scripts/project_copilot.py run \
  "review this PR for security risk" \
  --repo /path/to/repo \
  --base main \
  --head HEAD
```

The CLI intentionally does not apply `--allow-root`; the local user already chose the process and path boundary.

When the bundled installer is used, it keeps a local rollback receipt under `.aipc/multi-interface-upgrade-2.2.0-preview.2/`. Pre-existing identical files/Skill text are not claimed by the installer, and files replaced with `--force` are backed up there so rollback can restore them.

## 3. REST API

Start loopback-only service:

```bash
python scripts/project_copilot_api.py \
  --allow-root /path/to/repo
```

Check health:

```bash
curl http://127.0.0.1:8787/health
```

Call one capability:

```bash
curl -sS http://127.0.0.1:8787/v1/run \
  -H 'Content-Type: application/json' \
  -d '{
    "capability": "analyze_repository",
    "arguments": {
      "repo": "/path/to/repo",
      "task": "review authentication"
    }
  }'
```

Call the goal-oriented orchestrator:

```bash
curl -sS http://127.0.0.1:8787/v1/run \
  -H 'Content-Type: application/json' \
  -d '{
    "goal": "review this PR for security risk",
    "repo": "/path/to/repo",
    "base": "main",
    "head": "HEAD"
  }'
```

For authenticated remote access, keep the adapter on loopback and put the token only in the environment:

```bash
export AIPC_API_KEY='replace-with-a-long-random-secret'
python scripts/project_copilot_api.py \
  --host 127.0.0.1 \
  --allow-root /srv/repos
```

Configure the TLS reverse proxy to forward only authenticated requests to this loopback listener, then send `Authorization: Bearer <token>`. The preview server is a small adapter, not a full multi-tenant SaaS gateway.

## 4. MCP stdio server

Start manually:

```bash
python scripts/project_copilot_mcp.py \
  --allow-root /path/to/repo
```

A generic client configuration looks like:

```json
{
  "mcpServers": {
    "ai-project-copilot": {
      "command": "python3",
      "args": [
        "/absolute/path/to/skills/ai-project-copilot/scripts/project_copilot_mcp.py",
        "--allow-root",
        "/absolute/path/to/repo"
      ]
    }
  }
}
```

The server supports the finalized MCP 2026-07-28 stateless request-metadata flow (`server/discover`, required per-request `_meta`, `resultType`, and response server identity in result `_meta`) plus legacy `initialize` handshakes for 2025-11-25 and 2025-06-18 clients. It exposes only the fixed Project Copilot tool registry and writes protocol frames only to stdout; diagnostics go to stderr.

## Response envelope

Direct capability calls return a stable top-level envelope:

```json
{
  "schema_version": "aipc.multi-interface.v1",
  "engine_version": "2.2.0-preview.2",
  "request_id": "...",
  "capability": "scan_security",
  "status": "completed",
  "consequential": false,
  "results": [],
  "data": []
}
```

Each helper result includes exit code, duration, bounded stdout/stderr, truncation flags, and parsed JSON when the helper emitted JSON.

## Deployment progression

Use this sequence instead of jumping directly to a hosted multi-tenant service:

1. Validate local CLI parity with existing helper outputs.
2. Connect one local MCP client and verify path scoping.
3. Run REST on loopback with a single repository root.
4. Put an authenticated TLS reverse proxy in front before remote use.
5. Add durable job/task storage only when long-running remote workloads require it.
6. Add any write-capable operation as a separate reviewed protocol with explicit human confirmation; never broaden the generic gateway into arbitrary command execution.
