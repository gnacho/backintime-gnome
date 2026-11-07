"""First-run wizard: folders, frequency, destination, summary, first backup.

Writes the profile through ``config.Config`` and then runs the real
``Snapshots.backup()`` while polling ``takeSnapshotMessage()`` for progress,
the same way the main window does.
"""
import os

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import ui  # noqa: E402
from . import backend as _backend  # noqa: E402
from .backend import Worker  # noqa: E402

CANDIDATES = ['Documentos', 'Imágenes', 'Música', 'Vídeos', 'Escritorio',
              'Proyectos']


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
        self.frequency = 'day'
        self.destination = ''

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

        self._preselect()
        self._render()

    # ------------------------------------------------------------- choices
    def _preselect(self):
        home = os.path.expanduser('~')
        try:
            entries = [p for p, t in self.cfg.include() if t == 0]
        except Exception:  # noqa: BLE001
            entries = []
        for name in CANDIDATES:
            path = os.path.join(home, name)
            if path in entries:
                self.folders.add(path)
        if not self.folders:
            for name in ('Documentos', 'Imágenes'):
                path = os.path.join(home, name)
                if os.path.isdir(path):
                    self.folders.add(path)
        try:
            self.destination = self.backend.destination_path() or ''
        except Exception:  # noqa: BLE001
            self.destination = ''

    # -------------------------------------------------------------- render
    def _render(self):
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
        home = os.path.expanduser('~')
        for name in CANDIDATES:
            path = os.path.join(home, name)
            if not os.path.isdir(path):
                continue
            btn = Gtk.ToggleButton()
            btn.add_css_class('rw-choice')
            btn.set_active(path in self.folders)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            title = Gtk.Label(label=name, xalign=0)
            title.add_css_class('rw-choice-title')
            sub = Gtk.Label(label='Carpeta personal', xalign=0)
            sub.add_css_class('rw-choice-sub')
            box.append(title)
            box.append(sub)
            btn.set_child(box)
            btn.connect('toggled', self._on_folder_toggled, path)
            flow.insert(btn, -1)
        self.card.append(flow)

    def _on_folder_toggled(self, btn, path):
        if btn.get_active():
            self.folders.add(path)
        else:
            self.folders.discard(path)

    def _step_frequency(self):
        self._title('Paso 2 de 3', '¿Cada cuánto hacemos copia?',
                    'Rewind trabaja solo en segundo plano. Cada punto en el '
                    'tiempo ocupa poco: lo que no cambió no se duplica.')
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(2)
        flow.set_column_spacing(8)
        flow.set_row_spacing(8)
        options = [('hour', 'Cada hora', 'Para trabajo intenso'),
                   ('day', 'Cada día', 'El equilibrio perfecto'),
                   ('week', 'Cada semana', 'Si cambian poco'),
                   ('none', 'Solo cuando yo diga', 'Control total')]
        group = None
        self._freq_buttons = {}
        for key, title, sub in options:
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

    def _step_destination(self):
        self._title('Paso 3 de 3', '¿Dónde lo guardamos?',
                    'Un disco externo es la opción más segura. También vale '
                    'otro equipo de tu red (se configura luego en Ajustes).')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        btn = Gtk.Button(label=self.destination or 'Elegir carpeta o disco…')
        btn.add_css_class('rw-choice')
        btn.set_halign(Gtk.Align.FILL)
        btn.connect('clicked', self._choose_destination)
        box.append(btn)
        note = Gtk.Label(label='Destino actual: ' + (self.destination or 'sin '
                          'configurar'), xalign=0)
        note.add_css_class('rw-wiz-sub')
        self.destination_note = note
        box.append(note)
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
        freq = {'hour': 'Cada hora', 'day': 'Cada día a las 09:00',
                'week': 'Cada lunes', 'none': 'Solo manual'}[self.frequency]
        folders = ' · '.join(os.path.basename(p) for p in sorted(self.folders))
        for title, value in (('Carpetas', folders or 'ninguna'),
                             ('Frecuencia', freq),
                             ('Destino', self.destination or 'sin configurar'),
                             ('Conservación', '2 años (limpieza automática)')):
            box.append(ui.row(title, value))
        self.card.append(box)

    def _nav(self):
        nav = Gtk.Box(spacing=10)
        nav.set_margin_top(6)
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
        next_btn.get_style_context().add_class('rw-wiz-next')
        next_btn.connect('clicked', lambda *_: self._go(self.step + 1))
        nav.append(next_btn)

        if self.step == 0:
            skip = Gtk.Button(label='Ahora no')
            skip.add_css_class('rw-soft')
            skip.connect('clicked', lambda *_: self.close())
            nav.prepend(skip)
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
        if not self.destination:
            self._toast('Elige un destino para las copias')
            return False
        try:
            self.cfg.setInclude([(p, 0) for p in sorted(self.folders)])
            mode = {'hour': self.cfg.HOUR, 'day': self.cfg.DAY,
                    'week': self.cfg.WEEK, 'none': self.cfg.NONE}[self.frequency]
            self.cfg.setScheduleMode(mode)
            self.cfg.set_snapshots_path(self.destination)
            units = getattr(_backend.bitbase_module, 'TimeUnit', None)
            if units is not None:
                self.cfg.setRemoveOldSnapshots(True, 2, units.YEAR)
            if not ui.save(self.cfg):
                self._toast('No se pudo guardar la configuración')
                return False
        except Exception as exc:  # noqa: BLE001
            self._toast(f'No se pudo guardar: {exc}')
            return False
        return True

    def _step_progress(self):
        self._title('Trabajando', 'Primera copia en marcha',
                    'Los archivos que no cambian no se duplican: solo se '
                    'enlazan.')
        self.bar = Gtk.ProgressBar(show_text=False)
        self.bar.set_pulse_step(0.08)
        self.card.append(self.bar)
        self.file_label = Gtk.Label(label='Preparando…', xalign=0)
        self.file_label.add_css_class('rw-wiz-sub')
        self.card.append(self.file_label)

        self._pulse = GLib.timeout_add(300, self._tick)
        Worker(self.backend.take_snapshot, self._done)

    def _tick(self):
        self.bar.pulse()
        try:
            msg = self.backend.take_snapshot_message()
            if msg and msg[1]:
                self.file_label.set_text(msg[1][-110:])
        except Exception:  # noqa: BLE001
            pass
        return True

    def _done(self, error_message, error):
        if getattr(self, '_pulse', None):
            GLib.source_remove(self._pulse)
            self._pulse = None
        if error or error_message:
            detail = str(error or error_message)
            self.file_label.set_text(f'La primera copia falló: {detail[:160]}')
            self.bar.set_fraction(0.0)
        else:
            self.bar.set_fraction(1.0)
            self.file_label.set_text('Primera copia terminada')
        GLib.timeout_add(900, self._close_after)

    def _close_after(self):
        self.close()
        if self.on_done:
            self.on_done()
        return False

    def _toast(self, message):
        label = Gtk.Label(label=message, xalign=0)
        label.add_css_class('rw-wiz-sub')
        self.card.append(label)
