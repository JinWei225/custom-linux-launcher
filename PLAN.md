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

### 🔶 D3 — Snippet expansion while typing (needed for M5) — **leaning A, check again in M3**

**Status 2026-09-24:** typing in all three input languages works with espanso plus the gsettings fix. Keep option A for now. During M3 (global shortcuts), check whether pressing the launcher's shortcuts also makes espanso restart or flash its window. If espanso still causes trouble there, switch to option B.
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

### 🔶 D4 — How direct paste sends the keystroke (needed for M4)
| Option | Pros | Cons |
|---|---|---|
| **A. Shell extension virtual keyboard (`Clutter` virtual input device)** | No permissions or prompts; knows exactly when focus returns | Only works while the extension is enabled |
| B. `ydotool` | Works with any compositor | Needs a running daemon and uinput access; timing is guesswork |
| C. RemoteDesktop portal | Official API | Asks for permission (sometimes again after a restart); more complex |

**Recommendation: A.** The extension is needed for clipboard watching anyway. Fallback when it isn't running: copy the item to the clipboard only, and show "copied — press Ctrl+V".

### 🔶 D5 — File search backend (needed for M2)
| Option | Pros | Cons |
|---|---|---|
| **A. Own index in memory + `Gio.FileMonitor`/inotify** | Only the folders you choose; instant; predictable; no dependencies | We maintain it (small job) |
| B. GNOME `localsearch` (Tracker) over DBus/SPARQL | Already indexing; can search file contents | Its folder settings are shared with GNOME search; results can lag; harder to debug |
| C. Scan the folders on every query | Simplest | Slow on large Downloads folders |

**Recommendation: A.** Build a list of paths at startup, keep it current with inotify, and apply the same fuzzy matching used everywhere else. Configure include folders, exclude patterns (`node_modules`, `.git`, `*.part`) and a maximum depth.

### ✅ D6 — Configuration style — **decided: A (TOML files, hot-reloaded)**
- **A. TOML files only, hot-reloaded when they change** *(chosen: easy to back up and track in git)*
- B. TOML files plus a libadwaita preferences window (can be added in M6)
- C. Preferences window only (settings stored in GSettings)

### 🔶 D7 — Clipboard history rules (needed for M4)
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

### 4.2 Modes
`launcher --mode {all|apps|files|clipboard|snippets}` opens the window with only those providers, and a matching placeholder text and layout. For example, clipboard mode shows a list on the left and a large text or image preview on the right.

### 4.3 Paste flow (M4, M5)
1. The shortcut fires. launcherd asks the extension which window has focus (`wm_class`) and remembers it.
2. You pick an item → launcherd calls `SetClipboard(mime, bytes)` on the extension (the Shell owns the clipboard, so it survives the window hiding) → the window hides.
3. The extension waits until the remembered window has focus again (timeout 500 ms), then sends:
   - `Ctrl+Shift+V` if the wm_class is in `terminal_classes` (Ptyxis, gnome-terminal, kitty, Alacritty, WezTerm …)
   - `Ctrl+V` otherwise.
4. Optional (config): restore the clipboard to what it was before, for snippet pastes, so snippets don't take over your clipboard.

### 4.4 Snippet format (if D3 = A1)
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
│   ├── shortcuts.py              # gsettings custom-keybinding installer
│   └── providers/
│       ├── base.py               # Result, Provider and Host protocols
│       ├── commands.py           # built-in: reload config, open config, quit
│       ├── apps.py
│       ├── quicklinks.py         # quicklinks + fallback web searches
│       ├── files.py
│       ├── clipboard.py
│       └── snippets.py           # + espanso YAML generator
├── extension/launcher-helper@local/
│   ├── metadata.json             # shell-version: ["50"]
│   └── extension.js
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
| **M2** | File search over configured folders, open and reveal-in-folder actions | D5 | Finding a new download takes under 1 s after it lands |
| **M3** | `launcher install-shortcuts` with clash check; per-mode shortcuts | ✅ D8 | All mode shortcuts work from any app |
| **M4** | Shell extension; clipboard history (text and images), preview pane, pin, delete, direct paste | D4, D7 | Copying an image in Firefox → Super+V → Enter pastes it into a chat app |
| **M5** | Snippets: launcher search and paste; espanso YAML generation; placeholders | D3 | `;sig` expands in every app; the same snippet can be pasted from the launcher |
| **M6** | Polish: preferences window (if D6 = B), themes, per-item hotkeys, error notifications, README | — | Used daily for 2 weeks with no restarts needed |

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
- **Clipboard watching in GNOME 50:** check whether Mutter now supports `ext-data-control`. If it does, the extension may not be needed for watching (it would still be used for pasting).
- **espanso on Wayland** needs its uinput and evdev permissions set up. Check `espanso status` before M5.
- **Image paste** only works if the target app accepts `image/png` from the clipboard. Some Electron apps only accept file URIs. We could also offer `text/uri-list` pointing at the stored PNG.

---

## 8. Decision log

| ID | Decision | Chosen | Date | Notes |
|---|---|---|---|---|
| D1 | Language/toolkit | Python + PyGObject (GTK4/libadwaita) | 2026-09-24 | |
| D2 | UI host | GTK4 window + thin Shell extension | 2026-09-24 | |
| D3 | Typed expansion | espanso (A), provisional | 2026-09-24 | IBus ruled out (pinyin/hangul in use). Check again in M3; option B if espanso still causes trouble |
| D4 | Paste keystroke | | | |
| D5 | File search backend | | | |
| D6 | Config style | TOML files, hot-reloaded | 2026-09-24 | Preferences window may come in M6 |
| D7 | Clipboard rules | | | |
| D8 | Shortcuts | Super+Shift + Return / V / P / S / F | 2026-09-24 | Disable clipboard-history@alexsaveau.dev at M4 |
| D9 | Autostart/packaging | systemd --user service + uv | 2026-09-24 | |
