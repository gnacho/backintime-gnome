"""Settings sheet: the common options first, the expert ones folded away.

Everything here writes through ``config.Config`` setters and ``cfg.save()``.
The complete profile editor (encryption modes, udev, ssh keys, custom
schedules...) stays reachable from the "Perfiles" expander, so the sheet
simplifies the UI without removing options.
"""
import os

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk  # noqa: E402

from . import backend as _backend  # noqa: E402
from . import ui  # noqa: E402
from .i18n import _  # noqa: E402

MODE_LABELS = {
    'DISABLED': _('Manual'), 'AT_EVERY_BOOT': _('Al arrancar'),
    'MINUTES_5': _('Cada 5 minutos'), 'MINUTES_10': _('Cada 10 minutos'),
    'MINUTES_30': _('Cada 30 minutos'), 'HOUR': _('Cada hora'),
    'HOURS_2': _('Cada 2 horas'), 'HOURS_4': _('Cada 4 horas'),
    'HOURS_6': _('Cada 6 horas'), 'HOURS_12': _('Cada 12 horas'),
    'CUSTOM_HOUR': _('Horas personalizadas'), 'DAY': _('Cada día'),
    'REPEATEDLY': _('Repetidamente (anacron)'), 'UDEV': _('Al conectar la unidad'),
    'WEEK': _('Cada semana'), 'MONTH': _('Cada mes'), 'YEAR': _('Cada año'),
}
UNIT_NAMES = {'HOUR': _('horas'), 'DAY': _('días'), 'WEEK': _('semanas'),
              'MONTH': _('meses'), 'YEAR': _('años')}
# The core only removes snapshots for days, weeks and years; months and
# hours resolve to "never", so they are not offered.
RETENTION_UNSUPPORTED = ('MONTH', 'HOUR')
def _safe(getter, default=None):
    try:
        return getter()
    except Exception:  # noqa: BLE001
        return default


SCHEDULE_MODES = [
    ('hour', _('Cada hora')),
    ('day', _('Cada día')),
    ('week', _('Cada semana')),
    ('none', _('Manual')),
]


