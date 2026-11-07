"""Main window - "Rewind" layout.

Present mode answers "is everything protected?", time mode answers "what
did my files look like three days ago?". A timeline scrubber at the bottom
moves between snapshots; the browser above it shows the content at that
point with badges for what changed afterwards.

Layout (Adw 1.2 / GTK 4.8 baseline):
    Adw.ApplicationWindow
      Adw.HeaderBar (brand, search, settings, primary action)
      Adw.ToastOverlay
        Gtk.Overlay
          Gtk.Box
            Gtk.ScrolledWindow -> content (hero, date head, browser)
            timeline bar (pinned at the bottom)
          floating selection action bar
"""
import datetime
import os

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from bitgnome import ui  # noqa: E402
from bitgnome.backend import Worker, format_size  # noqa: E402
from bitgnome.timeline import Timeline  # noqa: E402

FILE_ICONS = {
    '.jpg': 'image-x-generic-symbolic',
    '.jpeg': 'image-x-generic-symbolic',
    '.png': 'image-x-generic-symbolic',
    '.gif': 'image-x-generic-symbolic',
    '.mp3': 'audio-x-generic-symbolic',
    '.flac': 'audio-x-generic-symbolic',
    '.ogg': 'audio-x-generic-symbolic',
    '.mp4': 'video-x-generic-symbolic',
    '.mkv': 'video-x-generic-symbolic',
    '.pdf': 'x-office-document-symbolic',
    '.odt': 'x-office-document-symbolic',
    '.ods': 'x-office-spreadsheet-symbolic',
    '.md': 'text-x-generic-symbolic',
}


