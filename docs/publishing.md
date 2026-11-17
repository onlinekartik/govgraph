# Publishing to PyPI

govgraph uses PyPI Trusted Publishing. GitHub Actions receives a short-lived
credential through OpenID Connect; no PyPI API token is stored in the repository.

## First release

1. Sign in to [PyPI](https://pypi.org/) and open **Account settings → Publishing**.
2. Add a pending publisher with these values:
   - PyPI project name: `govgraph`
   - Owner: `onlinekartik`
   - Repository: `govgraph`
   - Workflow filename: `publish.yml`
   - Environment: `pypi`
3. Before releasing, set the release version in `pyproject.toml` and
   `src/govgraph/__init__.py`, replace the `Unreleased` changelog heading with the release
   version and date, and update the README installation instructions to use PyPI. Merge
   those changes to `main`. The version in the tag must match the version in `pyproject.toml`.
4. Create and push a version tag, for example:

   ```console
   git tag v0.1.0
   git push origin v0.1.0
   ```

The workflow runs the test suite on Python 3.10–3.13, builds and checks the wheel and
source distribution, smoke-tests the installed wheel, then publishes both distributions.
The first successful publish creates the PyPI project and converts the pending publisher
to a regular publisher.

PyPI does not reserve the `govgraph` name when a pending publisher is configured. The
name can be claimed by someone else before the first successful upload.

## Later releases

For each release, update the package version in `pyproject.toml` and
`src/govgraph/__init__.py`, move the changes from the `Unreleased` section of
`CHANGELOG.md` into a dated version section, merge to `main`, and push the matching
`v<version>` tag.
