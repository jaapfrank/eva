"""Opt-in exact-batch score memoization against reviewed upstream bytes."""
import hashlib
from pathlib import Path
import sys

SOURCE_SHA256 = '3f12569a3d24fc2f1a1b4d4af1367b42c5ad8c349b1b3dd4c90f0ca82cbe4adf'


def patch(source: bytes) -> bytes:
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        raise ValueError('Hindsight cross_encoder source changed; review before updating')
    text = source.decode()
    helper = Path(__file__).with_name('rerank_score_cache.py').read_text()
    text = text.replace('class LocalSTCrossEncoder(CrossEncoderModel):',
                        helper + '\n\nclass LocalSTCrossEncoder(CrossEncoderModel):', 1)
    text = text.replace('        self._model = None\n',
        '        self._model = None\n'
        '        self._exact_score_cache = (ExactRerankScores()\n'
        '            if os.environ.get("EVA_HINDSIGHT_EXACT_RERANK_CACHE") == "1" else None)\n', 1)
    original = '    def _predict_sync(self, pairs: list[tuple[str, str]]) -> list[float]:\n'
    assert text.index(original) > text.index('class LocalSTCrossEncoder(CrossEncoderModel):')
    text = text.replace(original, '''    def _predict_sync(self, pairs: list[tuple[str, str]]) -> list[float]:
        if self._exact_score_cache is None:
            return self._predict_uncached_sync(pairs)
        import torch
        return self._exact_score_cache.predict(pairs, self._predict_uncached_sync,
            context=(self._model, self.model_name, self._device_type, self.fp16,
                     self.bucket_batching, self.batch_size, torch.get_num_threads(),
                     torch.get_num_interop_threads()))

    def _predict_uncached_sync(self, pairs: list[tuple[str, str]]) -> list[float]:
''', 1)
    compile(text, 'cross_encoder.py', 'exec')
    return text.encode()


if __name__ == '__main__':
    target = Path(sys.argv[1])
    target.write_bytes(patch(target.read_bytes()))
