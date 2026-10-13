"""Tests ensuring the hard constraint is never violated: NEVER SEND EMAIL."""
import os
import re
from pathlib import Path

FORBIDDEN_CALLS = [
    r"messages\(\)\.send",
    r"drafts\(\)\.send",
]


def test_no_send_calls_in_entire_source():
    """Ensure no code anywhere in the repo calls messages().send or drafts().send."""
    root_dir = Path(__file__).parent.parent
    checked_files = 0

    for py_file in root_dir.glob("**/*.py"):
        # Ignore virtual environment and tests themselves
        path_str = str(py_file)
        if "sentec_env" in path_str or "tests" in path_str or ".pytest_cache" in path_str:
            continue

        checked_files += 1
        content = py_file.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_CALLS:
            match = re.search(pattern, content)
            assert not match, f"CRITICAL SECURITY VIOLATION: '{pattern}' found in {py_file}!"

    assert checked_files > 0, "No source files were inspected."
