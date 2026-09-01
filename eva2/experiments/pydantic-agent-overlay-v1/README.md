# Minimal Pydantic agent overlay

This experiment defines the smallest dependency candidate currently needed to
evaluate Pydantic AI 2.37 in Eva's existing 38-distribution runtime.

- `pydantic-ai-slim` moves from 2.36.0 to 2.37.0.
- `pydantic-graph` moves from 2.36.0 to 2.37.0.
- `genai-prices` moves from 0.1.4 to 0.1.5 because Pydantic AI 2.37 raises
  that minimum.
- No distribution is added or removed.
- The MCP extra and its transport stack are deliberately absent.
- OpenAI 3.6.0 and Eva's existing `httpx2`/`httpcore2` transport stay fixed.

`requirements-overlay.lock` is not a product lock. It may only be applied with
`--no-deps` to a clean environment built from `requirements-4d.lock`. The
canonical authority binds the base lock, overlay lock and exact three wheel
bytes. `check_overlay.py --wheelhouse <sealed-wheelhouse>` requires and checks
those real bytes, their wheel metadata, every authority field, dependency
extras, indexes, URLs, changed transport packages, extra wheelhouse files and
the earlier 69-package candidate. A clean offline installation and `pip check`
remain separate release checks; this directory alone is not a product
promotion.

Run the metadata and mutation checks with:

```bash
python3 check_overlay.py --wheelhouse /absolute/path/to/sealed-wheelhouse
python3 -m pytest -q -s test_overlay.py
```

Passing this experiment does not authorize a product-lock change. Promotion
still requires the product adapter, semantic tool/crash/streaming regressions,
a scan-clean image and the normal A/B route.
