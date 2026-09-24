import pytest

from launcher import engine as engine_module
from launcher.engine import Engine
from launcher.providers.base import Result
from launcher.store import UsageStore


class FakeProvider:
    def __init__(self, name, results=(), error=None):
        self.name = name
        self._results = list(results)
        self._error = error

    def query(self, text):
        if self._error:
            raise self._error
        return list(self._results)


@pytest.fixture
def two_provider_mode(monkeypatch):
    monkeypatch.setitem(engine_module.MODES, "test", ("a", "b", "missing"))


def test_results_merged_and_sorted(two_provider_mode):
    engine = Engine(
        {
            "a": FakeProvider("a", [Result("a1", "A1", score=0.5), Result("a2", "A2", score=0.9)]),
            "b": FakeProvider("b", [Result("b1", "B1", score=0.7)]),
        }
    )
    assert [r.id for r in engine.query("x", "test", 10)] == ["a2", "b1", "a1"]


def test_limit(two_provider_mode):
    results = [Result(f"a{i}", "A", score=i / 10) for i in range(5)]
    engine = Engine({"a": FakeProvider("a", results)})
    assert len(engine.query("x", "test", 2)) == 2


def test_failing_provider_is_skipped(two_provider_mode, caplog):
    engine = Engine(
        {
            "a": FakeProvider("a", error=RuntimeError("boom")),
            "b": FakeProvider("b", [Result("b1", "B1", score=0.1)]),
        }
    )
    assert [r.id for r in engine.query("x", "test", 10)] == ["b1"]
    assert "provider 'a' failed" in caplog.text


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        Engine({}).query("x", "nope", 5)


def test_frecency_reorders_close_matches(two_provider_mode):
    usage = UsageStore(None)
    engine = Engine(
        {"a": FakeProvider("a", [Result("exact", "E", score=1.0), Result("pre", "P", score=0.85)])},
        usage=usage,
    )
    assert [r.id for r in engine.query("x", "test", 10)] == ["exact", "pre"]
    for _ in range(5):
        engine.record(Result("pre", "P"))
    assert [r.id for r in engine.query("x", "test", 10)] == ["pre", "exact"]


def test_frecency_never_beats_an_alias(two_provider_mode):
    usage = UsageStore(None)
    engine = Engine(
        {"a": FakeProvider("a", [Result("alias", "A", score=2.0), Result("hot", "H", score=1.0)])},
        usage=usage,
    )
    for _ in range(100):
        engine.record(Result("hot", "H"))
    assert engine.query("x", "test", 10)[0].id == "alias"


def test_results_with_learn_false_are_not_recorded_or_boosted(two_provider_mode):
    usage = UsageStore(None)
    engine = Engine({"a": FakeProvider("a", [Result("fb", "F", score=0.001, learn=False)])}, usage)
    engine.record(Result("fb", "F", learn=False))
    assert usage.boost("fb") == 0.0
    assert engine.query("x", "test", 10)[0].score == 0.001


def test_configure_skips_providers_without_it_and_survives_errors(caplog):
    class Configurable(FakeProvider):
        def configure(self, config):
            raise RuntimeError("bad")

    Engine({"a": FakeProvider("a"), "b": Configurable("b")}).configure(object())
    assert "provider 'b' failed to apply the config" in caplog.text


def test_fallbacks_are_last_and_never_cut_off(two_provider_mode):
    regular = [Result(f"r{i}", "R", score=0.5) for i in range(10)]
    fallbacks = [Result(f"f{i}", "F", score=0.001, fallback=True) for i in range(2)]
    engine = Engine({"a": FakeProvider("a", fallbacks + regular)})
    ids = [r.id for r in engine.query("x", "test", 5)]
    assert ids == ["r0", "r1", "r2", "f0", "f1"]
