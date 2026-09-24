from launcher.launching import scope_name


def test_scope_name_follows_xdg_convention():
    name = scope_name("org.gnome.Nautilus.desktop", 42)
    assert name == "app-launcher-org.gnome.Nautilus-42.scope"


def test_scope_name_escapes_like_systemd_escape():
    # "-" separates fields in the name, so it must be escaped (as systemd-escape does).
    assert scope_name("my-app.desktop", 7) == "app-launcher-my\\x2dapp-7.scope"
    assert scope_name("café", 1) == "app-launcher-caf\\xc3\\xa9-1.scope"