class SettingsSheet(Adw.Window):
    def __init__(self, app, on_change=None):
        super().__init__(transient_for=app.get_active_window(), modal=True,
                         title=_('Ajustes'), default_width=660, default_height=720)
        self.app = app
        self.backend = app.backend
        self.cfg = app.backend.cfg
        self.on_change = on_change
        self.schedule_status = None
        ui.apply_theme(self)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add_css_class('rw-sheet')
        self.set_content(root)

        head = Gtk.Box(spacing=8)
        head.add_css_class('rw-sheet-head')
        title = Gtk.Label(label=_('Ajustes'), xalign=0, hexpand=True)
        title.add_css_class('rw-sheet-title')
        head.append(title)
        close = Gtk.Button(icon_name='window-close-symbolic')
        close.add_css_class('rw-ghost')
        ui.a11y(close, _('Cerrar ajustes'))
        close.connect('clicked', lambda *_: self.close())
        head.append(close)
        root.append(head)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        root.append(scrolled)
        self._error_label = None
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.body.set_margin_top(6)
        self.body.set_margin_bottom(22)
        self.body.set_margin_start(20)
        self.body.set_margin_end(20)
        scrolled.set_child(self.body)

        if self.cfg is None:
            self.body.append(ui.row(_('Núcleo de Back In Time no disponible'),
                                    self.backend.error or ''))
            return

        self._build_folders()
        self._build_schedule()
        self._build_destination()
        self._build_retention()
        self._build_advanced()
        self._error_label = Gtk.Label(xalign=0, wrap=True)
        self._error_label.add_css_class('rw-srow-sub')
        self._error_label.set_margin_top(10)
        self.body.append(self._error_label)

    # ---------------------------------------------------------------- save
    def _parent_busy(self):
        """Refuse before writing anything, not after.

        The window cannot reload while an operation runs, so changing the
        profile first left the engine on one profile and the list on another.
        """
        parent = self.get_transient_for()
        check = getattr(parent, 'busy_reason', None)
        if check is None:
            return False
        reason = check()
        if reason:
            self._show_error(_('{reason}: no se ha cambiado nada').format(
                reason=reason))
            return True
        return False

    def _show_error(self, message):
        if self._error_label is not None:
            self._error_label.set_text(message)

    def _save(self):
        ok = ui.save(self.cfg)
        if self._error_label is not None:
            self._error_label.set_text('' if ok
                                       else _('No se pudo guardar la configuración'))
        if self.on_change:
            self.on_change()
        return ok

    # ------------------------------------------------------------- folders
    def _build_folders(self):
        self.body.append(ui.group_title(_('Qué se copia')))
        self.folders_box = Gtk.FlowBox()
        self.folders_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.folders_box.set_max_children_per_line(4)
        self.folders_box.set_column_spacing(6)
        self.folders_box.set_row_spacing(6)
        self.body.append(self.folders_box)
        self._fill_folders()

    def _fill_folders(self):
        while child := self.folders_box.get_first_child():
            self.folders_box.remove(child)
        try:
            entries = list(self.cfg.include())
        except Exception:  # noqa: BLE001
            entries = []
        for path, ftype in entries:
            name = os.path.basename(path.rstrip('/')) or path
            self.folders_box.insert(
                ui.chip(name, path, lambda p=path, t=ftype: self._remove_include(p, t)), -1)
        add = Gtk.Button(label=_('+ Añadir carpeta'))
        add.add_css_class('rw-chip')
        add.connect('clicked', self._add_include)
        self.folders_box.insert(add, -1)

    def _add_include(self, _btn):
        def on_path(path):
            try:
                entries = list(self.cfg.include())
            except Exception:  # noqa: BLE001
                entries = []
            entries.append((path, 0))
            self.cfg.setInclude(entries)
            self._save()
            self._fill_folders()

        ui.choose_folder(self, _('Añadir a la copia'), on_path)

    def _remove_include(self, path, ftype):
        entries = [e for e in self.cfg.include() if e != (path, ftype)]
        self.cfg.setInclude(entries)
        self._save()
        self._fill_folders()

    # ------------------------------------------------------------- schedule
    SCHEDULE_KEYS = {'HOUR': 'hour', 'DAY': 'day', 'WEEK': 'week',
                     'DISABLED': 'none'}

    def _schedule_mode_name(self):
        try:
            mode = self.cfg.scheduleMode()
        except Exception:  # noqa: BLE001
            return ''
        return getattr(mode, 'name', str(mode).split('.')[-1])

    def _current_schedule(self):
        return self.SCHEDULE_KEYS.get(self._schedule_mode_name(), 'none')

    def _build_schedule(self):
        self.body.append(ui.group_title(_('Cada cuánto')))
        current = self._current_schedule()
        mode_name = self._schedule_mode_name()
        known = mode_name in self.SCHEDULE_KEYS
        control = ui.segmented(
            [(key, label) for key, label in SCHEDULE_MODES],
            current if known else '__none__',
            self._on_schedule_changed)
        subtitle = _('Cuándo crear puntos de restauración')
        if not known and mode_name:
            subtitle = _('Ahora: {mode}').format(
                mode=MODE_LABELS.get(mode_name, mode_name))
        self.body.append(ui.row(_('Frecuencia'), subtitle, control))

        self.schedule_status = ui.row(_('Estado'),
                                      _('Comprobando el servicio de cron…'), None)
        self.body.append(self.schedule_status)
        self._refresh_schedule_status()

        switch = Gtk.Switch(active=self._get(self.cfg.notify, True),
                            valign=Gtk.Align.CENTER)
        switch.connect('notify::active', lambda sw, _: (
            self.cfg.setNotify(sw.get_active()), self._save()))
        self.body.append(ui.row(_('Avisar si una copia falla'),
                                _('Notificaciones del escritorio'), switch))

    def _on_schedule_changed(self, key):
        mode = {
            'hour': getattr(self.cfg, 'HOUR', None),
            'day': getattr(self.cfg, 'DAY', None),
            'week': getattr(self.cfg, 'WEEK', None),
            'none': getattr(self.cfg, 'NONE', None),
        }.get(key)
        if mode is None:
            return
        try:
            self.cfg.setScheduleMode(mode)
            self._save()
            _backend.Worker(self.backend.apply_automation,
                            lambda *_: self._refresh_schedule_status())
        except Exception:  # noqa: BLE001
            pass

    def _refresh_schedule_status(self):
        """Say plainly whether the chosen schedule will run."""
        def done(status, error):
            if self.schedule_status is None or self.schedule_status.subtitle \
                    is None:
                return False
            status = status if isinstance(status, dict) else {}
            if error or not status:
                text = _('No se pudo comprobar el estado de cron')
            elif status.get('problem') == 'udev':
                text = _('Al conectar la unidad no está disponible')
            elif not status.get('needs_cron'):
                text = _('No depende de cron')
            elif status.get('problem') == 'binary':
                text = _('No hay cron instalado: no se ejecutará')
            elif status.get('problem') == 'service':
                text = _('Cron instalado pero su servicio está parado')
            else:
                text = _('Listo: cron está activo')
            self.schedule_status.subtitle.set_text(text)
            return False

        _backend.Worker(self.backend.cron_status, done)

    # ---------------------------------------------------------- destination
    def _build_destination(self):
        self.body.append(ui.group_title(_('Dónde')))
        mode, path = self.backend.profile_summary()
        local = self.backend.profile_mode() == 'local'
        change = Gtk.Button(label=_('Cambiar…'))
        change.add_css_class('rw-soft')
        change.set_sensitive(local)
        change.connect('clicked', self._change_destination)
        subtitle = path or _('sin configurar')
        if not local:
            subtitle = _('Perfil en modo {mode}: la ruta se edita en '
                         'Gestionar perfiles').format(mode=mode)
        self.destination_row = ui.row(_('Destino ({mode})').format(mode=mode),
                                      subtitle, change)
        self.body.append(self.destination_row)
        encrypted = ('gocryptfs' in mode or 'encfs' in mode)
        if encrypted:
            backend = 'EncFS' if 'encfs' in mode else 'Gocryptfs'
            encrypt_subtitle = _('Cifrado activo ({backend})').format(
                backend=backend)
            encrypt_label = _('Configurar cifrado…')
        else:
            encrypt_subtitle = _('Protege el contenido de las copias')
            encrypt_label = _('Cifrar copia…')
        encrypt = Gtk.Button(label=encrypt_label)
        encrypt.add_css_class('rw-soft')
        encrypt.connect('clicked', self._open_encryption_setup)
        if encrypted and ('encfs' in mode or 'ssh' in mode):
            encrypt.set_sensitive(False)
            kind = 'SSH' if 'ssh' in mode else _('EncFS legado')
            encrypt_subtitle = _('Cifrado {kind}; usa Gestionar perfiles').format(
                kind=kind)
        self.body.append(ui.row(_('Cifrado'), encrypt_subtitle, encrypt))

    def _open_encryption_setup(self, _btn):
        from .encryption import EncryptionSetupDialog
        self.encryption_dialog = EncryptionSetupDialog(
            self, on_done=self._encryption_applied)
        self.encryption_dialog.present()

    def _encryption_applied(self, destination):
        if self.destination_row.subtitle is not None:
            self.destination_row.subtitle.set_text(destination)
        if self.on_change:
            self.on_change(reload=True)

    def _change_destination(self, _btn):
        if self._parent_busy():
            return

        def on_path(path):
            try:
                self.backend.set_destination_path(path)
                self._save()
                if self.destination_row.subtitle is not None:
                    self.destination_row.subtitle.set_text(path)
                if self.on_change:
                    self.on_change(reload=True)
            except Exception:  # noqa: BLE001
                pass

        ui.choose_folder(self, _('Elegir carpeta de destino'), on_path)

    # ------------------------------------------------------------ retention
    def _build_retention(self):
        self.body.append(ui.group_title(_('Conservación')))
        enabled, value, unit = self._retention()
        unit_name = getattr(unit, 'name', '')
        if not enabled:
            current = 'forever'
        elif unit_name == 'WEEK' and value == 2:
            current = '2w'
        elif unit_name == 'YEAR' and value == 1:
            current = '1y'
        elif unit_name == 'YEAR' and value == 2:
            current = '2y'
        else:
            current = 'custom'
        options = [('2w', _('2 semanas')), ('1y', _('1 año')),
                   ('2y', _('2 años')), ('forever', _('Siempre'))]
        control = ui.segmented(options, current, self._on_retention_changed)
        subtitle = _('Las más antiguas se eliminan solas para hacer hueco')
        if current == 'custom':
            subtitle = _('Ahora: {count} {unit}').format(
                count=value, unit=UNIT_NAMES.get(unit_name, '')).strip()
            if unit_name in RETENTION_UNSUPPORTED:
                subtitle += ' ' + _('(el motor no borra con esta unidad)')
        self.body.append(ui.row(_('Conservar copias durante'), subtitle, control))

    def _retention(self):
        try:
            return self.cfg.removeOldSnapshots()
        except Exception:  # noqa: BLE001
            return (False, 0, None)

    def _on_retention_changed(self, key):
        units = getattr(_backend.bitbase_module, 'TimeUnit', None)
        try:
            if key == 'forever':
                self.cfg.setRemoveOldSnapshots(False, 0, units.YEAR)
            elif key == '2w':
                self.cfg.setRemoveOldSnapshots(True, 2, units.WEEK)
            elif key == '1y':
                self.cfg.setRemoveOldSnapshots(True, 1, units.YEAR)
            else:
                self.cfg.setRemoveOldSnapshots(True, 2, units.YEAR)
            self._save()
            if self.on_change:
                self.on_change(retention=True)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------- advanced
    def _build_advanced(self):
        self.body.append(ui.group_title(_('Avanzado')))
        self.body.append(self._exclude_expander())
        self.body.append(self._ssh_expander())
        self.body.append(self._smart_expander())
        self.body.append(self._rsync_expander())
        self.body.append(self._profiles_expander())

    def _expander(self, title):
        expander = Gtk.Expander(label=title)
        expander.add_css_class('rw-srow')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(6)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_bottom(10)
        expander.set_child(box)
        return expander, box

    def _exclude_expander(self):
        expander, box = self._expander(_('Excluir archivos o patrones'))
        self.excludes_flow = Gtk.FlowBox()
        self.excludes_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.excludes_flow.set_max_children_per_line(3)
        self.excludes_flow.set_column_spacing(6)
        self.excludes_flow.set_row_spacing(6)
        box.append(self.excludes_flow)
        self._fill_excludes()

        add_row = Gtk.Box(spacing=8)
        entry = Gtk.Entry(placeholder_text=_('*/node_modules, *.tmp…'),
                          hexpand=True)
        entry.add_css_class('rw-entry')
        entry.add_css_class('rw-mono')
        button = Gtk.Button(label=_('Añadir'))
        button.add_css_class('rw-soft')

        def add_pattern(*_):
            pattern = entry.get_text().strip()
            if not pattern:
                return
            try:
                patterns = list(self.cfg.exclude())
            except Exception:  # noqa: BLE001
                patterns = []
            patterns.append(pattern)
            self.cfg.setExclude(patterns)
            self._save()
            entry.set_text('')
            self._fill_excludes()

        button.connect('clicked', add_pattern)
        entry.connect('activate', add_pattern)
        add_row.append(entry)
        add_row.append(button)
        box.append(add_row)
        return expander

    def _fill_excludes(self):
        while child := self.excludes_flow.get_first_child():
            self.excludes_flow.remove(child)
        try:
            patterns = list(self.cfg.exclude())
        except Exception:  # noqa: BLE001
            patterns = []
        if not patterns:
            empty = Gtk.Label(label=_('Sin exclusiones'), xalign=0)
            empty.add_css_class('rw-srow-sub')
            self.excludes_flow.insert(empty, -1)
            return
        for pattern in patterns:
            self.excludes_flow.insert(
                ui.chip(pattern, pattern,
                        lambda p=pattern: self._remove_exclude(p)), -1)

    def _remove_exclude(self, pattern):
        patterns = [p for p in self.cfg.exclude() if p != pattern]
        self.cfg.setExclude(patterns)
        self._save()
        self._fill_excludes()

    def _ssh_expander(self):
        expander, box = self._expander(_('Equipo remoto (SSH)'))
        fields = [
            (_('Servidor'), self.cfg.sshHost, self.cfg.setSshHost),
            (_('Puerto'), self.cfg.sshPort, self.cfg.setSshPort),
            (_('Usuario'), self.cfg.sshUser, self.cfg.setSshUser),
            (_('Ruta remota'), self.cfg.sshSnapshotsPath,
             self.cfg.setSshSnapshotsPath),
        ]
        for title, getter, setter in fields:
            entry = Gtk.Entry()
            entry.add_css_class('rw-entry')
            entry.set_width_chars(22)
            ui.a11y(entry, title)
            try:
                entry.set_text(str(getter() or ''))
            except Exception:  # noqa: BLE001
                pass

            def apply(e, set_=setter, get=getter):
                text = e.get_text().strip()
                if set_ is self.cfg.setSshPort:
                    if not text.isdigit() or not 1 <= int(text) <= 65535:
                        e.set_text(str(_safe(get) or '22'))
                        if self._error_label is not None:
                            self._error_label.set_text(_('Puerto SSH no válido'))
                        return
                try:
                    set_(text)
                    self._save()
                except Exception:  # noqa: BLE001
                    pass

            entry.connect('activate', apply)
            focus = Gtk.EventControllerFocus()
            focus.connect('leave', lambda _c, e=entry: apply(e))
            entry.add_controller(focus)
            box.append(ui.row(title, suffix=entry))
        return expander

    def _smart_expander(self):
        expander, box = self._expander(_('Conservación inteligente'))
        state = self._smart()
        enabled, keep_all, per_day, per_week, per_month = state
        switch = Gtk.Switch(active=enabled, valign=Gtk.Align.CENTER)
        box.append(ui.row(_('Activa'), _('Una copia al día, otra a la semana…'),
                          switch))

        spins = {}
        for key, title, value, unit in (
                ('all', _('Conservar todas durante'), keep_all, _('días')),
                ('day', _('Una al día durante'), per_day, _('días')),
                ('week', _('Una a la semana durante'), per_week, _('semanas')),
                ('month', _('Una al mes durante'), per_month, _('meses'))):
            spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(
                value=value, lower=0, upper=3650, step_increment=1),
                valign=Gtk.Align.CENTER, width_chars=5)
            spins[key] = spin
            box.append(ui.row(title, unit, spin))

        def write(*_):
            try:
                self.cfg.setSmartRemove(
                    switch.get_active(),
                    spins['all'].get_value_as_int(),
                    spins['day'].get_value_as_int(),
                    spins['week'].get_value_as_int(),
                    spins['month'].get_value_as_int())
                self._save()
            except Exception:  # noqa: BLE001
                pass

        switch.connect('notify::active', write)
        for spin in spins.values():
            spin.connect('value-changed', write)
        return expander

    def _smart(self):
        try:
            return self.cfg.smartRemove()
        except Exception:  # noqa: BLE001
            return (False, 2, 7, 4, 24)

    def _rsync_expander(self):
        expander, box = self._expander(_('rsync y registro'))
        for title, subtitle, getter, setter in (
                (_('Sumas de comprobación'),
                 _('Detecta cambios por contenido, no por fecha'),
                 self.cfg.useChecksum, self.cfg.setUseChecksum),
                (_('Preservar ACL'), _('Atributos de control de acceso'),
                 self.cfg.preserveAcl, self.cfg.setPreserveAcl),
                (_('Preservar atributos extendidos'),
                 _('xattrs del sistema de archivos'),
                 self.cfg.preserveXattr, self.cfg.setPreserveXattr)):
            switch = Gtk.Switch(active=self._get(getter, False),
                                valign=Gtk.Align.CENTER)
            switch.connect('notify::active', lambda sw, _s, set_=setter: (
                set_(sw.get_active()), self._save()))
            box.append(ui.row(title, subtitle, switch))

        level = 3
        try:
            level = int(self.cfg.logLevel())
        except Exception:  # noqa: BLE001
            pass
        control = ui.segmented([(1, _('Solo errores')), (2, _('Cambios')),
                                (3, _('Todo'))], level, self._set_log_level)
        box.append(ui.row(_('Nivel de registro'), _('Detalle del takesnapshot.log'),
                          control))
        return expander

    def _set_log_level(self, level):
        try:
            self.cfg.setLogLevel(level)
            self._save()
        except Exception:  # noqa: BLE001
            pass

    def _profiles_expander(self):
        expander, box = self._expander(_('Perfiles'))
        current = self.backend.current_profile()
        for pid, name in self.backend.profiles():
            btn = Gtk.ToggleButton(label=name)
            btn.add_css_class('rw-soft')
            btn.set_active(pid == current)
            btn.connect('clicked', self._on_profile_chosen, pid)
            box.append(btn)
        manage = Gtk.Button(label=_('Gestionar perfiles…'))
        manage.add_css_class('rw-soft')
        manage.set_halign(Gtk.Align.START)
        manage.connect('clicked', lambda *_: (
            self.close(), self.app.activate_action('advanced-preferences', None)))
        box.append(manage)
        return expander

    def _on_profile_chosen(self, _btn, pid):
        if self._parent_busy():
            return
        try:
            self.backend.set_current_profile(pid)
        except Exception:  # noqa: BLE001
            return
        if self.on_change:
            self.on_change(reload=True)
        self.close()

    # ---------------------------------------------------------------- utils
    @staticmethod
    def _get(getter, default):
        try:
            return bool(getter())
        except Exception:  # noqa: BLE001
            return default
