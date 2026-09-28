# Linux Launcher — Build Plan

A personal launcher for GNOME on Wayland. It replaces Ulauncher and Vicinae. The goal is a small set of features that work every time, rather than a large plugin ecosystem.

**Target environment (checked 2026-09-24):** Ubuntu 26.04, GNOME Shell 50.1, Wayland, Python 3.14. espanso is installed. Rust is not.

Decisions are marked **🔶 Dn**. Each lists the options and a recommendation. Record your choice in the *Decision log* at the bottom. Each milestone lists the decisions it needs, so you only have to decide them when you reach that milestone.

---

## 1. Features in scope

| # | Feature | Milestone |
|---|---|---|
| 1 | Launch apps | M1 |
| 2 | Quicklinks (URL in browser) | M1 |
| 3 | Web search with a query | M1 |
| 4 | Aliases for apps and quicklinks | M1 |
| 5 | File search over chosen folders (Downloads, Documents, …) | M2 |
| 8 | Global keyboard shortcuts per mode | M3 |
| 6 | Clipboard history (text and images) with direct paste | M4 |
| 7 | Snippets: direct paste and expansion while typing | M5 |
| 9 | Converters in the main search: dates in words, timezones, currency (online rates) | M6 |
| 10 | Notes: markdown notes with formatting applied as you type, sidebar of folders | M7 |

Out of scope for now: plugins/extensions API, calculator, window switcher, emoji picker. Any of these can be added later as another provider.

---

## 2. Architecture

```
┌────────────────────────────── GNOME Shell (Wayland compositor) ──────────────────────────────┐
│  launcher-helper@local  (tiny GNOME Shell extension, GJS)                                     │
│   • watches clipboard  → emits ClipboardChanged over DBus                                     │
│   • SetClipboard(mime, bytes)                                                                 │
│   • Paste(): waits for previous window to regain focus, sends Ctrl+V / Ctrl+Shift+V           │
│   • GetFocusedWindow(): wm_class (used to pick the paste key combination)                    │
└───────────────▲──────────────────────────────────────────────────────────────┬───────────────┘
                │ DBus (session bus)                                             │
┌───────────────┴──────────────────────────────────────────────────────────────▼───────────────┐
│  launcherd  (Python + GTK4/libadwaita, a single-instance Gio.Application)                     │
│   • stays running (systemd --user service); the window is hidden, not destroyed → opens fast │
│   • Providers: apps · quicklinks · websearch · files · clipboard · snippets                  │
│   • Ranking: fuzzy match + frecency (how often and how recently you picked an item)          │
│   • Storage: ~/.config/launcher/config.toml, ~/.local/share/launcher/{db.sqlite, clips/}     │
└───────────────▲──────────────────────────────────────────────────────────────────────────────┘
                │ `launcher --mode clipboard` (Gio.Application forwards the command line to the running instance)
┌───────────────┴──────────────┐          ┌──────────────────────────────────────┐
│ GNOME custom shortcuts        │          │ espanso (expansion while typing)      │
│ Super+Shift+Return → launcher │          │ reads snippet files that launcherd    │
│ Super+Shift+V → clipboard …   │          │ writes (see D3)                       │
└───────────────────────────────┘          └──────────────────────────────────────┘
```

Why the design is split this way:
- **Nearly all logic lives in the Python app.** The Shell extension is the part most likely to break on a GNOME upgrade, so it is kept under about 200 lines and only does what Wayland forbids normal apps from doing.
- **The app runs all the time.** Opening the window takes about 50 ms instead of a cold Python start, and clipboard history is recorded even while the window is closed.
- **Gio.Application gives single-instance behaviour for free.** Running `launcher --mode files` again just forwards the arguments to the running process.

---

## 3. Decisions

### ✅ D1 — Language / toolkit — **decided: A (Python + PyGObject)**
| Option | Pros | Cons |
|---|---|---|
| **A. Python + PyGObject (GTK4 + libadwaita)** | Already installed; fastest to write and change; same GObject API as the extension | Higher memory (~60–80 MB); runtime type errors (reduced with type hints + tests) |
| B. Rust + gtk4-rs | Low memory, strong type checking, one binary | Must install the toolchain; slower to iterate; GTK in Rust is verbose |
| C. Tauri / web UI | Easy styling | Heavy; looks less native on GNOME; more moving parts |

**Recommendation: A.** Startup speed doesn't matter because the app is always running. The main risk to stability is the Wayland integration, not the language.

### ✅ D2 — Where the UI lives — **decided: A (GTK4 window + thin Shell extension)**
| Option | Pros | Cons |
|---|---|---|
| **A. Normal GTK4 window + thin Shell extension** | Full GTK and Python; the extension stays tiny | Wayland doesn't let the app choose where its window appears (GNOME usually centres it); the window must hide itself before pasting |
| B. Whole UI inside a Shell extension (like Pano) | Perfect overlay, focus and position | Everything in GJS/St; can break with each GNOME release; a crash can affect the Shell itself |

**Recommendation: A.**

### ✅ D3 — Snippet expansion while typing — **decided: A1 (espanso expands; snippets.toml is the master copy)**

**Status 2026-09-24:** typing in all three input languages works with espanso plus the gsettings fix. Keep option A for now. During M5, check whether setting up and editing text snippets (espanso reloading its match files) makes it flash its window. If espanso still causes trouble there, switch to option B. (Hotkeys were checked in M3 and are fine.)

**M5 check 2026-09-24:** every change to a file in espanso's match folder restarts its worker, and the restart takes focus away from the active window. In 3 tries the launcher lost focus for ~5 ms twice, and once for long enough to hide. Typing and expanding cause no flash. **Decision: keep espanso.** The flash only happens when a snippet is saved, and the launcher rewrites `launcher.yml` only when its content actually changes (not on start, reload or unrelated edits). Several quick edits are debounced by espanso into one restart.
| Option | Pros | Cons |
|---|---|---|
| **A. espanso does the expansion; launcherd manages the snippets** | Already installed, proven on Wayland; snippet storage is our own | Relies on espanso's reliability; it needs to run with an `input` group or uinput capability |
| B. Our own evdev + uinput service | Full control | Keyboard layout mapping, dead keys and IME interaction are hard to get right; same `input` group security concern |
| C. IBus input method engine | Sees keys legitimately, no extra permissions | Conflicts with other input methods; only works in apps that use IBus. **Not an option here: you use IBus libpinyin and hangul** |

**Found 2026-09-24 (espanso 2.4.1):** espanso works out the keyboard layout from the *first* entry of GNOME's most-recently-used input-source list (`gsettings get org.gnome.desktop.input-sources mru-sources`). Every us/pinyin/hangul switch therefore looks like a layout change, and espanso restarts its worker. On startup the worker opens a small, undrawn Wayland window (the "rainbow" flash) that takes focus for a moment. Fixed with `make install-espanso-fix`, which puts a `gsettings` wrapper on espanso's PATH that always reports `us`. The launcher also ignores focus losses shorter than 150 ms. Keep this in mind when choosing D3: espanso still flashes that window whenever it restarts for other reasons, such as a config change.

Note: you use Pinyin and Hangul input sources. While one of them is active, the keys you type go to the input method before they become text. Plan on typed triggers only working in the `us` layout. This affects options A and B equally.

Sub-decision if A — where the snippets are stored:
- **A1.** `snippets.toml` is the master copy. launcherd generates `~/.config/espanso/match/launcher.yml` from it. *(Recommended: our own format, and espanso can be swapped out later.)*
- A2. espanso's YAML is the master copy, and launcherd reads it directly.

