"""Pytest configuration for the ns8-mail-toolbox QA & architecture audit suite."""

from pathlib import Path
import sys
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def pytest_collect_file(file_path: Path, parent):
    """Collect `tests/verify_ns8_rules.py` during directory runs (`pytest tests/ -v`) without duplicating explicit file runs."""
    if file_path.suffix == ".py" and file_path.name == "verify_ns8_rules.py":
        explicit_files = {
            Path(arg.split("::")[0]).resolve()
            for arg in parent.config.args
            if Path(arg.split("::")[0]).is_file()
        }
        if file_path.resolve() not in explicit_files:
            return pytest.Module.from_parent(parent, path=file_path)
    return None
