"""Preferences window: reads/writes the real Back In Time profile config.

Adw 1.2 baseline (PreferencesWindow, PreferencesPage/Group, ActionRow,
EntryRow, ComboRow, SpinRow). Every control writes through
``config.Config`` setters and calls ``cfg.save()`` - no mock state.
"""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk  # noqa: E402

from . import backend as _backend  # noqa: E402

_MODES, _MODE_LABELS = _backend.snapshot_modes()


def _mode_index(mode):
    """Index in the mode list, appending unknown modes instead of losing them."""
    if mode and mode not in _MODES:
        _MODES.append(mode)
        _MODE_LABELS.append(mode)
    return _MODES.index(mode) if mode in _MODES else 0


class PreferencesWindow(Adw.PreferencesWindow):
    def __init__(self, app):
        super().__init__(transient_for=app.get_active_window(), modal=True,
                         title='Gestionar perfiles',
                         default_width=820, default_height=640)
        self.backend = app.backend
        self.cfg = app.backend.cfg

        if self.cfg is None:
            page = Adw.PreferencesPage()
            group = Adw.PreferencesGroup()
            row = Adw.ActionRow(title='Núcleo de Back In Time no disponible',
                                subtitle=self.backend.error or '')
            group.add(row)
            page.add(group)
            self.add(page)
            return

        self.add(self._general_page())
        self.add(self._include_page())
        self.add(self._exclude_page())
        self.add(self._autoremove_page())
        self.add(self._options_page())
        self.add(self._expert_page())

    def _save(self):
        try:
            self.cfg.save()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------- helpers
    def _switch_row(self, group, title, subtitle, get, set_):
        try:
            value = bool(get())
        except Exception:  # noqa: BLE001
            value = False
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        sw = Gtk.Switch(active=value, valign=Gtk.Align.CENTER)

        def on_toggle(b, _):
            try:
                set_(b.get_active())
                self._save()
            except Exception:  # noqa: BLE001
                pass

        sw.connect('notify::active', on_toggle)
        row.add_suffix(sw)
        row.set_activatable_widget(sw)
        group.add(row)
        return sw

    def _entry_row(self, group, title, get, set_, mono=True):
        row = Adw.EntryRow(title=title)
        try:
            row.set_text(str(get() or ''))
        except Exception:  # noqa: BLE001
            pass

        def on_apply(r):
            try:
                set_(r.get_text())
                self._save()
            except Exception:  # noqa: BLE001
                pass

        row.connect('apply', on_apply)
        group.add(row)
        return row

    # ------------------------------------------------------------- general
    def _general_page(self):
        page = Adw.PreferencesPage(title='General', icon_name='emblem-system-symbolic')

        mode_group = Adw.PreferencesGroup(title='Modo')
        try:
            current_mode = self.cfg.snapshotsMode()
        except Exception:  # noqa: BLE001
            current_mode = 'local'
        mode_row = Adw.ComboRow(title='Modo de instantáneas',
                                subtitle='Dónde se guardan las copias')
        mode_row.set_model(Gtk.StringList.new(_MODE_LABELS))
        mode_row.set_selected(_mode_index(current_mode))
        mode_row.connect('notify::selected', self._on_mode_changed)
        mode_group.add(mode_row)
        self.mode_row = mode_row

        path_row = Adw.ActionRow(title='Guardar instantáneas en')
        self.path_entry = Gtk.Entry(hexpand=True, valign=Gtk.Align.CENTER)
        try:
            self.path_entry.set_text(self.cfg.snapshotsPath())
        except Exception:  # noqa: BLE001
            pass
        choose = Gtk.Button(label='Cambiar…', valign=Gtk.Align.CENTER)
        choose.connect('clicked', self._choose_snapshot_path)
        path_row.add_suffix(self.path_entry)
        path_row.add_suffix(choose)
        mode_group.add(path_row)
        page.add(mode_group)

        # SSH
        self.ssh_group = Adw.PreferencesGroup(title='SSH')
        self._entry_row(self.ssh_group, 'Servidor',
                        self.cfg.sshHost, self.cfg.setSshHost)
        port_row = Adw.ActionRow(title='Puerto')
        port_spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(
            value=22, lower=1, upper=65535, step_increment=1),
            valign=Gtk.Align.CENTER)
        try:
            port_spin.set_value(int(self.cfg.sshPort()))
        except Exception:  # noqa: BLE001
            pass
        port_spin.connect('value-changed', lambda r: (
            self.cfg.setSshPort(str(r.get_value_as_int())), self._save()))
        port_row.add_suffix(port_spin)
        self.ssh_group.add(port_row)
        self._entry_row(self.ssh_group, 'Usuario',
                        self.cfg.sshUser, self.cfg.setSshUser)
        self._entry_row(self.ssh_group, 'Ruta remota',
                        self.cfg.sshSnapshotsPath, self.cfg.setSshSnapshotsPath)
        self._entry_row(self.ssh_group, 'Clave privada',
                        self.cfg.sshPrivateKeyFile, self.cfg.setSshPrivateKeyFile)
        page.add(self.ssh_group)
        self._update_ssh_visibility(current_mode)

        # Schedule
        sched_group = Adw.PreferencesGroup(title='Programación')
        sched_row = Adw.ComboRow(title='Programar instantáneas',
                                 subtitle='Entrada de cron gestionada por Back In Time')
        labels = ['Desactivado', 'Cada hora', 'Cada día', 'Cada semana',
                  'Cada mes', 'Cuando se conecte la unidad (udev)',
                  'Repetidamente (anacron)', 'Horas personalizadas']
        modes = self._schedule_modes()
        sched_row.set_model(Gtk.StringList.new(labels))
        try:
            cur = self.cfg.scheduleMode()
            sched_row.set_selected(modes.index(cur) if cur in modes else 0)
        except Exception:  # noqa: BLE001
            sched_row.set_selected(0)

        def on_sched(r, *_):
            self.cfg.setScheduleMode(modes[r.get_selected()])
            self._save()

        sched_row.connect('notify::selected', on_sched)
        sched_group.add(sched_row)
        page.add(sched_group)

        return page

    def _schedule_modes(self):
        c = self.cfg
        out = []
        for name in ('NONE', 'HOUR', 'DAY', 'WEEK', 'MONTH', 'UDEV',
                     'REPEATEDLY', 'CUSTOM_HOUR'):
            out.append(getattr(c, name, None))
        return [m for m in out if m is not None]

    def _on_mode_changed(self, row, *_):
        mode = _MODES[row.get_selected()]
        self.cfg.setSnapshotsMode(mode)
        self._save()
        self._update_ssh_visibility(mode)

    def _update_ssh_visibility(self, mode):
        self.ssh_group.set_visible('ssh' in mode)

    def _choose_snapshot_path(self, *_):
        dlg = Gtk.FileChooserNative(title='Elegir carpeta de destino',
                                    transient_for=self,
                                    action=Gtk.FileChooserAction.SELECT_FOLDER)

        def on_response(d, response):
            if response == Gtk.ResponseType.ACCEPT:
                f = d.get_file()
                if f:
                    self.path_entry.set_text(f.get_path())
                    self.backend.set_destination_path(f.get_path())
                    self._save()

        dlg.connect('response', on_response)
        dlg.show()

    # ------------------------------------------------------------- include
    def _include_page(self):
        page = Adw.PreferencesPage(title='Incluir',
                                   icon_name='folder-symbolic')
        group = Adw.PreferencesGroup(
            title='Carpetas y archivos a respaldar',
            description='rsync enlaza los archivos sin cambios: no ocupan '
                        'espacio extra.')
        self.include_group = group
        page.add(group)
        self._fill_include()

        add_row = Adw.ActionRow(title='Añadir…')
        box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        folder_btn = Gtk.Button(label='Carpeta…')
        folder_btn.connect('clicked', self._add_include,
                           Gtk.FileChooserAction.SELECT_FOLDER)
        file_btn = Gtk.Button(label='Archivo…')
        file_btn.connect('clicked', self._add_include,
                         Gtk.FileChooserAction.OPEN)
        box.append(folder_btn)
        box.append(file_btn)
        add_row.add_suffix(box)
        group.add(add_row)
        return page

    def _fill_include(self):
        # remove previous list rows (all but the last "add" row is rebuilt)
        for row in list(self._include_rows if hasattr(self, '_include_rows') else []):
            self.include_group.remove(row)
        self._include_rows = []
        try:
            entries = self.cfg.include()
        except Exception:  # noqa: BLE001
            entries = []
        for path, ftype in entries:
            row = Adw.ActionRow(title=path,
                                subtitle='carpeta' if ftype == 0 else 'archivo')
            rm = Gtk.Button(icon_name='user-trash-symbolic',
                            valign=Gtk.Align.CENTER, has_frame=False)
            rm.connect('clicked', self._remove_include, path, ftype)
            row.add_suffix(rm)
            self.include_group.add(row)
            self._include_rows.append(row)

    def _add_include(self, _btn, action):
        dlg = Gtk.FileChooserNative(title='Añadir a la copia',
                                    transient_for=self, action=action)

        def on_response(d, response):
            if response != Gtk.ResponseType.ACCEPT:
                return
            f = d.get_file()
            if not f:
                return
            path = f.get_path()
            ftype = 0 if action == Gtk.FileChooserAction.SELECT_FOLDER else 1
            try:
                entries = list(self.cfg.include())
            except Exception:  # noqa: BLE001
                entries = []
            entries.append((path, ftype))
            self.cfg.setInclude(entries)
            self._save()
            self._fill_include()

        dlg.connect('response', on_response)
        dlg.show()

    def _remove_include(self, _btn, path, ftype):
        entries = [e for e in self.cfg.include() if e != (path, ftype)]
        self.cfg.setInclude(entries)
        self._save()
        self._fill_include()

    # ------------------------------------------------------------- exclude
    def _exclude_page(self):
        page = Adw.PreferencesPage(title='Excluir',
                                   icon_name='action-unavailable-symbolic')
        group = Adw.PreferencesGroup(
            title='Patrones de exclusión',
            description='Comodines de rsync: * dentro de un nivel, '
                        '** a cualquier profundidad.')
        self.exclude_group = group
        page.add(group)
        self._fill_exclude()

        add_row = Adw.EntryRow(title='Nuevo patrón o ruta…')
        add_row.connect('apply', self._add_exclude)
        group.add(add_row)

        size_group = Adw.PreferencesGroup(title='Por tamaño')
        try:
            enabled = self.cfg.excludeBySizeEnabled()
            value = self.cfg.excludeBySize()
        except Exception:  # noqa: BLE001
            enabled, value = False, 500
        row = Adw.ActionRow(title='Excluir archivos mayores de')
        sw = Gtk.Switch(active=enabled, valign=Gtk.Align.CENTER)
        spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(
            value=value, lower=1, upper=999999, step_increment=50))
        spin.set_valign(Gtk.Align.CENTER)

        def save_size(*_):
            self.cfg.setExcludeBySize(sw.get_active(),
                                      int(spin.get_value()))
            self._save()

        sw.connect('notify::active', save_size)
        spin.connect('value-changed', save_size)
        row.add_suffix(spin)
        row.add_suffix(sw)
        size_group.add(row)
        page.add(size_group)
        return page

    def _fill_exclude(self):
        for row in getattr(self, '_exclude_rows', []):
            self.exclude_group.remove(row)
        self._exclude_rows = []
        try:
            entries = self.cfg.exclude()
        except Exception:  # noqa: BLE001
            entries = []
        for pattern in entries:
            row = Adw.ActionRow(title=pattern)
            rm = Gtk.Button(icon_name='user-trash-symbolic',
                            valign=Gtk.Align.CENTER, has_frame=False)
            rm.connect('clicked', self._remove_exclude, pattern)
            row.add_suffix(rm)
            self.exclude_group.add(row)
            self._exclude_rows.append(row)

    def _add_exclude(self, row):
        pattern = row.get_text().strip()
        if not pattern:
            return
        entries = list(self.cfg.exclude()) + [pattern]
        self.cfg.setExclude(entries)
        self._save()
        row.set_text('')
        self._fill_exclude()

    def _remove_exclude(self, _btn, pattern):
        entries = [e for e in self.cfg.exclude() if e != pattern]
        self.cfg.setExclude(entries)
        self._save()
        self._fill_exclude()

    # ---------------------------------------------------------- auto-remove
    def _autoremove_page(self):
        page = Adw.PreferencesPage(title='Autoeliminar',
                                   icon_name='user-trash-symbolic')
        cfg = self.cfg

        old_group = Adw.PreferencesGroup(title='Antigüedad')
        try:
            enabled, value, unit = cfg.removeOldSnapshots()
        except Exception:  # noqa: BLE001
            enabled, value, unit = True, 10, 80
        row = Adw.ActionRow(title='Eliminar instantáneas más antiguas de')
        sw = Gtk.Switch(active=enabled, valign=Gtk.Align.CENTER)
        spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(
            value=value, lower=1, upper=9999, step_increment=1))
        spin.set_valign(Gtk.Align.CENTER)
        combo = Gtk.DropDown.new_from_strings(['días', 'semanas', 'años'])
        units = [20, 30, 80]
        try:
            unit_val = int(unit.value if hasattr(unit, 'value') else unit)
        except Exception:  # noqa: BLE001
            unit_val = 80
        combo.set_selected(units.index(unit_val) if unit_val in units else 2)
        combo.set_valign(Gtk.Align.CENTER)

        def save_old(*_):
            cfg.setRemoveOldSnapshots(sw.get_active(), int(spin.get_value()),
                                      units[combo.get_selected()])
            self._save()

        sw.connect('notify::active', save_old)
        spin.connect('value-changed', save_old)
        combo.connect('notify::selected', save_old)
        for w in (spin, combo, sw):
            row.add_suffix(w)
        old_group.add(row)
        page.add(old_group)

        smart_group = Adw.PreferencesGroup(
            title='Eliminación inteligente',
            description='Tras cada instantánea se aplica este patrón de '
                        'conservación.')
        try:
            smart = cfg.smartRemove()
        except Exception:  # noqa: BLE001
            smart = (False, 2, 7, 4, 24)
        enabled, keep_all, keep_day, keep_week, keep_month = smart

        row_sw = Adw.ActionRow(title='Eliminación inteligente',
                               subtitle='Conservar solo lo que indica el patrón')
        sw = Gtk.Switch(active=enabled, valign=Gtk.Align.CENTER)
        row_sw.add_suffix(sw)
        row_sw.set_activatable_widget(sw)
        smart_group.add(row_sw)

        spins = {}

        def save_smart(*_):
            cfg.setSmartRemove(sw.get_active(), spins['all'].get_value_as_int(),
                               spins['day'].get_value_as_int(),
                               spins['week'].get_value_as_int(),
                               spins['month'].get_value_as_int())
            self._save()

        sw.connect('notify::active', save_smart)

        def spin_row(key, title, value):
            r = Adw.ActionRow(title=title)
            spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(
                value=value, lower=0, upper=999, step_increment=1),
                valign=Gtk.Align.CENTER)
            spin.connect('value-changed', save_smart)
            r.add_suffix(spin)
            smart_group.add(r)
            spins[key] = spin

        spin_row('all', 'Conservar todas durante (días)', keep_all)
        spin_row('day', 'Conservar una al día durante (días)', keep_day)
        spin_row('week', 'Conservar una a la semana durante (semanas)', keep_week)
        spin_row('month', 'Conservar una al mes durante (meses)', keep_month)
        page.add(smart_group)
        return page

    # ------------------------------------------------------------- options
    def _options_page(self):
        page = Adw.PreferencesPage(title='Opciones',
                                   icon_name='emblem-system-symbolic')
        cfg = self.cfg

        notif = Adw.PreferencesGroup(title='Notificaciones')
        self._maybe_switch(notif, 'Notificar errores y avisos', '',
                           'notify', 'setNotify')
        page.add(notif)

        perf = Adw.PreferencesGroup(title='Rendimiento')
        self._maybe_switch(perf, 'Ejecutar cron con nice -n19',
                           'Menor prioridad de CPU',
                           'niceOnCron', 'setNiceOnCron')
        self._maybe_switch(perf, 'Ejecutar cron con ionice -c2 -n7',
                           'Menor prioridad de E/S',
                           'ioniceOnCron', 'setIoniceOnCron')
        page.add(perf)

        rest = Adw.PreferencesGroup(title='Restauración y archivos')
        self._maybe_switch(rest, 'Copia de seguridad antes de restaurar',
                           'archivo.backup.YYYYMMDD',
                           'backupOnRestore', 'setBackupOnRestore')
        self._maybe_switch(rest, 'Continuar aunque haya errores',
                           'Conserva instantáneas incompletas',
                           'continueOnErrors', 'setContinueOnErrors')
        self._maybe_switch(rest, 'Usar sumas de comprobación',
                           'Más lento, pero más fiable que fecha+tamaño',
                           'useChecksum', 'setUseChecksum')
        self._maybe_switch(rest, 'Copiar enlaces simbólicos como archivos', '',
                           'copyLinks', 'setCopyLinks')
        self._maybe_switch(rest, 'Preservar ACL', '',
                           'preserveAcl', 'setPreserveAcl')
        self._maybe_switch(rest, 'Preservar atributos extendidos (xattr)', '',
                           'preserveXattr', 'setPreserveXattr')
        page.add(rest)
        return page

    def _maybe_switch(self, group, title, subtitle, getter, setter):
        """Add a switch row only if the Config version has this option."""
        get = getattr(self.cfg, getter, None)
        set_ = getattr(self.cfg, setter, None)
        if not callable(get) or not callable(set_):
            return
        self._switch_row(group, title, subtitle, get, set_)

    # -------------------------------------------------------------- expert
    def _expert_page(self):
        page = Adw.PreferencesPage(title='Avanzado',
                                   icon_name='utilities-terminal-symbolic')

        rsync_group = Adw.PreferencesGroup(title='rsync')
        self._maybe_switch(rsync_group, 'Modo rsync completo',
                           'Una sola pasada; más fiel pero más lento',
                           'fullRsync', 'setFullRsync')
        self._maybe_switch(rsync_group, 'Sumas de comprobación en rsync completo',
                           '', 'fullRsyncWithChecksum',
                           'setFullRsyncWithChecksum')
        page.add(rsync_group)

        ssh_group = Adw.PreferencesGroup(title='SSH')
        self._maybe_switch(ssh_group, 'Comprobar comandos en el remoto', '',
                           'sshCheckCommands', 'setSshCheckCommands')
        self._maybe_switch(ssh_group, 'Comprobar que el remoto está en línea (ping)',
                           '', 'sshCheckPingHost', 'setSshCheckPingHost')
        self._maybe_switch(ssh_group, 'Guardar contraseña en el llavero', '',
                           'passwordSave', 'setPasswordSave')
        self._maybe_switch(ssh_group, 'Usar caché de contraseña', '',
                           'passwordUseCache', 'setPasswordUseCache')
        page.add(ssh_group)

        log_group = Adw.PreferencesGroup(title='Registro y cron')
        self._maybe_switch(log_group, 'Redirigir stdout a /dev/null en cron', '',
                           'redirectStdoutInCron', 'setRedirectStdoutInCron')
        self._maybe_switch(log_group, 'Redirigir stderr a /dev/null en cron', '',
                           'redirectStderrInCron', 'setRedirectStderrInCron')
        self._maybe_switch(log_group, 'No copiar con batería', '',
                           'noSnapshotOnBattery', 'setNoSnapshotOnBattery')
        page.add(log_group)
        return page
