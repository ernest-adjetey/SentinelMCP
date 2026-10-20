"""Tests for public package metadata."""

from sentinelmcp import __version__


def test_pre_alpha_version_is_explicit() -> None:
    """The package communicates that the runtime is not production-ready."""
    assert __version__ == "0.1.0a0"
