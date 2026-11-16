# Security policy

## Reporting a vulnerability

Please do not report security problems in a public issue.

Report them privately through the repository's private vulnerability reporting form, under
the "Security" tab of the repository page. If that form is not available, open an issue
that asks a maintainer to contact you and includes no details of the problem.

Include, as far as you can:

- what the problem is and what an attacker could do with it;
- the version or commit you found it in;
- the steps, input or code needed to reproduce it.

You should get an acknowledgement within five working days. We will tell you whether we
can reproduce the problem and what we intend to do, keep you informed while we work on a
fix, and credit you in the release notes unless you prefer otherwise. Please give us a
reasonable time to release a fix before you publish details.

## Supported versions

Security fixes are made to the latest released version.

## What counts

The properties the project sets out to provide are listed in
[docs/security.md](docs/security.md). A way to break any of them is a vulnerability. For
example:

- making a tool write to, or delete from, the graph;
- getting text supplied to a tool executed as SQL;
- making a tool run past its depth, row or time limit;
- obtaining SQL, connection details or other store internals through a tool result;
- getting a tool to return a claim whose citations do not support it;
- script or markup injection through the viewer.

The same page lists what the library deliberately leaves to the deployer, such as
authenticating callers. Those are not vulnerabilities in govgraph, although suggestions
are welcome as ordinary issues.
