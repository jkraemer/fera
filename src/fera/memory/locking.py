from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(filepath: str):
    """Advisory file lock using fcntl. Blocks until lock is acquired."""
    lock_path = filepath + ".lock"
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)

    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
