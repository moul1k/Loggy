# Security and privacy

Loggy is a single-user local application. It listens on `127.0.0.1`, validates Host
and Origin headers, and accepts JSON writes only. Do not expose it through a public
reverse proxy or change the bind address without adding authentication and a deployment
security review. Other processes running as your local user can access the app.

There is no analytics, telemetry, cloud account, external model call, or remote asset
loading. SQLite data remains on your device. Folder watching reads only top-level
JSON files in the configured directory (or its `artifacts/runs` child). The meeting-eval
adapter keeps aggregate measurements and metadata, not transcripts or case outputs.
Generic run descriptions may include whatever text their author supplies.

Reports contain published text and source filenames/checksums. Backups include project
folder paths, drafts, trashed entries, and prior revisions. Treat backups as private.
Deleting an entry moves it to recoverable trash; it does not erase the historical data.
Restoring a backup never resumes its original folder paths automatically.

Report vulnerabilities through the repository's private vulnerability reporting feature
when enabled. Do not put sensitive data or working exploits in a public issue. If private
reporting is unavailable, ask the maintainer for a private reporting channel first.
