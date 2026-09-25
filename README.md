# Loggy

A local project logging agent for `99p_transcription_agent`. Master repository:
https://github.com/moul1k/Loggy

Loggy maintains the two documents described in the capstone specification:
an **Experiments Log** (date, attempt, setup, result, next step) and a
**Learnings Field Guide** (date, general lesson, explanation, supporting experiments).

## Run

Python 3.10+; no third-party dependencies or API keys required.

```powershell
python -m loggy serve --project "../99p_transcription_agent 2" --watch
```

Open http://127.0.0.1:8765. Supply the path to your transcription repository if
it lives elsewhere. The agent checks `artifacts/runs/*.json` every 30 seconds
while running with `--watch`. The Sync button also imports runs on demand.
It never modifies the transcription repository.

## Workflow

1. Run your transcription benchmark, then sync Loggy.
2. Review the imported draft, explain what you tried and choose your next step.
3. Set the outcome and publish the entry when it accurately describes your work.
4. Add a learning with the supporting experiment IDs shown on the cards.
5. View the combined report or download either document as Markdown. Download
   the HTML report to view offline, or print it to PDF from your browser.

The importer is deterministic: it captures recorded metadata and aggregate
metrics, not an LLM interpretation. It does not invent conclusions. Fixture
results are explicitly labeled as harness checks. New runs are drafts with
an unverified outcome. Only published entries appear in document exports;
the JSON backup includes drafts too. Identical source files are deduplicated
by SHA-256, and each imported entry retains its source filename and checksum.
Raw transcripts and case outputs are not copied into Loggy.

## CLI and storage

```powershell
python -m loggy sync --project "../99p_transcription_agent 2"
python -m loggy export --output output
python -m unittest discover -s tests -v
```

Data persists in `.loggy/loggy.db` (SQLite), separate from source control.
Use `--db` to select another database and `--port` to change the local port.
Back up the database while the server is stopped to preserve source deduplication.
JSON downloads are portable readable snapshots; this version has no JSON restore UI.
The server listens only on the loopback interface and is intended for one local
user, not public deployment. No cloud sync, external model, or scheduled background
service is configured. Exports may contain any sensitive details you enter manually.
