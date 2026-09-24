import pytest


class FakeHost:
    """Records every Host call instead of touching the desktop."""

    def __init__(self):
        self.calls = []

    def reload_config(self):
        self.calls.append(("reload",))

    def open_config(self):
        self.calls.append(("open-config",))

    def quit_launcher(self):
        self.calls.append(("quit",))

    def launch_app(self, app_id):
        self.calls.append(("launch", app_id))

    def open_uri(self, uri):
        self.calls.append(("open", uri))

    def copy_text(self, text):
        self.calls.append(("copy", text))

    def open_file(self, path):
        self.calls.append(("open-file", path))

    def reveal_file(self, path):
        self.calls.append(("reveal", path))

    def open_settings(self, edit=None):
        self.calls.append(("settings", edit))


@pytest.fixture
def host():
    return FakeHost()
