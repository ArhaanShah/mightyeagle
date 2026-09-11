"""conftest.py — pytest configuration for the test suite."""
import tempfile
import shutil
from pathlib import Path
import pytest


@pytest.fixture
def tmp_path(tmp_path_factory):
    """Override tmp_path to use a local tmpdir instead of the system temp."""
    tmpdir = Path(tempfile.mkdtemp(dir="."))
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)
