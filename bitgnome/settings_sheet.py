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

MODE_LABELS = {
    'DISABLED': 'Manual', 'AT_EVERY_BOOT': 'Al arrancar',
    'MINUTES_5': 'Cada 5 minutos', 'MINUTES_10': 'Cada 10 minutos',
    'MINUTES_30': 'Cada 30 minutos', 'HOUR': 'Cada hora',
    'HOURS_2': 'Cada 2 horas', 'HOURS_4': 'Cada 4 horas',
    'HOURS_6': 'Cada 6 horas', 'HOURS_12': 'Cada 12 horas',
    'CUSTOM_HOUR': 'Horas personalizadas', 'DAY': 'Cada día',
    'REPEATEDLY': 'Repetidamente (anacron)', 'UDEV': 'Al conectar la unidad',
    'WEEK': 'Cada semana', 'MONTH': 'Cada mes', 'YEAR': 'Cada año',
}
UNIT_NAMES = {'HOUR': 'horas', 'DAY': 'días', 'WEEK': 'semanas',
              'MONTH': 'meses', 'YEAR': 'años'}
def _safe(getter, default=None):
    try:
        return getter()
    except Exception:  # noqa: BLE001
        return default


SCHEDULE_MODES = [
    ('hour', 'Cada hora'),
    ('day', 'Cada día'),
    ('week', 'Cada semana'),
    ('none', 'Manual'),
]