### ✅ D4 — How direct paste sends the keystroke — **decided: A (Shell extension virtual keyboard)**
| Option | Pros | Cons |
|---|---|---|
| **A. Shell extension virtual keyboard (`Clutter` virtual input device)** | No permissions or prompts; knows exactly when focus returns | Only works while the extension is enabled |
| B. `ydotool` | Works with any compositor | Needs a running daemon and uinput access; timing is guesswork |
| C. RemoteDesktop portal | Official API | Asks for permission (sometimes again after a restart); more complex |

**Recommendation: A.** The extension is needed for clipboard watching anyway. Fallback when it isn't running: copy the item to the clipboard only, and show "copied — press Ctrl+V".

### ✅ D5 — File search backend — **decided: A (own in-memory index + inotify)**
| Option | Pros | Cons |
|---|---|---|
| **A. Own index in memory + `Gio.FileMonitor`/inotify** | Only the folders you choose; instant; predictable; no dependencies | We maintain it (small job) |
| B. GNOME `localsearch` (Tracker) over DBus/SPARQL | Already indexing; can search file contents | Its folder settings are shared with GNOME search; results can lag; harder to debug |
| C. Scan the folders on every query | Simplest | Slow on large Downloads folders |

**Recommendation: A.** Build a list of paths at startup, keep it current with inotify, and apply the same fuzzy matching used everywhere else. Configure include folders, exclude patterns (`node_modules`, `.git`, `*.part`) and a maximum depth.

### ✅ D6 — Configuration style — **decided: B (TOML file + Launcher Settings window)**, upgraded from A on 2026-09-24
- A. TOML files only, hot-reloaded when they change
- **B. TOML files plus a libadwaita preferences window** *(chosen: the file stays the single source of truth and can still be edited by hand)*
- C. Preferences window only (settings stored in GSettings)

### ✅ D7 — Clipboard history rules — **decided: the proposals below** (500 entries, 30 days, 20 MB images, password hints + app excludes, pause, dedupe, no primary selection)
Choose values for:
- Maximum entries (proposal: **500**) and maximum age (proposal: **30 days**). Pinned entries are never removed.
- Maximum image size to store (proposal: **20 MB**). Images are stored as PNG files in `clips/`, with a thumbnail cache.
- Privacy: skip content marked as a password (`x-kde-passwordManagerHint`, used by KeePassXC and Bitwarden); skip copies from apps you list by wm_class; provide a "pause recording" toggle.
- Skip duplicates: copying the same thing again moves it to the top instead of adding a new entry.
- Also record the primary selection (text you highlight)? Proposal: **no**.

### ✅ D8 — Default shortcuts — **decided: all use Super+Shift**
Super+Shift shortcuts are caught by GNOME before any app sees them, so they never clash with terminal or editor shortcuts the way Ctrl+Shift does.

**Existing Super+Shift shortcuts on this machine** (checked 2026-09-24 with `gsettings`, including the schemas of enabled extensions):

