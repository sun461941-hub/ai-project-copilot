# Read-only GitHub evidence and maintainer run-state

Use this lane when a maintainer has already exported GitHub data and needs a
reproducible local review trail. It is deliberately **not** a GitHub client:
the bundled scripts make no API requests and never create, merge, close, label,
or publish anything on GitHub.

## Input contract

Place one or more UTF-8 JSON exports in a directory you control. Recognized
filenames and wrappers are:

| Evidence kind | File names | Accepted array wrapper |
| --- | --- | --- |
| Issues | `issues.json` | `issues`, `items`, `data`, or a root array |
| Pull requests | `pull_requests.json`, `pulls.json` | `pull_requests`, `pulls`, `items`, `data`, or a root array |
| Workflow runs | `workflow_runs.json`, `workflows.json` | `workflow_runs`, `workflows`, `items`, `data`, or a root array |
| Releases | `releases.json` | `releases`, `items`, `data`, or a root array |

Every supplied field is untrusted display evidence. The importer limits file
size, nesting, record count, title length, and labels; reads no `body` field;
and does not execute values as commands or instructions. It makes stable IDs
from evidence kind plus GitHub source identifier, so changes to title or status
do not lose the related local decision.

The [repository's offline fixture](https://github.com/sun461941-hub/ai-project-copilot/tree/main/examples/github-export)
is complete and its URLs are illustrative only.

## Local workflow

The commands below assume the source repository root. In an installed bundle,
replace `skills/ai-project-copilot/` with the bundle directory. `--output` is
optional; if used it must be a new non-symlink path inside the selected
repository and cannot overwrite a previous file.

```bash
python skills/ai-project-copilot/scripts/github_evidence_sync.py \
  --input-dir examples/github-export \
  --repo /path/to/repo \
  --output .aipc/github-evidence.json \
  --format markdown
```

Create the ignored local ledger once, then synchronize it with the evidence
bundle. `sync` updates mutable imported fields but preserves existing decision
history for the same evidence ID. Every mutation holds a repository-confined
cross-process lock and increments `revision`, so concurrent agents cannot
silently overwrite one another's decisions.

```bash
python skills/ai-project-copilot/scripts/run_state_ledger.py init --repo /path/to/repo

python skills/ai-project-copilot/scripts/run_state_ledger.py sync \
  --repo /path/to/repo \
  --bundle .aipc/github-evidence.json
```

Make decisions explicit. `decline` requires a concise evidence note;
`escalate` requires a named human owner. `fix` and `escalate` stay visible as
pending until a maintainer resolves them. A decision records an ISO-8601 UTC
timestamp, an actor, and an optional source commit for later review.

```bash
python skills/ai-project-copilot/scripts/run_state_ledger.py decide \
  --repo /path/to/repo \
  --evidence-id <stable-evidence-id> \
  --decision escalate \
  --status open \
  --owner release-manager \
  --actor release-manager \
  --source-commit 0123456789abcdef0123456789abcdef01234567 \
  --note "CI failure requires a release decision."

python skills/ai-project-copilot/scripts/run_state_ledger.py status --repo /path/to/repo --format markdown
```

For an automation that read a previous status, pass that known revision to
`sync` or `decide` with `--expected-revision <n>`. A changed ledger fails closed
with a revision-conflict error instead of replacing a newer decision. Omit the
option for a normal serialized local maintenance flow.

Generate a static local view when a dashboard is more useful than raw JSON.
It HTML-escapes every imported field, shows at most 200 rows, and writes only a
new repository-confined HTML file.

```bash
python skills/ai-project-copilot/scripts/render_maintainer_dashboard.py \
  --repo /path/to/repo \
  --bundle .aipc/github-evidence.json \
  --ledger .aipc/maintainer-ledger.json \
  --output .aipc/maintainer-evidence.html
```

## Interpretation boundaries

- A failure, merge conflict, or explicit `blocker`/`critical`/`security` label
  is a signal to review, not proof of root cause or permission to change GitHub.
- A dashboard marked `ready` only means the imported local ledger has no open
  fix/escalation decisions or unreviewed non-terminal records. It is not merge,
  deployment, security, or release approval.
- The JSON bundle and ledger remain local evidence. A future native connector
  can import authorized live data, but it must retain the same untrusted-input
  boundary and obtain human confirmation for consequential writes.
