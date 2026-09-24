from launcher.providers.commands import CommandsProvider


def test_empty_query_returns_nothing(host):
    assert CommandsProvider(host).query("  ") == []


def test_query_finds_and_runs_command(host):
    results = CommandsProvider(host).query("quit launcher")
    assert results[0].id == "command:quit"
    results[0].action()
    assert host.calls == [("quit",)]


def test_quit_is_never_the_top_result_for_generic_query(host):
    results = sorted(CommandsProvider(host).query("launcher"), key=lambda r: -r.score)
    assert results[0].id != "command:quit"


def test_each_result_runs_its_own_command(host):
    results = {r.id: r for r in CommandsProvider(host).query("launcher")}
    results["command:reload"].action()
    results["command:open-config"].action()
    assert host.calls == [("reload",), ("open-config",)]
