# PR review convergence

Use this after review threads have been classified with the fix / decline / escalate rubric.

## State contract

Represent each thread with:

- `id` — stable thread identifier;
- `decision` — `fix`, `decline`, or `escalate`;
- `status` — `open` or `resolved`;
- `evidence` — why the decision was made;
- `reply_sha` — pushed commit SHA for a resolved fix when available;
- `owner` — human owner for an escalation.

## Gate

Run:

```bash
python scripts/review_convergence.py --threads-json review-state.json --format markdown
```

The loop is ready to re-request review only when:

- no `fix` thread remains open;
- no `decline` thread remains open awaiting a reply/resolve action;
- every open escalation has an explicit human owner.

A passing convergence gate means the review workflow has no unowned agent-actionable thread. It does **not** mean the PR is safe to merge. Re-run tests, CI, risk review, and required human approvals.
