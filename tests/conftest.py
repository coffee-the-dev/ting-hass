"""Test setup that avoids importing Home Assistant's integration package."""

from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType


PACKAGE_NAME = "custom_components.ting"
PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "ting"


# Importing custom_components.ting normally executes __init__.py, which needs
# Home Assistant. Extraction tests only need api.py and its ordinary relatives.
package = ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_PATH)]
sys.modules.setdefault(PACKAGE_NAME, package)
