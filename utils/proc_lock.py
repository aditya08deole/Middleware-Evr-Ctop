"""
Cross-platform non-blocking exclusive file lock.

Used by utils/scheduler.py and utils/scheduler_firestore.py to ensure only
one process owns the BackgroundScheduler/EMQX MQTT clients when the app runs
under multiple worker processes (e.g. several Gunicorn workers each
importing app.py and calling init_scheduler() independently). Without this,
N workers means N BackgroundScheduler instances all polling/posting the same
devices, and N MQTT clients fighting the broker over the same client_id.

Previously this used fcntl directly and silently returned "lock acquired"
on any platform without fcntl (i.e. Windows) — meaning Windows deployments
with multiple worker processes got zero protection. This module adds a
msvcrt-based path for Windows so the guard is actually effective there too,
falling back to the old no-op behavior only if neither primitive exists.
"""

import os


def acquire_exclusive_lock(lock_path: str):
    """
    Try to acquire a non-blocking exclusive lock on lock_path.

    Returns the open file object (keep a reference — closing it or letting
    it get garbage-collected releases the lock) if the lock was acquired,
    or None if another process already holds it.

    On a platform with neither fcntl nor msvcrt, always "succeeds" (returns
    an open file object without actually locking) — matches every
    platform's behavior before this module existed, rather than regressing
    single-process setups that never needed the guard.
    """
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)

    try:
        import fcntl

        lock_file = open(lock_path, "w")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock_file.close()
            return None
        return lock_file
    except ImportError:
        pass

    try:
        import msvcrt

        lock_file = open(lock_path, "a+b")
        try:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                # msvcrt.locking() locks a byte range of the file — make
                # sure that range actually exists before locking it.
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            lock_file.close()
            return None
        return lock_file
    except ImportError:
        pass

    # Neither locking primitive is available — no real protection, but this
    # is a no-op single-process fallback, never a reason to crash startup.
    return open(lock_path, "w")
