"""
state_io.py — crash-safe writes for the bot's JSON state files.

write_json writes to a temp file in the same folder, fsyncs it, then swaps it
in with os.replace, which is atomic. A crash or restart mid-write leaves the
old file intact, and readers (the dashboard, the next cycle) never see a
half-written file. The existing file's permissions are kept.
"""
import json
import os
import tempfile


def write_json(path: str, data, **dump_kwargs) -> None:
    """Atomically replace `path` with `data` as JSON. dump_kwargs go to json.dump (e.g. indent=2)."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    try:
        mode = os.stat(path).st_mode & 0o777
    except FileNotFoundError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{os.path.basename(path)}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, **dump_kwargs)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
