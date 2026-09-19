"""Re-export of `core.repair`: the logic moved, the demo's import path did not.

Aliasing the module object (rather than `from core.repair import *`) keeps
private names like `_compile` reachable and makes `probe.repair is core.repair`
true — so a test that monkeypatches one sees it in the other.

See core/__init__.py for why the split runs where it does.
"""

import sys

from core import repair as _core_module

sys.modules[__name__] = _core_module
