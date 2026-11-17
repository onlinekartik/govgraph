# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-17

First public release.

### Added

- Core ontology 1.0.0, shipped as YAML and validated against a JSON schema: ten node types
  (`DataAsset`, `BusinessTerm`, `CriticalDataElement`, `Regulation`, `DataProduct`, `Owner`,
  `DQRule`, `DQResult`, `DQFailure`, `LineageEdge`) and eleven edge types (`DEFINES`,
  `CLASSIFIED_AS`, `GOVERNED_BY`, `OWNED_BY`, `VALIDATES`, `PROTECTS`, `DERIVES_FROM`,
  `FAILED_ON`, `IMPACTS`, `RESULT_OF`, `RAISED_FROM`).
- Ontology extensions in YAML, with dangling edge types and conflicting definitions
  rejected.
- Deterministic node and edge IDs derived from the natural key.
- PostgreSQL store with one table per node and edge type generated from the ontology,
  idempotent merging upserts, and a reader that is read-only, runs only allow-listed
  parameterized statements and refuses a role that can write.
- In-memory store backed by NetworkX, with the same behaviour.
- PuppyGraph schema generation from the ontology.
- Agent tools `get_asset_context`, `explain_failure`, `impact_of`, `upstream_causes` and
  `find_by_term`, with JSON-schema signatures, hard limits on depth, rows and runtime,
  citations on every claim, and an audit event per call.
- MCP server that serves the tools over standard input and output.
- Adapters for CSV, JSON, dbt artifacts, OpenMetadata exports and DataHub exports,
  discovered through the `govgraph.adapters` entry-point group.
- Bounded JSON export and a dependency-free SVG viewer.
- `govgraph` command-line interface.
- A synthetic online-store dataset, and an example that runs it on PostgreSQL and
  PuppyGraph with Docker Compose.
