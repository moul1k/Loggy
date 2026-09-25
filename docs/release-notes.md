Loggy turns experiment runs into an evidence-backed journal and shareable report.

This first public release includes project onboarding, a synthetic demo, JSON uploads,
folder watching, metric comparisons, experiment and learning entries, revision history,
recoverable trash, and restorable backups. Reports are available as HTML and Markdown,
with browser printing for PDF.

**Requires Python 3.10+.** Install the attached wheel with `python -m pip install
loggy_journal-0.1.0-py3-none-any.whl`, then run `loggy --open`.

Or install from this repository's tag:

```sh
python -m pip install "git+https://github.com/moul1k/Loggy.git@v0.1.0"
loggy --open
```

No account, API key, or runtime dependencies. Data stays local. This is a local beta,
not a hosted multi-user service. The deterministic agent imports evidence as drafts;
it does not generate LLM summaries or claim fixture metrics measure model quality.

Existing initial-version databases migrate automatically with a `.pre-v01.db` backup.
Keep old exported entry-only JSON snapshots for reference; only the new versioned
backup format supports restoration. See README.md, SECURITY.md, and CHANGELOG.md.
