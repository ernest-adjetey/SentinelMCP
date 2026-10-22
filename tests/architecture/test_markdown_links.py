"""Validate repository-local targets used by inline Markdown links."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
INLINE_LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
GENERATED_ROOTS = frozenset({"artifacts", "benchmark-results", "build", "dist"})


def test_local_markdown_link_targets_exist() -> None:
    missing: list[str] = []
    for document in sorted(REPOSITORY_ROOT.rglob("*.md")):
        relative_parts = document.relative_to(REPOSITORY_ROOT).parts
        if (
            any(part.startswith(".") for part in relative_parts)
            or relative_parts[0] in GENERATED_ROOTS
        ):
            continue
        content = document.read_text(encoding="utf-8")
        for raw_target in INLINE_LINK_PATTERN.findall(content):
            target = raw_target.strip().strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            resolved = (document.parent / unquote(parsed.path)).resolve()
            if not resolved.exists():
                missing.append(f"{document.relative_to(REPOSITORY_ROOT)} -> {target}")

    assert not missing, "Missing local Markdown targets:\n" + "\n".join(missing)
