"""The engine's output schema, and the name clash it exists to avoid."""
import pandas as pd
import pytest

import fair_schema


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(fair_schema, "STORE", tmp_path)
    return tmp_path


def _blank_row(**kw) -> dict:
    row = {f.name: None for f in fair_schema.FAIR}
    row.update({"cycle_id": "c", "market_id": "1"})
    row.update(kw)
    return row


def test_the_engine_schema_does_not_shadow_the_collectors():
    """Both trees are flat on `sys.path`, so the names must not collide.

    `data/src/schemas.py` (the collector's table definitions) and the engine's
    output schema would otherwise be one module name in one process; whichever
    loaded first would win for everything and the other would fail at its one
    distinctive call - `coerce` for the collector, `write_fair` for the engine.
    Running both test suites in one pytest process is enough to trigger it,
    which is how this was found. Hence `fair_schema`.
    """
    import schemas as collector_schemas

    assert hasattr(fair_schema, "write_fair")
    assert hasattr(collector_schemas, "coerce")
    assert fair_schema.__file__ != collector_schemas.__file__


def test_every_declared_column_survives_a_round_trip(store):
    row = _blank_row(fair_K=0.25, deep_otm=True, n_strikes_after=12, flags="deep_otm")
    back = pd.read_parquet(fair_schema.write_fair([row], "2026-09-16"))
    assert list(back.columns) == [f.name for f in fair_schema.FAIR]
    assert back["fair_K"].iloc[0] == pytest.approx(0.25)
    assert bool(back["deep_otm"].iloc[0])
    assert int(back["n_strikes_after"].iloc[0]) == 12


def test_the_file_lands_in_the_dates_partition(store):
    path = fair_schema.write_fair([_blank_row()], "2026-09-16")
    assert path.parent.name == "date=2026-09-16"
    assert path.name.startswith("part-") and path.suffix == ".parquet"


def test_empty_rows_write_nothing(store):
    assert fair_schema.write_fair([], "2026-09-16") is None


def test_unparseable_numbers_become_null_rather_than_a_type_error(store):
    """A bad value must not take the type of a whole column with it."""
    row = _blank_row(fair_K="not a number", quote_time_utc="NaT", n_strikes_after="")
    back = pd.read_parquet(fair_schema.write_fair([row], "2026-09-16"))
    assert pd.isna(back["fair_K"].iloc[0])
    assert pd.isna(back["quote_time_utc"].iloc[0])   # written as an Arrow null
    assert pd.isna(back["n_strikes_after"].iloc[0])


def test_a_rerun_replaces_the_date_rather_than_doubling_it(store, monkeypatch):
    """`write_fair` appends, so `run_date` has to clear the partition first."""
    import run_fair

    monkeypatch.setattr(run_fair.fair_schema, "STORE", store)
    monkeypatch.setattr(run_fair.data, "session_cycles", lambda date: [])
    fair_schema.write_fair([_blank_row()], "2026-09-16")
    partition = store / "fair" / "date=2026-09-16"
    assert len(list(partition.glob("*.parquet"))) == 1
    run_fair.run_date("2026-09-16", paths=100, write=True)
    assert len(list(partition.glob("*.parquet"))) == 0
