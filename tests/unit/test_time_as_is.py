"""The framework never converts time. This keeps it so.

The rule: a time is written and read exactly as it is given - no zone added, removed or moved,
anywhere in the library. Getting time right is the application's job; the default clock gives
UTC with no zone attached. Any of the patterns below in `src/` is a conversion creeping back,
so this fails and names the line. The allow-list is the clock's one read of the wall clock and
nothing else that touches a user's value.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "ekbatan"

#: What a conversion looks like in source.
CONVERSIONS = re.compile(
    r"astimezone\(|tzinfo\s*=|\btz\s*=|utcfromtimestamp|utcnow|timezone\.utc|"
    r"\bto_instant\b|\bto_storage\b|\b_as_utc\b|datetime\.now\(UTC\)"
)

#: (file, the line's text) that may match, and why.
ALLOWED = {
    # The clock's one read of the wall clock: the current UTC time, no zone attached.
    ("clock.py", "return datetime.now(UTC).replace(tzinfo=None)"),
    # The migration history's own text stamp - written by the migrator about itself, not a
    # time an application handed in.
    ("migrations.py", '"installed_on": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),'),
}


def test_no_code_in_the_library_converts_a_time():
    found = []
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC).as_posix()
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            code = line.split("#", 1)[0]
            if not CONVERSIONS.search(code):
                continue
            if (relative, line.strip()) in ALLOWED:
                continue
            found.append(f"src/ekbatan/{relative}:{number}: {line.strip()}")
    assert not found, "the framework must not convert time:\n" + "\n".join(found)


def test_the_guard_sees_a_conversion():
    """The patterns catch what they are meant to - otherwise the test above proves nothing."""
    for line in (
        "value.astimezone(UTC)",
        "value.replace(tzinfo=UTC)",
        "datetime.fromtimestamp(x, tz=UTC)",
        "datetime.now(UTC)",
        "to_storage(v)",
    ):
        assert CONVERSIONS.search(line), line


def test_the_allow_list_is_still_true():
    """An allowed line that is gone must leave the list, so the list cannot grow stale."""
    for relative, text in ALLOWED:
        assert text in (SRC / relative).read_text(), (relative, text)
