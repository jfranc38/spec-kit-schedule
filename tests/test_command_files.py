"""The slash-command files must run the wrapper through bash.

``specify extension add --from <zip>`` extracts the release archive without
the executable bit on ``bin/speckit-schedule`` (only ``*.sh`` files get it
back), so a bare ``"$EXT/bin/speckit-schedule"`` fails with "permission
denied" on every zip install.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

COMMANDS = sorted((Path(__file__).resolve().parents[1] / "commands").glob("*.md"))
# An invocation: the quoted path not preceded by ``bash ``, a ``-f`` test or ``=``.
_INVOCATION = re.compile(r'(?<!bash )(?<!-f )(?<!=)"\$(?:EXT/bin/speckit-schedule|SKS)"')


@pytest.mark.parametrize("path", COMMANDS, ids=[p.name for p in COMMANDS])
def test_wrapper_is_invoked_through_bash(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "-x \"$EXT/bin/speckit-schedule\"" not in text, "test for existence (-f), not -x"
    bare = [line.strip() for line in text.splitlines() if _INVOCATION.search(line)]
    assert not bare, f"bare wrapper invocation(s): {bare}"