def _icon_for(name, is_dir):
    if is_dir:
        return 'folder-symbolic'
    ext = os.path.splitext(name)[1].lower()
    return FILE_ICONS.get(ext, 'text-x-generic-symbolic')


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='Rewind')
        self.set_default_size(1160, 780)
        self.app = app
        self.backend = app.backend
        self.cfg = app.backend.cfg

        # state
        self.sids = []          # newest first, as returned by the core
        self.chrono = []        # oldest first, timeline order
        self.index = None       # index into chrono; None = "Ahora"
        self.rel = ''           # path inside the backup, '' = roots
        self.view = 'grid'
        self.selected = None    # (name, is_dir)
        self._snap_worker_running = False
        self._quit_when_done = False
        self._request_stop = None
        self._close_dialog = None
        self._playing = False
        self._play_source = None

        self._load_css()
        self._build_ui()
        self._build_shortcuts()
        self.connect('close-request', self._on_close_request)

        if self.backend.error:
            self._show_backend_error(self.backend.error)
        else:
            self.reload_snapshots()
            GLib.idle_add(self._maybe_wizard)

    # ------------------------------------------------------------------ UI
    def _load_css(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'style.css')
        provider = Gtk.CssProvider()
        try:
            provider.load_from_path(path)
        except GLib.Error:
            return
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add_css_class('rw-root')
        self.set_content(root)
        self.body = root

        root.append(self._build_header())

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_vexpand(True)
        root.append(self.toast_overlay)

        self.overlay = Gtk.Overlay()
        self.toast_overlay.set_child(self.overlay)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.overlay.set_child(column)

        self.scrolled = Gtk.ScrolledWindow(vexpand=True)
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        column.append(self.scrolled)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.content.add_css_class('rw-content')
        self.scrolled.set_child(self.content)

        self._build_hero()
        self._build_date_head()
        self._build_section()
        self._build_crumbs()
        self._build_browser()

        column.append(self._build_timeline())
        self._build_actionbar()

    def _build_header(self):
        header = Adw.HeaderBar()
        header.add_css_class('rw-header')

        brand = Gtk.Box(spacing=10)
        brand.set_valign(Gtk.Align.CENTER)
        logo = Gtk.Box()
        logo.add_css_class('rw-logo')
        logo.set_valign(Gtk.Align.CENTER)
        logo.set_halign(Gtk.Align.CENTER)
        image = Gtk.Image(icon_name='edit-undo-symbolic', pixel_size=16)
        image.set_valign(Gtk.Align.CENTER)
        image.set_halign(Gtk.Align.CENTER)
        logo.append(image)
        brand.append(logo)

        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        titles.set_valign(Gtk.Align.CENTER)
        self.title_main = Gtk.Label(label='Rewind', xalign=0)
        self.title_main.add_css_class('rw-title')
        self.title_sub = Gtk.Label(label='Copias de seguridad', xalign=0)
        self.title_sub.add_css_class('rw-subtitle')
        titles.append(self.title_main)
        titles.append(self.title_sub)
        brand.append(titles)
        header.pack_start(brand)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text('Buscar en el tiempo…')
        self.search.add_css_class('rw-search')
        self.search.set_width_chars(18)
        self.search.connect('search-changed', lambda *_: self.render_files())
        header.set_title_widget(self.search)

        primary = Gtk.Button(label='Crear copia ahora')
        primary.add_css_class('rw-primary')
        primary.set_tooltip_text('Tomar una instantánea ahora (Ctrl+S)')
        primary.connect('clicked', lambda *_: self.take_snapshot())
        header.pack_end(primary)
        self.snap_btn = primary

        self.settings_btn = Gtk.Button(icon_name='emblem-system-symbolic')
        self.settings_btn.add_css_class('rw-ghost')
        self.settings_btn.set_tooltip_text('Ajustes (Ctrl+,)')
        self.settings_btn.connect('clicked', lambda *_: self.open_settings())
        header.pack_end(self.settings_btn)

        menu = Gio.Menu()
        menu.append('Tomar instantánea', 'app.take-snapshot')
        menu.append('Restaurar…', 'app.restore')
        menu.append('Restaurar en otra ubicación…', 'app.restore-to')
        section = Gio.Menu()
        section.append('Registro de la instantánea', 'app.log-view')
        section.append('Comparar con la anterior', 'app.diff')
        section.append('Eliminar esta instantánea', 'app.delete-snapshot')
        menu.append_section(None, section)
        section2 = Gio.Menu()
        section2.append('Gestionar perfiles', 'app.advanced-preferences')
        section2.append('Atajos de teclado', 'app.shortcuts')
        section2.append('Tema claro/oscuro', 'app.toggle-theme')
        section2.append('Acerca de Back In Time', 'app.about')
        menu.append_section(None, section2)

        menu_btn = Gtk.MenuButton(icon_name='open-menu-symbolic',
                                  menu_model=menu)
        menu_btn.add_css_class('rw-ghost')
        menu_btn.set_tooltip_text('Menú principal')
        header.pack_end(menu_btn)
        return header

    def _build_hero(self):
        self.hero = Gtk.Box(spacing=22)
        self.hero.add_css_class('rw-hero')
        self.content.append(self.hero)

        ring = Gtk.Overlay()
        ring.set_valign(Gtk.Align.CENTER)
        area = Gtk.DrawingArea()
        area.set_content_width(82)
        area.set_content_height(82)
        area.set_draw_func(self._draw_ring)
        ring.set_child(area)
        check = Gtk.Image(icon_name='object-select-symbolic', pixel_size=26)
        check.set_halign(Gtk.Align.CENTER)
        check.set_valign(Gtk.Align.CENTER)
        ring.add_overlay(check)
        self.ring_check = check
        self.ring_area = area
        self.hero.append(ring)

        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        texts.set_valign(Gtk.Align.CENTER)
        texts.set_hexpand(True)
        self.hero_title = Gtk.Label(xalign=0)
        self.hero_title.add_css_class('rw-hbig')
        self.hero_sub = Gtk.Label(xalign=0)
        self.hero_sub.add_css_class('rw-hsub')
        self.hero_sub.set_wrap(True)
        self.hero_sub.set_max_width_chars(64)
        texts.append(self.hero_title)
        texts.append(self.hero_sub)
        self.hero.append(texts)

        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        meta.set_valign(Gtk.Align.CENTER)
        self.meta_dest = Gtk.Label(xalign=1)
        self.meta_dest.add_css_class('rw-meta')
        self.meta_ret = Gtk.Label(xalign=1)
        self.meta_ret.add_css_class('rw-meta')
        meta.append(self.meta_dest)
        meta.append(self.meta_ret)
        self.hero.append(meta)

    def _build_date_head(self):
        self.date_head = Gtk.Box(spacing=16)
        self.date_head.set_margin_bottom(6)
        self.date_big = Gtk.Label(xalign=0)
        self.date_big.add_css_class('rw-date')
        self.date_rel = Gtk.Label(xalign=0)
        self.date_rel.add_css_class('rw-date-rel')
        self.date_rel.set_valign(Gtk.Align.END)
        self.date_head.append(self.date_big)
        self.date_head.append(self.date_rel)
        self.date_head.set_visible(False)
        self.content.append(self.date_head)

    def _build_section(self):
        section = Gtk.Box(spacing=12)
        section.set_valign(Gtk.Align.CENTER)
        section.set_margin_top(18)
        section.set_margin_bottom(10)
        self.section_title = Gtk.Label(label='Tus carpetas protegidas', xalign=0)
        self.section_title.add_css_class('rw-section-title')
        self.section_hint = Gtk.Label(label='Doble clic para entrar',
                                      xalign=0)
        self.section_hint.add_css_class('rw-hint')
        grow = Gtk.Box()
        grow.set_hexpand(True)
        section.append(self.section_title)
        section.append(self.section_hint)
        section.append(grow)

        toggle = Gtk.Box(spacing=2)
        toggle.add_css_class('rw-seg')
        self.grid_btn = Gtk.ToggleButton(icon_name='view-grid-symbolic')
        self.grid_btn.set_active(True)
        self.grid_btn.set_tooltip_text('Cuadrícula')
        self.grid_btn.connect('toggled', self._on_view_toggled, 'grid')
        self.list_btn = Gtk.ToggleButton(icon_name='view-list-symbolic')
        self.list_btn.set_tooltip_text('Lista')
        self.list_btn.connect('toggled', self._on_view_toggled, 'list')
        self.list_btn.set_group(self.grid_btn)
        toggle.append(self.grid_btn)
        toggle.append(self.list_btn)
        section.append(toggle)
        self.content.append(section)

    def _build_crumbs(self):
        self.crumbs = Gtk.Box(spacing=4)
        self.crumbs.add_css_class('rw-crumbs')
        self.content.append(self.crumbs)

    def _build_browser(self):
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.content.append(self.stack)

        self.grid = Gtk.FlowBox()
        self.grid.add_css_class('rw-grid')
        self.grid.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.grid.set_max_children_per_line(8)
        self.grid.set_min_children_per_line(1)
        self.grid.set_homogeneous(True)
        self.grid.set_column_spacing(10)
        self.grid.set_row_spacing(10)
        self.grid.connect('selected-children-changed', self._on_grid_selected)
        self.grid.connect('child-activated', self._on_grid_activated)
        self.stack.add_named(self.grid, 'grid')

        self.file_list = Gtk.ListBox()
        self.file_list.add_css_class('rw-list')
        self.file_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.file_list.set_valign(Gtk.Align.START)
        self.file_list.connect('row-selected', self._on_list_selected)
        self.file_list.connect('row-activated', self._on_list_activated)
        self.stack.add_named(self.file_list, 'list')

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        empty.add_css_class('rw-empty')
        empty.set_halign(Gtk.Align.CENTER)
        self.empty_title = Gtk.Label(label='Nada por aquí')
        self.empty_title.add_css_class('rw-empty-title')
        self.empty_hint = Gtk.Label(label='Carpeta vacía en este momento')
        empty.append(self.empty_title)
        empty.append(self.empty_hint)
        self.stack.add_named(empty, 'empty')

    def _build_timeline(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bar.add_css_class('rw-timeline')

        top = Gtk.Box(spacing=12)
        self.play_btn = Gtk.Button(icon_name='media-playback-start-symbolic')
        self.play_btn.add_css_class('rw-play')
        self.play_btn.set_tooltip_text('Reproducir el paso del tiempo')
        self.play_btn.connect('clicked', lambda *_: self.toggle_play())
        top.append(self.play_btn)

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.tl_label = Gtk.Label(label='Ahora', xalign=0)
        self.tl_label.add_css_class('rw-tl-label')
        self.tl_sub = Gtk.Label(label='Estado actual de tus archivos', xalign=0)
        self.tl_sub.add_css_class('rw-tl-sub')
        labels.append(self.tl_label)
        labels.append(self.tl_sub)
        top.append(labels)

        grow = Gtk.Box()
        grow.set_hexpand(True)
        top.append(grow)

        self.present_btn = Gtk.Button(label='Volver al presente')
        self.present_btn.add_css_class('rw-soft')
        self.present_btn.connect('clicked', lambda *_: self.set_snapshot(None))
        self.present_btn.set_visible(False)
        top.append(self.present_btn)
        bar.append(top)

        self.timeline = Timeline()
        self.timeline.connect('seek', lambda _w, index: self.set_snapshot(index))
        bar.append(self.timeline)
        return bar

    def _build_actionbar(self):
        bar = Gtk.Box(spacing=4)
        bar.add_css_class('rw-actionbar')
        bar.set_halign(Gtk.Align.CENTER)
        bar.set_valign(Gtk.Align.END)
        bar.set_margin_bottom(96)
        self.actionbar_name = Gtk.Label(label='', xalign=0)
        self.actionbar_name.add_css_class('rw-actionbar-name')
        bar.append(self.actionbar_name)

        self.restore_btn = Gtk.Button(label='Restaurar')
        self.restore_btn.add_css_class('rw-primary')
        self.restore_btn.connect('clicked', lambda *_: self.restore_selection())
        bar.append(self.restore_btn)

        changes = Gtk.Button(label='Ver cambios')
        changes.add_css_class('rw-soft')
        changes.connect('clicked', lambda *_: self.show_diff())
        bar.append(changes)

        forget = Gtk.Button(label='Dejar de copiar')
        forget.add_css_class('rw-soft')
        forget.connect('clicked', lambda *_: self.forget_selection())
        bar.append(forget)

        self.actionbar = bar
        self.overlay.add_overlay(bar)
        bar.set_visible(False)

    def _build_shortcuts(self):
        controller = Gtk.ShortcutController()
        triggers = [
            (Gtk.KeyvalTrigger.new(Gdk.keyval_from_name('Left'), 0),
             self._on_left),
            (Gtk.KeyvalTrigger.new(Gdk.keyval_from_name('Right'), 0),
             self._on_right),
            (Gtk.KeyvalTrigger.new(Gdk.keyval_from_name('Escape'), 0),
             self._on_escape),
        ]
        for trigger, action in triggers:
            controller.add_shortcut(Gtk.Shortcut.new(
                trigger, Gtk.CallbackAction.new(action)))
        self.add_controller(controller)

    def _text_focused(self):
        focus = self.get_focus()
        return isinstance(focus, (Gtk.Entry, Gtk.SearchEntry, Gtk.Text))

    def _on_left(self, *_):
        if self._text_focused():
            return False
        self._step_snapshot(-1)
        return True

    def _on_right(self, *_):
        if self._text_focused():
            return False
        self._step_snapshot(1)
        return True

    def _on_escape(self, *_):
        if self.search.get_text():
            self.search.set_text('')
            return True
        self.set_snapshot(None)
        return True

    # --------------------------------------------------------------- utils
    def toast(self, message, action_label=None, action=None):
        toast = Adw.Toast(title=message, timeout=4)
        if action_label and action:
            toast.set_button_label(action_label)
            toast.connect('button-clicked', lambda *_: action())
        self.toast_overlay.add_toast(toast)

    def _maybe_wizard(self):
        if self.backend.error or self.cfg is None:
            return False
        if getattr(self.app, 'force_wizard', False):
            self.open_wizard()
            return False
        try:
            first_run = not self.cfg.include() or \
                not self.backend.destination_path()
        except Exception:  # noqa: BLE001
            first_run = False
        if first_run:
            self.open_wizard()
        return False

    def open_wizard(self):
        from bitgnome.wizard import FirstRunWizard
        wizard = FirstRunWizard(self.app, on_done=self.reload_snapshots)
        self._wizard = wizard
        wizard.present()
        return wizard

    def open_settings(self):
        from bitgnome.settings_sheet import SettingsSheet
        sheet = SettingsSheet(self.app, on_change=self._on_settings_changed)
        self._settings_sheet = sheet
        sheet.present()

    def _toggle_dark(self, *_):
        manager = Adw.StyleManager.get_default()
        manager.set_color_scheme(
            Adw.ColorScheme.DEFAULT if manager.get_dark()
            else Adw.ColorScheme.FORCE_DARK)

    def _on_settings_changed(self):
        self._update_hero()
        self.render_files()

    # ------------------------------------------------------------ snapshots
    def reload_snapshots(self):
        self.sids = self.backend.list_snapshots()
        self.chrono = list(reversed(self.sids))
        if self.index is not None:
            self.index = min(self.index, len(self.chrono) - 1) \
                if self.chrono else None
        self.timeline.set_count(len(self.chrono))
        self._update_hero()
        self._apply_mode()
        self.render_files()

    def _current_sid(self):
        if self.index is None or not self.chrono:
            return None
        return self.chrono[self.index]

    def _newer_sid(self):
        """Snapshot right after the selected point, None when it is 'now'."""
        if self.index is None:
            return None
        if self.index + 1 < len(self.chrono):
            return self.chrono[self.index + 1]
        return None

    def _step_snapshot(self, delta):
        total = len(self.chrono)
        if total == 0:
            return
        if self.index is None:
            self.set_snapshot(total - 1)
            return
        target = self.index + delta
        if target >= total:
            self.set_snapshot(None)
        elif target < 0:
            self.set_snapshot(0)
        else:
            self.set_snapshot(target)

    def set_snapshot(self, index):
        if index is None:
            self.index = None
        elif not self.chrono:
            self.index = None
        else:
            self.index = max(0, min(int(index), len(self.chrono) - 1))
        self.rel = ''
        self.selected = None
        self._stop_play()
        self._apply_mode()
        self.render_files()

    def _apply_mode(self):
        in_time = self.index is not None
        if in_time:
            self.add_css_class('time')
        else:
            self.remove_css_class('time')
        self.hero.set_visible(not in_time)
        self.date_head.set_visible(in_time)
        self.present_btn.set_visible(in_time)
        self.timeline.set_current(self.index)
        self.timeline.set_time_mode(in_time)

        if in_time:
            sid = self._current_sid()
            when = self.backend.snapshot_label(sid) if sid is not None else None
            if isinstance(when, datetime.datetime):
                self.title_sub.set_text('Viajando en el tiempo')
                self.section_title.set_text('Así se veían tus carpetas')
                self.section_hint.set_text('Lo que cambió después lleva etiqueta')
                self.date_big.set_text(ui.day_label(when))
                self.date_rel.set_markup(
                    f'Así estaba todo <b>{ui.ago_label(when)}</b> '
                    f'a las {ui.time_label(when)}')
                self.tl_label.set_text(
                    f'{ui.day_label(when)} · {ui.time_label(when)}')
                self.tl_sub.set_text(
                    f'punto {self.index + 1} de {len(self.chrono)}')
        else:
            self.title_sub.set_text('Copias de seguridad')
            self.section_title.set_text('Tus carpetas protegidas')
            self.section_hint.set_text('Doble clic para entrar')
            self.tl_label.set_text('Ahora')
            self.tl_sub.set_text('Estado actual de tus archivos')
        if self._snap_worker_running:
            self.title_sub.set_text('Creando copia…')
        self._update_actionbar()

    # ---------------------------------------------------------------- hero
    def _disk_usage(self):
        path = self.backend.destination_path()
        if path and os.path.isdir(path):
            try:
                usage = os.statvfs(path)
                return (usage.f_bavail * usage.f_frsize,
                        usage.f_blocks * usage.f_frsize)
            except OSError:
                pass
        return None, None

    def _update_hero(self):
        count = len(self.chrono)
        free, total = self._disk_usage()
        try:
            includes = self.cfg.include() if self.cfg else []
        except Exception:  # noqa: BLE001
            includes = []
        failed = bool(getattr(self._last_sid(), 'failed', False)) if count else False

        if count == 0:
            self.hero_title.set_text('Sin copias todavía')
            self.hero_sub.set_text(
                'Crea la primera copia para empezar a viajar en el tiempo.')
            self.ring_check.set_from_icon_name('dialog-warning-symbolic')
            self.ring_state = 'warn'
        elif failed:
            self.hero_title.set_text('Última copia con errores')
            self.hero_sub.set_text(
                f'{count} puntos en el tiempo · revisa el registro')
            self.ring_check.set_from_icon_name('dialog-warning-symbolic')
            self.ring_state = 'warn'
        else:
            self.hero_title.set_text('Todo protegido')
            self.hero_sub.set_text(
                f'Última copia {self._last_copy_text()} · {self._next_copy_text()}'
                f'\n{len(includes)} carpetas vigiladas · {count} puntos en el tiempo')
            self.ring_check.set_from_icon_name('object-select-symbolic')
            self.ring_state = 'ok'

        if free is not None and total:
            pct = int(free * 100 / total) if total else 0
            self.meta_dest.set_markup(
                f'Destino · <b>{format_size(free)} libres</b> ({pct}%)')
        else:
            self.meta_dest.set_text('Destino · sin configurar')
        self.meta_ret.set_markup(self._retention_text())
        self.ring_area.queue_draw()

    def _last_sid(self):
        return self.chrono[-1] if self.chrono else None

    def _last_copy_text(self):
        sid = self._last_sid()
        when = self.backend.snapshot_label(sid) if sid is not None else None
        if not isinstance(when, datetime.datetime):
            return 'sin fecha'
        return f'{ui.ago_label(when)} a las {ui.time_label(when)}'

    def _next_copy_text(self):
        try:
            mode = str(self.cfg.scheduleMode()).lower()
        except Exception:  # noqa: BLE001
            return 'sin programación'
        if 'hour' in mode:
            return 'la próxima, en menos de una hora'
        if 'day' in mode:
            try:
                hour, minute = self.cfg.scheduleHourMinute()
                return f'la próxima, mañana a las {hour:02d}:{minute:02d}'
            except Exception:  # noqa: BLE001
                return 'la próxima, mañana'
        if 'week' in mode:
            return 'la próxima, la semana que viene'
        if 'udev' in mode:
            return 'cuando conectes la unidad'
        return 'sin copias automáticas'

    def _retention_text(self):
        try:
            enabled, value, unit = self.cfg.removeOldSnapshots()
        except Exception:  # noqa: BLE001
            return 'Retención · <b>sin límite</b>'
        if not enabled:
            return 'Retención · <b>sin límite</b>'
        names = {'HOUR': ('hora', 'horas'), 'DAY': ('día', 'días'),
                 'WEEK': ('semana', 'semanas'), 'MONTH': ('mes', 'meses'),
                 'YEAR': ('año', 'años')}
        unit_name = getattr(unit, 'name', 'DAY')
        singular, plural = names.get(unit_name, ('día', 'días'))
        label = singular if value == 1 else plural
        return f'Retención · <b>{value} {label}</b>'

    def _draw_ring(self, area, cr, width, height):
        state = getattr(self, 'ring_state', 'warn')
        color = (0.078, 0.722, 0.651, 1) if state == 'ok' \
            else (0.851, 0.467, 0.024, 1)
        if self.has_css_class('time'):
            color = (0.176, 0.831, 0.749, 1) if state == 'ok' \
                else (0.984, 0.749, 0.141, 1)
        import math
        cx, cy = width / 2, height / 2
        radius = min(cx, cy) - 6
        cr.set_line_width(7)
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.18)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()
        if state == 'ok':
            cr.set_source_rgba(*color)
            cr.set_line_cap(1)  # round
            cr.arc(cx, cy, radius, -math.pi / 2, 1.5 * math.pi)
            cr.stroke()

    # ------------------------------------------------------------- browser
    def _include_roots(self):
        try:
            return [(path, ftype) for path, ftype in self.cfg.include()]
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _sid_path(sid, rel):
        # 2.0's pathBackup() rejects an empty string argument
        return sid.pathBackup(rel) if rel else sid.pathBackup()

    def _current_dir(self):
        """Filesystem directory for the current point and path."""
        if not self.rel:
            return None
        sid = self._current_sid()
        if sid is not None:
            return self._sid_path(sid, self.rel)
        return '/' + self.rel

    def _root_entries(self):
        entries = []
        for path, ftype in self._include_roots():
            name = os.path.basename(path.rstrip('/')) or path
            entries.append({'name': name, 'is_dir': ftype == 0,
                            'size': None, 'mtime': 0,
                            'rel': path.lstrip('/')})
        entries.sort(key=lambda e: (not e['is_dir'], e['name'].lower()))
        return entries

    def _iter_dir(self, base):
        try:
            with os.scandir(base) as it:
                entries = list(it)
        except OSError:
            return []
        out = []
        for entry in entries:
            try:
                st = entry.stat(follow_symlinks=False)
                out.append({'name': entry.name,
                            'is_dir': entry.is_dir(follow_symlinks=False),
                            'size': st.st_size, 'mtime': st.st_mtime,
                            'rel': f'{self.rel}/{entry.name}' if self.rel
                            else entry.name})
            except OSError:
                continue
        out.sort(key=lambda e: (not e['is_dir'], e['name'].lower()))
        return out

    def _deltas(self):
        """Names that changed or disappeared after the selected snapshot."""
        sid = self._current_sid()
        if sid is None or not self.rel:
            return set(), set()
        newer = self._newer_sid()
        try:
            cur_entries = {e.name: e for e in os.scandir(
                self._sid_path(sid, self.rel))}
        except OSError:
            return set(), set()
        if newer is None:
            new_dir = '/' + self.rel
        else:
            new_dir = self._sid_path(newer, self.rel)
        try:
            new_entries = {e.name: e for e in os.scandir(new_dir)}
        except OSError:
            new_entries = {}

        changed, gone = set(), set()
        for name, entry in cur_entries.items():
            other = new_entries.get(name)
            if other is None:
                gone.add(name)
                continue
            try:
                a = entry.stat(follow_symlinks=False)
                b = other.stat(follow_symlinks=False)
                if a.st_size != b.st_size or int(a.st_mtime) != int(b.st_mtime):
                    changed.add(name)
            except OSError:
                pass
        return changed, gone

    def _crumb_parts(self):
        """[(label, rel)] for the include root we are under, if any."""
        for path, _ftype in self._include_roots():
            root_rel = path.lstrip('/')
            if self.rel == root_rel:
                return [(os.path.basename(path.rstrip('/')) or path, root_rel)]
            if self.rel.startswith(root_rel + '/'):
                rest = self.rel[len(root_rel) + 1:].split('/')
                parts = [(os.path.basename(path.rstrip('/')) or path, root_rel)]
                for i, segment in enumerate(rest):
                    parts.append((segment, root_rel + '/' + '/'.join(rest[:i + 1])))
                return parts
        return []

    def _render_crumbs(self):
        while child := self.crumbs.get_first_child():
            self.crumbs.remove(child)

        def add(label, rel, current=False):
            btn = Gtk.Button(label=label)
            btn.add_css_class('rw-crumb')
            if current:
                btn.add_css_class('current')
            btn.connect('clicked', lambda *_: self._crumb_to(rel))
            self.crumbs.append(btn)

        add('Tus carpetas', '', current=not self.rel)
        parts = self._crumb_parts()
        for i, (label, rel) in enumerate(parts):
            sep = Gtk.Label(label='›')
            sep.add_css_class('rw-crumb-sep')
            self.crumbs.append(sep)
            add(label, rel, current=i == len(parts) - 1)

    def _crumb_to(self, rel):
        self.rel = rel
        self.selected = None
        self.render_files()

    def _on_view_toggled(self, btn, view):
        if not btn.get_active():
            return
        self.view = view
        self.render_files()

    def render_files(self):
        if self.cfg is None and self.backend.error:
            return
        self._render_crumbs()

        if self.rel:
            base = self._current_dir()
            if not base or not os.path.isdir(base):
                self._show_empty('Carpeta no disponible',
                                 f'No existe o no hay permisos: {base or self.rel}')
                return
            entries = self._iter_dir(base)
        else:
            entries = self._root_entries()

        query = self.search.get_text().strip().lower()
        if query:
            entries = [e for e in entries if query in e['name'].lower()]
        changed, gone = self._deltas()

        if not entries:
            self._show_empty('Nada por aquí',
                             'Prueba con otro nombre.' if query
                             else 'Carpeta vacía en este momento.')
            return

        self.stack.set_visible_child_name(self.view)
        if self.view == 'grid':
            self._render_grid(entries, changed, gone)
        else:
            self._render_list(entries, changed, gone)
        self.selected = None
        self._update_actionbar()

    def _show_empty(self, title, hint):
        self.empty_title.set_text(title)
        self.empty_hint.set_text(hint)
        self.stack.set_visible_child_name('empty')
        self.selected = None
        self._update_actionbar()

    def _badge(self, name, changed, gone):
        if name in gone:
            return ('Se borró después', 'rw-badge-gone')
        if name in changed:
            return ('Cambió después', 'rw-badge-changed')
        return None

    def _render_grid(self, entries, changed, gone):
        while child := self.grid.get_first_child():
            self.grid.remove(child)
        for entry in entries:
            name, is_dir = entry['name'], entry['is_dir']
            child = Gtk.FlowBoxChild()
            child.set_valign(Gtk.Align.START)
            tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            tile.add_css_class('rw-tile')
            tile.set_size_request(-1, 104)
            tile.set_valign(Gtk.Align.START)

            icon = Gtk.Image(icon_name=_icon_for(name, is_dir), pixel_size=32)
            icon.set_halign(Gtk.Align.CENTER)
            tile.append(icon)

            label = Gtk.Label(label=name, xalign=0.5)
            label.add_css_class('rw-tile-name')
            label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            label.set_max_width_chars(18)
            tile.append(label)

            detail = Gtk.Label(xalign=0.5)
            detail.add_css_class('rw-tile-size')
            if is_dir:
                detail.set_text('carpeta')
            elif entry['size'] is None:
                detail.set_text('')
            else:
                detail.set_text(format_size(entry['size']))
            tile.append(detail)

            badge = self._badge(name, changed, gone)
            if badge is not None:
                b = Gtk.Label(label=badge[0])
                b.add_css_class('rw-badge')
                b.add_css_class(badge[1])
                b.set_halign(Gtk.Align.CENTER)
                tile.append(b)

            child.set_child(tile)
            child.bit_entry = entry
            self.grid.append(child)

    def _render_list(self, entries, changed, gone):
        while row := self.file_list.get_first_child():
            self.file_list.remove(row)
        for entry in entries:
            name, is_dir = entry['name'], entry['is_dir']
            row = Gtk.ListBoxRow()
            box = Gtk.Box(spacing=10)
            box.set_margin_top(6)
            box.set_margin_bottom(6)
            box.set_margin_start(10)
            box.set_margin_end(10)

            icon = Gtk.Image(icon_name=_icon_for(name, is_dir), pixel_size=18)
            box.append(icon)

            label = Gtk.Label(label=name, xalign=0, hexpand=True)
            label.add_css_class('rw-row-name')
            label.set_ellipsize(Pango.EllipsizeMode.END)
            box.append(label)

            badge = self._badge(name, changed, gone)
            if badge is not None:
                b = Gtk.Label(label=badge[0])
                b.add_css_class('rw-badge')
                b.add_css_class(badge[1])
                box.append(b)

            if is_dir or entry['size'] is None:
                size_text = '-'
            else:
                size_text = format_size(entry['size'])
            size_label = Gtk.Label(label=size_text, xalign=1)
            size_label.add_css_class('rw-row-dim')
            size_label.set_size_request(90, -1)
            box.append(size_label)

            if entry['mtime']:
                when = datetime.datetime.fromtimestamp(entry['mtime'])
                time_text = f'{when:%d/%m/%Y %H:%M}'
            else:
                time_text = ''
            time_label = Gtk.Label(label=time_text, xalign=1)
            time_label.add_css_class('rw-row-dim')
            time_label.set_size_request(130, -1)
            box.append(time_label)

            row.set_child(box)
            row.bit_entry = entry
            self.file_list.append(row)

    def _on_grid_selected(self, flow):
        children = flow.get_selected_children()
        if children and hasattr(children[0], 'bit_entry'):
            self.selected = children[0].bit_entry
        else:
            self.selected = None
        self._update_actionbar()

    def _on_grid_activated(self, _flow, child):
        if hasattr(child, 'bit_entry'):
            self._activate(child.bit_entry)

    def _on_list_selected(self, _list, row):
        if row is not None and hasattr(row, 'bit_entry'):
            self.selected = row.bit_entry
        else:
            self.selected = None
        self._update_actionbar()

    def _on_list_activated(self, _list, row):
        if hasattr(row, 'bit_entry'):
            self._activate(row.bit_entry)

    def _activate(self, entry):
        if entry['is_dir']:
            self.rel = entry['rel']
            self.selected = None
            self.render_files()
        elif self._current_sid() is not None:
            self.show_diff(entry['rel'])

    def _update_actionbar(self):
        if not self.selected:
            self.actionbar.set_visible(False)
            return
        name = self.selected['name']
        self.actionbar_name.set_text(name)
        in_time = self._current_sid() is not None
        self.restore_btn.set_sensitive(in_time)
        self.restore_btn.set_label('Restaurar esta versión' if in_time
                                   else 'Restaurar')
        self.actionbar.set_visible(True)

    # -------------------------------------------------------------- actions
    def take_snapshot(self):
        if self._snap_worker_running:
            self.toast('Ya hay una instantánea en curso')
            return
        if self.backend.error:
            self.toast('Núcleo no disponible')
            return
        self._snap_worker_running = True
        self._snapshots_before_take = len(self.chrono)
        self.snap_btn.set_sensitive(False)
        self.snap_btn.set_label('Copiando…')
        self.title_sub.set_text('Creando copia…')

        dialog = Adw.Window(transient_for=self, modal=True,
                            title='Tomando instantánea',
                            default_width=440, resizable=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(24)
        box.set_margin_bottom(20)
        box.set_margin_start(24)
        box.set_margin_end(24)
        spinner = Gtk.Spinner(spinning=True, width_request=32,
                              height_request=32, halign=Gtk.Align.CENTER)
        box.append(spinner)
        stage = Gtk.Label(label='Iniciando rsync…')
        stage.set_wrap(True)
        stage.set_max_width_chars(52)
        box.append(stage)

        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        stop_btn = Gtk.Button(label='Parar la copia')
        stop_btn.add_css_class('rw-danger')
        background_btn = Gtk.Button(label='Seguir en segundo plano')
        background_btn.add_css_class('rw-soft')
        buttons.append(stop_btn)
        buttons.append(background_btn)
        box.append(buttons)
        dialog.set_content(box)
        dialog.present()
        self._snapshot_dialog = dialog

        state = {'stopped': False, 'background': False, 'attempts': 0}

        def escalate():
            if self._snap_worker_running and state['stopped']:
                import signal
                self.backend.stop_snapshot(signal.SIGKILL)
            return False

        def attempt_stop():
            if not state['stopped'] or not self._snap_worker_running:
                return False
            pids = self.backend.stop_snapshot()
            if not pids and state['attempts'] < 4:
                state['attempts'] += 1
                GLib.timeout_add(1000, attempt_stop)
            return False

        def stop_clicked(*_):
            if state['stopped']:
                return
            state['stopped'] = True
            stage.set_text('Deteniendo la copia…')
            stop_btn.set_sensitive(False)
            background_btn.set_sensitive(False)
            attempt_stop()
            GLib.timeout_add_seconds(6, escalate)

        def to_background(*_):
            state['background'] = True
            dialog.close()
            self.toast('La copia sigue en segundo plano. Te aviso al terminar')

        stop_btn.connect('clicked', stop_clicked)
        background_btn.connect('clicked', to_background)
        self._request_stop = stop_clicked
        dialog.bit_stop_btn = stop_btn
        dialog.bit_background_btn = background_btn

        def poll():
            if state['background'] or state['stopped']:
                return False
            message = self.backend.take_snapshot_message()
            if message and message[1]:
                stage.set_text(message[1][-140:])
            return True

        GLib.timeout_add(300, poll)

        def done(error_message, error):
            self._snap_worker_running = False
            self._request_stop = None
            self.snap_btn.set_sensitive(True)
            self.snap_btn.set_label('Crear copia ahora')
            dialog.close()
            if self._close_dialog is not None:
                self._close_dialog.destroy()
                self._close_dialog = None
            fresh = self.backend.list_snapshots()
            created = len(fresh) > self._snapshots_before_take
            if state['stopped'] and created:
                # rsync returns 20 on SIGTERM and the core keeps the partial
                # snapshot marked as failed. Discard it: the user asked to stop.
                newest = fresh[0]
                if getattr(newest, 'failed', False):
                    try:
                        self.backend.delete_snapshot(newest)
                        created = False
                    except Exception as exc:  # noqa: BLE001
                        self.toast(f'No se pudo descartar la copia parcial: {exc}')
            if state['stopped'] and not created:
                self.toast('Copia detenida. No se guardó nada')
            elif state['stopped'] and created:
                self.toast('La copia terminó antes de poder detenerla')
            elif error:
                self.toast(f'Error en la instantánea: {error}')
            elif error_message:
                self.toast(f'Instantánea terminada con errores: {error_message}')
            else:
                self.toast('Punto en el tiempo creado', 'Ver ahora',
                           lambda: self.set_snapshot(None))
            self.reload_snapshots()
            if self._quit_when_done:
                GLib.idle_add(self._close_now)

        Worker(self.backend.take_snapshot, done)

    def _on_close_request(self, *_):
        if not self._snap_worker_running:
            return False
        if self._close_dialog is not None:
            return True
        dialog = Adw.MessageDialog(
            transient_for=self, modal=True,
            heading='Hay una copia en curso',
            body='Si sales ahora, la copia se quedará a medias.')
        dialog.add_response('cancel', 'Cancelar')
        dialog.add_response('wait', 'Esperar')
        dialog.add_response('stop', 'Detener y salir')
        dialog.set_response_appearance('stop',
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('wait')
        dialog.set_close_response('cancel')
        dialog.connect('response', self._on_close_response)
        self._close_dialog = dialog
        dialog.present()
        return True

    def _on_close_response(self, dialog, response):
        self._close_dialog = None
        dialog.destroy()
        if response == 'cancel':
            return
        if response == 'stop' and self._request_stop is not None:
            self._request_stop()
        self._quit_when_done = True
        self.toast('Se cerrará al terminar la copia' if response == 'wait'
                   else 'Deteniendo la copia y saliendo…')
        if not self._snap_worker_running:
            GLib.idle_add(self._close_now)

    def _close_now(self):
        self._quit_when_done = False
        self.close()
        return False

    def restore_selection(self, other_location=False):
        sid = self._current_sid()
        if sid is None:
            self.toast('Viaja a un punto en el tiempo para restaurar')
            return
        if not self.selected:
            self.toast('Selecciona un archivo o carpeta')
            return
        # BiT restores from absolute paths in both 1.6.x and 2.0
        rel = '/' + self.selected['rel']
        from bitgnome.dialogs import RestoreDialog
        dialog = RestoreDialog(self, self.backend, sid, rel, other_location)
        dialog.on_confirm = self._on_restore_confirmed
        dialog.present()

    def _on_restore_confirmed(self, sid, rel, restore_to, delete, backup,
                              only_new):
        self.toast('Restauración iniciada…')

        def work():
            self.backend.restore(sid, rel, restore_to=restore_to,
                                 delete=delete, backup=backup,
                                 only_new=only_new)

        def done(_result, error):
            if error:
                self.toast(f'Error al restaurar: {error}')
            else:
                self.toast('Restaurado', 'Ver registro', self.show_log)

        Worker(work, done)

    def forget_selection(self):
        if not self.selected:
            return
        name = self.selected['name']
        absolute = '/' + self.selected['rel']
        try:
            patterns = list(self.cfg.exclude())
        except Exception:  # noqa: BLE001
            patterns = []
        if absolute in patterns:
            return
        patterns.append(absolute)
        try:
            self.cfg.setExclude(patterns)
            ui.save(self.cfg)
        except Exception as exc:  # noqa: BLE001
            self.toast(f'No se pudo excluir: {exc}')
            return
        self.toast(f'«{name}» dejará de copiarse', 'Deshacer',
                   lambda: self._undo_exclude(absolute))

    def _undo_exclude(self, absolute):
        try:
            patterns = [p for p in self.cfg.exclude() if p != absolute]
            self.cfg.setExclude(patterns)
            ui.save(self.cfg)
            self.toast('Sigue protegido')
        except Exception:  # noqa: BLE001
            pass

    def delete_current_snapshot(self):
        sid = self._current_sid()
        if sid is None:
            self.toast('Elige un punto en el tiempo primero')
            return
        when = self.backend.snapshot_label(sid)
        label = ui.short_label(when) if isinstance(when, datetime.datetime) \
            else str(sid)
        dialog = Adw.MessageDialog(
            transient_for=self, modal=True,
            heading='¿Eliminar este punto en el tiempo?',
            body=f'Se eliminará permanentemente la instantánea del {label}. '
                 'Esta acción no se puede deshacer.')
        dialog.add_response('cancel', 'Cancelar')
        dialog.add_response('delete', 'Eliminar')
        dialog.set_response_appearance('delete',
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect('response', self._on_delete_response, sid)
        dialog.present()

    def _on_delete_response(self, _dialog, response, sid):
        if response != 'delete':
            return

        def work():
            self.backend.delete_snapshot(sid)

        def done(_result, error):
            if error:
                self.toast(f'Error al eliminar: {error}')
                return
            self.toast('Punto en el tiempo eliminado')
            self.index = None
            self.reload_snapshots()

        Worker(work, done)

    def show_log(self):
        from bitgnome.dialogs import LogViewDialog
        LogViewDialog(self, self.backend).present()

    def show_diff(self, filename=None):
        sid = self._current_sid()
        if sid is None:
            self.toast('«Ahora» no tiene con qué comparar: elige un punto')
            return
        if filename:
            rel = f'{self.rel}/{filename}' if self.rel else filename
        elif self.selected:
            if self.selected['is_dir']:
                self.toast('La comparación funciona con archivos, no carpetas')
                return
            rel = self.selected['rel']
        else:
            self.toast('Selecciona un archivo para comparar')
            return
        from bitgnome.dialogs import DiffDialog
        DiffDialog(self, sid, '/' + rel).present()

    # ---------------------------------------------------------------- play
    def toggle_play(self):
        if self._playing:
            self._stop_play()
            return
        if not self.chrono:
            return
        self.set_snapshot(0)
        self._playing = True
        self.play_btn.set_icon_name('media-playback-pause-symbolic')
        self._play_source = GLib.timeout_add(650, self._play_tick)

    def _play_tick(self):
        if not self._playing or self.index is None:
            self._stop_play()
            return False
        if self.index + 1 >= len(self.chrono):
            self._stop_play()
            return False
        self.set_snapshot(self.index + 1)
        return True

    def _stop_play(self):
        self._playing = False
        if self._play_source is not None:
            GLib.source_remove(self._play_source)
            self._play_source = None
        self.play_btn.set_icon_name('media-playback-start-symbolic')

    # --------------------------------------------------------------- error
    def _show_backend_error(self, error):
        while child := self.content.get_first_child():
            self.content.remove(child)
        page = Adw.StatusPage(
            title='Núcleo de Back In Time no disponible',
            description=(f'{error}\n\nInstala el paquete backintime (o '
                         'backintime-common) o define BACKINTIME_COMMON '
                         'apuntando a la carpeta common/ del código fuente.'),
            icon_name='dialog-warning-symbolic', vexpand=True)
        self.content.append(page)
        self.hero.set_visible(False)
