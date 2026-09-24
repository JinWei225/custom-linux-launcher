# espanso: stop restarts on input-source switches

espanso 2.4 on GNOME reads the keyboard layout from the first entry of
`org.gnome.desktop.input-sources mru-sources`. GNOME reorders that list every time you
switch input source (us → pinyin → hangul), so espanso thinks the layout changed and
restarts its worker. The new worker briefly opens an undrawn Wayland window (a
multicoloured flash) that steals focus from whatever you are typing in.

All configured sources type on the US layout, so `gsettings` here is a wrapper that
answers `[('xkb', 'us')]` for `org.gnome.desktop.input-sources` and passes everything
else through to `/usr/bin/gsettings`. `stable-layout.conf` is a systemd drop-in that
puts the wrapper first on espanso's PATH only.

    make install-espanso-fix     # install + restart espanso
    make uninstall-espanso-fix   # revert

If you add an input source with a different physical layout (e.g. `xkb de`), remove
this fix or change the wrapper's answer.
