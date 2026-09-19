"""Re-export of `core.sources`: the collectors moved, the demo's path did not.

Aliasing the package object matters more here than elsewhere: the tests patch
`telegram.search_many` on the module they imported. A copied package would give
them a second module object, the patch would land on it, and the code under
test would happily go to the network instead.

See core/__init__.py for why the split runs where it does.
"""

import sys

from core import sources as _core_sources

sys.modules[__name__] = _core_sources
