# Output Evaluation Rubric

Score each dimension from 0 to 4. A release candidate should have no zeroes and should score at least 24/32.

| Dimension | 0 | 2 | 4 |
|---|---|---|---|
| Product wedge | vague AI idea | named user and use case | painful moment, promise, proof, and fallback are crisp |
| AI necessity | decorative AI | partly useful | AI uniquely enables the outcome and complexity is justified |
| Vertical slice | mock or disconnected pieces | partial end-to-end path | realistic input reaches a useful verified artifact |
| Experience | success-only UI | basic states | empty/loading/streaming/cancel/error/fallback states are intentional |
| Trust | unsupported claims | some tests or citations | claims, permissions, provenance, and limitations are visible |
| Evaluation | no regression checks | one happy-path test | versioned happy/failure/adversarial fixtures and explainable graders |
| Privacy and licensing | boundaries missing | generic policy text | data flow, retention, model source/license, and redistribution are explicit |
| GitHub presentation | install-first README | understandable docs | real visual, 60-second demo, quick start, architecture, evidence, roadmap |

## Automatic failure conditions

- fabricated benchmark, compatibility, user, screenshot, or test claim;
- committed secret or private trace;
- bundled third-party model weights without verified redistribution permission;
- destructive or public action without explicit approval;
- “local/offline” claim that depends on a hidden required server call;
- no working primary path;
- overwriting unrelated user files or existing release output without permission.
