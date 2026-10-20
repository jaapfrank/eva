"""Collect independent, explicitly opted-in provider reads in request scope."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from eva.application.cancellation import check_cancellation


def collect_provider_context(providers, turn):
    readers = [p for p in providers if callable(getattr(p, "active_context", None))]

    def read(provider):
        check_cancellation()
        result = provider.active_context(turn)
        check_cancellation()
        return result

    def independent(provider):
        opt_in = getattr(provider, "parallel_context_reads", False)
        return (opt_in(turn) if callable(opt_in) else opt_in) is True

    parallel = [index for index, provider in enumerate(readers) if independent(provider)]
    if not parallel:
        return tuple(item for provider in readers for item in read(provider))
    # The provider opts in only for non-mutating, independent metadata reads.
    # Each reader owns its connections and performs all its existing scope checks.
    # Wait for started readers even on error; no work escapes the request lifetime.
    with ThreadPoolExecutor(max_workers=min(4, len(parallel)),
                            thread_name_prefix="eva-provider-context") as pool:
        pending = {}
        for index in parallel:
            check_cancellation()
            pending[index] = pool.submit(copy_context().run, read, readers[index])
        result = []
        for index, provider in enumerate(readers):
            check_cancellation()
            result.extend(pending[index].result() if index in pending else read(provider))
        check_cancellation()
        return tuple(result)
