"""Restore, log-view and diff dialogs."""
import difflib
import os

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, Pango  # noqa: E402

from bitgnome.backend import format_size  # noqa: E402


class RestoreDialog(Adw.Window):
    """Options dialog for restoring paths from a snapshot."""

    def __init__(self, parent, backend, sid, rel_path, other_location=False):
        super().__init__(transient_for=parent, modal=True, title='Restaurar',
                         default_width=460)
        self.backend = backend
        self.sid = sid
        self.rel = rel_path
        self.on_confirm = None

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_margin_top(20)
        box.set_margin_bottom(16)
        box.set_margin_start(20)
        box.set_margin_end(20)
        self.set_content(box)

        head = Gtk.Box(spacing=12)
        img = Gtk.Image(icon_name='edit-undo-symbolic', pixel_size=32)
        img.add_css_class('accent')
        head.append(img)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title = Gtk.Label(label=f'Restaurar «{os.path.basename(rel_path)}»', xalign=0)
        title.add_css_class('title-3')
        try:
            snap_name = sid.displayName
        except Exception:  # noqa: BLE001
            snap_name = str(sid)
        sub = Gtk.Label(label=f'Desde la instantánea «{snap_name}»', xalign=0)
        sub.add_css_class('dim-label')
        labels.append(title)
        labels.append(sub)
        head.append(labels)
        box.append(head)

        dest_group = Adw.PreferencesGroup(title='Destino')
        self.orig_check = Gtk.CheckButton(label='Ubicación original')
        self.orig_check.set_active(not other_location)
        orig_row = Adw.ActionRow(title='Ubicación original',
                                 subtitle=rel_path if rel_path.startswith('/') else '/' + rel_path)
        orig_row.add_prefix(self.orig_check)
        orig_row.set_activatable_widget(self.orig_check)
        dest_group.add(orig_row)

        self.other_check = Gtk.CheckButton(group=self.orig_check)
        self.other_check.set_active(other_location)
        self.other_row = Adw.ActionRow(title='Otra carpeta…')
        self.other_entry = Gtk.Entry(placeholder_text='/ruta/destino',
                                     hexpand=True, valign=Gtk.Align.CENTER,
                                     sensitive=other_location)
        self.other_check.connect('toggled', lambda b:
                                 self.other_entry.set_sensitive(b.get_active()))
        self.other_row.add_prefix(self.other_check)
        self.other_row.add_suffix(self.other_entry)
        self.other_row.set_activatable_widget(self.other_check)
        dest_group.add(self.other_row)
        box.append(dest_group)

        opts = Adw.PreferencesGroup(title='Opciones')
        self.sw_backup = self._switch_row(
            opts, 'Crear copia de los archivos existentes',
            'Renombra los actuales a archivo.backup.YYYYMMDD', True)
        self.sw_only_new = self._switch_row(
            opts, 'Restaurar solo archivos nuevos o más recientes',
            'Equivale a rsync --update', False)
        self.sw_delete = self._switch_row(
            opts, 'Eliminar archivos que no están en la instantánea',
            'Peligroso: borra archivos posteriores a la copia', False)
        box.append(opts)

        btns = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancel = Gtk.Button(label='Cancelar')
        cancel.connect('clicked', lambda *_: self.close())
        ok = Gtk.Button(label='Restaurar')
        ok.add_css_class('suggested-action')
        ok.connect('clicked', self._confirm)
        btns.append(cancel)
        btns.append(ok)
        box.append(btns)

    def _switch_row(self, group, title, subtitle, active):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        sw = Gtk.Switch(active=active, valign=Gtk.Align.CENTER)
        row.add_suffix(sw)
        row.set_activatable_widget(sw)
        group.add(row)
        return sw

    def _confirm(self, *_):
        restore_to = ''
        if self.other_check.get_active():
            restore_to = self.other_entry.get_text().strip()
            if not restore_to:
                return
        if self.on_confirm:
            self.on_confirm(self.sid, self.rel, restore_to,
                            self.sw_delete.get_active(),
                            self.sw_backup.get_active(),
                            self.sw_only_new.get_active())
        self.close()


