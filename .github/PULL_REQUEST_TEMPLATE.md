## What this changes

<!-- What the change does and why. Link the issue it closes, if there is one. -->

## How it was checked

<!-- The tests you added or changed, and anything you verified by hand. -->

## Checklist

- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] `mypy` passes
- [ ] `pytest` passes, with the PostgreSQL tests if the store or the SQL changed
- [ ] New behaviour has tests, and they use only synthetic data generated in code
- [ ] Documentation is updated, and `docs/ontology.md` is regenerated if the core ontology changed
- [ ] `CHANGELOG.md` has a line for anything a user would notice
- [ ] No SQL is built outside `store/ddl.py` and `store/queries.py`
- [ ] Tools still only read, stay within their limits and cite a node for every claim
- [ ] No secrets, real data, or company, product, cloud-vendor or country-specific names were added
