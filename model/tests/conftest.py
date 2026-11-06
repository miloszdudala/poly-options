import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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


from contextlib import contextmanager  # noqa: E402

import pytest  # noqa: E402

import pipeline  # noqa: E402,F401  (registers the step variants)

STORE = ROOT.parent / "data" / "store"


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


@pytest.fixture(scope="session")
def synthetic_root(tmp_path_factory):
    """The made-up store of `synthetic.py`, built once per test session."""
    import synthetic

    return synthetic.build(tmp_path_factory.mktemp("synthetic") / "parquet")


@pytest.fixture
def synthetic_store(synthetic_root, tmp_path, monkeypatch):
    """Read the synthetic store, with a cache of this test's own."""
    import store
    from pipeline import cache

    previous = store.ROOT
    store.use(synthetic_root)
    monkeypatch.setattr(cache, "ROOT", tmp_path / "cache")
    yield synthetic_root
    store.use(previous)
