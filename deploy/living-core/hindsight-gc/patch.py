"""Apply the reviewed GC change only to the exact installed upstream source."""
import hashlib
from pathlib import Path
import sys

SOURCE_SHA256 = "88d99f8fabe4926a556ad6a6e7980ac89939ac8c3df81f1fc5a448ce7cc03365"


def patch(source: bytes) -> bytes:
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        raise ValueError("Hindsight local_device source changed; review before updating")
    text = source.decode().replace("import sys\n", "import sys\nimport os\nimport threading\nimport time\n")
    text = text.replace("_heap_trim = _resolve_heap_trim()", '''_heap_trim = _resolve_heap_trim()

# Explicit opt-in; automatic GC, heap trim and GPU cache release are unchanged.
# Serialize our explicit collections across embedding/reranking worker threads.
# This is a cadence bound, not a bound on retained cyclic object volume.
_coalesce_gc = os.environ.get("EVA_HINDSIGHT_COALESCE_GC") == "1"
_gc_lock = threading.Lock()
_last_full_gc = -float("inf")


def _collect_inference_garbage() -> None:
    global _last_full_gc
    if not _coalesce_gc:
        gc.collect()
        return
    with _gc_lock:
        if time.monotonic() - _last_full_gc >= 1.0:
            gc.collect()
            _last_full_gc = time.monotonic()
        else:
            gc.collect(0)''')
    text = text.replace("    gc.collect()\n    _heap_trim()", "    _collect_inference_garbage()\n    _heap_trim()")
    return text.encode()


if __name__ == "__main__":
    path = Path(sys.argv[1])
    path.write_bytes(patch(path.read_bytes()))
