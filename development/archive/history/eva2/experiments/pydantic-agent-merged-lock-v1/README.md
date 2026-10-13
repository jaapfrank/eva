# Historical Pydantic AI 2.36 merged-lock experiment

This directory is a replayable historical record of the 2.32.1-to-2.36.0
candidate. It is not a current release gate.

- `requirements-4d-baseline-2.32.lock` is the byte-identical 2.32 baseline
  (SHA-256 `ce92ff9385c0b708ea0596b4cbfdfe5473379a744d1eb3dc6c1080959d17334f`).
- `managed-b-wheel-coverage-2.32-amd64.json` is the byte-identical historical
  coverage authority
  (SHA-256 `0eceb7bce89a847680cccf8c0a12a82fc6a3cbe3701f1dc0d1ea09c413ac382c`).
- `requirements-4d-candidate.lock` is the historical 2.36 candidate
  (SHA-256 `204fefe5052a2f457b8a12aece51ca675520bb250bf97771a69a7e22e14c81c5`).

The candidate checker reads only these experiment-local frozen inputs. It does
not read the active `eva2/requirements-4d.lock` or active release metadata.

`release-readiness-requirements.json` preserves the missing gates as they
were recorded at that decision. Its checker classifies them as historical and
explicitly refuses to present the snapshot as current or as evidence for
Pydantic AI 2.37.

Run:

```bash
python3 check_candidate.py
python3 check_release_readiness.py
python3 -m pytest -q -s test_candidate.py test_release_readiness.py
```

Passing these checks proves only that the historical inputs and decision remain
internally replayable. It does not authorize a release or describe today's
product state.
