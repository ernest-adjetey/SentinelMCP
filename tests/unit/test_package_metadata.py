"""Tests for public package metadata."""

import tomllib
from pathlib import Path

from sentinelmcp import __version__

ROOT = Path(__file__).resolve().parents[2]


def test_pre_alpha_version_is_explicit() -> None:
    """The package communicates that the runtime is not production-ready."""
    assert __version__ == "0.1.0a0"


def test_build_excludes_workspace_artifacts_and_declares_mcp_sdk() -> None:
    document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = set(document["project"]["dependencies"])
    excluded = set(document["tool"]["hatch"]["build"]["exclude"])

    assert "mcp>=2.3,<3" in dependencies
    assert {
        "/.devtools/**",
        "/.hypothesis/**",
        "/.mypy_cache/**",
        "/.pytest_cache/**",
        "/.ruff_cache/**",
        "/artifacts/**",
        "/dist/**",
    } <= excluded
