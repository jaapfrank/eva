# Independent review: typed programming input

Date: 2026-10-02
Base reviewed: `d383826a`
Decision: GO for the bounded typed-input change.

The new `write_programming_file` and `start_programming_process` descriptors
accept literal content and command strings through the existing conversation
tool mechanism. Their object schemas are closed and all properties are required,
which fits the existing strict Pydantic tool transformation. Upstream targets and
parameters match the previously inspected pinned DesktopCommander 0.2.52 schemas.
The change needs no free-form object schema, dynamic catalog loader, or second
execution route.

The adapter maps each typed name to its fixed upstream MCP tool, forwards a native
arguments dictionary through the existing client, and retains the original typed
call in durable receipts. The workspace/session binding, indexed request identity,
legacy discovery and generic calls, prior input recovery, transport uncertainty,
and final cleanup remain unchanged. The new capabilities are admitted explicitly;
existing composition binds their descriptors and changed execution sources into
the core/worker profile.

The runner's programming evidence branch includes both aliases and uses the same
trusted mapping. A typed process launch therefore remains insufficient completion
evidence, and file writes still require an explicit successful MCP result.
Trusted idle checking and stop cleanup remain in the existing path. A successful
file write or idle process registry does not establish code quality, independent
evaluation, or installation.

Validation: independently inspected the three runtime diffs and three associated
test diffs against the stated base. The coordinator reports 135 focused checks
passing; the reviewer did not rerun tests. Added checks cover literal multiline
content with quotes, backslashes and Unicode, exact original receipt replay,
transport uncertainty and cleanup, typed/legacy launch exclusion, upstream write
errors, and capability/profile admission. No blocking defect was found.

The controlled checks do not capture a real provider-generated typed call or prove
installed usability. The planned genuine code-writing and execution smoke must
separately verify discovery, literal content, readback and terminal results on the
installed route. This review does not reopen the closed AIDE follow-up or claim a
new evaluated/self-applied improvement.

## Reviewed file hashes

- `src/eva/adapters/postgres/programming_actions.py`: `59d2638ab3a9f5406f0a93e0cc536a32cb48ee42dd01a3b9a12f82571a71fa61`
- `src/eva/contracts/delegated_digital_work.py`: `ada789a746420e33234d66f9f4b8f03ea270757bbff5da5a6d63bd65e218db51`
- `src/eva/bootstrap/delegated_digital_runner.py`: `1c13d982762c7f39d2256056f7223f19ee4745b82117069309f90e7c2c0cc2d5`
- `tests/operations/test_programming_service.py`: `84b8a03116bd1a54b091aa95b3b7a75a7d27c951862a93e667fdb6ce0d399186`
- `tests/application/test_delegated_digital_runner.py`: `8ed0c0db5601a6a3c6b2cb0caa8782b7fba57f74d63cfc959376c69b953a700d`
- `tests/bootstrap/test_delegated_digital_composition.py`: `3df00bee64e0442706b74d86027d5960044c0f91f0548e558e0672f3588d7f45`
