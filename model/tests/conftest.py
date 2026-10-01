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
