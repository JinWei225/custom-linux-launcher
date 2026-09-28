"""Settings → Status: the setup check (launcher --doctor) with a fix for each problem."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import crash, doctor  # noqa: E402
from ..config import Config  # noqa: E402
from .dialogs import action_row  # noqa: E402

log = logging.getLogger(__name__)

ICONS = {
    doctor.OK: ("object-select-symbolic", "success"),
    doctor.WARNING: ("dialog-warning-symbolic", "warning"),
    doctor.ERROR: ("dialog-error-symbolic", "error"),
}
RECHECK_AFTER_FIX_MS = 1500  # a started service needs a moment before it counts as running


class StatusPage(Adw.PreferencesPage):
    key = "status"

    def __init__(
        self, window, gather: Callable[[], doctor.Facts] = lambda: doctor.gather()
    ) -> None:
        super().__init__(title="Status", icon_name="checkbox-checked-symbolic")
        self.window = window
        self._gather = gather
        self._running = False
        self._again = False
        self.checks: list[doctor.Check] = []

        self._group = Adw.PreferencesGroup(
            title="Setup",
            description="What the launcher relies on. Also available as launcher --doctor.",
        )
        self._button = Gtk.Button(
            icon_name="view-refresh-symbolic", tooltip_text="Check Again", valign=Gtk.Align.CENTER
        )
        self._button.add_css_class("flat")
        self._button.connect("clicked", lambda _b: self.run_checks())
        self._group.set_header_suffix(self._button)
        self._rows: list[Adw.ActionRow] = []
        self._show_checking()
        self._crash_group = self._build_crash_group()
        self.add(self._crash_group)  # first: the crash notice's button leads here
        self.add(self._group)
        self.connect("map", lambda _p: (self.run_checks(), self.show_crash()))

    # --- checking --------------------------------------------------------------------

    def refresh(self, _config: Config) -> None:
        if self.get_mapped():
            self.run_checks()  # the config may have fixed (or caused) something

    def run_checks(self) -> None:
        """Check in a thread (systemctl and D-Bus calls take a moment), then show."""
        if self._running:
            self._again = True
            return
        self._running = True
        self._button.set_sensitive(False)

        def work() -> None:
            try:
                checks = doctor.evaluate(self._gather())
            except Exception:
                log.exception("the setup check failed")
                checks = []
            GLib.idle_add(self._done, checks)

        threading.Thread(target=work, name="setup-check", daemon=True).start()

    def _done(self, checks: list[doctor.Check]) -> bool:
        self._running = False
        self._button.set_sensitive(True)
        self.checks = checks
        self._show(checks)
        if self._again:
            self._again = False
            self.run_checks()
        return GLib.SOURCE_REMOVE

    # --- showing ---------------------------------------------------------------------

    def _set_rows(self, rows: list[Adw.ActionRow]) -> None:
        for row in self._rows:
            self._group.remove(row)
        self._rows = rows
        for row in rows:
            self._group.add(row)

    def _show_checking(self) -> None:
        row = action_row(title="Checking…")
        row.add_prefix(Adw.Spinner())
        self._set_rows([row])

    def _show(self, checks: list[doctor.Check]) -> None:
        if not checks:
            self._set_rows([action_row(title="The setup check failed; see the log")])
            return
        self._set_rows([self._row(check) for check in checks])
        problems = [c for c in checks if c.status != doctor.OK]
        page = self.window.stack_page(self)
        if page is not None:
            page.set_needs_attention(any(c.status == doctor.ERROR for c in problems))

    def _row(self, check: doctor.Check) -> Adw.ActionRow:
        subtitle = check.detail
        if check.status != doctor.OK and check.fix:
            subtitle += "\n" + check.fix
        row = action_row(title=check.title, subtitle=subtitle)
        row.set_subtitle_lines(0)
        icon_name, css = ICONS[check.status]
        icon = Gtk.Image(icon_name=icon_name, tooltip_text=check.status.capitalize())
        icon.add_css_class(css)
        row.add_prefix(icon)
        if check.status != doctor.OK and check.command:
            fix = Gtk.Button(
                label="Fix", valign=Gtk.Align.CENTER, tooltip_text=" ".join(check.command)
            )
            fix.add_css_class("suggested-action")
            fix.connect("clicked", lambda b, c=check: self._fix(b, c))
            row.add_suffix(fix)
        if check.status != doctor.OK and check.shell:
            copy = Gtk.Button(
                label="Copy Command", valign=Gtk.Align.CENTER, tooltip_text=check.shell
            )
            copy.connect("clicked", lambda _b, c=check: self._copy(c.shell))
            row.add_suffix(copy)
        return row

    # --- the last crash --------------------------------------------------------------

    def _build_crash_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="The Launcher Crashed", visible=False)
        buttons = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        copy = Gtk.Button(label="Copy Log")
        copy.connect("clicked", lambda _b: self._copy_crash())
        clear = Gtk.Button(label="Clear")
        clear.connect("clicked", lambda _b: (crash.clear(), self.show_crash()))
        buttons.append(copy)
        buttons.append(clear)
        group.set_header_suffix(buttons)
        self._crash_log = Gtk.Label(
            xalign=0, yalign=0, selectable=True, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
            margin_top=12, margin_bottom=12, margin_start=12, margin_end=12,
        )  # fmt: skip
        self._crash_log.add_css_class("monospace")
        frame = Gtk.Frame(child=self._crash_log)
        frame.add_css_class("view")
        group.add(frame)
        return group

    def show_crash(self) -> None:
        report = crash.load()
        self._crash_group.set_visible(report is not None)
        if report is None:
            return
        when = GLib.DateTime.new_from_unix_local(int(report.time)).format("%-d %B, %H:%M")
        restarts = "once" if report.restarts == 1 else f"{report.restarts} times"
        self._crash_group.set_description(f"{when}: restarted {restarts}. Its last log lines:")
        self._crash_log.set_text("\n".join(report.lines) or "(nothing was logged)")

    def _copy_crash(self) -> None:
        report = crash.load()
        if report is not None:
            Gdk.Display.get_default().get_clipboard().set("\n".join(report.lines))
            self.window.toast("Copied the log")

    # --- fixing ----------------------------------------------------------------------

    def _fix(self, button: Gtk.Button, check: doctor.Check) -> None:
        button.set_sensitive(False)
        log.info("fixing %s: %s", check.key, " ".join(check.command))
        try:
            process = Gio.Subprocess.new(
                list(check.command),
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_PIPE,
            )
        except GLib.Error as e:
            self.window.toast(f"Could not run {check.command[0]}: {e.message}")
            button.set_sensitive(True)
            return

        def finished(proc: Gio.Subprocess, result: Gio.AsyncResult) -> None:
            try:
                _ok, _out, err = proc.communicate_utf8_finish(result)
            except GLib.Error as e:
                err = e.message
            if not proc.get_successful():
                self.window.toast(f"{check.title}: {(err or '').strip() or 'the fix failed'}")
            GLib.timeout_add(RECHECK_AFTER_FIX_MS, lambda: (self.run_checks(), False)[1])

        process.communicate_utf8_async(None, None, finished)

    def _copy(self, text: str) -> None:
        Gdk.Display.get_default().get_clipboard().set(text)
        self.window.toast("Copied; paste it into a terminal")
