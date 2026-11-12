"""First-run wizard: folders, frequency, destination, summary, first backup.

Writes the profile through ``config.Config`` and then runs the real
``Snapshots.backup()`` while polling the message file. It never replaces the
existing include list: entries that are not offered as candidates (files,
folders outside the XDG dirs) are preserved, and the destination is written
through the setter of the profile's current mode.
"""
import os

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from . import backend as _backend  # noqa: E402
from . import ui  # noqa: E402
from .backend import Worker  # noqa: E402
from .encryption import destination_is_empty_or_initialized  # noqa: E402

SCHEDULE_CHOICES = [
    ('hour', 'Cada hora', 'Para trabajo intenso'),
    ('day', 'Cada día', 'El equilibrio perfecto'),
    ('week', 'Cada semana', 'Si cambian poco'),
    ('none', 'Solo cuando yo diga', 'Control total'),
]
SCHEDULE_NAMES = {'HOUR': 'hour', 'DAY': 'day', 'WEEK': 'week',
                  'DISABLED': 'none'}
SCHEDULE_CONST = {'hour': 'HOUR', 'day': 'DAY', 'week': 'WEEK',
                  'none': 'DISABLED'}


def candidate_dirs():
    """(label, path) for the user's standard folders, without duplicates."""
    wanted = [
        GLib.UserDirectory.DIRECTORY_DOCUMENTS,
        GLib.UserDirectory.DIRECTORY_PICTURES,
        GLib.UserDirectory.DIRECTORY_MUSIC,
        GLib.UserDirectory.DIRECTORY_VIDEOS,
        GLib.UserDirectory.DIRECTORY_DESKTOP,
        GLib.UserDirectory.DIRECTORY_DOWNLOAD,
    ]
    seen, out = set(), []
    for kind in wanted:
        path = GLib.get_user_special_dir(kind)
        if not path or not os.path.isdir(path) or path in seen:
            continue
        seen.add(path)
        out.append((os.path.basename(path.rstrip('/')) or path, path))
    if out:
        return out
    # XDG dirs are not configured (common in minimal setups): fall back to
    # the conventional names in both Spanish and English.
    home = os.path.expanduser('~')
    for name in ('Documentos', 'Documents', 'Imágenes', 'Pictures',
                 'Música', 'Music', 'Vídeos', 'Videos', 'Escritorio',
                 'Desktop', 'Descargas', 'Downloads'):
        path = os.path.join(home, name)
        if os.path.isdir(path) and path not in seen:
            seen.add(path)
            out.append((name, path))
    return out


