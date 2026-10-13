# Agent Browser sidecar — isolated evaluation

This is a disposable evaluation of Vercel's `agent-browser` 0.35.1, not an
Eva deployment.  It deliberately runs only as an unprivileged Linux user.  A
future Eva adapter must keep its own durable grant, allowlist, ledger and
readback rules; it must not expose `eval`, plugins, saved browser state or the
full `--tools all` MCP profile to an Eva model.

The image is built from a pinned Node base.  The functional probe must run on
an internal, gatewayless Docker network against a local fixture and must use
the provider's domain allowlist.  A fresh vulnerability scan and a controlled
MCP-protocol test are still required before a selection decision.