| Shortcut already in use | Owner |
|---|---|
| Super+Shift+**Space** | GNOME: previous input source (you use us / pinyin / hangul) |
| Super+Shift+**V** | clipboard-history@alexsaveau.dev extension: open its menu |
| Super+Shift+**P** | clipboard-history@alexsaveau.dev extension: private mode |
| Super+Shift+**0–9** | Ubuntu Dock: open a new window of dock app N |
| Super+Shift+**arrows / Home / End / PgUp / PgDn** | GNOME: move window to another monitor or workspace |
| Super+Shift+**Tab**, Super+Shift+**\`** | GNOME: switch apps or windows backward |
| Super+Shift+**Escape** | GNOME: cancel input capture |

All other letters (except V and P) and Return are free.

| Action | Shortcut | Notes |
|---|---|---|
| Main launcher (apps, quicklinks, web search) | **`Super+Shift+Return`** | Super+Shift+Space is taken by input-source switching, which you use |
| Clipboard history | **`Super+Shift+V`** | Taken over from clipboard-history@alexsaveau.dev: disable that extension when M4 ships. Until then, the launcher's clipboard mode is not available through a shortcut |
| Pause clipboard recording | **`Super+Shift+P`** | Same takeover as above (replaces that extension's "private mode") |
| Snippets | **`Super+Shift+S`** | free |
| File search | **`Super+Shift+F`** | free |
| Specific quicklinks or apps | optional `hotkey = "<Super><Shift>g"` per item in config | Only letters that aren't already taken |

Shortcuts are registered by `launcher install-shortcuts`, which writes GNOME custom keybindings through `gsettings`. It only adds or updates entries it owns (named with the prefix `launcher-`) and leaves your existing entries alone, including `custom0` (Ulauncher on Ctrl+Space; remove it once M1 replaces Ulauncher). **Before writing anything, it checks for clashes** with the same schemas listed above (`org.gnome.desktop.wm.keybindings`, `org.gnome.shell.keybindings`, `org.gnome.mutter.*`, media-keys, and the schemas of enabled extensions). If it finds one, it names the owner and refuses to install that shortcut unless you pass `--force`.

### ✅ D9 — Autostart / packaging — **decided: A (systemd `--user` service + `uv`)**
- **A. systemd `--user` service** *(chosen: restarts automatically after a crash, logs go to `journalctl --user -u launcher`)*
- B. XDG autostart `.desktop` file
- Install method: **`uv`** for the Python package, plus a `make install` target for the extension, service and desktop file.

### ✅ D10 — Converters (M6) — **decided 2026-09-28**
- **Trigger:** detected automatically in the main launcher; text that isn't a conversion adds nothing. No keyword or separate mode.
- **Keys:** Enter copies the answer, Alt+Enter pastes it into the window the launcher was opened from (like a snippet: not recorded, the previous clipboard entry is put back).
- **Ranking:** an answer scores 1.9: above any fuzzy match (even with a frecency boost), below an exact alias (2.0). A bare place name ("tokyo") ranks below every real match instead. Answers are never learned for frecency.
- **Dates:** English only, own parser, no dependency. Main row copies ISO `2026-11-28` (long form as subtitle), second row copies `Saturday, 28 November 2026`. Day counts ("days until …", "X to Y"). Fixed-date holidays only (Christmas, New Year, Halloween…), no lunar or moving ones.
- **Timezones:** "time in / <place> time / <place>", "<time> <place>" → local time, "<time> <place> to <place>". Cities, countries, IANA ids, abbreviations, UTC±N, plus an alias table for cities the tz database doesn't name. 12/24 h follows GNOME's `clock-format`.
- **Currency:** open.er-api.com (free, no key, ~160 currencies, daily), fetched in the background, cached on disk and refreshed every 6 h; typing never waits on the network and cached rates are used offline. ISO codes, currency names, shorthand (1.5k) and arithmetic; no symbols. Without a target, only the home currency is shown.
- **Settings:** a Converters page in Launcher Settings (`[converters]` in config.toml).

### ✅ D11 — Notes (M7) — **decided 2026-09-28**
- **Editor:** native Gtk.TextView with our own live formatting (no WebKit: only the GTK 3 build is installed, and a native view works best with the pinyin/hangul input methods).
- **Markers:** applied as you type and hidden; shown again on the line the cursor is on (live preview, like Obsidian/Typora). The file always stays plain markdown.
- **Storage:** one `.md` file per note in `~/Notes` (configurable), sub-folders as notebooks; named after the title; pins in `.notes.json`; deleting goes to the Trash.
- **Sidebar:** Pinned, Recent, then the folder tree; hideable (F9). No tags or full-text search for now.
- **Formatting:** headings, bullet/numbered lists, checkboxes, quotes, code blocks, horizontal lines, bold/italic/strike/code/highlight, links, pasted images.
- **Window:** a normal window in its own process (like Launcher Settings), Super+Shift+N.
- **Autosave:** 1 s after typing stops, as soon as another window is focused, on switching notes and on closing; nothing is written if the text is unchanged.
- **Extras:** outline of headings, export to PDF, notes in the launcher's search.


### ✅ D12 — Polish (M8) — **decided 2026-09-28**
- **Appearance:** one System / Light / Dark setting (`[ui] appearance`) for the launcher, Launcher Settings and Notes, applied live.
- **Setup check:** `launcher --doctor` and a Status page in Launcher Settings check the helper extension, espanso, python3-gi-cairo, shortcuts, the notes folder and exchange rates, each with a fix. `make install` runs it and prints problems (never fails the install).
- **Setup problems in the launcher:** the banner shows them too, not only config mistakes.
- **Crash notice:** after systemd restarts a crashed service, one notification; its button opens Settings → Status with the crash's log lines.
- **README:** a public GitHub page with screenshots from the nested shell; MIT license.
- **Stability:** no soak test; you report issues from daily use.
---

## 4. Behaviour details

### 4.1 Main search (M1, as built)
- A single input box. Results are gathered from all providers enabled in the current mode and ranked by `match score × (1 + 0.7 × frecency boost)`. The frecency boost is a counter that halves every 14 days, mapped into [0, 1) and stored in `~/.local/share/launcher/launcher.db`. Weight 0.7 lets a frequently used prefix match overtake an unused exact match, but never an exact alias.
- **Fuzzy matching:** exact > prefix > word-start substring > substring > letters in order. Letters scattered across a long name are rejected unless most of them start words (`vsc` → Visual Studio Code).
- **App aliases:** `[aliases]` maps an alias to a desktop id or app name (`code = "code.desktop"`, `ff = "Firefox"`). An exact alias match scores 2.0, above any fuzzy match.
- **Quicklinks and web search are one thing:** `[[quicklink]]` with `name`, `url`, optional `alias`/`icon`. A `{query}` in the URL makes it a search: `g cats` opens it with `cats` URL-encoded. Typing only the alias and pressing Tab completes to `g `.
- **Fallback searches:** quicklinks with `fallback = true` appear as "Search X for '…'" rows at the bottom for any typed text, in config order. They always get room within the result limit and are never boosted by frecency (Ulauncher's "default search" behaviour).
- **Ulauncher import:** `launcher --import-ulauncher >> ~/.config/launcher/config.toml` converts `shortcuts.json` (`%s` → `{query}`, default searches → `fallback = true`).
- **Keys:** Enter runs the item. Alt+Enter runs the second action (copy the URL for quicklinks; later milestones add reveal-in-folder and copy without pasting). Ctrl+1…9 picks a result directly. Tab completes. Esc hides.
- **Apps:** `Gio.AppInfo.get_all()` (Flatpak and Snap included, `NoDisplay` respected), refreshed through `Gio.AppInfoMonitor`. Matched on name, executable, and on generic name and keywords (substring matches only). In `apps` mode, an empty query lists apps by frecency.
- **Website icons:** quicklinks without `icon = …` show the site's icon, from Google's favicon service (`s2/favicons?domain=…&sz=64`, which falls back to the parent domain when there's no icon). Icons are fetched in the background at startup and on config change, cached in `~/.cache/launcher/favicons/` for 30 days, and never block typing. Sites with no icon are retried after 3 days, network errors after 10 minutes. Turn off with `[ui] favicons = false`.
- **Interim shortcut (until M3):** the existing GNOME custom shortcut `custom0` (Ctrl+Space, previously `ulauncher-toggle`) now runs `~/.local/bin/launcher`. M3's `install-shortcuts` takes over from it.
- **Launching survives launcher restarts:** each launched app (and the browser opened for a URL) is spawned by the launcher, then moved into its own systemd scope `app-launcher-<id>-<pid>.scope`, as GNOME Shell does. Otherwise `systemctl --user restart launcher` would kill every app opened through it. The app's output goes to /dev/null, not the launcher's journal. Actions run *before* the window hides, because GNOME only honours the xdg-activation token (which gives the new app focus) from the focused window.

### 4.1b File search (M2, as built)
- `[files]` sets `folders` (default `~/Downloads`, `~/Documents`), `exclude` (glob patterns matched against names anywhere below, e.g. `.git`, `node_modules`, `*.part`), `max_depth` (default 8 levels) and `show_hidden`.
- **Index:** at startup (and on config change) a background thread scans the folders without following symlinks. The result is then owned by the main thread. One `Gio.FileMonitor` per folder reports changes, and any event re-reads just that folder after a 300 ms debounce, so new downloads show up within about half a second. Safety limits are 200,000 entries and 20,000 watched folders; beyond those it logs a warning instead of eating memory or inotify watches.
- **Search:** fuzzy match on the file name (or on the path below the folder if the query contains `/`), boosted by how recently the file was modified (half-life 7 days). A regex over all names joined into one string pre-filters in C, so only real candidates are scored in Python. Measured on 100k synthetic files: about 50 ms per query, about 120 ms for a single letter. An empty query lists the newest files.
- **Actions:** Enter opens with the default app for the file's content type, in its own systemd scope like app launches. Alt+Enter shows the file selected in Files (`org.freedesktop.FileManager1.ShowItems`, with an activation token). Files only appear in `files` mode (Super+Shift+F), not the main launcher.

### 4.1c Launcher Settings and hotkeys (built 2026-09-24, pulled forward from M3/M6)
- **Launcher Settings** (`launcher --settings`, "Launcher Settings" in the app grid or the launcher's results) is a separate libadwaita process with pages General, Shortcuts, Apps, Quicklinks, Snippets, Clipboard, Files and Converters. A bug in it can't crash the launcher, and a second invocation goes to the open window.
- **Ctrl+E** on an app, quicklink or fallback search in the launcher opens Settings at that item's alias/hotkey dialog.
- **Safe writes** (`config_writer.py`): every change re-reads `config.toml`, edits it with tomlkit (comments and layout kept), validates the whole result with the launcher's own parser, and only then replaces the file in one step. If a change would make the config invalid, it is refused and nothing is written. If the file was hand-edited into an invalid state, Settings pauses editing and shows why, instead of overwriting it.
- **Config format:** `[shortcuts]` (launcher, files, clipboard, snippets), `[apps."<desktop id>"]` with `alias`/`hotkey` (replaces `[aliases]`), and `hotkey` on `[[quicklink]]`. Hotkeys must use Super, Ctrl or Alt (or be F1–F24), and must be unique across the config; duplicate aliases and quicklink names are errors.
- **Hotkeys = GNOME custom keybindings** (`shortcuts.py`). On startup and every config reload, the launcher makes the `launcher-*` entries under `org.gnome.settings-daemon.plugins.media-keys` match the config, and never touches any other entry. A hand-made custom shortcut that runs the launcher with a key the config now owns is replaced (this is how the interim Ctrl+Space `custom0` became `launcher-main`). A key already used elsewhere is skipped and shown in the banner.
- **Clash checks** read about 200 shortcuts: GNOME's wm/shell/mutter/media-keys schemas, every *enabled* extension's schema (found through `metadata.json`, or its code when the schema isn't declared, as with Ubuntu Dock), and other custom shortcuts. The recorder dialog rejects clashes as you press them.
- **Item hotkeys** run `launcher --run app:<id>` / `--run quicklink:<name>`. A hotkey on a search quicklink opens the launcher with its alias typed (`g `).
- **Known limit:** keys GNOME has already grabbed never reach the recorder, so they can't be recorded. They would be clashes anyway, apart from re-recording the launcher's own current key.

### 4.2 Modes
`launcher --mode {all|apps|files|clipboard|snippets}` opens the window with only those providers, and a matching placeholder text and layout. For example, clipboard mode shows a list on the left and a large text or image preview on the right.

### 4.3 Clipboard history and paste (M4, as built)
- **Launcher Helper extension** (`extension/launcher-helper@jinwei.github.io`, about 200 lines of GJS) exports `io.github.jinwei.LauncherHelper` on GNOME Shell's bus name at `/io/github/jinwei/LauncherHelper`:
  - `ClipboardChanged(as mimetypes, s wm_class, s app_id)`: from `Meta.Selection::owner-changed`. It carries types and the source app only, never the content.
  - `GetClipboard(s) -> ay` and `SetClipboard(s, ay)`: through `St.Clipboard`, with bytes passed as `GLib.Bytes` so 20 MB images aren't unpacked in JS.
  - `GetFocusedWindow() -> (u id, s wm_class, s app_id)` and `Paste(u id, b shift) -> b`: `Paste` waits up to 700 ms for that window to have focus again (activating it if needed), then types Ctrl+V or Ctrl+Shift+V with a Clutter virtual keyboard.
  - Every method except `GetVersion` refuses callers that don't own `io.github.jinwei.Launcher`, so other apps can't use it to read the clipboard or inject keys.
- **Recording** (`clipboard_recorder.py`): text wins over images when both are offered (spreadsheets offer both). The recorder skips copies marked as secrets (`x-kde-passwordManagerHint`), copies from apps in `exclude_apps`, anything while paused, text over 1 MB, and images over `max_image_mb`. Thumbnails (64 px icon, 480 px preview) are made with GdkPixbuf in a worker thread. Re-copying moves an entry to the top, and when the launcher itself re-copies an entry, its original source app is kept.
- **Storage** (`clipboard_store.py`): `~/.local/share/launcher/clipboard.db` plus `clips/<sha256>.{data,icon.png,preview.png}`, all 0700/0600. Old entries are pruned after every addition. Pinned entries never expire.
- **Window:** clipboard mode (Super+Shift+V) has a scrolling list of up to 200 entries, a 380 px preview pane (wrapped text or the image), and a footer with key hints. Enter pastes into the window the launcher was opened from, Alt+Enter copies only, Ctrl+Shift+P pins, Ctrl+Delete deletes. A status line explains a missing extension or paused recording.
- **Manual deleting:** a trash button on each clipboard row (visible on hover or selection) or Ctrl+Delete. Launcher Settings → Clipboard edits the limits and the never-record / terminal app lists, shows the entry count, and deletes the last 15 minutes / hour / 24 hours / everything after confirming. Settings asks the running launcher to do the deleting (`clear-clipboard` action with seconds, 0 = all); pinned entries are always kept.
- **Pinned section:** pinned entries are listed first under a *Pinned* header, the rest under *Recent*. Searches keep the split.
- **Paste flow:** when the launcher opens, the daemon asks the extension for the focused window (ignoring its own). On Enter it calls `SetClipboard`, hides the window, then calls `Paste(window, shift)` 50 ms later. `shift` is true if the window's class or app id matches `[clipboard] terminal_apps`. If the extension isn't active, the entry is copied through GTK and a notification says to press Ctrl+V.
- **Pause:** Super+Shift+P (`launcher --clipboard-pause`, or the Pause/Resume command) toggles recording and shows a notification. It isn't persisted, so recording is always back on after a restart.
- **Testing without logging out:** `tools/nested-shell.sh` runs a private headless GNOME Shell (`--headless --virtual-monitor`) with its own session bus and settings. `make test-extension` runs 13 extension checks there, and `tools/nested-shell.sh .venv/bin/python tools/nested_e2e_test.py [snapshot dir]` runs 12 end-to-end checks with the real daemon and a GTK test app (copy, image, paste back, pause, terminal paste).

### 4.4 Snippets (M5, as built)
- **Storage:** `~/.config/launcher/snippets.toml` (`[[snippet]]` with `name`, `body`, optional `trigger`, `alias`, `hotkey`). It is loaded and validated together with config.toml: names and triggers must be unique, triggers can't contain spaces and need at least 2 characters, and aliases and hotkeys share the same namespace as apps and quicklinks. Launcher Settings → Snippets edits it (multi-line editor, placeholder buttons, hotkey recording).
- **espanso:** the daemon writes `~/.config/espanso/match/launcher.yml` (JSON-quoted YAML, no YAML library) for snippets that have a trigger. `{date:fmt}` becomes a `date` var, `{clipboard}` becomes a `clipboard` var, and `{cursor}` becomes `$|$`. The file is written only when its content changes, and removed when no snippet has a trigger.
- **Import:** `launcher --import-espanso` (or the "Move Here" button in Settings) moves simple matches (a single trigger, `replace`, date/clipboard vars, `label`) from espanso's base.yml into snippets.toml. It keeps `base.yml.bak`, and leaves matches that need espanso features (shell vars, `word`, regex, forms…) in base.yml. **Done 2026-09-24:** 7 moved, `:shell` kept.
- **Launcher:** Super+Shift+S opens snippets mode, which lists every snippet with a preview pane. Snippets also appear in the main search by name, trigger or alias (an exact alias ranks first). Enter pastes, Alt+Enter copies, Ctrl+E edits, and "New Snippet “…”" opens Settings with the name filled in. A snippet hotkey runs `launcher --run snippet:<name>` and pastes into the focused window.
- **Paste flow:** the daemon fills in the placeholders (`{clipboard}` read through the extension), puts the text on the clipboard, and pastes it like a clipboard entry. The recorder skips the pasted text, so snippets never enter the history. 500 ms later, the entry that was on the clipboard before is put back, if it was a recorded entry (never a password or an excluded app's copy). `{cursor}` presses Left through the extension's `MoveCursorLeft` (extension v2, needs a new login).
- **Tests:** `tests/test_snippets.py` (placeholders, YAML, config validation, writer round trips, import). The nested end-to-end test adds 6 snippet checks: {clipboard}, {cursor}, not recorded, clipboard put back, `--run snippet:`, and espanso regeneration after an edit.

Format:
```toml
[[snippet]]
name = "Email signature"
trigger = ";sig"          # typed expansion (via espanso)
alias = "sig"             # launcher search
body = """
Best regards,
Jinwei
"""

