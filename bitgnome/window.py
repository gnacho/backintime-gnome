"""Main window: snapshots sidebar + file browser, GNOME style.

Layout (Adw 1.2 baseline): AdwHeaderBar on top of a GtkBox; an
AdwToastOverlay wraps a horizontal split - snapshot list (GtkListBox)
and the file view (toolbar + GtkListBox + status bar).
"""
import datetime
import os
import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from bitgnome.backend import Worker, format_size  # noqa: E402


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='Back In Time')
        self.set_default_size(1180, 760)
        self.app = app
        self.backend = app.backend

        # state
        self.sids = []                 # SID objects, newest first
        self.current_sid = None        # None = "Ahora" (live filesystem)
        self.path = []                 # breadcrumb segments (relative)
        self.only_changes = False
        self.selected_path = None      # absolute path of selected row
        self._snap_worker_running = False

        self._build_ui()
        self._build_actions()

        if self.backend.error:
            self._show_backend_error(self.backend.error)
        else:
            self.reload_snapshots()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(root)

        # --- header bar
        header = Adw.HeaderBar()

        self.sidebar_btn = Gtk.ToggleButton(icon_name='view-sidebar-symbolic',
                                            active=True)
        self.sidebar_btn.set_tooltip_text('Mostrar/ocultar instantáneas (F9)')
        self.sidebar_btn.connect('toggled', self._on_sidebar_toggled)
        header.pack_start(self.sidebar_btn)

        self.profile_btn = Gtk.MenuButton(icon_name='avatar-default-symbolic')
        self.profile_btn.set_tooltip_text('Perfil actual')
        self.profile_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.profile_box.set_margin_top(6)
        self.profile_box.set_margin_bottom(6)
        self.profile_box.set_margin_start(6)
        self.profile_box.set_margin_end(6)
        self.profile_pop = Gtk.Popover()
        self.profile_pop.set_child(self.profile_box)
        self.profile_btn.set_popover(self.profile_pop)
        self._fill_profile_popover()
        header.pack_start(self.profile_btn)

        self.title_widget = Adw.WindowTitle(title='Back In Time',
                                            subtitle='Explorando: Ahora')
        header.set_title_widget(self.title_widget)

        menu = Gio.Menu()
        menu.append('Tomar instantánea', 'app.take-snapshot')
        menu.append('Restaurar…', 'app.restore')
        menu.append('Restaurar en otra ubicación…', 'app.restore-to')
        sec = Gio.Menu()
        sec.append('Registro de la instantánea', 'app.log-view')
        sec.append('Comparar con instantánea anterior', 'app.diff')
        menu.append_section(None, sec)
        sec2 = Gio.Menu()
        sec2.append('Gestionar perfiles', 'app.preferences')
        sec2.append('Atajos de teclado', 'app.shortcuts')
        sec2.append('Acerca de Back In Time', 'app.about')
        menu.append_section(None, sec2)
        menu_btn = Gtk.MenuButton(icon_name='open-menu-symbolic',
                                  menu_model=menu,
                                  tooltip_text='Menú principal')
        header.pack_end(menu_btn)

        dark_btn = Gtk.Button(icon_name='display-brightness-symbolic',
                              tooltip_text='Tema claro/oscuro')
        dark_btn.connect('clicked', self._toggle_dark)
        header.pack_end(dark_btn)

        restore_btn = Gtk.Button(icon_name='edit-undo-symbolic',
                                 label='Restaurar',
                                 tooltip_text='Restaurar la selección (Ctrl+R)')
        restore_btn.connect('clicked', lambda *_: self.restore_selection())
        header.pack_end(restore_btn)

        snap_btn = Gtk.Button(icon_name='camera-photo-symbolic',
                              label='Tomar instantánea')
        snap_btn.add_css_class('suggested-action')
        snap_btn.set_tooltip_text('Tomar instantánea ahora (Ctrl+S)')
        snap_btn.connect('clicked', lambda *_: self.take_snapshot())
        header.pack_end(snap_btn)
        self.snap_btn = snap_btn

        root.append(header)

        # --- split: snapshots sidebar | file view
        self.toast_overlay = Adw.ToastOverlay()
        root.append(self.toast_overlay)
        self.toast_overlay.set_vexpand(True)

        split = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.toast_overlay.set_child(split)

        # sidebar
        self.sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sidebar.set_size_request(280, -1)
        self.sidebar.add_css_class('sidebar')
        split.append(self.sidebar)

        side_head = Gtk.Box(spacing=6)
        side_head.set_margin_start(12)
        side_head.set_margin_end(6)
        side_head.set_margin_top(10)
        side_head.set_margin_bottom(4)
        lbl = Gtk.Label(label='INSTANTÁNEAS', xalign=0, hexpand=True)
        lbl.add_css_class('dim-label')
        lbl.add_css_class('caption-heading')
        refresh = Gtk.Button(icon_name='view-refresh-symbolic',
                             tooltip_text='Actualizar')
        refresh.add_css_class('flat')
        refresh.connect('clicked', lambda *_: self.reload_snapshots())
        side_head.append(lbl)
        side_head.append(refresh)
        self.sidebar.append(side_head)

        side_scroll = Gtk.ScrolledWindow(vexpand=True)
        side_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.snap_list = Gtk.ListBox()
        self.snap_list.add_css_class('navigation-sidebar')
        self.snap_list.set_selection_mode(Gtk.SelectionMode.NONE)
        side_scroll.set_child(self.snap_list)
        self.sidebar.append(side_scroll)

        split.append(Gtk.Separator())

        # main pane
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        split.append(main)

        toolbar = Gtk.Box(spacing=6)
        toolbar.set_margin_start(10)
        toolbar.set_margin_end(10)
        toolbar.set_margin_top(8)
        toolbar.set_margin_bottom(8)
        main.append(toolbar)

        nav_box = Gtk.Box()
        nav_box.add_css_class('linked')
        up_btn = Gtk.Button(icon_name='go-up-symbolic', tooltip_text='Subir un nivel')
        up_btn.connect('clicked', lambda *_: self._go_up())
        home_btn = Gtk.Button(icon_name='go-home-symbolic',
                              tooltip_text='Ir a la raíz de inclusión')
        home_btn.connect('clicked', lambda *_: self._go_home())
        nav_box.append(up_btn)
        nav_box.append(home_btn)
        toolbar.append(nav_box)
        self.up_btn = up_btn

        crumb_scroll = Gtk.ScrolledWindow(hexpand=True)
        crumb_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.crumbs = Gtk.Box()
        self.crumbs.add_css_class('linked')
        self.crumbs.set_valign(Gtk.Align.CENTER)
        crumb_scroll.set_child(self.crumbs)
        toolbar.append(crumb_scroll)

        self.changes_btn = Gtk.ToggleButton(icon_name='filter-symbolic',
                                            label='Solo cambios')
        self.changes_btn.set_tooltip_text(
            'Mostrar solo archivos distintos de la instantánea anterior')
        self.changes_btn.set_sensitive(False)
        self.changes_btn.connect('toggled', self._on_only_changes)
        toolbar.append(self.changes_btn)

        self.search = Gtk.SearchEntry(placeholder_text='Buscar archivos…')
        self.search.connect('search-changed', lambda *_: self.render_files())
        toolbar.append(self.search)

        # file view
        self.file_scroll = Gtk.ScrolledWindow(vexpand=True)
        self.file_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.file_list = Gtk.ListBox()
        self.file_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.file_list.add_css_class('rich-list')
        self.file_list.connect('row-activated', self._on_file_activated)
        self.file_list.connect('row-selected', self._on_file_selected)
        self.file_scroll.set_child(self.file_list)
        main.append(self.file_scroll)

        # empty/error state page (swapped in when needed)
        self.status_page = Adw.StatusPage(title='Sin instantáneas',
                                          icon_name='camera-photo-symbolic',
                                          vexpand=True)
        self.status_page.set_visible(False)
        main.append(self.status_page)

        # status bar
        status = Gtk.Box(spacing=12)
        status.add_css_class('toolbar')
        status.set_margin_start(12)
        status.set_margin_end(12)
        status.set_margin_top(4)
        status.set_margin_bottom(4)
        self.status_left = Gtk.Label(xalign=0, hexpand=True)
        self.status_left.add_css_class('dim-label')
        self.status_left.add_css_class('caption')
        self.status_right = Gtk.Label(xalign=1)
        self.status_right.add_css_class('dim-label')
        self.status_right.add_css_class('caption')
        status.append(self.status_left)
        status.append(self.status_right)
        main.append(status)

        # F9 shortcut
        ctrl = Gtk.ShortcutController()
        ctrl.add_shortcut(Gtk.Shortcut.new(
            Gtk.KeyvalTrigger.new(Gdk_key('F9'), 0),
            Gtk.CallbackAction.new(self._sc_toggle_sidebar)))
        self.add_controller(ctrl)

    def _sc_toggle_sidebar(self, *_):
        self.sidebar_btn.set_active(not self.sidebar_btn.get_active())
        return True

    def _on_sidebar_toggled(self, btn):
        self.sidebar.set_visible(btn.get_active())

    def _build_actions(self):
        pass  # actions live on the Application (see app.py)

    def _fill_profile_popover(self):
        while child := self.profile_box.get_first_child():
            self.profile_box.remove(child)
        head = Gtk.Label(label='PERFILES', xalign=0)
        head.add_css_class('dim-label')
        head.add_css_class('caption-heading')
        head.set_margin_bottom(4)
        self.profile_box.append(head)
        current = self.backend.current_profile()
        for pid, name in self.backend.profiles():
            row = Gtk.Button()
            row.add_css_class('flat')
            row.set_has_frame(False)
            box = Gtk.Box(spacing=8)
            check = Gtk.Image(icon_name='object-select-symbolic')
            check.set_visible(pid == current)
            box.append(check)
            name_lbl = Gtk.Label(label=name, xalign=0)
            name_lbl.add_css_class('heading')
            mode, path = self.backend.profile_summary(pid)
            sub = Gtk.Label(label=f'{mode} · {path}', xalign=0)
            sub.add_css_class('dim-label')
            sub.add_css_class('caption')
            labels2 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            labels2.append(name_lbl)
            labels2.append(sub)
            box.append(labels2)
            row.set_child(box)
            row.connect('clicked', self._on_profile_chosen, pid)
            self.profile_box.append(row)
        sep = Gtk.Separator()
        sep.set_margin_top(4)
        sep.set_margin_bottom(4)
        self.profile_box.append(sep)
        new_btn = Gtk.Button(label='  Gestionar perfiles…', has_frame=False)
        new_btn.connect('clicked', lambda *_: (
            self.profile_pop.popdown(),
            self.app.activate_action('preferences', None)))
        self.profile_box.append(new_btn)

    def _on_profile_chosen(self, _btn, pid):
        self.profile_pop.popdown()
        try:
            self.backend.set_current_profile(pid)
        except Exception as exc:  # noqa: BLE001
            self.toast(f'No se pudo cambiar de perfil: {exc}')
            return
        self.path = []
        self.current_sid = None
        self.reload_snapshots()

    def _toggle_dark(self, *_):
        sm = Adw.StyleManager.get_default()
        sm.set_color_scheme(
            Adw.ColorScheme.DEFAULT
            if sm.get_dark() else Adw.ColorScheme.FORCE_DARK)

    def toast(self, msg, action_label=None, action=None):
        t = Adw.Toast(title=msg, timeout=4)
        if action_label and action:
            t.set_button_label(action_label)
            t.connect('button-clicked', lambda *_: action())
        self.toast_overlay.add_toast(t)

    # ------------------------------------------------------- snapshots list
    def reload_snapshots(self):
        self.sids = self.backend.list_snapshots()
        self._render_snapshots()
        self._update_title()
        self.render_files()
        self._update_status()

    def _render_snapshots(self):
        while row := self.snap_list.get_first_child():
            self.snap_list.remove(row)

        # "Ahora" entry
        now_row = Gtk.ListBoxRow()
        box = Gtk.Box(spacing=10)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)
        box.append(Gtk.Image(icon_name='user-available-symbolic'))
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        t = Gtk.Label(label='Ahora', xalign=0)
        t.add_css_class('heading')
        s = Gtk.Label(label='Sistema de archivos actual', xalign=0)
        s.add_css_class('dim-label')
        s.add_css_class('caption')
        labels.append(t)
        labels.append(s)
        box.append(labels)
        now_row.set_child(box)
        now_row.bit_sid = None
        self.snap_list.append(now_row)
        self._snap_row_widgets = {None: now_row}

        last_group = None
        for sid in self.sids:
            d = self.backend.snapshot_label(sid)
            group = _group_label(d) if d else 'Más antiguas'
            if group != last_group:
                last_group = group
                hdr = Gtk.ListBoxRow(selectable=False, activatable=False)
                hl = Gtk.Label(label=group.upper(), xalign=0)
                hl.add_css_class('dim-label')
                hl.add_css_class('caption-heading')
                hl.set_margin_start(10)
                hl.set_margin_top(8)
                hdr.set_child(hl)
                self.snap_list.append(hdr)

            row = Gtk.ListBoxRow()
            box = Gtk.Box(spacing=10)
            box.set_margin_top(6)
            box.set_margin_bottom(6)
            box.set_margin_start(6)
            box.set_margin_end(6)
            box.append(Gtk.Image(icon_name='camera-photo-symbolic'))
            labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            try:
                name = sid.displayName
            except Exception:  # noqa: BLE001
                name = str(sid)
            t = Gtk.Label(label=name, xalign=0)
            t.add_css_class('heading')
            labels.append(t)
            if getattr(sid, 'failed', False):
                w = Gtk.Label(label='Con errores', xalign=0)
                w.add_css_class('error')
                w.add_css_class('caption')
                labels.append(w)
            box.append(labels)
            box.append(Gtk.Image())  # spacer
            del_btn = Gtk.Button(icon_name='edit-delete-symbolic',
                                 tooltip_text='Eliminar instantánea',
                                 has_frame=False, valign=Gtk.Align.CENTER)
            del_btn.add_css_class('flat')
            del_btn.connect('clicked', self._ask_delete_snapshot, sid)
            box.append(del_btn)
            row.set_child(box)
            row.bit_sid = sid
            row.connect('activate', self._on_snap_activated)
            gesture = Gtk.GestureClick()
            gesture.connect('released', self._on_snap_clicked, sid)
            row.add_controller(gesture)
            self.snap_list.append(row)
            self._snap_row_widgets[sid] = row

        self._mark_selected_snap()

    def _on_snap_clicked(self, _g, _n, _x, _y, sid):
        self._select_snapshot(sid)

    def _on_snap_activated(self, row):
        self._select_snapshot(row.bit_sid)

    def _select_snapshot(self, sid):
        self.current_sid = sid
        self.selected_path = None
        self._mark_selected_snap()
        self._update_title()
        self.changes_btn.set_sensitive(sid is not None and len(self.sids) > 1)
        if sid is None:
            self.changes_btn.set_active(False)
        self.render_files()
        self._update_status()

    def _mark_selected_snap(self):
        for sid, row in self._snap_row_widgets.items():
            if sid == self.current_sid:
                row.add_css_class('selected-snap')
            else:
                row.remove_css_class('selected-snap')

    def _update_title(self):
        if self.current_sid is None:
            self.title_widget.set_subtitle('Explorando: Ahora')
        else:
            try:
                name = self.current_sid.displayName
            except Exception:  # noqa: BLE001
                name = str(self.current_sid)
            self.title_widget.set_subtitle(f'Instantánea: {name}')

    def _ask_delete_snapshot(self, _btn, sid):
        try:
            name = sid.displayName
        except Exception:  # noqa: BLE001
            name = str(sid)
        dlg = Adw.MessageDialog(
            transient_for=self, modal=True,
            heading='¿Eliminar instantánea?',
            body=f'Se eliminará permanentemente la instantánea «{name}». '
                 'Esta acción no se puede deshacer.')
        dlg.add_response('cancel', 'Cancelar')
        dlg.add_response('delete', 'Eliminar')
        dlg.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect('response', self._on_delete_response, sid)
        dlg.present()

    def _on_delete_response(self, dlg, response, sid):
        if response != 'delete':
            return

        def work():
            self.backend.delete_snapshot(sid)

        def done(_r, err):
            if err:
                self.toast(f'Error al eliminar: {err}')
            else:
                if self.current_sid == sid:
                    self.current_sid = None
                self.toast('Instantánea eliminada')
                self.reload_snapshots()

        Worker(work, done)

    # ------------------------------------------------------------ file view
    def _base_dir(self):
        """Absolute path of the directory we are browsing."""
        rel = os.path.join(*self.path) if self.path else ''
        if self.current_sid is None:
            include = self.backend.cfg.include_directories() \
                if hasattr(self.backend.cfg, 'include_directories') else []
            if not include:
                try:
                    include = [p for p, t in self.backend.cfg.include() if t == 0]
                except Exception:  # noqa: BLE001
                    include = [os.path.expanduser('~')]
            root = include[0] if include else os.path.expanduser('~')
            return os.path.join(root, rel)
        return self.current_sid.pathBackup(rel)

    def _go_up(self):
        if self.path:
            self.path.pop()
            self.selected_path = None
            self.render_files()

    def _go_home(self):
        self.path = []
        self.selected_path = None
        self.render_files()

    def _on_only_changes(self, btn):
        self.only_changes = btn.get_active()
        self.render_files()

    def _render_crumbs(self):
        while child := self.crumbs.get_first_child():
            self.crumbs.remove(child)

        def add(label, depth, icon=None, current=False):
            b = Gtk.Button()
            b.add_css_class('flat')
            if icon:
                b.set_icon_name(icon)
            else:
                b.set_label(label)
            if current:
                b.add_css_class('accent')
            b.connect('clicked', lambda *_: self._crumb_to(depth))
            self.crumbs.append(b)

        root_label = (self.backend.cfg.profileName()
                      if self.backend.cfg else 'Inicio')
        add(root_label, 0, icon='go-home-symbolic', current=not self.path)
        for i, seg in enumerate(self.path):
            add(seg, i + 1, current=i == len(self.path) - 1)

    def _crumb_to(self, depth):
        self.path = self.path[:depth]
        self.selected_path = None
        self.render_files()

    def _iter_dir(self, base):
        """Yield (name, is_dir, size, mtime) for a real directory."""
        try:
            with os.scandir(base) as it:
                entries = list(it)
        except OSError:
            return []
        out = []
        for e in entries:
            try:
                st = e.stat(follow_symlinks=False)
                out.append((e.name, e.is_dir(follow_symlinks=False),
                            st.st_size, st.st_mtime))
            except OSError:
                continue
        out.sort(key=lambda x: (not x[1], x[0].lower()))
        return out

    def _changed_names(self):
        """Names in the current dir that differ from the previous snapshot."""
        if self.current_sid is None or self.only_changes is None:
            return set(), set()
        idx = None
        for i, sid in enumerate(self.sids):
            if sid == self.current_sid:
                idx = i
                break
        if idx is None or idx + 1 >= len(self.sids):
            return set(), set()
        prev = self.sids[idx + 1]
        rel = os.path.join(*self.path) if self.path else ''
        cur_dir = self.current_sid.pathBackup(rel)
        prev_dir = prev.pathBackup(rel)
        changed, new = set(), set()
        try:
            prev_entries = {e.name: e for e in os.scandir(prev_dir)}
        except OSError:
            prev_entries = {}
        try:
            for e in os.scandir(cur_dir):
                p = prev_entries.get(e.name)
                if p is None:
                    new.add(e.name)
                else:
                    try:
                        a, b = e.stat(follow_symlinks=False), \
                            p.stat(follow_symlinks=False)
                        if a.st_size != b.st_size or \
                                int(a.st_mtime) != int(b.st_mtime):
                            changed.add(e.name)
                    except OSError:
                        pass
        except OSError:
            pass
        return changed, new

    def render_files(self):
        self._render_crumbs()
        while row := self.file_list.get_first_child():
            self.file_list.remove(row)
        self.up_btn.set_sensitive(bool(self.path))

        base = self._base_dir()
        if not os.path.isdir(base):
            self._show_status('Carpeta no disponible',
                              f'No existe o no hay permisos: {base}')
            return
        self.status_page.set_visible(False)
        self.file_scroll.set_visible(True)

        entries = self._iter_dir(base)
        changed, new = self._changed_names() if self.current_sid else (set(), set())

        query = self.search.get_text().strip().lower()
        if query:
            entries = [e for e in entries if query in e[0].lower()]
        if self.only_changes and self.current_sid is not None:
            entries = [e for e in entries if e[0] in changed or e[0] in new]

        if not entries:
            msg = 'Sin cambios aquí' if self.only_changes else 'Carpeta vacía'
            row = Gtk.ListBoxRow(selectable=False, activatable=False)
            lbl = Gtk.Label(label=msg)
            lbl.add_css_class('dim-label')
            lbl.set_margin_top(30)
            lbl.set_margin_bottom(30)
            row.set_child(lbl)
            self.file_list.append(row)
            return

        for name, is_dir, size, mtime in entries:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(spacing=10)
            box.set_margin_top(5)
            box.set_margin_bottom(5)
            box.set_margin_start(10)
            box.set_margin_end(10)
            icon = 'folder-symbolic' if is_dir else 'text-x-generic-symbolic'
            box.append(Gtk.Image(icon_name=icon))
            name_lbl = Gtk.Label(label=name, xalign=0, hexpand=True,
                                 ellipsize=Pango.EllipsizeMode.END)
            name_lbl.set_max_width_chars(60)
            box.append(name_lbl)

            if name in new:
                badge = Gtk.Label(label='Nuevo')
                badge.add_css_class('caption')
                badge.add_css_class('accent')
                box.append(badge)
            elif name in changed:
                badge = Gtk.Label(label='Modificado')
                badge.add_css_class('caption')
                badge.add_css_class('warning')
                box.append(badge)

            if not is_dir:
                sl = Gtk.Label(label=format_size(size))
                sl.add_css_class('dim-label')
                box.append(sl)
                ml = Gtk.Label(label=datetime.datetime.fromtimestamp(mtime)
                               .strftime('%d/%m/%Y %H:%M'))
                ml.add_css_class('dim-label')
                box.append(ml)
            row.set_child(box)
            row.bit_entry = (name, is_dir)
            self.file_list.append(row)

    def _on_file_selected(self, _list, row):
        if row and hasattr(row, 'bit_entry'):
            name, _ = row.bit_entry
            self.selected_path = os.path.join(self._base_dir(), name)
        else:
            self.selected_path = None

    def _on_file_activated(self, _list, row):
        if not row or not hasattr(row, 'bit_entry'):
            return
        name, is_dir = row.bit_entry
        if is_dir:
            self.path.append(name)
            self.selected_path = None
            self.render_files()
        elif self.current_sid is not None:
            self.show_diff(name)

    def _show_status(self, title, description=''):
        self.status_page.set_title(title)
        self.status_page.set_description(description)
        self.status_page.set_visible(True)
        self.file_scroll.set_visible(False)

    def _show_backend_error(self, error):
        self.status_page.set_title('Núcleo de Back In Time no disponible')
        self.status_page.set_description(
            f'{error}\n\nInstala el paquete backintime (o backintime-common) '
            'o define la variable de entorno BACKINTIME_COMMON apuntando a '
            'la carpeta common/ del código fuente.')
        self.status_page.set_visible(True)
        self.file_scroll.set_visible(False)

    def _update_status(self):
        if self.sids:
            try:
                name = self.sids[0].displayName
            except Exception:  # noqa: BLE001
                name = str(self.sids[0])
            self.status_left.set_text(f'Última instantánea: {name}')
        else:
            self.status_left.set_text('Sin instantáneas todavía')
        free, total = self.backend.free_space()
        if free is not None and total:
            self.status_right.set_text(
                f'Espacio libre en destino: {format_size(free)} '
                f'de {format_size(total)}')
        else:
            self.status_right.set_text('')

    # ------------------------------------------------------------ actions
    def take_snapshot(self):
        if self._snap_worker_running:
            self.toast('Ya hay una instantánea en curso')
            return
        if self.backend.error:
            self.toast('Núcleo no disponible')
            return
        self._snap_worker_running = True
        self.snap_btn.set_sensitive(False)

        dlg = Adw.Window(transient_for=self, modal=True,
                         title='Tomando instantánea',
                         default_width=420, resizable=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(24)
        box.set_margin_bottom(20)
        box.set_margin_start(24)
        box.set_margin_end(24)
        spinner = Gtk.Spinner(spinning=True, width_request=32, height_request=32,
                              halign=Gtk.Align.CENTER)
        box.append(spinner)
        stage = Gtk.Label(label='Iniciando rsync…')
        box.append(stage)
        progress = Gtk.ProgressBar(show_text=True)
        box.append(progress)
        cancel = Gtk.Button(label='Cancelar', halign=Gtk.Align.CENTER)
        box.append(cancel)
        dlg.set_content(box)
        dlg.present()

        cancel.connect('clicked', lambda *_: (
            dlg.close(), setattr(self, '_cancel_snapshot', True)))
        self._cancel_snapshot = False

        def poll_message():
            if self._cancel_snapshot:
                return False
            msg = self.backend.take_snapshot_message()
            if msg and msg[1]:
                stage.set_text(msg[1][-120:])
            return True

        GLib.timeout_add(300, poll_message)

        def work():
            return self.backend.take_snapshot()

        def done(err_msg, err):
            self._snap_worker_running = False
            self.snap_btn.set_sensitive(True)
            dlg.close()
            if err:
                self.toast(f'Error en la instantánea: {err}')
            elif err_msg:
                self.toast(f'Instantánea terminada con errores: {err_msg}')
            else:
                self.toast('Instantánea creada correctamente')
            self.reload_snapshots()

        Worker(work, done)

    def restore_selection(self, other_location=False):
        if self.current_sid is None:
            self.toast('Selecciona una instantánea en la lista lateral')
            return
        if not self.selected_path:
            self.toast('Selecciona un archivo o carpeta para restaurar')
            return
        from bitgnome.dialogs import RestoreDialog
        rel = os.path.relpath(self.selected_path,
                              self.current_sid.pathBackup(''))
        dlg = RestoreDialog(self, self.backend, self.current_sid, rel,
                            other_location)
        dlg.on_confirm = self._on_restore_confirmed
        dlg.present()

    def _on_restore_confirmed(self, sid, rel, restore_to, delete,
                              backup, only_new):
        self.toast('Restauración iniciada…')

        def work():
            # restore() waits for the rsync process internally
            self.backend.restore(sid, rel, restore_to=restore_to,
                                 delete=delete, backup=backup,
                                 only_new=only_new)

        def done(_r, err):
            if err:
                self.toast(f'Error al restaurar: {err}')
            else:
                self.toast('Restauración completada',
                           'Ver registro', self.show_log)

        Worker(work, done)

    def show_log(self):
        from bitgnome.dialogs import LogViewDialog
        LogViewDialog(self, self.backend).present()

    def show_diff(self, filename=None):
        if self.current_sid is None:
            self.toast('«Ahora» no tiene con qué comparar: elige una instantánea')
            return
        if filename:
            rel = os.path.join(self._base_dir(), filename)
        elif self.selected_path:
            rel = self.selected_path
        else:
            self.toast('Selecciona un archivo para comparar')
            return
        if os.path.isdir(rel):
            self.toast('La comparación funciona con archivos, no con carpetas')
            return
        from bitgnome.dialogs import DiffDialog
        idx = None
        for i, sid in enumerate(self.sids):
            if sid == self.current_sid:
                idx = i
                break
        prev_sid = self.sids[idx + 1] if idx is not None and idx + 1 < len(self.sids) \
            else None
        DiffDialog(self, self.current_sid, rel, prev_sid).present()


def _group_label(d):
    """Sidebar group for a snapshot datetime."""
    now = datetime.datetime.now()
    today = now.date()
    day = d.date()
    if day == today:
        return 'Hoy'
    if day == today - datetime.timedelta(days=1):
        return 'Ayer'
    if day >= today - datetime.timedelta(days=7):
        return 'Esta semana'
    return 'Más antiguas'


def Gdk_key(name):
    from gi.repository import Gdk
    return Gdk.keyval_from_name(name)
