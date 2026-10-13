# Eva2

A digital housemate that knows people, takes initiative, completes work and learns
to create capabilities when needed. This repository is Eva2; commands run here.

| Directory | Contents |
| --- | --- |
| `src/` | Application, optional connectors and runtime helpers |
| `deploy/` | Installation, migrations, schemas and locked dependencies |
| `development/` | Documentation, tests, benchmarks, project dashboard and archives |

Start with the [project goal](development/docs/PROJECTDOEL.md),
[architecture](development/docs/ARCHITECTUUR.md) and
[current workboard](development/docs/WERKBORD.md).
The [repository reassessment](development/docs/project-reassessment-20261013.md)
records actual gaps and retain/archive decisions.

The Python application uses `src/eva` and `pyproject.toml`. The installer entrypoint
is `start-installer.py`. The retired Eva1 application and duplicate source backups
are preserved in Git history, rather than shipped as a second application. Only
the Eva1 benchmark inputs still read by existing tests remain locally. See the
[cleanup record](development/docs/repository-pruning-20261013.md). Existing WSL runtime data and pinned container images are
separate from this source tree. Historical results remain unchanged; no general
superiority over other agents has been established.
