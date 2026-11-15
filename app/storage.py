"""Atomic local run checkpoints with an operating-system lock."""
import json
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID
from .sources import ROOT


def run_folder(run_id):
    return ROOT / 'data' / 'runs' / str(UUID(run_id))


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)


@contextmanager
def run_lock(folder):
    folder.mkdir(parents=True,exist_ok=True)
    stream = (folder / 'run.lock').open('a+b')
    stream.seek(0)
    if stream.read(1)==b'':
        stream.write(b'0'); stream.flush()
    stream.seek(0)
    acquired = False
    try:
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            raise ValueError('This run is already active in another process') from exc
        yield
    finally:
        if acquired:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
        stream.close()
