from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if os.path.isdir(os.path.join(_ROOT, "core")) and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import censor as _cen

HOMOGLYPH = _cen.HOMOGLYPH
LATIN = _cen.LATIN
CYR = _cen.CYR

basic = _cen.basic
to_cyr = _cen.to_cyr
collapse = _cen.collapse
variants = _cen.variants
is_latin = _cen.is_latin
