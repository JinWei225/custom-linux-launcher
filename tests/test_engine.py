import pytest

from launcher import engine as engine_module
from launcher.engine import Engine
from launcher.providers.base import Result


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
