# Context-efficiency benchmark

This benchmark measures deterministic preprocessing used by the AIPC Context Accelerator. It does **not** call Codex or any model API.

Run:

```bash
python benchmarks/run_context_efficiency.py --repeats 15
```

It generates temporary synthetic repositories and reports:

- repository file count;
- initial file selection count;
- path-character reduction versus a naive all-path catalog;
- local `repo_context.py` full-map runtime versus `context_accelerator.py` runtime;
- noisy test-log character/line compaction while preserving failure markers and raw-log SHA-256;
- evidence-cache exact-hit, critical-gate bypass, and changed-input invalidation behavior.

## Interpretation

Path and log characters are deterministic **context-size proxies**. They are useful for regression testing the accelerator itself, but they are not model token counts. Local Python timings measure reconnaissance/preprocessing only, not Codex generation latency.

For real token claims, collect client/API usage telemetry from the same representative task before and after the change, including input, cached input, reasoning, and output tokens when those fields are available.
