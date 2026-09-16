import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def pytest_configure(config):
    """Keep the test run out of the collector's log file.

    get_logger() attaches a FileHandler to the ROOT logger pointing at
    data/logs/collector-<date>.log, so importing any collector module from a
    test makes pytest append to the same file the scheduled job writes. Tests
    that exercise malformed input then leave WARNING lines in the operational
    log that look like real collection failures.
    """
    import util

    util._log_configured = True          # suppress the file handler entirely
    logging.getLogger().addHandler(logging.NullHandler())
