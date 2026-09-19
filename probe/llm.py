"""Re-export of `core.llm`: the logic moved, the demo's import path did not.

Aliasing the module object (rather than `from core.llm import *`) keeps
private names like `_compile` reachable and makes `probe.llm is core.llm`
true — so a test that monkeypatches one sees it in the other.

See core/__init__.py for why the split runs where it does.
"""

import sys

from core import llm as _core_module

sys.modules[__name__] = _core_module