class SettingsSheet(Adw.Window):
    def __init__(self, app, on_change=None):
        super().__init__(transient_for=app.get_active_window(), modal=True,
                         title='Ajustes', default_width=660, default_height=720)
        self.app = app
        self.backend = app.backend
        self.cfg = app.backend.cfg
        self.on_change = on_change

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add_css_class('rw-sheet')
        self.set_content(root)

        head = Gtk.Box(spacing=8)
        head.add_css_class('rw-sheet-head')
        title = Gtk.Label(label='Ajustes', xalign=0, hexpand=True)
        title.add_css_class('rw-sheet-title')
        head.append(title)
        close = Gtk.Button(icon_name='window-close-symbolic')
        close.add_css_class('rw-ghost')
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
            self.body.append(ui.row('Núcleo de Back In Time no disponible',
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
    def _save(self):
        ok = ui.save(self.cfg)
        if self._error_label is not None:
            self._error_label.set_text('' if ok
                                       else 'No se pudo guardar la configuración')
        if self.on_change:
            self.on_change()
        return ok

    # ------------------------------------------------------------- folders
    def _build_folders(self):
        self.body.append(ui.group_title('Qué se copia'))
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
        add = Gtk.Button(label='+ Añadir carpeta')
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

        ui.choose_folder(self, 'Añadir a la copia', on_path)

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
        self.body.append(ui.group_title('Cada cuánto'))
        current = self._current_schedule()
        mode_name = self._schedule_mode_name()
        known = mode_name in self.SCHEDULE_KEYS
        control = ui.segmented(
            [(key, label) for key, label in SCHEDULE_MODES],
            current if known else '__none__',
            self._on_schedule_changed)
        subtitle = 'Cuándo crear puntos de restauración'
        if not known and mode_name:
            subtitle = f'Ahora: {MODE_LABELS.get(mode_name, mode_name)}'
        self.body.append(ui.row('Frecuencia', subtitle, control))

        switch = Gtk.Switch(active=self._get(self.cfg.notify, True),
                            valign=Gtk.Align.CENTER)
        switch.connect('notify::active', lambda sw, _: (
            self.cfg.setNotify(sw.get_active()), self._save()))
        self.body.append(ui.row('Avisar si una copia falla',
                                'Notificaciones del escritorio', switch))

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
            _backend.Worker(self.backend.apply_automation, lambda *_: None)
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------- destination
    def _build_destination(self):
        self.body.append(ui.group_title('Dónde'))
        mode, path = self.backend.profile_summary()
        local = 'local' in self.backend.profile_mode()
        change = Gtk.Button(label='Cambiar…')
        change.add_css_class('rw-soft')
        change.set_sensitive(local)
        change.connect('clicked', self._change_destination)
        subtitle = path or 'sin configurar'
        if not local:
            subtitle = (f'Perfil en modo {mode}: la ruta se edita en '
                        'Gestionar perfiles')
        self.destination_row = ui.row(f'Destino ({mode})', subtitle, change)
        self.body.append(self.destination_row)

    def _change_destination(self, _btn):
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

        ui.choose_folder(self, 'Elegir carpeta de destino', on_path)

    # ------------------------------------------------------------ retention
    def _build_retention(self):
        self.body.append(ui.group_title('Conservación'))
        enabled, value, unit = self._retention()
        unit_name = getattr(unit, 'name', '')
        if not enabled:
            current = 'forever'
        elif unit_name == 'YEAR' and value == 2:
            current = '2y'
        elif unit_name == 'MONTH' and value == 6:
            current = '6m'
        else:
            current = 'custom'
        options = [('6m', '6 meses'), ('2y', '2 años'), ('forever', 'Siempre')]
        control = ui.segmented(options, current, self._on_retention_changed)
        subtitle = 'Las más antiguas se eliminan solas para hacer hueco'
        if current == 'custom':
            subtitle = f'Ahora: {value} {UNIT_NAMES.get(unit_name, "")}'.strip()
        self.body.append(ui.row('Conservar copias durante', subtitle, control))

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
            elif key == '6m':
                self.cfg.setRemoveOldSnapshots(True, 6, units.MONTH)
            else:
                self.cfg.setRemoveOldSnapshots(True, 2, units.YEAR)
            self._save()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------- advanced
    def _build_advanced(self):
        self.body.append(ui.group_title('Avanzado'))
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
        expander, box = self._expander('Excluir archivos o patrones')
        self.excludes_flow = Gtk.FlowBox()
        self.excludes_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.excludes_flow.set_max_children_per_line(3)
        self.excludes_flow.set_column_spacing(6)
        self.excludes_flow.set_row_spacing(6)
        box.append(self.excludes_flow)
        self._fill_excludes()

        add_row = Gtk.Box(spacing=8)
        entry = Gtk.Entry(placeholder_text='*/node_modules, *.tmp…',
                          hexpand=True)
        entry.add_css_class('rw-entry')
        entry.add_css_class('rw-mono')
        button = Gtk.Button(label='Añadir')
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
            empty = Gtk.Label(label='Sin exclusiones', xalign=0)
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
        expander, box = self._expander('Equipo remoto (SSH)')
        fields = [
            ('Servidor', self.cfg.sshHost, self.cfg.setSshHost),
            ('Puerto', self.cfg.sshPort, self.cfg.setSshPort),
            ('Usuario', self.cfg.sshUser, self.cfg.setSshUser),
            ('Ruta remota', self.cfg.sshSnapshotsPath,
             self.cfg.setSshSnapshotsPath),
        ]
        for title, getter, setter in fields:
            entry = Gtk.Entry()
            entry.add_css_class('rw-entry')
            entry.set_width_chars(22)
            try:
                entry.set_text(str(getter() or ''))
            except Exception:  # noqa: BLE001
                pass

            def apply(e, set_=setter, label=title):
                text = e.get_text().strip()
                if label == 'Puerto':
                    if not text.isdigit() or not 1 <= int(text) <= 65535:
                        e.set_text(str(_safe(getter) or '22'))
                        if self._error_label is not None:
                            self._error_label.set_text('Puerto SSH no válido')
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
        expander, box = self._expander('Conservación inteligente')
        state = self._smart()
        enabled, keep_all, per_day, per_week, per_month = state
        switch = Gtk.Switch(active=enabled, valign=Gtk.Align.CENTER)
        box.append(ui.row('Activa', 'Una copia al día, otra a la semana…', switch))

        spins = {}
        for key, title, value, unit in (
                ('all', 'Conservar todas durante', keep_all, 'días'),
                ('day', 'Una al día durante', per_day, 'días'),
                ('week', 'Una a la semana durante', per_week, 'semanas'),
                ('month', 'Una al mes durante', per_month, 'meses')):
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
        expander, box = self._expander('rsync y registro')
        for title, subtitle, getter, setter in (
                ('Sumas de comprobación',
                 'Detecta cambios por contenido, no por fecha',
                 self.cfg.useChecksum, self.cfg.setUseChecksum),
                ('Preservar ACL', 'Atributos de control de acceso',
                 self.cfg.preserveAcl, self.cfg.setPreserveAcl),
                ('Preservar atributos extendidos', 'xattrs del sistema de archivos',
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
        control = ui.segmented([(1, 'Solo errores'), (2, 'Cambios'),
                                (3, 'Todo')], level, self._set_log_level)
        box.append(ui.row('Nivel de registro', 'Detalle del takesnapshot.log',
                          control))
        return expander

    def _set_log_level(self, level):
        try:
            self.cfg.setLogLevel(level)
            self._save()
        except Exception:  # noqa: BLE001
            pass

    def _profiles_expander(self):
        expander, box = self._expander('Perfiles')
        current = self.backend.current_profile()
        for pid, name in self.backend.profiles():
            btn = Gtk.ToggleButton(label=name)
            btn.add_css_class('rw-soft')
            btn.set_active(pid == current)
            btn.connect('clicked', self._on_profile_chosen, pid)
            box.append(btn)
        manage = Gtk.Button(label='Gestionar perfiles…')
        manage.add_css_class('rw-soft')
        manage.set_halign(Gtk.Align.START)
        manage.connect('clicked', lambda *_: (
            self.close(), self.app.activate_action('advanced-preferences', None)))
        box.append(manage)
        return expander

    def _on_profile_chosen(self, _btn, pid):
        try:
            self.backend.set_current_profile(pid)
        except Exception:  # noqa: BLE001
            return
        if self.on_change:
            self.on_change()
        self.close()

    # ---------------------------------------------------------------- utils
    @staticmethod
    def _get(getter, default):
        try:
            return bool(getter())
        except Exception:  # noqa: BLE001
            return default
