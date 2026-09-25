# Contributing to Loggy

Use Python 3.10 or newer. Clone the repo, create a branch, and run:

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
python -m loggy --db .loggy/dev.db --open
```

Use a development database and the built-in synthetic demo, never private transcripts
in tests or screenshots. Open a pull request explaining the problem, resulting behavior,
and validation. Keep runtime dependencies minimal and preserve local-first operation.

Data migrations must retain user data and have a migration test. Importers must reject
malformed measurements, retain provenance, avoid copying raw transcripts, and produce
drafts. Comparison logic must not infer metric direction from an arbitrary name.

Before a release: run the test matrix, inspect the browser at desktop and narrow widths,
verify an installed wheel includes browser assets and samples, update the changelog,
and tag the reviewed main commit. The tag workflow tests again before publishing
the source distribution and wheel to GitHub Releases. PyPI publishing is not configured.
