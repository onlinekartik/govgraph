# Security notes

govgraph is meant to sit between an AI agent and governance metadata. This page says what
the library guarantees, how each guarantee is enforced, and what it leaves to you.

To report a vulnerability, see [SECURITY.md](../SECURITY.md).

## What the agent can reach

An agent calls five tools. A tool is given a `GraphReader`, which has three methods:
get nodes by ID, get the edges of one type at a set of nodes, and find nodes of one type by
a text property. There is no method that writes, and no method that takes a query.

The agent supplies only node IDs, a depth and a search text. Each is checked against the
tool's JSON schema before anything else happens: IDs must match the ID pattern, depth must
be an integer within the tool's cap, the text must be 1 to 200 characters, and unknown
arguments are rejected. A rejected call never reaches the store.

## Guarantees and how they are enforced

| Guarantee | Enforced by |
| --- | --- |
| Tools cannot modify the graph. | The reader interface has no write method. On PostgreSQL every read runs in a `READ ONLY` transaction, and by default the reader refuses to work as a role that could write. |
| Only known queries run. | All SQL is built in [`store/queries.py`](../src/govgraph/store/queries.py). The reader refuses any statement whose name is not on the read allow-list. |
| Agent input never becomes SQL text. | Every value is a bound parameter. Table and column names come only from the validated ontology and pass through one quoting function that accepts lower-case letters, digits and underscores. Wildcards in search text are escaped. |
| A call cannot run away. | Depth caps are fixed per tool (3 for `impact_of`, 5 for `upstream_causes`). Each call has a row budget and a deadline. Each query is given only the time that is left, as a PostgreSQL `statement_timeout`, and that time also covers waiting for a connection another caller is using. Ceilings of 1000 rows and 30 seconds cannot be raised by configuration. |
| Every claim can be checked. | A claim cannot be created without citations, and a result cannot be built if a citation is not a node in that result. |
| A truncated answer is incomplete, never wrong. | A read that would exceed the budget keeps what fits and stops the tool there, so no conclusion is drawn from partial data. "Nothing found" statements are only made by calls that ran to completion. |
| Tools never return SQL or store internals. | Results contain nodes, edges and sentences. A store failure reaches the agent as `store_error: the graph store could not answer`; the reason is logged for the operator under `govgraph.tools`. |
| Every call produces an audit event. | Each call, whether it succeeds, is refused or fails unexpectedly, produces an event with the tool, arguments, outcome, row count and duration. It goes to the function you pass as `Toolkit(audit=...)`, or by default to the `govgraph.audit` logger at INFO level. |

Each row is covered by tests in [`tests/`](../tests), including tests that run against a
real PostgreSQL database.

Two limits of these guarantees are worth knowing:

- **Keeping the audit trail is up to the application.** As a library, govgraph emits audit
  events but does not configure logging, and Python discards INFO records unless a handler
  is set up. The `govgraph tool` and `govgraph mcp` commands do set one up: they write one
  JSON line per call to standard error, or to the file given with `--audit-log`.
- **The deadline is approximate at the edges.** If a call has to open a new database
  connection, connecting may take up to two seconds even when less time is left, because
  the database client does not accept a shorter connection timeout. Call `reader.connect()`
  at start-up, as the command line does, so that tool calls reuse an open connection.

## The read-only role

Use two database roles: one that owns the schema and loads metadata, and one that can only
read, for the tools and for PuppyGraph.

```sql
CREATE ROLE agent_reader LOGIN PASSWORD '<choose one and keep it in your secret store>';
```

```console
govgraph grant-reader agent_reader
```

`grant-reader` runs exactly these statements for the schema in use:

```sql
GRANT USAGE ON SCHEMA "govgraph" TO "agent_reader";
GRANT SELECT ON ALL TABLES IN SCHEMA "govgraph" TO "agent_reader";
ALTER DEFAULT PRIVILEGES IN SCHEMA "govgraph" GRANT SELECT ON TABLES TO "agent_reader";
```

When a reader connects it asks PostgreSQL whether its role is a superuser, may create
objects in the schema, or holds `INSERT`, `UPDATE`, `DELETE` or `TRUNCATE` on any table in
it, including on individual columns. If so it raises `UnsafeRoleError`. You can pass `allow_write_capable_role=True` to
accept such a role, for example in a throwaway test database; the read-only transaction and
the allow-list still apply.

The check looks at the graph schema only. A role with rights elsewhere in the database
passes it, so give the reader role nothing else.

## Secrets

- The library stores no credentials and reads none from files.
- The command line takes connection strings from `GOVGRAPH_DSN` and `GOVGRAPH_READER_DSN`,
  and the JDBC password from `GOVGRAPH_JDBC_PASSWORD`, never from arguments, so they do not
  appear in shell history or process listings.
- govgraph's own error messages and log lines do not include connection strings.
- A PuppyGraph schema generated with a JDBC connection contains that connection's password,
  because PuppyGraph needs it. Treat that file as a secret, and use the read-only role in it.
- The example generates its passwords at first start into a Docker volume. None is in the
  repository.

## Network and imports

Importing `govgraph` makes no network call and opens no connection. The database driver and
the MCP library are imported only when used. The bundled adapters read local files only.

## The viewer

The viewer is one HTML file with a content security policy of `default-src 'none'`: it
cannot load or send anything. Text from the export is written as text, never as markup, and
embedded data is escaped so it cannot close its script element. A graph can contain names
chosen by whoever wrote the metadata, which is why this matters.

## What is left to you

- **Who may call the tools.** govgraph does not authenticate callers. The MCP server speaks
  over standard input and output and trusts whoever started it.
- **What the metadata reveals.** The tools return whatever the reader role can read. There
  is no per-user or per-row filtering. If some metadata must not reach some agents, keep it
  out of that graph or use separate schemas and roles.
- **The content of the metadata.** Descriptions and names are returned to the agent as
  written. Text in a catalog can contain instructions aimed at a model. Treat tool output
  as data, and decide in your agent what it is allowed to act on.
- **Adapters.** An adapter is code you choose to run with the privileges of the loading
  process. Install adapters only from sources you trust.
- **Transport security** for PostgreSQL and PuppyGraph connections is configured in the
  connection string and in those systems, not here.
