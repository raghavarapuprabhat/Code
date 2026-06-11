"""Backend app package bootstrap.

Adds the repository root to ``sys.path`` so imports from sibling packages like
``shared`` and ``agents`` work when running from ``Code/backend``.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, "../.."))
if _REPO_ROOT not in sys.path:
	sys.path.insert(0, _REPO_ROOT)

