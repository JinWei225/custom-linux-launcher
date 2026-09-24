import pytest

from launcher.store import HALF_LIFE_DAYS, UsageStore, decay

DAY = 86400.0


def test_unknown_id_has_no_boost():
    assert UsageStore(None).boost("app:x") == 0.0


def test_boost_grows_with_use_and_stays_below_one():
    store = UsageStore(None)
    boosts = []
    for _ in range(20):
        store.record("app:x", now=1000.0)
        boosts.append(store.boost("app:x", now=1000.0))
    assert boosts == sorted(boosts)
    assert 0 < boosts[0] < boosts[-1] < 1


def test_boost_decays_with_half_life():
    assert decay(4.0, HALF_LIFE_DAYS * DAY) == pytest.approx(2.0)
    store = UsageStore(None)
    store.record("app:x", now=0.0)
    assert store.boost("app:x", now=90 * DAY) < store.boost("app:x", now=0.0) / 10


def test_persists_across_instances(tmp_path):
    path = tmp_path / "sub" / "launcher.db"
    store = UsageStore(path)
    store.record("app:x", now=1000.0)
    store.close()
    assert UsageStore(path).boost("app:x", now=1000.0) > 0


def test_unwritable_path_falls_back_to_memory(tmp_path, caplog):
    blocker = tmp_path / "file"
    blocker.write_text("")
    store = UsageStore(blocker / "launcher.db")  # parent is a file: cannot be created
    store.record("app:x")
    assert store.boost("app:x") > 0
    assert "history will not be saved" in caplog.text
