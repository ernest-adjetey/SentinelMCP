"""Tests for public package metadata."""

import tomllib
from pathlib import Path

from sentinelmcp import __version__

ROOT = Path(__file__).resolve().parents[2]


def test_public_version_is_consistent() -> None:
    assert __version__ == "0.1.1"


def test_build_excludes_workspace_artifacts_and_declares_mcp_sdk() -> None:
    document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = set(document["project"]["dependencies"])
    excluded = set(document["tool"]["hatch"]["build"]["exclude"])

    assert "mcp>=2.3,<3" in dependencies
    assert document["project"]["version"] == __version__
    assert document["project"]["description"] == (
        "Reliability, safety, security, and observability control plane for AI-agent tool execution"
    )
    assert "security" in document["project"]["keywords"]
    assert {
        "/.devtools/**",
        "/.hypothesis/**",
        "/.mypy_cache/**",
        "/.pytest_cache/**",
        "/.ruff_cache/**",
        "/artifacts/**",
        "/benchmark-results/**",
        "/benchmarks/results/**",
        "/dist/**",
    } <= excluded
