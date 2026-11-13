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
from .i18n import _  # noqa: E402
from .settings_sheet import MODE_LABELS  # noqa: E402

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
                         title=_('Gestionar perfiles'),
                         default_width=820, default_height=640)
        self.backend = app.backend
        self.cfg = app.backend.cfg
        from bitgnome import ui as _ui
        _ui.apply_theme(self)

        if self.cfg is None:
            page = Adw.PreferencesPage()
            group = Adw.PreferencesGroup()
            row = Adw.ActionRow(title=_('Núcleo de Back In Time no disponible'),
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

    def _check_ssh(self, _btn=None):
        """Probe the SSH destination from the values on screen."""
        try:
            host = self.cfg.sshHost() or ''
            port = self.cfg.sshPort() or 22
            user = self.cfg.sshUser() or ''
            path = self.cfg.sshSnapshotsPath() or ''
        except Exception as exc:          # noqa: BLE001
            self._show_ssh_result(
                False, _('No se pudo leer la configuración: {error}').format(
                    error=exc))
            return
        self.ssh_check_btn.set_sensitive(False)
        self._show_ssh_result(True, _('Comprobando…'))

        def done(result, error):
            self.ssh_check_btn.set_sensitive(True)
            if error:
                self._show_ssh_result(
                    False, _('La comprobación falló: {error}').format(
                        error=error))
                return False
            ok, message = result
            self._show_ssh_result(ok, message)
            return False

        _backend.Worker(lambda: self.backend.check_ssh_destination(
            host, port, user, path), done)

    def _show_ssh_result(self, ok, message):
        if self.ssh_result is None:
            return
        self.ssh_result.set_visible(True)
        self.ssh_result.set_title(_('Todo listo') if ok else _('Hay un problema'))
        self.ssh_result.set_subtitle(message)

    def _save(self):
        try:
            self.cfg.save()
            return True
        except Exception as exc:  # noqa: BLE001
            # Silent failures left the screen showing a value the config
            # never got.
            dialog = Adw.MessageDialog(
                transient_for=self, modal=True,
                heading=_('No se pudo guardar la configuración'),
                body=_('El cambio se ve en pantalla pero no se ha guardado: '
                       '{error}').format(error=exc))
            dialog.add_response('close', _('Cerrar'))
            dialog.set_default_response('close')
            dialog.present()
            self._save_dialog = dialog
            return False

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
        page = Adw.PreferencesPage(title=_('General'),
                                   icon_name='emblem-system-symbolic')

        mode_group = Adw.PreferencesGroup(title=_('Modo'))
        try:
            current_mode = self.cfg.snapshotsMode()
        except Exception:  # noqa: BLE001
            current_mode = 'local'
        mode_row = Adw.ComboRow(title=_('Modo de instantáneas'),
                                subtitle=_('Dónde se guardan las copias'))
        mode_row.set_model(Gtk.StringList.new(_MODE_LABELS))
        mode_row.set_selected(_mode_index(current_mode))
        mode_row.connect('notify::selected', self._on_mode_changed)
        mode_group.add(mode_row)
        self.mode_row = mode_row

        self.path_row = Adw.ActionRow(title=_('Guardar instantáneas en'))
        path_row = self.path_row
        self.path_entry = Gtk.Entry(hexpand=True, valign=Gtk.Align.CENTER)
        try:
            self.path_entry.set_text(self.cfg.snapshotsPath())
        except Exception:  # noqa: BLE001
            pass
        choose = Gtk.Button(label=_('Cambiar…'), valign=Gtk.Align.CENTER)
        choose.connect('clicked', self._choose_snapshot_path)
        path_entry = self.path_entry

        def apply_path(_entry):
            text = path_entry.get_text().strip()
            if not text:
                return
            try:
                self.backend.set_destination_path(text)
                self._save()
            except Exception:  # noqa: BLE001
                pass

        path_entry.connect('activate', apply_path)
        path_row.add_suffix(self.path_entry)
        path_row.add_suffix(choose)
        mode_group.add(path_row)
        page.add(mode_group)

        # SSH
        self.ssh_group = Adw.PreferencesGroup(title='SSH')
        self._entry_row(self.ssh_group, _('Servidor'),
                        self.cfg.sshHost, self.cfg.setSshHost)
        port_row = Adw.ActionRow(title=_('Puerto'))
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
        self._entry_row(self.ssh_group, _('Usuario'),
                        self.cfg.sshUser, self.cfg.setSshUser)
        self._entry_row(self.ssh_group, _('Ruta remota'),
                        self.cfg.sshSnapshotsPath, self.cfg.setSshSnapshotsPath)
        self._entry_row(self.ssh_group, _('Clave privada'),
                        self.cfg.sshPrivateKeyFile, self.cfg.setSshPrivateKeyFile)

        check_row = Adw.ActionRow(
            title=_('Probar la conexión'),
            subtitle=_('Comprueba ssh, la ruta remota y que allí haya rsync'))
        self.ssh_check_btn = Gtk.Button(label=_('Probar'))
        self.ssh_check_btn.add_css_class('rw-soft')
        self.ssh_check_btn.set_valign(Gtk.Align.CENTER)
        self.ssh_check_btn.connect('clicked', self._check_ssh)
        check_row.add_suffix(self.ssh_check_btn)
        self.ssh_group.add(check_row)
        self.ssh_result = Adw.ActionRow(title='', visible=False)
        self.ssh_result.set_activatable(False)
        self.ssh_group.add(self.ssh_result)
        page.add(self.ssh_group)
        self._update_ssh_visibility(current_mode)

        # Schedule
        sched_group = Adw.PreferencesGroup(title=_('Programación'))
        sched_row = Adw.ComboRow(
            title=_('Programar instantáneas'),
            subtitle=_('Entrada de cron gestionada por Back In Time'))
        modes = self._schedule_modes()
        labels = [MODE_LABELS.get(getattr(m, 'name', str(m)),
                                  getattr(m, 'name', str(m))) for m in modes]
        sched_row.set_model(Gtk.StringList.new(labels))
        try:
            cur = getattr(self.cfg.scheduleMode(), 'name', '')
            names = [getattr(m, 'name', '') for m in modes]
            sched_row.set_selected(names.index(cur) if cur in names else 0)
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
        """Every schedule mode the loaded core knows about."""
        c = self.cfg
        enum = getattr(_backend.bitbase_module, 'ScheduleMode', None)
        if enum is None:
            names = ('NONE', 'HOUR', 'DAY', 'WEEK', 'MONTH', 'UDEV',
                     'REPEATEDLY', 'CUSTOM_HOUR')
            return [getattr(c, n, None) for n in names
                    if getattr(c, n, None) is not None]
        out = []
        for member in enum:
            out.append(getattr(c, member.name, member))
        return out

    def _on_mode_changed(self, row, *_args):
        mode = _MODES[row.get_selected()]
        current = self.backend.profile_mode()
        if mode != current and mode == 'local_gocryptfs':
            row.set_selected(_mode_index(current))
            from .encryption import EncryptionSetupDialog
            self.encryption_dialog = EncryptionSetupDialog(
                self, on_done=self._encryption_applied)
            self.encryption_dialog.present()
            return
        if mode != current and ('gocryptfs' in mode or 'encfs' in mode):
            row.set_selected(_mode_index(current))
            kind = 'SSH' if 'ssh' in mode else 'EncFS'
            dialog = Adw.MessageDialog(
                transient_for=self, modal=True,
                heading=_('Cifrado {kind}').format(kind=kind),
                body=_('Este modo necesita un flujo guiado que todavía no está '
                       'disponible. Usa Gestionar perfiles con un perfil '
                       'existente o crea uno nuevo antes de cambiar el modo.'))
            dialog.add_response('close', _('Cerrar'))
            dialog.present()
            return
        self.cfg.setSnapshotsMode(mode)
        self._save()
        self._update_ssh_visibility(mode)

    def _encryption_applied(self, destination):
        self.mode_row.set_selected(
            _mode_index(self.backend.profile_mode()))
        self.path_entry.set_text(destination)
        self._update_ssh_visibility(self.backend.profile_mode())

    def _update_ssh_visibility(self, mode):
        ssh = 'ssh' in str(mode)
        self.ssh_group.set_visible(ssh)
        # The local folder row is meaningless for an SSH destination, which
        # keeps its own remote path.
        if getattr(self, 'path_row', None) is not None:
            self.path_row.set_visible(not ssh)

    def _choose_snapshot_path(self, *_args):
        dlg = Gtk.FileChooserNative(title=_('Elegir carpeta de destino'),
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
        page = Adw.PreferencesPage(title=_('Incluir'),
                                   icon_name='folder-symbolic')
        group = Adw.PreferencesGroup(
            title=_('Carpetas y archivos a respaldar'),
            description=_('rsync enlaza los archivos sin cambios: no ocupan '
                          'espacio extra.'))
        self.include_group = group
        page.add(group)
        self._fill_include()

        add_row = Adw.ActionRow(title=_('Añadir…'))
        box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        folder_btn = Gtk.Button(label=_('Carpeta…'))
        folder_btn.connect('clicked', self._add_include,
                           Gtk.FileChooserAction.SELECT_FOLDER)
        file_btn = Gtk.Button(label=_('Archivo…'))
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
            row = Adw.ActionRow(
                title=path,
                subtitle=_('carpeta') if ftype == 0 else _('archivo'))
            rm = Gtk.Button(icon_name='user-trash-symbolic',
                            valign=Gtk.Align.CENTER, has_frame=False)
            rm.connect('clicked', self._remove_include, path, ftype)
            row.add_suffix(rm)
            self.include_group.add(row)
            self._include_rows.append(row)

    def _add_include(self, _btn, action):
        dlg = Gtk.FileChooserNative(title=_('Añadir a la copia'),
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
        page = Adw.PreferencesPage(title=_('Excluir'),
                                   icon_name='action-unavailable-symbolic')
        group = Adw.PreferencesGroup(
            title=_('Patrones de exclusión'),
            description=_('Comodines de rsync: * dentro de un nivel, '
                          '** a cualquier profundidad.'))
        self.exclude_group = group
        page.add(group)
        self._fill_exclude()

        add_row = Adw.EntryRow(title=_('Nuevo patrón o ruta…'))
        add_row.connect('apply', self._add_exclude)
        group.add(add_row)

        size_group = Adw.PreferencesGroup(title=_('Por tamaño'))
        try:
            enabled = self.cfg.excludeBySizeEnabled()
            value = self.cfg.excludeBySize()
        except Exception:  # noqa: BLE001
            enabled, value = False, 500
        row = Adw.ActionRow(title=_('Excluir archivos mayores de'))
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
        page = Adw.PreferencesPage(title=_('Autoeliminar'),
                                   icon_name='user-trash-symbolic')
        cfg = self.cfg

        old_group = Adw.PreferencesGroup(title=_('Antigüedad'))
        try:
            enabled, value, unit = cfg.removeOldSnapshots()
        except Exception:  # noqa: BLE001
            enabled, value, unit = True, 10, 80
        row = Adw.ActionRow(title=_('Eliminar instantáneas más antiguas de'))
        sw = Gtk.Switch(active=enabled, valign=Gtk.Align.CENTER)
        spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(
            value=value, lower=1, upper=9999, step_increment=1))
        spin.set_valign(Gtk.Align.CENTER)
        combo = Gtk.DropDown.new_from_strings([_('días'), _('semanas'), _('años')])
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
            title=_('Eliminación inteligente'),
            description=_('Tras cada instantánea se aplica este patrón de '
                          'conservación.'))
        try:
            smart = cfg.smartRemove()
        except Exception:  # noqa: BLE001
            smart = (False, 2, 7, 4, 24)
        enabled, keep_all, keep_day, keep_week, keep_month = smart

        row_sw = Adw.ActionRow(
            title=_('Eliminación inteligente'),
            subtitle=_('Conservar solo lo que indica el patrón'))
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

        spin_row('all', _('Conservar todas durante (días)'), keep_all)
        spin_row('day', _('Conservar una al día durante (días)'), keep_day)
        spin_row('week', _('Conservar una a la semana durante (semanas)'), keep_week)
        spin_row('month', _('Conservar una al mes durante (meses)'), keep_month)
        page.add(smart_group)
        return page

    # ------------------------------------------------------------- options
    def _options_page(self):
        page = Adw.PreferencesPage(title=_('Opciones'),
                                   icon_name='emblem-system-symbolic')
        cfg = self.cfg

        notif = Adw.PreferencesGroup(title=_('Notificaciones'))
        self._maybe_switch(notif, _('Notificar errores y avisos'), '',
                           'notify', 'setNotify')
        page.add(notif)

        perf = Adw.PreferencesGroup(title=_('Rendimiento'))
        self._maybe_switch(perf, _('Ejecutar cron con nice -n19'),
                           _('Menor prioridad de CPU'),
                           'niceOnCron', 'setNiceOnCron')
        self._maybe_switch(perf, _('Ejecutar cron con ionice -c2 -n7'),
                           _('Menor prioridad de E/S'),
                           'ioniceOnCron', 'setIoniceOnCron')
        page.add(perf)

        rest = Adw.PreferencesGroup(title=_('Restauración y archivos'))
        self._maybe_switch(rest, _('Copia de seguridad antes de restaurar'),
                           'archivo.backup.YYYYMMDD',
                           'backupOnRestore', 'setBackupOnRestore')
        self._maybe_switch(rest, _('Continuar aunque haya errores'),
                           _('Conserva instantáneas incompletas'),
                           'continueOnErrors', 'setContinueOnErrors')
        self._maybe_switch(rest, _('Usar sumas de comprobación'),
                           _('Más lento, pero más fiable que fecha+tamaño'),
                           'useChecksum', 'setUseChecksum')
        self._maybe_switch(rest, _('Copiar enlaces simbólicos como archivos'), '',
                           'copyLinks', 'setCopyLinks')
        self._maybe_switch(rest, _('Preservar ACL'), '',
                           'preserveAcl', 'setPreserveAcl')
        self._maybe_switch(rest, _('Preservar atributos extendidos (xattr)'), '',
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
        page = Adw.PreferencesPage(title=_('Avanzado'),
                                   icon_name='utilities-terminal-symbolic')

        rsync_group = Adw.PreferencesGroup(title='rsync')
        self._maybe_switch(rsync_group, _('Modo rsync completo'),
                           _('Una sola pasada; más fiel pero más lento'),
                           'fullRsync', 'setFullRsync')
        self._maybe_switch(rsync_group, _('Sumas de comprobación en rsync completo'),
                           '', 'fullRsyncWithChecksum',
                           'setFullRsyncWithChecksum')
        page.add(rsync_group)

        ssh_group = Adw.PreferencesGroup(title='SSH')
        self._maybe_switch(ssh_group, _('Comprobar comandos en el remoto'), '',
                           'sshCheckCommands', 'setSshCheckCommands')
        self._maybe_switch(ssh_group,
                           _('Comprobar que el remoto está en línea (ping)'),
                           '', 'sshCheckPingHost', 'setSshCheckPingHost')
        self._maybe_switch(ssh_group, _('Guardar contraseña en el llavero'), '',
                           'passwordSave', 'setPasswordSave')
        self._maybe_switch(ssh_group, _('Usar caché de contraseña'), '',
                           'passwordUseCache', 'setPasswordUseCache')
        page.add(ssh_group)

        log_group = Adw.PreferencesGroup(title=_('Registro y cron'))
        self._maybe_switch(log_group, _('Redirigir stdout a /dev/null en cron'), '',
                           'redirectStdoutInCron', 'setRedirectStdoutInCron')
        self._maybe_switch(log_group, _('Redirigir stderr a /dev/null en cron'), '',
                           'redirectStderrInCron', 'setRedirectStderrInCron')
        self._maybe_switch(log_group, _('No copiar con batería'), '',
                           'noSnapshotOnBattery', 'setNoSnapshotOnBattery')
        page.add(log_group)
        return page
