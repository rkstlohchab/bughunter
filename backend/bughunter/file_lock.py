"""Non-blocking single-owner file lock for Unix and Windows.

The caller keeps the file object open for as long as it owns the lock. Closing
it releases the lock on either platform.
"""
import os

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def try_lock(file):
    """Return False when another backend owns the lock; raise on other I/O errors."""
    if os.name == "nt":
        # msvcrt locks a byte range; zero-length files need a byte to lock.
        file.seek(0, os.SEEK_END)
        if file.tell() == 0:
            file.write("0")
            file.flush()
        file.seek(0)
        try:
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError as exc:
            if getattr(exc, "winerror", None) in {33, 36} or getattr(exc, "errno", None) in {13, 11}:
                return False
            raise
    try:
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False
