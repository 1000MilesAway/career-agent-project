"""Where the probe keeps its state.

One place, because the alternative has already cost real money: a smoke run
that reassigned a module constant after import still wrote to the live
`out/found.json` and wiped evaluations the user had paid for. Callers that had
already read the old constant kept writing to it.

`PROBE_OUT` redirects the whole directory before anything reads it, so a
throwaway run cannot touch the real list no matter which module it goes
through.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def out_dir() -> Path:
    """The state directory, read from the environment each time it is asked.

    Read per call rather than cached at import: a test that sets PROBE_OUT
    after importing probe must still be redirected.
    """
    return Path(os.environ.get("PROBE_OUT") or (ROOT / "out"))


def out_file(name: str) -> Path:
    return out_dir() / name
