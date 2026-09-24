# PyGObject/GTK/libadwaita come from the system Python, so every venv is created from
# /usr/bin/python3 with --system-site-packages.
PYTHON      := /usr/bin/python3
APP_ID      := io.github.jinwei.Launcher
VENV        := .venv
INSTALL_VENV := $(HOME)/.local/lib/launcher
BIN_DIR     := $(HOME)/.local/bin
APPS_DIR    := $(HOME)/.local/share/applications
UNIT_DIR    := $(HOME)/.config/systemd/user

.PHONY: venv dev test lint format install uninstall logs install-espanso-fix uninstall-espanso-fix

venv: $(VENV)/.done
$(VENV)/.done: pyproject.toml
	uv venv --quiet --allow-existing --python $(PYTHON) --system-site-packages $(VENV)
	uv pip install --quiet --python $(VENV)/bin/python -e . --group dev
	touch $@

# Run in the foreground with debug logging (stops the installed service first).
dev: venv
	-systemctl --user stop launcher.service 2>/dev/null
	$(VENV)/bin/launcher --daemon --debug

test: venv
	$(VENV)/bin/pytest -q

lint: venv
	$(VENV)/bin/ruff check src tests
	$(VENV)/bin/ruff format --check src tests

format: venv
	$(VENV)/bin/ruff check --fix src tests
	$(VENV)/bin/ruff format src tests

install:
	uv venv --quiet --allow-existing --python $(PYTHON) --system-site-packages $(INSTALL_VENV)
	uv pip install --quiet --python $(INSTALL_VENV)/bin/python --reinstall .
	install -d $(BIN_DIR)
	ln -sf $(INSTALL_VENV)/bin/launcher $(BIN_DIR)/launcher
	install -Dm644 data/$(APP_ID).desktop $(APPS_DIR)/$(APP_ID).desktop
	install -Dm644 data/launcher.service $(UNIT_DIR)/launcher.service
	systemctl --user daemon-reload
	systemctl --user enable launcher.service
	systemctl --user restart launcher.service

uninstall:
	-systemctl --user disable --now launcher.service
	rm -f $(UNIT_DIR)/launcher.service $(APPS_DIR)/$(APP_ID).desktop $(BIN_DIR)/launcher
	rm -rf $(INSTALL_VENV)
	systemctl --user daemon-reload

logs:
	journalctl --user -u launcher.service -f

# Stop espanso restarting (and flashing a focus-stealing window) on every input-source
# switch. See contrib/espanso/README.md.
ESPANSO_SHIM := $(HOME)/.local/lib/launcher-espanso-shim
ESPANSO_DROPIN := $(UNIT_DIR)/espanso.service.d/stable-layout.conf

install-espanso-fix:
	install -Dm755 contrib/espanso/gsettings $(ESPANSO_SHIM)/gsettings
	install -Dm644 contrib/espanso/stable-layout.conf $(ESPANSO_DROPIN)
	systemctl --user daemon-reload
	systemctl --user restart espanso.service

uninstall-espanso-fix:
	rm -rf $(ESPANSO_SHIM) $(ESPANSO_DROPIN)
	systemctl --user daemon-reload
	systemctl --user restart espanso.service
