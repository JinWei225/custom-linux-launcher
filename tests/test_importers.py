import tomllib

from launcher.config import parse_config
from launcher.importers import ulauncher_to_toml

ULAUNCHER = {
    "a": {
        "name": "Google Search",
        "keyword": "g",
        "cmd": "https://google.com/search?q=%s",
        "is_default_search": True,
    },
    "b": {"name": 'Say "hi"', "keyword": "hi", "cmd": "https://x.com/", "is_default_search": True},
    "c": {"name": "Script", "keyword": "s", "cmd": "#!/bin/bash\necho %s"},
    "d": {"name": "", "keyword": "e", "cmd": "https://empty.com/"},
}


def test_converts_to_valid_config():
    config, warnings = parse_config(tomllib.loads(ulauncher_to_toml(ULAUNCHER)))
    assert warnings == []
    google, hi = config.quicklinks
    assert (google.alias, google.url, google.fallback) == (
        "g",
        "https://google.com/search?q={query}",
        True,
    )
    # fallback needs {query}; a plain link marked default-search stays a plain link
    assert (hi.name, hi.fallback) == ('Say "hi"', False)


def test_accepts_list_form():
    assert "Google Search" in ulauncher_to_toml(list(ULAUNCHER.values()))
