# services/embed/eval — embedding bake-off harness

Tracking doc and decision-rule authority: **[`docs/archive/embedding-eval-2026-04.md`](../../../docs/archive/embedding-eval-2026-04.md)** (archived 2026-04-25; the eval harness here is still used for drift checks on any future model change).

Layout:

```
eval/
├── README.md             ← this file
├── requirements.txt      ← eval-only deps (kept out of the production embed image)
├── queries/
│   └── queries.jsonl     ← 40–50 hand-labeled queries with ground-truth chunk UUIDs
├── sample/
│   └── chunk_ids.txt     ← 5 000 stratified chunk UUIDs (3 800 EN / 1 200 FR)
├── scripts/              ← harness Python (encode, retrieve, score)
├── results/              ← per-model per-variant JSON outputs
└── REPORT.md             ← go/no-go written up after Phase 2 completes
```

Everything here is disposable artifact + reproducible pipeline. The production embed service (one directory up) does not depend on anything in this tree.

See the tracking doc for phase status, decision rule, and constraints (local inference only, don't break idempotency, etc.).
