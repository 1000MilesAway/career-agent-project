"""Re-export of `core.sources`: the collectors moved, the demo's path did not.

Aliasing matters more here than elsewhere: the tests patch
`telegram.search_many` on the module they imported. A second module object
would take the patch while the code under test kept the original and went to
the network.

Aliasing the package alone is not enough. `import probe.sources.telegram`
bypasses this module's `sys.modules` entry and imports the submodule a second
time under its own name, so each submodule is registered explicitly below.
That form is what `app/` and a stray import in a test would use, and it must
resolve to the same object as `from probe.sources import telegram`.
"""

import sys

from core import sources as _core_sources
from core.sources import habr as _habr
from core.sources import habr_api as _habr_api
from core.sources import telegram as _telegram
from core.sources import tg_channels as _tg_channels

sys.modules[__name__] = _core_sources
for _name, _module in (
    ("habr", _habr),
    ("habr_api", _habr_api),
    ("telegram", _telegram),
    ("tg_channels", _tg_channels),
):
    sys.modules[f"{__name__}.{_name}"] = _module