class LogViewDialog(Adw.Window):
    """Snapshot log viewer with level filter."""

    def __init__(self, parent, backend):
        super().__init__(transient_for=parent, modal=False,
                         title='Registro de instantáneas',
                         default_width=760, default_height=520)
        self.backend = backend
        self.filter = 'all'

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(box)

        header = Adw.HeaderBar()
        chips = Gtk.Box(spacing=6)
        chips.set_margin_start(6)
        self.chip_buttons = {}
        for key, label in (('all', 'Todo'), ('I', 'Info'),
                           ('W', 'Avisos'), ('E', 'Errores')):
            b = Gtk.ToggleButton(label=label)
            b.add_css_class('pill')
            if key == 'all':
                b.set_active(True)
            b.connect('toggled', self._on_chip, key)
            chips.append(b)
            self.chip_buttons[key] = b
        header.pack_start(chips)
        box.append(header)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.view = Gtk.TextView(editable=False, monospace=True,
                                 top_margin=10, bottom_margin=10,
                                 left_margin=12, right_margin=12)
        self.view.add_css_class('osd')
        scroll.set_child(self.view)
        box.append(scroll)

        self._load()

    def _on_chip(self, btn, key):
        if not btn.get_active():
            return
        self.filter = key
        for k, b in self.chip_buttons.items():
            if k != key:
                b.set_active(False)
        self._load()

    def _load(self):
        buf = self.view.get_buffer()
        buf.set_text('')
        tags = {
            'I': buf.create_tag('i', foreground='#8ad8a0'),
            'W': buf.get_tag_table().lookup('w') or buf.create_tag(
                'w', foreground='#f8d07a'),
            'E': buf.create_tag('e', foreground='#ff8a8a'),
        }
        end = buf.get_end_iter()
        try:
            lines = list(self.backend.snapshot_log())
        except Exception as exc:  # noqa: BLE001
            lines = [f'No se pudo leer el registro: {exc}']
        for line in lines:
            lv = 'I'
            if line.startswith('[E]') or 'ERROR' in line:
                lv = 'E'
            elif line.startswith('[W]') or 'WARNING' in line:
                lv = 'W'
            if self.filter != 'all' and lv != self.filter:
                continue
            buf.insert_with_tags(end, line + '\n', tags.get(lv))
            _, end = buf.get_bounds()


class DiffDialog(Adw.Window):
    """Unified diff between the file in a snapshot and the previous one
    (or the live file when comparing «Ahora»)."""

    def __init__(self, parent, sid, rel_path, prev_sid):
        name = os.path.basename(rel_path.rstrip('/')) or rel_path
        super().__init__(transient_for=parent, modal=False,
                         title=f'Cambios - {name}',
                         default_width=820, default_height=560)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(box)
        box.append(Adw.HeaderBar())

        scroll = Gtk.ScrolledWindow(vexpand=True)
        view = Gtk.TextView(editable=False, monospace=True,
                            top_margin=8, bottom_margin=8,
                            left_margin=10, right_margin=10)
        scroll.set_child(view)
        box.append(scroll)
        buf = view.get_buffer()
        tag_add = buf.create_tag('add', background='rgba(46,194,126,0.25)')
        tag_del = buf.create_tag('del', background='rgba(224,27,36,0.22)')
        tag_h = buf.create_tag('h', foreground='#7aa2d8')

        def snapshot_path(snap, rel):
            return snap.pathBackup(rel) if rel else snap.pathBackup()

        try:
            new_path = snapshot_path(sid, rel_path)
        except Exception:  # noqa: BLE001
            new_path = None
        old_path = None
        if prev_sid is not None:
            try:
                old_path = snapshot_path(prev_sid, rel_path)
            except Exception:  # noqa: BLE001
                old_path = None

        def read(p):
            try:
                with open(p, encoding='utf-8', errors='replace') as f:
                    return f.readlines()
            except OSError:
                return None

        new_lines = read(new_path) if new_path else None
        old_lines = read(old_path) if old_path else None

        if new_lines is None:
            buf.set_text('No se puede leer el archivo en la instantánea.')
            return
        if prev_sid is None:
            buf.set_text('No hay una instantánea anterior con la que '
                         'comparar: este es el punto más antiguo.')
            return
        if old_lines is None:
            buf.set_text('El archivo no existía en la instantánea anterior '
                         '(es nuevo).')
            return

        diff = difflib.unified_diff(old_lines, new_lines,
                                    fromfile=f'anterior ({prev_sid})',
                                    tofile=f'actual ({sid})', lineterm='')
        out = list(diff)
        if not out:
            buf.set_text('Sin diferencias con la instantánea anterior.')
            return
        for line in out:
            it = buf.get_end_iter()
            if line.startswith(('+++', '---', '@@')):
                buf.insert_with_tags(it, line + '\n', tag_h)
            elif line.startswith('+'):
                buf.insert_with_tags(it, line + '\n', tag_add)
            elif line.startswith('-'):
                buf.insert_with_tags(it, line + '\n', tag_del)
            else:
                buf.insert(it, line + '\n')
