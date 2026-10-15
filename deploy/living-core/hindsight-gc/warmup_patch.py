"""Pin optional startup warmup AFTER parallel loaders and dtype normalization."""
import hashlib
from pathlib import Path
import sys

SOURCE_SHA256 = "12ec0ef2d30514813c778d05597f0b8f0e8af3fee18d5acfd9eb6e166dd9ab5a"
ANCHOR = "        # Run database migrations if enabled\n"
WARMUP = '''        # All model loaders have joined and torch's global dtype is restored.
        # Keep synthetic inference off the event loop and before readiness.
        if os.environ.get("EVA_HINDSIGHT_WARMUP_RERANKER") == "1":
            from .cross_encoder import LocalSTCrossEncoder

            cross_encoder = self._cross_encoder_reranker.cross_encoder
            if isinstance(cross_encoder, LocalSTCrossEncoder):
                await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        cross_encoder._predict_sync,
                        [
                            ("Which object is described?", "A sample document describes a blue chair. " * (i + 1))
                            for i in range(8)
                        ],
                    ),
                    timeout=init_timeout,
                )

'''


def patch(source: bytes) -> bytes:
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        raise ValueError("Hindsight memory_engine source changed; review before updating")
    text = source.decode()
    if text.count(ANCHOR) != 1 or text.count("import logging\n") != 1:
        raise ValueError("Hindsight warmup insertion point changed")
    return text.replace("import logging\n", "import logging\nimport os\n", 1).replace(
        ANCHOR, WARMUP + ANCHOR, 1
    ).encode()


if __name__ == "__main__":
    path = Path(sys.argv[1])
    path.write_bytes(patch(path.read_bytes()))
