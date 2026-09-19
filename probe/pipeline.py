"""Re-export of `core.pipeline`: the logic moved, the demo's import path did not.

Aliasing the module object (rather than `from core.pipeline import *`) keeps
private names like `_compile` reachable and makes `probe.pipeline is core.pipeline`
true — so a test that monkeypatches one sees it in the other.

See core/__init__.py for why the split runs where it does.
"""

import sys

from core import pipeline as _core_module

sys.modules[__name__] = _core_module
