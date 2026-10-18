"""Opt-in CPU batch scheduling on the exact deployed cached upstream module."""
import hashlib
from pathlib import Path
import sys

SOURCE_SHA256 = 'd10cc5fc3e644c20197b936ede04ea9101146fffbbaeff76581db86a406ac026'


def patch(source: bytes) -> bytes:
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        raise ValueError('Hindsight cross_encoder source changed; review before updating')
    text = source.decode()
    helper = Path(__file__).with_name('parallel_rerank_batches.py').read_text()
    marker = 'class LocalSTCrossEncoder(CrossEncoderModel):'
    assert text.count(marker) == 1
    text = text.replace(marker, helper + '\n\n' + marker, 1)
    init = '        self._model = None\n'
    assert text.count(init) == 1
    text = text.replace(init, init +
        '        self._parallel_rerank_batches = (os.environ.get(\n'
        '            "EVA_HINDSIGHT_PARALLEL_RERANK_BATCHES") == "1")\n', 1)
    context = '                     torch.get_num_interop_threads()))'
    assert text.count(context) == 1
    text = text.replace(context,
        '                     torch.get_num_interop_threads(), self._parallel_rerank_batches))', 1)
    call = '                sorted_scores = self._model.predict(sorted_pairs, batch_size=self.batch_size, show_progress_bar=False)'
    assert text.count(call) == 1
    text = text.replace(call, '''                if (self._parallel_rerank_batches and self._device_type == "cpu"
                        and isinstance(self.batch_size, int) and self.batch_size > 0
                        and len(sorted_pairs) > self.batch_size):
                    sorted_scores = predict_parallel_rerank_batches(
                        self._model, sorted_pairs, self.batch_size)
                else:
                    sorted_scores = self._model.predict(sorted_pairs, batch_size=self.batch_size, show_progress_bar=False)''', 1)
    compile(text, 'cross_encoder.py', 'exec')
    return text.encode()


if __name__ == '__main__':
    target = Path(sys.argv[1])
    target.write_bytes(patch(target.read_bytes()))
