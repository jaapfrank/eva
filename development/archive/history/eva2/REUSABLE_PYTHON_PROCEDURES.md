# Reusable Python procedures

Eva can create and save a Python file with the existing verified document tool, explicitly designate it as reusable, find it later, and execute it on new input with `run_reusable_procedure`.

The action accepts the stored `document_id` and `input_json`. Source content is fetched through the existing procedure reference and current readback, not supplied as arbitrary code in the execution action. The source must be a `.py` artifact, at most 16 KiB. The program reads one JSON value from stdin (for example `json.load(sys.stdin)`) and prints its result to stdout. Input is at most 32 KiB. Python standard-library use is supported; installing dependencies and network access are outside this route.

Execution reuses the installed component-evaluation HTTP host with an optional `/run-procedure` endpoint. It is disabled unless operator configuration enables it. Core/worker clients pin the local image and use the existing private bearer credential. The model cannot select an image, command, host path, mounts or credentials.

Each execution uses a fresh nonroot, read-only container with no network and no Docker socket or host data mount. Only the verified script is mounted read-only. Temporary scratch storage disappears afterwards. CPU, memory, processes, output and a 30-second deadline are bounded. The installed output bound is inherited from the evaluator configuration. The host force-removes the container on completion or failure.

The existing procedure-action journal records the dispatch boundary and returned report. Completed calls return their saved report; uncertain started calls are not replayed automatically. Task cancellation still stops further task dispatch and output release. An already dispatched local procedure may run until its bounded deadline; immediate remote cancellation is not implemented by this HTTP endpoint.

Reports bind image, script and canonical JSON input hashes and include exit code, elapsed time, stdout/stderr and truncation/deadline flags. Successful process exit is not semantic verification. Eva must inspect output and save a desired lasting result using the existing document tool. No generated program is installed into Eva's production source.

The local JSON echo startup check proves the installed transport and isolated interpreter start. An unseen task learned by Eva and reused on a second task remains part of the deferred AU-004 trial.
