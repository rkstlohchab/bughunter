"""One cancellable inference lease for all app-managed local Ollama callers."""
from contextlib import contextmanager
import threading

_lock = threading.Lock()

@contextmanager
def local_inference(check=None, enabled=True):
    if not enabled:
        yield
        return
    while not _lock.acquire(timeout=.1):
        if check: check()
    try:
        if check: check()
        yield
    finally:
        _lock.release()

class GatedClient:
    """Preserve the caller's client API and serialize only inference requests."""
    def __init__(self, client, check=None):
        self.client, self.check = client, check

    def post(self, url, **kwargs):
        if not any(url.rstrip('/').endswith(path) for path in ('/api/chat','/chat/completions','/responses')):
            return self.client.post(url, **kwargs)
        # Keep the exact caller-owned transport/session. The request worker owns
        # the lease until the request actually finishes, including after a stop.
        # This prevents another model call overlapping an uncancellable transport.
        done = threading.Event()
        cancelled = threading.Event()
        active = threading.Event()
        result = []
        errors = []

        def check():
            if cancelled.is_set():
                raise InterruptedError('Local inference stopped by operator.')
            if self.check:
                self.check()

        def request():
            try:
                with local_inference(check):
                    active.set()
                    check()
                    result.append(self.client.post(url, **kwargs))
            except BaseException as exc:
                errors.append(exc)
            finally:
                active.clear()
                done.set()

        check()
        worker = threading.Thread(target=request, name='local-inference-request', daemon=True)
        worker.start()
        try:
            while not done.wait(.1):
                check()
            check()
            if errors:
                raise errors[0]
            return result[0]
        except BaseException:
            cancelled.set()
            if active.is_set() and not done.is_set():
                # HTTPX closes its transport/pool here. Custom clients retain
                # their own close semantics; their worker keeps the lease until
                # completion, even if closing cannot interrupt a pending read.
                close = getattr(self.client, 'close', None)
                if close:
                    def close_transport():
                        try:
                            close()
                        except Exception:
                            pass
                    closer = threading.Thread(target=close_transport, name='local-inference-close', daemon=True)
                    closer.start()
            raise
