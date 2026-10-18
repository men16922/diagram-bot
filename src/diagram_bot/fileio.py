"""Atomic file replacement shared by the stores and the team users file."""
import os
import tempfile
from pathlib import Path


def atomic_write(path, text):
    """Readers see the old or the new file, never a torn one. Each writer gets its own temporary file, so concurrent
    writers of one path cannot interleave; the file is created owner-only (0600: the users file holds password hashes)."""
    path = Path(path)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
