# Historical references

This directory is not a second Eva application or an additional work queue.

- `history/`: historical sources still used by current source bindings, installer validation, dashboard links and tests.
- `eva1/benchmark/agentbench-fc-v1/`: inputs read by existing benchmark tests; no Eva1 application.
- The two migration manifests record earlier snapshots. Their `originalCopy` paths may now exist only in Git history.
- `pruning-20261013.json`: exact removed paths, hashes and recovery revision.

Retrieve a removed file with `git show eab83747a3c33a83f2f63f38180dc7d6330635ec:<path>`.
Do not rewrite old scores, hashes or failure records to describe later cleanup.
