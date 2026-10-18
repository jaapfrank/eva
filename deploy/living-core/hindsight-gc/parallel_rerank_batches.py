"""Schedule unchanged local CPU batches; never cache inputs or alter scores."""
from concurrent.futures import ThreadPoolExecutor, wait
import threading

_rerank_batch_pool = None
_rerank_batch_pool_lock = threading.Lock()


def _shared_rerank_batch_pool():
    global _rerank_batch_pool
    with _rerank_batch_pool_lock:
        if _rerank_batch_pool is None:
            _rerank_batch_pool = ThreadPoolExecutor(
                max_workers=2, thread_name_prefix='eva-rerank-batch')
        return _rerank_batch_pool


def predict_parallel_rerank_batches(model, sorted_pairs, batch_size):
    """Keep each original ordered batch and reassemble in original batch order.

    The existing outer recall executor remains bounded. All recalls share this
    separate two-worker compute pool, avoiding per-recall thread multiplication.
    """
    chunks = [sorted_pairs[i:i + batch_size]
              for i in range(0, len(sorted_pairs), batch_size)]

    def compute(chunk):
        scores = model.predict(chunk, batch_size=batch_size, show_progress_bar=False)
        scores = scores.tolist() if hasattr(scores, 'tolist') else list(scores)
        if len(scores) != len(chunk):
            raise ValueError('Local reranker returned incomplete batch scores')
        return scores

    pool = _shared_rerank_batch_pool()
    futures = {}
    try:
        # Inputs are already length sorted by the pinned upstream method. Start
        # its longest unchanged blocks first; only scheduling order changes.
        for index in reversed(range(len(chunks))):
            futures[index] = pool.submit(compute, chunks[index])
        return [score for index in range(len(chunks))
                for score in futures[index].result()]
    except BaseException:
        # Do not cancel another recall's queued work or shut down the shared
        # pool. Running native inference cannot be forcibly cancelled; drain
        # our own work before the caller's existing memory-cleanup finally.
        for future in futures.values():
            future.cancel()
        wait(tuple(futures.values()))
        raise
