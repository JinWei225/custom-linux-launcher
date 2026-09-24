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

    paused = False

    def paste_clip(self, clip_id):
        self.calls.append(("paste", clip_id))

    def copy_clip(self, clip_id):
        self.calls.append(("copy-clip", clip_id))

    def pin_clip(self, clip_id, pinned):
        self.calls.append(("pin", clip_id, pinned))

    def delete_clip(self, clip_id):
        self.calls.append(("delete", clip_id))

    def toggle_clipboard_pause(self):
        self.paused = not self.paused
        self.calls.append(("pause",))

    def clear_clipboard(self):
        self.calls.append(("clear",))

    def clipboard_paused(self):
        return self.paused


@pytest.fixture
def host():
    return FakeHost()
