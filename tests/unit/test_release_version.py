"""The version is written in more places than the release workflow checks.

`release.yml` holds the tag, both `pyproject.toml` versions and the scheduler pin to one another
before it builds anything. Two places are outside it: `ekbatan.__version__`, which a user reads,
and the website's nav, which shows the version on every page. Neither was on the release
checklist, so a bump would have left both behind; here they are held to the installed
distribution's version on every run.
"""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

import ekbatan

ROOT = Path(__file__).resolve().parents[2]


def test_the_module_version_is_the_distribution_s() -> None:
    assert ekbatan.__version__ == metadata.version("ekbatan")


def test_the_website_shows_the_distribution_s_version() -> None:
    nav = (ROOT / "website" / "src" / "components" / "Nav.astro").read_text()
    assert f">v{metadata.version('ekbatan')}<" in nav
