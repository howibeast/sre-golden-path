# SLO as code

A service adds `services/<name>/slo.yaml` and runs:

```bash
python golden-path/slo/generate.py
```

Commit the file under `generated/`. The policy gate fails if it is stale (`--check`).

The spec carries the SLI (route, latency, what counts as good), the target, the window, and the dependency the team wants paged. Burn rates are not in the spec. Every service gets the same multi-window set, scaled to its own budget, so a team cannot quietly invent a page that never fires.

Rules land in Prometheus from `golden-path/slo/generated`. `promtool test rules` on `promtool/burn.test.yml` checks that a 2% failure rate pages and that a clean period does not.
