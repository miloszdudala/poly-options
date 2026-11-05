import logging
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT.parent / "data" / "store"
sys.path.insert(0, str(ROOT))                        # model/ wins: both it and
sys.path.append(str(ROOT.parent / "data" / "src"))   # data/src define `schemas`


def pytest_configure(config):
    """Same reason as data/tests/conftest.py: keep tests out of the collector log.

    `run_calibration` imports `config` from data/src, which pulls in `util` and
    its root FileHandler, so a model test would otherwise append to
    data/logs/collector-<date>.log.
    """
    import util

    util._log_configured = True
    logging.getLogger().addHandler(logging.NullHandler())


@contextmanager
def _skip_without_store():
    """Tests that read the store skip when there is none, as in a cloud session.

    Only then: with a store on disk, a missing file is still an error.
    """
    import duckdb

    try:
        yield
    except duckdb.IOException as error:
        if STORE.exists() or "No files found" not in str(error):
            raise
        pytest.skip("needs data/store")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    with _skip_without_store():
        return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    with _skip_without_store():
        return (yield)
