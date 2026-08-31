# Minimal Pydantic agent overlay

This experiment proves the smallest dependency change needed to evaluate
Pydantic AI 2.36 in Eva's existing 38-distribution runtime.

- `pydantic-ai-slim` moves from 2.32.1 to 2.36.0.
- `pydantic-graph` moves from 2.32.1 to 2.36.0.
- No distribution is added or removed.
- The MCP extra and its transport stack are deliberately absent.
- OpenAI 3.6.0 and Eva's existing `httpx2`/`httpcore2` transport stay fixed.

`requirements-overlay.lock` is not a product lock. It may only be applied with
`--no-deps` to a clean environment built from `requirements-4d.lock`. The
canonical authority binds the base lock, overlay lock and exact two wheel
bytes. `check_overlay.py` rejects dependency extras, indexes, URLs, changed
transport packages, extra wheelhouse files and the earlier 69-package
candidate.

Run the metadata and mutation checks with:

```bash
python3 check_overlay.py
python3 -m pytest -q -s test_overlay.py
```

Passing this experiment does not authorize a product-lock change. Promotion
still requires the product adapter, semantic tool/crash/streaming regressions,
a scan-clean image and the normal A/B route.
