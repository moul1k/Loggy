# Run JSON format (version 1)

Use **Get example JSON** in the app or copy `loggy/samples/01-baseline.json`. A minimal run:

```json
{
  "schema_version": 1,
  "date": "2026-09-20",
  "title": "Prompt v2 benchmark",
  "attempt": "Recover missing action items with a checklist.",
  "setup": "Same 20 synthetic meetings as the baseline.",
  "result": "Recall improved but latency increased.",
  "next": "Shorten the checklist and rerun.",
  "metadata": {
    "provider": "my-provider",
    "model": "my-model",
    "prompt_version": "v2",
    "dataset": "meetings-20-v1",
    "git_sha": "abc123"
  },
  "metrics": {
    "recall": {"value": 0.86, "direction": "higher"},
    "latency_ms": {"value": 1250, "direction": "lower"}
  }
}
```

Required: `schema_version` (1), `date` (YYYY-MM-DD), `title`, `attempt`, `setup`, and
`metrics`. `result` and `next` get review prompts when absent. `metadata` is optional;
missing keys are recorded as `unknown`. Text fields are capped at 20,000 characters.

Provide 1–200 named metrics. Values must be finite JSON numbers, never booleans or
numeric strings. `direction` is `higher`, `lower`, or `unknown` (the default). Include
units in the metric name, and keep names and units consistent across runs. Changing
units without changing the metric name makes the delta misleading.

Every imported file becomes a draft with an unverified outcome. Filename and SHA-256
are retained as provenance. Raw input is not stored; retain your originals if you need
to audit them. Unknown top-level fields are ignored, not interpreted as instructions.

## Meeting-eval adapter

Files containing `run`, `aggregate`, and `cases` are recognized automatically. The
adapter records `created_at`, provider/model/prompt/commit/dataset metadata, aggregate
metrics, and a count of cases with errors or scores below 1. It does not copy transcripts,
model output objects, or error details. Known meeting-eval precision, recall, accuracy,
and `no_hallucinations` metrics are higher-is-better; other names remain unknown.

## Comparison semantics

Deltas are candidate minus baseline in the metric's original units. No percentage-point
or relative-percent conversion is implied. Direction determines the displayed signal.
Missing values stay missing; conflicting directions are unclassified. Dataset differences
and fixture runs display caveats. Comparisons are descriptive, not statistical tests.