[[snippet]]
name = "Today"
trigger = ";date"
body = "{date:%Y-%m-%d}"  # placeholders: {date:fmt}, {clipboard}, {cursor}
```
launcherd translates placeholders into espanso `vars` when it generates the YAML, and expands them itself when pasting directly.

### 4.5 Converters (M6, see D10)
Answers appear at the top of the main launcher while you type. Enter copies, Alt+Enter pastes (`Host.paste_text`, shared with snippets: the pasted text is skipped by the recorder and the entry that was on the clipboard before is put back). `[converters]` in config.toml turns each one on or off.

**Dates** (`dates.py` parser, `providers/dates.py`; built 2026-09-28). Everything is relative to today:
- Named days: today/now, tomorrow (tmr), yesterday, the day after tomorrow, the day before yesterday.
- Offsets with number words: `in 3 days`, `two months after today`, `3 days ago`, `a week from tomorrow`, `2 weeks and 3 days from now`, `one week after 26 October 2026`, `christmas - 3 days`, `today + 10d`. Units: days, weeks, fortnights, months, years.
- Weekdays: `friday` and `next friday` are the first Friday after today (never today), `this friday` is the one in the current Monday–Sunday week, `last friday` the most recent before today. Abbreviations (`fri`) only count after next/last/this, so `sun` or `wed` still searches apps.
- `next week/month/year` = today + 1 of them. `end of month`, `start of next month`, `end of next week`, `end of february`; `last monday of october`, `2nd tue in march 2027`.
- Written dates: ISO, day-first `25/12[/2026]`, `dec 25`, `25th of december 2027`. Without a year: the next one (today counts). A month name alone is not a date.
- Fixed-date holidays: new year('s day/eve), valentine's, april fools, halloween, christmas (eve), xmas, boxing day; optionally with a year.
- Day counts: `days until christmas`, `until friday`, `days since 2026-01-01`, `2026-10-01 to 2026-12-25`, `days between X and Y` → "88 days", subtitle "12 weeks 4 days · Mon 28 Sep 2026 → Fri 25 Dec 2026"; copies the number. In a "since" phrase, `friday`/`christmas` mean the last one before today.
- Adding months stops at the end of the month: Jan 31 + 1 month = Feb 28.
- **Tests:** `tests/test_dates.py` (~160 cases); the nested end-to-end test checks that Alt+Enter on `tomorrow` pastes the date, that it isn't recorded, and that the clipboard is put back.

**Timezones** (`timezones.py`, `providers/timezones.py`; built 2026-09-28):
- `time in tokyo`, `what time is it in tokyo`, `tokyo time`, `now in tokyo` → "11:30", subtitle "Tokyo · Mon 28 Sep · 1h ahead of you · UTC+9". A place alone (`tokyo`) gives the same row with score 0.2, so any real app/link/snippet match stays above it; bare names must be full city or country names of 4+ letters (`sf`, `pst`, `utc` alone do nothing).
- `3pm tokyo`, `tokyo 3pm`, `15:00 in seoul`, `tomorrow 9am new york`, `25 dec 8pm new york` → the local time: "03:00 (next day)", subtitle "Kuala Lumpur, Mon 28 Sep · from 15:00 New York, 12h behind". The day (today, or a day phrase from the date parser) is the *place's* day.
- `3pm pst to london`, `3pm pst in london`, `3pm to london` (from local) → the time in the second place.
- Clock input: `3pm`, `3 p.m.`, `9:30am`, `15:00`, `noon`, `midnight`, `now`; a bare number is not a time. Output follows GNOME's `clock-format` (read through Gio.Settings on each query). Enter copies the time only, without "(next day)".
- Places, first match wins: UTC offsets (`utc`, `gmt-5`, `utc+5:30`), abbreviations mapped to zones so DST applies (`pst` in July is PDT; CST = US Central, IST = India), an alias table of cities the tz database lacks (Beijing, Mumbai, San Francisco, NYC, KL…), every tz city and zone id, then countries from `/usr/share/zoneinfo/{zone,iso3166}.tab` (multi-zone countries use a main zone: United States → New York, Australia → Sydney, and the label says so). Building the index takes ~15 ms, done at config time.
- A wall time skipped by DST moves on by the gap (2:30 on spring-forward day → 3:30).
- The local zone comes from `$TZ` or the `/etc/localtime` link, read on each query.
- **Tests:** `tests/test_timezones.py` (~140 cases: places, offsets, abbreviations with DST, day phrases, formatting, ranking against apps); the end-to-end test checks that Enter copies the time in `time in utc`.

**Currency** (`currency.py`, `providers/currency.py`; built 2026-09-28):
- `100 usd` → "407.44 MYR", subtitle "100 USD → MYR · 1 USD = 4.0744 MYR · rates from today". Only the home currency is shown; `100 myr` (already home) shows USD instead. `100 usd to/in/into/as/-> jpy`, `usd to myr` (1 unit).
- Input: ISO codes (any the rates know), names (`dollars`, `ringgit`, `won`, `yuan`, `yen`, `euros`, `pounds`, `singapore dollars`…; `peso`/`krone` need the country), `100usd`, `1,234.50`, shorthand `1.5k`/`2m`/`3 bn`, and arithmetic `(12+30)*3`, `2 x 19.99`, `2^10` through a small parser (no `eval`). No symbols (`$`, `RM`). Codes that are English words (`top`, `all`, `try`, `cup`…) only count in upper case or with a target, so `10 top` stays a search.
- Enter copies the plain amount (`225000`, no separators); the title uses separators. Two decimals, none for JPY/KRW/VND/IDR…, and 3 significant digits below 1 (`0.00300`).
- **Rates:** `https://open.er-api.com/v6/latest/USD` (ExchangeRate-API's keyless endpoint, updated daily; attribution: exchangerate-api.com), stored atomically in `~/.cache/launcher/rates.json`. `RatesCache` downloads in a worker thread when the rates are older than `refresh_hours` (default 6), at config time and when a currency query is typed; never for other typing. After a failure it waits 10 minutes; old rates keep working and the subtitle adds "(can't update: offline?)" once they are due. Before the first download a row says "no exchange rates yet" (Enter does nothing). New rates redraw an open window.
- Home currency: `home_currency` in `[converters]`, or else from the timezone's country (zone.tab → a country→currency table), else USD.
- **Tests:** `tests/test_currency.py` (~105 cases: parsing, arithmetic, formatting, the cache's refresh/back-off/in-flight rules with a fake clock, the API response and its failures, the provider); the end-to-end test seeds `rates.json` and checks that Enter on `1.5k usd to jpy` copies `225000`.

**Settings → Converters** (built 2026-09-28): a switch per converter; home currency (a searchable list of the codes in `rates.json`, "Automatic (MYR)" first); how often to download rates (1–168 h); when the rates were published and downloaded, with **Refresh Now**, which runs the daemon's `refresh-rates` action (the daemon owns the cache) and re-reads the file after 1, 4 and 12 s; and the ExchangeRate-API attribution link. The window's default width grew to 1140 px so all 8 page names fit. Checked by driving the real page in the nested shell (switch and combo write the config, the action reaches the daemon, a stopped daemon is reported); `tests/test_config_writer.py` covers the `[converters]` round trip.

### 4.6 Notes (M7, see D11)
**Part 1 — window, storage, sidebar, autosave** (built 2026-09-28):
- `launcher --notes` (Super+Shift+N, "Notes" in the app grid) runs its own single-instance app, `io.github.jinwei.Launcher.Notes`. A second call brings the window to the front; `--open NOTE` opens a note (relative to the notes folder, or an absolute path inside it), `--new TITLE` starts one.
- **Storage** (`notes_store.py`, pure): `[notes] folder` (default `~/Notes`, set in Settings → General). Only `*.md` files are notes; names starting with `.` and `attachments` folders are hidden. The title is the first non-empty line without `#`/list markers (a bare `#` being typed counts as no title). New notes are `Untitled.md`, `Untitled 2.md`…; clashes are checked case-insensitively. Writes are atomic (temp file + fsync + rename).
- **Renaming follows the title** only when the title was edited in this window, so files made elsewhere (e.g. `my-file-name.md`) keep their names until you change their title. Pins follow renames, moves and folder renames.
- **Autosave** (`NoteSession`): 1 s after the last change; immediately when the compositor reports another window focused (the same `Gdk.ToplevelState.FOCUSED` signal the launcher uses, so input-method popups don't count); when switching notes; on close. Unchanged text is never rewritten. Text still being composed by an input method isn't in the buffer yet and is saved once committed. A note created empty and left empty is deleted instead of saved.
- **Other apps:** every folder is watched (Gio.FileMonitor, 300 ms debounce). A changed file reloads silently when there are no unsaved edits (cursor kept); with unsaved edits a banner offers Reload, and typing on keeps yours. A deleted/moved open note closes, or is written back if it had unsaved edits.
- **Window:** Adw.OverlaySplitView sidebar (Pinned, Recent (10), All Notes tree with expandable folders), toggled with F9 or the header button; below 640sp wide the sidebar overlays the text (half a screen beside slides). The text column is at most 820 px, centred. Right-click menus: Pin/Unpin, Rename…, Move To…, Move to Trash; folders: New Note Here, New Folder Here…, Rename…, Move to Trash (with a count). Ctrl+N new note (in the open note's folder), Ctrl+W close. Size, maximized, sidebar, expanded folders and the last note are kept in `~/.local/state/launcher/notes.json`.
- **Right-click menus** open one main-loop turn after they are created (a menu popped up in the frame it was made in is sized before its items are measured and has to scroll). The menu hangs off its row, never the list box (whose `remove_all()` loops forever on a non-row child); the sidebar detaches it before rows are destroyed or the window closes (GTK crashes finalizing a row with a menu attached), and holds back rebuilds until an open menu closes.
- **Trash:** via GIO; if the Trash isn't available (e.g. notes on a system mount) the note stays and a toast says why.
- **Tests:** `tests/test_notes_store.py` (titles, names, tree, create/save/rename/move/delete, folders, pins, NoteSession rules, config). `tools/nested_notes_test.py` drives the real window in the nested shell (34 checks: menus show every item and survive a refresh, pause autosave and rename, save on focus loss, no write when unchanged, pins, folders, reload vs conflict banner, empty-note discard, Trash success and failure, close/restore, narrow layout, single instance).

**Part 2 — live block formatting** (built 2026-09-28; `notes_markdown.py` pure, `notes/editor.py`):
- The buffer always holds the plain markdown; saving writes exactly that. After each change every line is classified (heading, bullet, task, ordered, quote, rule, fence, code; code fences and list nesting span lines) and only lines whose text or kind changed are re-tagged (~1 ms for a few hundred lines, 15 ms for 6000).
- **Headings** `#`–`###` (4–6 look like 3): bigger bold text; `## ` is dimmed on the cursor's line and hidden elsewhere. `#` without a space stays text.
- **Lists:** `- `/`* `/`+ ` bullets (drawn •, ◦, ▪ by depth), `1. `/`1) ` numbers (kept visible, bold), `- [ ] ` checkboxes (drawn; ticked ones struck through), `[] `/`[ ] `/`- [] ` typed at a line start become `- [ ] `. Markers are hidden and never hold the cursor (Home or an arrow key into one moves to the text). Nesting depth follows the file's own indentation (tabs, 2 or 4 spaces); Tab adds a tab like Obsidian.
- **Keys:** Enter continues a list or quote (the next number, an unticked box); Enter on an empty item outdents it, or ends the list at the top level; Tab / Shift+Tab indent / outdent the selected items; Backspace at the start of an item's text outdents it or drops its marker (and turns a heading back into text); Ctrl+Enter or a click ticks a checkbox. While an input method is composing, these keys are left to it.
- **Numbering:** ordered lists around the edited lines renumber from their first item (deeper items and blank lines don't break a list; a paragraph or bullet does). Opening a note never rewrites it.
- **Blocks:** `> ` quotes (accent bar, italic; only once the space after `>` is typed, though a bare `>` right after a quote line continues it), `---`/`***`/`___` rules (drawn; the text shows on the cursor's line), fenced code (monospace on a rounded background; fences dimmed; `` ``` `` + Enter adds the closing fence and puts the cursor inside; nothing inside is formatted).
- **Undo:** automatic edits (renumbering, `[] `) are made in the buffer's `end-user-action` handler, before GTK closes the undo step, so one Ctrl+Z undoes a keystroke together with them; undo/redo themselves never trigger new automatic edits. Editing inside `changed` was unsafe (a deletion is still in progress there).
- **Drawing:** bullets, checkboxes, quote bars, rules and code backgrounds are painted in `snapshot_layer(BELOW_TEXT)` in buffer coordinates. Bullets sit on the text's baseline and checkboxes are centred between the baseline and cap height; the baseline comes from a Pango layout of the line's own first characters (so CJK rows line up), because the text view reports a 0 height for positions right after hidden markup and on empty items; indents are tag margins that include the centring margin (a tag's left margin replaces the view's, it doesn't add to it), updated when the window is resized.
- **Tests:** `tests/test_notes_markdown.py` (51: kinds, markers, code fences, nesting, continuation, indent, markers, checkbox, shorthand, renumbering). `tools/nested_notes_editor_test.py` types into the real editor in the nested shell (48 checks: every rule above, bullets and checkboxes not moving when text is typed after them and aligned with it, one-step undo, checkbox click, IME composing, CJK, no rewrite on open, saved text).

**Part 3 — inline styles, links, pictures** (built 2026-09-28):
- **Inline styles** (`notes_markdown.inline_spans`, pure): `**bold**`/`__bold__`, `*italic*`/`_italic_` (not inside words: `snake_case` stays), `***both***`, `~~strike~~`, `` `code` ``, `==highlight==`, `<u>underline</u>`; `\*` escapes. Applied once the closing marker is typed; markers are dimmed on the cursor's line and hidden elsewhere (like heading `#`). Nothing inside code spans, code blocks or link addresses is styled; link text can be bold. Parsing starts after a list marker, so `* item *x*` doesn't pair the bullet.
- **Shortcuts** wrap the selection (or insert empty markers with the cursor between) and unwrap it when pressed again: Ctrl+B bold, Ctrl+I italic, Ctrl+U underline (`<u>`, which markdown readers render), Ctrl+E code, Ctrl+Shift+X strike, Ctrl+Shift+H highlight. One Ctrl+Z undoes them.
- **Links:** `[text](url)` shows only the text (accent, underlined); `<https://…>` and bare `https://…`/`www.…` URLs are links too (trailing punctuation excluded). Ctrl+K turns the selection into `[text]()` with the cursor in the address (or `[](url)` if a URL is selected). Hovering shows the address; Ctrl+click opens web links in the browser, `.md` links to other notes in Notes, and other files in their default app.
- **Pictures:** Ctrl+V of a picture (image/* data from a screenshot tool, or a texture) when the clipboard has no text saves it as `attachments/<Note-name>-<date-time>.png` next to the note and inserts `![](attachments/….png)` on its own line. A line holding only an image shows the picture (scaled to the text column, at most 600 px tall, rounded corners) below its text, which is hidden off the cursor's line; the room comes from a `pixels-below-lines` tag, so the buffer never contains anything but the markdown. Web images aren't downloaded. Moving a note to another folder moves the attachments it links to.
- **Tests:** `tests/test_notes_markdown.py` (87 with inline styles, links, image lines, wrapping, attachment names); `tests/test_notes_store.py` (attachments follow a move); `tools/nested_notes_editor_test.py` (87 checks: styles while typing, shortcuts and undo, Ctrl+K, links under the pointer, opening a note link, a real paste of a texture and of image/png bytes saving the file and reserving the picture's height, text paste unchanged).
- **Hiding markup** (fix, 2026-09-28): hidden markup is 1-unit transparent text (tabs get a 1 px stop), not GtkTextView `invisible` text. With invisible text GTK 4.22 mixes display and buffer byte offsets when it maps screen positions to characters (clicks, hovering, moving between display lines, an input method's preedit), which placed clicks a few characters off on lines with hidden markup and, in the user's session, aborted Notes with `Byte index … is off the end of the line`. A line's markup is revealed in an idle callback after the cursor moves, not while GTK is still handling the click. Left at the start of an item's text jumps to the previous line instead of into the hidden marker.
- **Tests:** `tools/nested_notes_input_test.py` drives Notes with real pointer and keyboard events through a test-only shell extension (`tools/nested-input@jinwei.github.io`: virtual pointer/keyboard, input-source switching, window frame over D-Bus), which `tools/nested-shell.sh` enables (18 checks: typing lands where a line with hidden markup was clicked, clicking between lines while typing, clicks past line ends, hovering links, Ctrl+click, a real checkbox click, Home/Left/Right around a hidden marker, typed markdown, the saved file). The misplaced click fails on the old code; the abort itself didn't reproduce in the nested shell, even with IBus Pinyin/Hangul composing.


**Part 4 — outline, launcher search, PDF** (built 2026-09-28):
- **Outline:** the header's list button or Ctrl+Shift+O opens the note's headings in a popover (indented by level, inline markers removed, none from code blocks), with the cursor's section selected and focused, so arrow keys + Enter work at once. Picking one puts the cursor at the heading's text and scrolls it to the top of the view.
- **Launcher search:** notes appear in the main search (`all` mode, after snippets), matched by title or file name with the usual fuzzy scoring; the subtitle is the folder and when it was edited. Enter opens the note in Notes (`launcher --notes --open <path>`, with the activation token, so a running Notes window comes to the front); Alt+Enter shows the file in the file manager. The folder is rescanned at most once a second while typing, and `NotesStore` re-reads a note's title only when its mtime or size changed. Titles read as the note does: inline markers are dropped (`## Week 2 **labs**` → "Week 2 labs") in the sidebar, the header and the launcher. The Notes window's title names the open note ("Lecture 3 – Notes") for Alt+Tab and the overview.
- **PDF export:** "Export to PDF…" in the note's menu or Ctrl+Shift+E asks where to save (`<note>.pdf`, first in Documents, then the last folder used). `notes/pdf.py` lays the note out on A4 with Pango and draws it with cairo, line by line as the editor shows it: headings (the PDF's bookmarks, nested by level, kept with the next lines), bullets by depth, numbers in the gutter, checkboxes (ticked ones struck through), quote bars, rules, code blocks on a grey background (nothing styled inside), inline styles, pictures scaled to the column (at most 420 pt tall; a missing or web picture prints its link, dimmed) and page numbers. Long paragraphs break between lines across pages. Web links are clickable; links to notes or files aren't (they only open inside Notes). The UI font is used, so CJK text prints. It is written to a temporary file and renamed, so a failed export leaves an existing PDF alone; a toast offers to open it. Needs the system package `python3-gi-cairo` (PyGObject's cairo bridge, which `apt autoremove` once removed): Notes checks for it before asking where to save and says what to install, `make install` warns when it is missing, and any other export error is shown as a toast rather than failing silently.
- **Tests:** `tests/test_notes_pdf.py` (the text as it reads with no markup, pdfinfo metadata and A4, clickable web links only, bookmarks, a long note over several pages, a large picture, an empty note, unwritable path and a failed export keeping the old file); `tools/nested_notes_input_test.py` (the menu item, Ctrl+Shift+E, a real export with the note's picture, the Open toast, the remembered folder, a failure toast); the real save dialog was checked to open in the nested shell. `tests/test_notes_provider.py` (title and file-name matches, hidden and attachment files skipped, open/reveal, the 1 s rescan with a fake clock, a changed title, a missing folder, only changed notes re-read); `tools/nested_e2e_test.py` (typing "fourier" in the real launcher and pressing Enter opens that note's Notes window). `tests/test_notes_markdown.py` (`plain_text`, `outline`, `section_at`); `tools/nested_notes_input_test.py` (Ctrl+Shift+O, the list and selection, no scrolling for a short list, Down + Enter and a real click jump and scroll, typing continues in the editor).

### 4.7 Polish (M8, see D12)
- **Appearance** (built 2026-09-28): `[ui] appearance = "system" | "light" | "dark"` (Settings → General → Appearance → Style). Each process sets libadwaita's colour scheme from it (`appearance.py`): the launcher on start and config reload, Launcher Settings whenever it loads or saves the file, Notes on start and through its own watch on the config folder. "system" follows GNOME's Style setting live. An invalid value is a config error, so the last good appearance stays.
- **Tests:** `tests/test_config.py`, `tests/test_config_writer.py` (values, validation, round trip into an older config); `tools/nested_appearance_test.py` (14 checks: Notes follows a change written by another process and recolours its text, System follows GNOME's dark style both ways, an invalid value changes nothing, the Style row shows the choice, applies at once and saves, Light overrides a dark GNOME); `tools/nested_e2e_test.py` (the running launcher turns dark and back).
---

## 5. Project layout

```
linux-launcher/
├── PLAN.md
├── pyproject.toml
├── Makefile                      # install / uninstall / dev / test / lint
├── src/launcher/
│   ├── __main__.py               # CLI entry: fast client path, else start the primary instance
│   ├── client.py                 # talks to the running instance via `gdbus` (no gi import)
│   ├── app.py                    # Adw.Application: actions, config hot-reload, Host for providers
│   ├── window.py                 # GTK4 window, search entry, result list, preview pane
│   ├── engine.py                 # modes → providers, merge + rank results
│   ├── ranking.py                # fuzzy match (+ frecency in M1)
│   ├── config.py                 # TOML load/validate (pure Python)
│   ├── paths.py                  # XDG locations
│   ├── style.css
│   ├── store.py                  # SQLite (frecency, clipboard, snippets metadata)
│   ├── launching.py              # spawn apps/URIs into their own systemd scopes
│   ├── importers.py              # Ulauncher shortcuts.json → [[quicklink]]
│   ├── favicons.py               # website icons, cached, fetched in background
│   ├── files_index.py            # file index: scan, rescan_dir, search (pure Python)
│   ├── file_watcher.py           # scan thread + Gio.FileMonitor per folder
│   ├── shortcuts.py              # sync launcher-* GNOME custom keybindings, clash detection
│   ├── accel.py                  # accelerator parsing/normalizing (pure)
│   ├── config_writer.py          # validated, comment-preserving config edits (tomlkit)
│   ├── helper.py                 # D-Bus client for the Launcher Helper extension
│   ├── clipboard_store.py        # clipboard history storage (pure)
│   ├── clipboard_recorder.py     # ClipboardChanged → store, privacy rules, thumbnails
│   ├── settings/                 # Launcher Settings app (window, dialogs, debug renderer)
│   ├── notes_store.py            # notes on disk: tree, save/rename, pins, NoteSession (pure)
│   ├── notes_markdown.py         # markdown line kinds and editing rules (pure)
│   ├── notes/                    # Notes app: app.py, window.py, sidebar.py, editor.py
│   └── providers/
│       ├── base.py               # Result, Provider and Host protocols
│       ├── commands.py           # built-in: reload config, open config, quit
│       ├── apps.py
│       ├── quicklinks.py         # quicklinks + fallback web searches
│       ├── files.py
│       ├── clipboard.py
│       ├── currency.py           # currency answers (parser and rates cache in ../currency.py)
│       ├── dates.py              # date answers (parser in ../dates.py)
│       ├── timezones.py          # time answers (places and parsing in ../timezones.py)
│       └── snippets.py           # + espanso YAML generator
├── extension/launcher-helper@jinwei.github.io/
│   ├── metadata.json             # shell-version: ["50"]
│   └── extension.js              # clipboard watch/read/write, focused window, paste
├── tools/                        # nested headless GNOME Shell harness + e2e tests
├── data/
│   ├── launcher.service          # systemd --user unit
│   └── io.github.jinwei.Launcher.desktop
└── tests/                        # provider, ranking, config, espanso-gen tests (no GUI needed)
```

---

## 6. Milestones

Each milestone ends with something you can use every day. Use the launcher yourself before starting the next one.

| M | Deliverable | Decisions needed | Done when |
|---|---|---|---|
| **M0** ✅ | Skeleton: Gio.Application single instance, window that hides and shows, config loading, systemd unit, `make dev` | ✅ all decided | `launcher` shows or hides the window within 100 ms from the command line. **Done 2026-09-24:** about 60 ms end to end (command + window focused) |
| **M1** ✅ | Apps, quicklinks, web search, aliases, frecency ranking, Ulauncher import | — | Ulauncher can be uninstalled. **Built 2026-09-24:** waiting on your daily-use check |
| **M2** ✅ | File search over configured folders, open and reveal-in-folder actions | ✅ D5 = A | Finding a new download takes under 1 s after it lands. **Built 2026-09-24:** new files show up in about 0.5 s; Super+Shift+F added early |
| **M3** ✅ | Shortcut sync with clash check; per-mode and per-item hotkeys (done early, through Launcher Settings) | ✅ D8 | All mode shortcuts work from any app. **Done:** recording and hotkeys confirmed by hand |
| **M4** ✅ | Shell extension; clipboard history (text and images), preview pane, pin, delete, direct paste | ✅ D4, D7 | Copying an image in Firefox → Super+V → Enter pastes it into a chat app. **Built 2026-09-24:** 13/13 extension and 12/12 end-to-end checks in a nested shell; waiting on your first login with the extension |
| **M5** ✅ | Snippets: launcher search and paste; espanso YAML generation; placeholders | ✅ D3 | `;sig` expands in every app; the same snippet can be pasted from the launcher. **Built 2026-09-24:** 20/20 end-to-end and 14/14 extension checks in a nested shell; espanso loads the generated file; waiting on your check and a new login for extension v2 |
| **M6** ✅ | Converters: dates in words, timezones, currency, Settings page | ✅ D10 | `tomorrow`, `3pm tokyo` and `100 usd` answer in the main launcher, currency works offline from cached rates. **Built 2026-09-28:** ~350 new unit tests; 26/26 end-to-end checks in a nested shell; waiting on your daily-use check |
| **M7** | Notes: live-markdown editor, sidebar, autosave, outline, PDF, launcher search | ✅ D11 | Taking a full lecture's notes needs no manual save and no markdown syntax on screen. **Built 2026-09-28:** parts 1 (window, storage, sidebar, autosave), 2 (live block formatting), 3 (inline styles, links, pictures) and 4 (outline, launcher search, PDF export); clicks and hovering on lines with hidden markup fixed; waiting on your check |
| **M8** | Polish: themes, error notifications, README | ✅ D12 | Used daily for 2 weeks with no restarts needed |

### Stability rules (apply throughout)
- Providers are plain Python with no GTK imports, so they can be unit tested without a display.
- If a provider crashes, it is logged and skipped. It never takes down the window.
- The extension only exposes the DBus methods and signals listed in §2. If it isn't running, features fall back to copy-only.
- `metadata.json` declares support only for the GNOME versions it has been tested on. Check it after each GNOME upgrade.
- All state is kept in SQLite with WAL mode enabled, so a crash or power loss can't corrupt history.

---

## 7. Risks / open questions
- ~~**Wayland focus:**~~ Tested in M0: GNOME 50 gives the window focus even without an activation token (64 ms on the first show, about 30 ms after that). The client still forwards `XDG_ACTIVATION_TOKEN` if one is set, and the window logs a warning if it ever fails to get focus. Check this again from a real shortcut in M3.
- **Startup cost:** importing PyGObject takes about 140 ms, so `launcher` never imports it when a daemon is already running. It calls `org.gtk.Actions.Activate` through `gdbus` instead (about 50 ms in total).
- **Focus loss vs. Shell popups:** GTK's `is-active` follows keyboard focus, which GNOME Shell also takes while its own popups are open (the Super+Space input switcher, Alt+Tab, polkit). Hiding on that closed the launcher whenever you switched input source. The launcher now hides only when the compositor says another window is focused (`Gdk.ToplevelState.FOCUSED`). Tested with a polkit dialog (stays open) and a new window (hides).
- **Input methods:** while Pinyin or Hangul is composing text, the window leaves Enter, the arrow keys and Esc to the input method. The window tracks this with GtkText `preedit-changed`. Check it by hand with real Pinyin input.
- ~~**Clipboard watching in GNOME 50:**~~ Not needed: the extension watches `Meta.Selection` directly, and was tested in a nested GNOME Shell 50.
- ~~**espanso on Wayland**~~ needs its uinput and evdev permissions set up. Checked in M5: running, and it loads the generated launcher.yml.
- **Image paste** only works if the target app accepts `image/png` from the clipboard. Some Electron apps only accept file URIs. We could also offer `text/uri-list` pointing at the stored PNG.

---

## 8. Decision log

| ID | Decision | Chosen | Date | Notes |
|---|---|---|---|---|
| D1 | Language/toolkit | Python + PyGObject (GTK4/libadwaita) | 2026-09-24 | |
| D2 | UI host | GTK4 window + thin Shell extension | 2026-09-24 | |
| D3 | Typed expansion | espanso (A1): snippets.toml is the master copy; launcher writes espanso's launcher.yml | 2026-09-24 | IBus ruled out (pinyin/hangul in use). M5 check: espanso takes focus briefly whenever the snippet file changes; accepted because it only happens on save and writes are skipped when nothing changed. Existing base.yml matches moved in (backup kept) |
| D4 | Paste keystroke | Shell extension virtual keyboard (A) | 2026-09-24 | Extension only answers the launcher's bus name owner |
| D5 | File search backend | Own in-memory index + Gio.FileMonitor (A) | 2026-09-24 | |
| D6 | Config style | TOML + Launcher Settings window (B) | 2026-09-24 | Every GUI edit is validated before it is written; comments preserved (tomlkit) |
| D7 | Clipboard rules | 500 / 30 days / 20 MB; secret hint + exclude_apps; pause; dedupe; no primary | 2026-09-24 | Pinned entries never expire; list is newest-first |
| D8 | Shortcuts | Super+Shift + Return / V / P / S / F; main launcher kept on Ctrl+Space | 2026-09-24 | All editable in Launcher Settings with clash checks |
| D9 | Autostart/packaging | systemd --user service + uv | 2026-09-24 | |
| D10 | Converters | Auto-detect in main search; Enter copies / Alt+Enter pastes; own English date parser; open.er-api.com with disk cache; home currency only; 12/24 h from GNOME; Settings page | 2026-09-28 | Fixed-date holidays only |
| D11 | Notes | Native GtkTextView live preview; .md files in ~/Notes; folders + pinned/recent; normal window, Super+Shift+N; autosave on pause and focus loss | 2026-09-28 | Part 1 built |
| D12 | Polish | One System/Light/Dark setting; setup check (`--doctor`, Status page, after install); setup problems in the launcher; crash notice; public README, MIT | 2026-09-28 | Stability issues reported from daily use |
