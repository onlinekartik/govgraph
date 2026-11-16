# Contributing

Thank you for considering a contribution. This page covers how to set up, what a change
needs before it can be merged, and a few rules that are specific to this project.

By taking part you agree to follow the [code of conduct](CODE_OF_CONDUCT.md). Report
security problems privately, as described in [SECURITY.md](SECURITY.md), not in a public
issue.

## Set up

You need Python 3.10 or later.

```console
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Checks

Run all four before opening a pull request. Continuous integration runs the same commands
on Python 3.10 to 3.13.

```console
ruff check .
ruff format --check .
mypy
pytest
```

`pytest` also measures coverage and fails below 90%.

### PostgreSQL tests

Tests marked `postgres` need a database and are skipped without one. To run them, start a
disposable PostgreSQL and point `GOVGRAPH_TEST_DSN` at a role that may create schemas and
roles. Pick your own password; it only has to match in both commands.

```console
export PGPASSWORD="$(openssl rand -hex 16)"
docker run --rm -d --name govgraph-test-db -p 127.0.0.1:55432:5432 \
  -e POSTGRES_PASSWORD="$PGPASSWORD" postgres:16-alpine
export GOVGRAPH_TEST_DSN="postgresql://postgres@127.0.0.1:55432/postgres"
pytest
docker stop govgraph-test-db
```

Each test creates its own schema and read-only role and removes them afterwards. Never
point `GOVGRAPH_TEST_DSN` at a database you care about.

## Rules for changes

These keep the properties the project exists for. A change that breaks one will not be
merged, however useful it is otherwise.

1. **The ontology is the single source of truth.** Do not hard-code a node type, edge
   type, table or column where it could be derived from the ontology. Types the tools are
   written against are the exception, and are named in one place in
   `tools/toolkit.py`.
2. **All SQL lives in `store/ddl.py` and `store/queries.py`.** Identifiers go through
   `quote_ident`; values are bound parameters. A new read query must be added to
   `READ_QUERIES` deliberately, with a test.
3. **Tools only read, are bounded, and cite.** A tool takes a `ToolContext`, reads through
   it so that the budget applies, and makes claims through `ctx.result.claim`, which
   requires the nodes that support the claim. A statement that something is absent may only
   be made after the reads it depends on have completed.
4. **Adapters only yield records.** They are never given a store.
5. **No real data and no real names.** Tests and examples use synthetic data generated in
   code. Do not add company, product, cloud-vendor or country-specific names to code, tests,
   documentation or examples, other than the technologies the project integrates with.
6. **No secrets, and no network or file access at import time.**
7. **Everything is typed.** `mypy --strict` must pass without new `type: ignore` comments
   in `src/`.

## Tests

- Add a test for every behaviour you add or change. A bug fix starts with a test that
  fails without the fix.
- Behaviour that every store must share goes in `tests/test_store.py`, which runs against
  both the in-memory store and PostgreSQL.
- If you change the core ontology, regenerate the reference in `docs/ontology.md` with
  `govgraph ontology docs`. A test fails if it is stale.

## Commits and pull requests

- Keep commits small and give each a subject line that says what it does.
- Describe what changed and why in the pull request, and how you checked it.
- Add a line to the top section of [CHANGELOG.md](CHANGELOG.md) for anything a user would
  notice.

## Versions

The project follows [Semantic Versioning](https://semver.org/). While the version is below
1.0, a minor release may change the API. These count as breaking changes: removing or
renaming anything in `govgraph.__all__`, changing a tool's name, arguments or result
shape, changing how IDs are computed, and removing or changing a type or property in the
core ontology. The core ontology has its own version, in its file name and its `version`
field.

## Licence

Contributions are accepted under the [Apache License 2.0](LICENSE), the licence of the
project.
