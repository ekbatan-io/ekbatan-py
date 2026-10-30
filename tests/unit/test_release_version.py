"""The version is written in more places than the release workflow checks.

`release.yml` holds the tag, both `pyproject.toml` versions and the scheduler pin to one another
before it builds anything. Three places are outside it: `ekbatan.__version__`, which a user reads,
and the website's nav and home page, which show the version. None was on the release checklist,
and the home page was left behind at 0.1.0; here all three are held to the installed
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
    """In the nav on every page, and in the home page's hero."""
    version = metadata.version("ekbatan")
    nav = (ROOT / "website" / "src" / "components" / "Nav.astro").read_text()
    home = (ROOT / "website" / "src" / "pages" / "index.astro").read_text()
    assert f">v{version}<" in nav
    assert f">v{version} · " in home