class FirstRunWizard(Adw.Window):
    def __init__(self, app, on_done=None):
        super().__init__(transient_for=app.get_active_window(), modal=True,
                         title='Primera copia', default_width=760,
                         default_height=640)
        self.app = app
        self.backend = app.backend
        self.cfg = app.backend.cfg
        self.on_done = on_done
        self.step = 0
        self.folders = set()
        self.preserved = []
        self.extra_dirs = []
        self.frequency = 'day'
        self.destination = ''
        self.encrypt = False
        self.password = ''
        self.password_confirm_value = ''
        self._running = False
        self._stop_requested = False
        self._stop_attempts = 0
        self._applied = False
        self._frequency_ready = False
        self._closing = False
        self._escalate_source = None
        self._closed_after_run = False
        self._pulse = None
        self._snapshots_before = 0

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class('rw-wizard')
        self.set_content(root)
        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.card.add_css_class('rw-wiz-card')
        self.card.set_halign(Gtk.Align.CENTER)
        self.card.set_valign(Gtk.Align.CENTER)
        self.card.set_vexpand(True)
        self.card.set_size_request(560, -1)
        root.append(self.card)

        self.connect('close-request', self._on_close_request)
        self._preselect()
        self._render()

    # ------------------------------------------------------------- choices
    def _preselect(self):
        try:
            entries = list(self.cfg.include())
        except Exception:  # noqa: BLE001
            entries = []
        candidates = {path for _label, path in candidate_dirs()}
        self.folders = {path for path, _t in entries if path in candidates}
        self.preserved = [(path, ftype) for path, ftype in entries
                          if path not in candidates]
        if not self.folders:
            for _label, path in candidate_dirs()[:2]:
                self.folders.add(path)
        try:
            self.destination = self.backend.destination_path() or ''
        except Exception:  # noqa: BLE001
            self.destination = ''

    def _preselect_frequency(self):
        if self._frequency_ready:
            return
        self._frequency_ready = True
        try:
            name = getattr(self.cfg.scheduleMode(), 'name', '')
        except Exception:  # noqa: BLE001
            name = ''
        self.frequency = SCHEDULE_NAMES.get(name, 'none')

    # -------------------------------------------------------------- render
    def _render(self):
        if getattr(self, 'password_entry', None) is not None:
            self.password = self.password_entry.get_text()
            self.password_confirm_value = self.password_confirm.get_text()
        while child := self.card.get_first_child():
            self.card.remove(child)

        steps = [self._step_welcome, self._step_folders, self._step_frequency,
                 self._step_destination, self._step_summary]
        if self.step < len(steps):
            steps[self.step]()
            self._nav()
        else:
            self._step_progress()

    def _title(self, eyebrow, title, subtitle=None):
        label = Gtk.Label(label=eyebrow.upper(), xalign=0)
        label.add_css_class('rw-wiz-eyebrow')
        self.card.append(label)
        heading = Gtk.Label(label=title, xalign=0)
        heading.add_css_class('rw-wiz-title')
        heading.set_wrap(True)
        self.card.append(heading)
        if subtitle:
            sub = Gtk.Label(label=subtitle, xalign=0)
            sub.add_css_class('rw-wiz-sub')
            sub.set_wrap(True)
            self.card.append(sub)

    def _step_welcome(self):
        self._title('Bienvenida a Rewind', 'Tu pasado, siempre a mano',
                    'Copias automáticas de tus carpetas importantes. Recupera '
                    'cualquier archivo tal como estaba ayer, la semana pasada '
                    'o el año pasado. Sin nubes raras: tus discos, tus reglas.')

    def _step_folders(self):
        self._title('Paso 1 de 3', '¿Qué quieres proteger?',
                    'Elige las carpetas que no querrías perder jamás. Puedes '
                    'cambiarlo cuando quieras.')
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(3)
        flow.set_column_spacing(8)
        flow.set_row_spacing(8)

        options = list(candidate_dirs())
        known = {path for _label, path in options}
        for path in self.extra_dirs:
            if path not in known:
                options.append((os.path.basename(path.rstrip('/')) or path,
                                path))
                known.add(path)
        for label, path in options:
            flow.insert(self._folder_toggle(label, path), -1)

        self.card.append(flow)
        if not options:
            hint = Gtk.Label(label='No se han encontrado carpetas estándar. '
                             'Usa «Elegir otra carpeta…».', xalign=0, wrap=True)
            hint.add_css_class('rw-wiz-sub')
            self.card.append(hint)
        add = Gtk.Button(label='Elegir otra carpeta…')
        add.add_css_class('rw-soft')
        add.set_halign(Gtk.Align.START)
        add.connect('clicked', self._choose_folder)
        self.card.append(add)

        if self.preserved:
            names = ', '.join(os.path.basename(p.rstrip('/')) or p
                              for p, _t in self.preserved[:4])
            more = '' if len(self.preserved) <= 4 else ' y más'
            note = Gtk.Label(label=f'Se mantienen además: {names}{more}',
                             xalign=0, wrap=True)
            note.add_css_class('rw-wiz-sub')
            self.card.append(note)

    def _folder_toggle(self, label, path):
        btn = Gtk.ToggleButton()
        btn.add_css_class('rw-choice')
        btn.set_active(path in self.folders)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label=label, xalign=0)
        title.add_css_class('rw-choice-title')
        home = os.path.expanduser('~')
        shown = path.replace(home, '~', 1) if path.startswith(home) else path
        sub = Gtk.Label(label=shown, xalign=0,
                        ellipsize=Pango.EllipsizeMode.MIDDLE)
        sub.add_css_class('rw-choice-sub')
        box.append(title)
        box.append(sub)
        btn.set_child(box)
        btn.connect('toggled', self._on_folder_toggled, path)
        return btn

    def _on_folder_toggled(self, btn, path):
        if btn.get_active():
            self.folders.add(path)
        else:
            self.folders.discard(path)

    def _choose_folder(self, _btn):
        def on_path(path):
            if path not in self.extra_dirs and path not in self.folders:
                self.extra_dirs.append(path)
            self.folders.add(path)
            self._render()

        ui.choose_folder(self, 'Añadir a la copia', on_path)

    def _step_frequency(self):
        self._preselect_frequency()
        self._title('Paso 2 de 3', '¿Cada cuánto hacemos copia?',
                    'Rewind trabaja solo en segundo plano. Cada punto en el '
                    'tiempo ocupa poco: lo que no cambió no se duplica.')
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(2)
        flow.set_column_spacing(8)
        flow.set_row_spacing(8)
        group = None
        self._freq_buttons = {}
        for key, title, sub in SCHEDULE_CHOICES:
            btn = Gtk.ToggleButton()
            btn.add_css_class('rw-choice')
            btn.set_active(key == self.frequency)
            if group is not None:
                btn.set_group(group)
            group = btn
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            head = Gtk.Label(label=title, xalign=0)
            head.add_css_class('rw-choice-title')
            detail = Gtk.Label(label=sub, xalign=0)
            detail.add_css_class('rw-choice-sub')
            box.append(head)
            box.append(detail)
            btn.set_child(box)
            btn.connect('clicked', self._on_frequency, key)
            self._freq_buttons[key] = btn
            flow.insert(btn, -1)
        self.card.append(flow)

    def _on_frequency(self, _btn, key):
        self.frequency = key

    def _on_encrypt_toggled(self, btn):
        self.encrypt = btn.get_active()
        self._render()

    def _destination_editable(self):
        return 'local' in self.backend.profile_mode()

    def _step_destination(self):
        self._title('Paso 3 de 3', '¿Dónde lo guardamos?',
                    'Un disco externo es la opción más segura. También vale '
                    'otro equipo de tu red (se configura en Ajustes).')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        btn = Gtk.Button(label=self.destination or 'Elegir carpeta o disco…')
        btn.add_css_class('rw-choice')
        btn.set_halign(Gtk.Align.FILL)
        btn.set_sensitive(self._destination_editable())
        btn.connect('clicked', self._choose_destination)
        box.append(btn)
        note = Gtk.Label(xalign=0, wrap=True)
        note.add_css_class('rw-wiz-sub')
        if self._destination_editable():
            note.set_text('Destino actual: ' + (self.destination or
                                                'sin configurar'))
        else:
            note.set_text('Este perfil usa el modo '
                          f'{self.backend.profile_mode()}: el destino se '
                          'cambia en Ajustes.')
        self.destination_note = note
        box.append(note)
        if self.backend.profile_mode() in ('local', 'local_gocryptfs'):
            encrypt = Gtk.CheckButton(label='Cifrar la copia')
            encrypt.set_active(self.encrypt)
            encrypt.connect('toggled', self._on_encrypt_toggled)
            box.append(encrypt)
        if self.encrypt:
            backend = 'EncFS' if _backend.supports_encfs() else 'Gocryptfs'
            detail = Gtk.Label(label=f'Cifrado con {backend}', xalign=0)
            detail.add_css_class('rw-wiz-sub')
            box.append(detail)
            box.append(Gtk.Label(label='Contraseña', xalign=0))
            self.password_entry = Gtk.PasswordEntry()
            self.password_entry.set_text(self.password)
            ui.a11y(self.password_entry, 'Contraseña del cifrado')
            box.append(self.password_entry)
            box.append(Gtk.Label(label='Confirmar contraseña', xalign=0))
            self.password_confirm = Gtk.PasswordEntry()
            self.password_confirm.set_text(self.password_confirm_value)
            ui.a11y(self.password_confirm, 'Confirmar contraseña del cifrado')
            box.append(self.password_confirm)
            warning = Gtk.Label(
                label='Guarda la contraseña en un lugar seguro: sin ella no '
                      'podrás restaurar tus copias.', xalign=0, wrap=True)
            warning.add_css_class('rw-wiz-sub')
            box.append(warning)
        self.card.append(box)

    def _choose_destination(self, btn):
        def on_path(path):
            self.destination = path
            btn.set_label(path)
            self.destination_note.set_text('Destino actual: ' + path)

        ui.choose_folder(self, 'Carpeta de destino', on_path)

    def _step_summary(self):
        self._title('Todo listo', 'Esto es lo que haré')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        freq = {key: title for key, title, _sub in SCHEDULE_CHOICES}
        folders = ' · '.join(os.path.basename(p.rstrip('/')) or p
                             for p in sorted(self.folders))
        rows = [('Carpetas', folders or 'ninguna'),
                ('Frecuencia', freq.get(self.frequency, '')),
                ('Destino', self.destination or 'sin configurar')]
        if self.encrypt:
            backend = 'EncFS' if _backend.supports_encfs() else 'Gocryptfs'
            rows.append(('Cifrado', backend))
        rows.append(('Conservación', '2 años (limpieza automática)'))
        for title, value in rows:
            box.append(ui.row(title, value))
        self.card.append(box)

    def _nav(self):
        nav = Gtk.Box(spacing=10)
        nav.set_margin_top(6)

        # Leaving is always possible: the window manager close button already
        # works on every page, and this makes it visible and consistent.
        # Nothing is written until _apply(), so dismissing saves nothing.
        skip = Gtk.Button(label='Ahora no')
        skip.add_css_class('rw-soft')
        skip.set_tooltip_text('Salir sin configurar nada')
        ui.a11y(skip, 'Salir sin configurar nada')
        skip.connect('clicked', lambda *_: self.close())
        nav.append(skip)

        back = Gtk.Button(label='Atrás')
        back.add_css_class('rw-soft')
        back.set_visible(self.step > 0)
        back.connect('clicked', lambda *_: self._go(self.step - 1))
        nav.append(back)

        dots = Gtk.Box(spacing=6, hexpand=True, halign=Gtk.Align.CENTER)
        dots.add_css_class('rw-wiz-dots')
        for i in range(5):
            dot = Gtk.Label(label='●')
            if i == self.step:
                dot.add_css_class('on')
            dots.append(dot)
        nav.append(dots)

        labels = ['Empezar', 'Continuar', 'Continuar', 'Continuar',
                  'Crear mi primera copia']
        next_btn = Gtk.Button(label=labels[min(self.step, 4)])
        next_btn.add_css_class('rw-primary')
        next_btn.connect('clicked', lambda *_: self._go(self.step + 1))
        nav.append(next_btn)

        self.card.append(nav)

    def _go(self, step):
        if step >= 5:
            if not self._apply():
                return
        self.step = step
        self._render()

    # -------------------------------------------------------------- finish
    def _apply(self):
        if not self.folders:
            self._toast('Elige al menos una carpeta')
            return False
        if self._destination_editable() and not self.destination:
            self._toast('Elige un destino para las copias')
            return False
        if self.encrypt:
            if not destination_is_empty_or_initialized(self.destination):
                self._toast('Elige una carpeta nueva o vacía para cifrar')
                return False
            password = self.password_entry.get_text()
            if not password:
                self._toast('Escribe una contraseña para el cifrado')
                return False
            if password != self.password_confirm.get_text():
                self._toast('Las contraseñas no coinciden')
                return False
        try:
            entries, seen = [], set()
            for item in self.preserved + [(p, 0) for p in sorted(self.folders)]:
                if item in seen:
                    continue
                seen.add(item)
                entries.append(item)
            self.cfg.setInclude(entries)
            const = SCHEDULE_CONST.get(self.frequency, 'DISABLED')
            mode = getattr(self.cfg, const, self.cfg.NONE)
            self.cfg.setScheduleMode(mode)
            units = getattr(_backend.bitbase_module, 'TimeUnit', None)
            if units is not None:
                self.cfg.setRemoveOldSnapshots(True, 2, units.YEAR)
            if self.encrypt:
                self.backend.setup_local_gocryptfs(
                    self.destination, self.password_entry.get_text(),
                    save_in_keyring=bool(getattr(
                        _backend.tools, 'KEYRING_SUPPORTED', False)),
                    use_ram_cache=True)
            else:
                if self._destination_editable() and self.destination:
                    self.backend.set_destination_path(self.destination)
                if not ui.save(self.cfg):
                    self._toast('No se pudo guardar la configuración')
                    return False
            self._applied = True
        except Exception as exc:  # noqa: BLE001
            self._toast(f'No se pudo guardar: {exc}')
            return False
        Worker(self.backend.apply_automation, lambda *_: None)
        return True

    def _step_progress(self):
        self._title('Trabajando', 'Primera copia en marcha',
                    'Los archivos que no cambian no se duplican: solo se '
                    'enlazan.')
        self.bar = Gtk.ProgressBar(show_text=False)
        self.bar.set_pulse_step(0.08)
        self.card.append(self.bar)
        self.file_label = Gtk.Label(label='Preparando…', xalign=0, wrap=True)
        self.file_label.add_css_class('rw-wiz-sub')
        self.card.append(self.file_label)

        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        self.stop_btn = Gtk.Button(label='Parar la copia')
        self.stop_btn.add_css_class('rw-danger')
        self.stop_btn.connect('clicked', self._stop_backup)
        buttons.append(self.stop_btn)
        self.card.append(buttons)

        if self.backend.busy_backup:
            self._fail('Ya hay otra copia en curso. Espera a que termine.')
            return
        self._snapshots_before = len(self.backend.list_snapshots())
        self._running = True
        self.backend.busy_backup = True
        self.backend.busy_owner = 'wizard'
        self._pulse = GLib.timeout_add(400, self._tick)
        Worker(self.backend.take_snapshot, self._done)

    def _tick(self):
        self.bar.pulse()
        try:
            message = self.backend.take_snapshot_message()
            if message and message[1]:
                self.file_label.set_text(message[1][-110:])
        except Exception:  # noqa: BLE001
            pass
        return True

    def _stop_backup(self, *_):
        self._stop_requested = True
        self._stop_attempts = 0
        if hasattr(self, 'file_label'):
            self.file_label.set_text('Deteniendo la copia…')
        if hasattr(self, 'stop_btn'):
            self.stop_btn.set_sensitive(False)
        self._attempt_stop()
        if self._escalate_source is None:
            self._escalate_source = GLib.timeout_add_seconds(
                6, self._escalate_stop)

    def _attempt_stop(self):
        if not self._stop_requested or not self._running:
            return False
        if not self.backend.stop_snapshot() and self._stop_attempts < 4:
            self._stop_attempts += 1
            GLib.timeout_add(1000, self._attempt_stop)
        return False

    def _escalate_stop(self):
        self._escalate_source = None
        if self._running and self._stop_requested:
            import signal
            self.backend.stop_snapshot(signal.SIGKILL)
        return False

    def _done(self, error_message, error):
        self._stop_pulse()
        self._running = False
        if self._escalate_source is not None:
            GLib.source_remove(self._escalate_source)
            self._escalate_source = None
        if self.backend.busy_owner == 'wizard':
            self.backend.busy_backup = False
            self.backend.busy_owner = None
        stopped = getattr(self, '_stop_requested', False)
        fresh = self.backend.list_snapshots()
        created = len(fresh) > self._snapshots_before
        if stopped and created:
            newest = fresh[0]
            if getattr(newest, 'failed', False):
                try:
                    self.backend.delete_snapshot(newest)
                    created = False
                except Exception:  # noqa: BLE001
                    pass

        # notify after discarding the partial snapshot, or the window would
        # list a point that no longer exists on disk
        self._notify()
        if self._closing:
            self.close()
            return
        if stopped and not created:
            self._fail('Copia detenida. No se guardó nada.')
            return
        if error or error_message:
            detail = str(error or error_message)
            self._fail(f'La primera copia falló: {detail[:160]}')
            return

        self.bar.set_fraction(1.0)
        self.file_label.set_text('Primera copia terminada')
        GLib.timeout_add(900, self._close_after)

    def _fail(self, message):
        self.bar.set_fraction(0.0)
        self.file_label.set_text(message)
        row = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        retry = Gtk.Button(label='Reintentar')
        retry.add_css_class('rw-primary')
        retry.connect('clicked', lambda *_: self._retry())
        close = Gtk.Button(label='Cerrar')
        close.add_css_class('rw-soft')
        close.connect('clicked', lambda *_: self.close())
        row.append(close)
        row.append(retry)
        self.card.append(row)

    def _retry(self):
        self._stop_requested = False
        self.step = 5
        self._render()

    def _stop_pulse(self):
        if self._pulse is not None:
            GLib.source_remove(self._pulse)
            self._pulse = None

    def _notify(self):
        if self._applied and self.on_done:
            callback, self.on_done = self.on_done, None
            try:
                callback()
            except Exception:  # noqa: BLE001
                pass

    def _close_after(self):
        self._closed_after_run = True
        self._notify()
        self.close()
        return False

    def _on_close_request(self, *_):
        if not self._running:
            self._notify()
            return False
        dialog = Adw.MessageDialog(
            transient_for=self, modal=True,
            heading='La primera copia sigue en marcha',
            body='Si cierras ahora, la copia se quedará a medias.')
        dialog.add_response('cancel', 'Cancelar')
        dialog.add_response('stop', 'Detener y salir')
        dialog.set_response_appearance('stop',
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response('cancel')

        def on_response(_d, response):
            if response != 'stop':
                return
            # keep _running True so the stop keeps retrying and escalating;
            # _done closes the window after discarding the partial snapshot
            self._closing = True
            self._stop_backup()

        dialog.connect('response', on_response)
        dialog.present()
        return True

    def _toast(self, message):
        label = Gtk.Label(label=message, xalign=0, wrap=True)
        label.add_css_class('rw-wiz-sub')
        self.card.append(label)
