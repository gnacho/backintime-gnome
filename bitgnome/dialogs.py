"""Restore, log-view and diff dialogs."""
import difflib
import os

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, Pango  # noqa: E402

from bitgnome.backend import format_size  # noqa: E402
from bitgnome import ui as _ui  # noqa: E402
from bitgnome.i18n import _  # noqa: E402


class RestoreDialog(Adw.Window):
    """Options dialog for restoring paths from a snapshot."""

    def __init__(self, parent, backend, sid, rel_path, other_location=False):
        super().__init__(transient_for=parent, modal=True, title=_('Restaurar'),
                         default_width=460)
        _ui.apply_theme(self)
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
        title = Gtk.Label(
            label=_('Restaurar «{name}»').format(
                name=os.path.basename(rel_path)), xalign=0)
        title.add_css_class('title-3')
        try:
            snap_name = sid.displayName
        except Exception:  # noqa: BLE001
            snap_name = str(sid)
        sub = Gtk.Label(
            label=_('Desde la instantánea «{name}»').format(name=snap_name),
            xalign=0)
        sub.add_css_class('dim-label')
        labels.append(title)
        labels.append(sub)
        head.append(labels)
        box.append(head)

        dest_group = Adw.PreferencesGroup(title=_('Destino'))
        self.orig_check = Gtk.CheckButton(label=_('Ubicación original'))
        self.orig_check.set_active(not other_location)
        orig_row = Adw.ActionRow(title=_('Ubicación original'),
                                 subtitle=rel_path if rel_path.startswith('/') else '/' + rel_path)
        orig_row.add_prefix(self.orig_check)
        orig_row.set_activatable_widget(self.orig_check)
        dest_group.add(orig_row)

        self.other_check = Gtk.CheckButton(group=self.orig_check)
        self.other_check.set_active(other_location)
        self.other_row = Adw.ActionRow(title=_('Otra carpeta…'))
        self.other_entry = Gtk.Entry(placeholder_text=_('/ruta/destino'),
                                     hexpand=True, valign=Gtk.Align.CENTER,
                                     sensitive=other_location)
        self.other_check.connect('toggled', lambda b:
                                 self.other_entry.set_sensitive(b.get_active()))
        self.other_row.add_prefix(self.other_check)
        self.other_row.add_suffix(self.other_entry)
        self.other_row.set_activatable_widget(self.other_check)
        dest_group.add(self.other_row)
        box.append(dest_group)

        opts = Adw.PreferencesGroup(title=_('Opciones'))
        self.sw_backup = self._switch_row(
            opts, _('Crear copia de los archivos existentes'),
            _('Renombra los actuales a archivo.backup.YYYYMMDD'), True)
        self.sw_only_new = self._switch_row(
            opts, _('Restaurar solo archivos nuevos o más recientes'),
            _('Equivale a rsync --update'), False)
        self.sw_delete = self._switch_row(
            opts, _('Eliminar archivos que no están en la instantánea'),
            _('Peligroso: borra archivos posteriores a la copia'), False)
        box.append(opts)

        btns = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancel = Gtk.Button(label=_('Cancelar'))
        cancel.connect('clicked', lambda *_: self.close())
        ok = Gtk.Button(label=_('Restaurar'))
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

    def _confirm(self, *_args):
        restore_to = ''
        if self.other_check.get_active():
            restore_to = self.other_entry.get_text().strip()
            if not restore_to:
                self.other_entry.add_css_class('error')
                self.other_entry.set_placeholder_text(
                    _('Elige una carpeta de destino'))
                return
            self.other_entry.remove_css_class('error')
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
                         title=_('Registro de instantáneas'),
                         default_width=760, default_height=520)
        self.backend = backend
        self.filter = 'all'
        _ui.apply_theme(self)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(box)

        header = Adw.HeaderBar()
        chips = Gtk.Box(spacing=6)
        chips.set_margin_start(6)
        self.chip_buttons = {}
        for key, label in (('all', _('Todo')), ('I', _('Info')),
                           ('W', _('Avisos')), ('E', _('Errores'))):
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
        buf.set_text(_('cargando…'))
        from bitgnome.backend import Worker
        token = getattr(self, '_load_token', 0) + 1
        self._load_token = token
        Worker(self.backend.snapshot_log,
               lambda lines, error: self._fill(lines, error, token))

    def _fill(self, lines, error, token):
        if token != getattr(self, '_load_token', 0):
            return False
        buf = self.view.get_buffer()
        buf.set_text('')
        tags = {
            'I': buf.create_tag('i', foreground='#8ad8a0'),
            'W': buf.get_tag_table().lookup('w') or buf.create_tag(
                'w', foreground='#f8d07a'),
            'E': buf.create_tag('e', foreground='#ff8a8a'),
        }
        end = buf.get_end_iter()
        if error or lines is None:
            lines = [_('No se pudo leer el registro: {error}').format(error=error)]
        else:
            lines = list(lines)
        for line in lines:
            lv = 'I'
            if line.startswith('[E]') or 'ERROR' in line:
                lv = 'E'
            elif line.startswith('[W]') or 'WARNING' in line:
                lv = 'W'
            if self.filter != 'all' and lv != self.filter:
                continue
            buf.insert_with_tags(end, line + '\n', tags.get(lv))
            end = buf.get_bounds()[1]
        return False


class DiffDialog(Adw.Window):
    """Unified diff between the file in a snapshot and the previous one
    (or the live file when comparing «Ahora»)."""

    def __init__(self, parent, sid, rel_path, prev_sid):
        name = os.path.basename(rel_path.rstrip('/')) or rel_path
        super().__init__(transient_for=parent, modal=False,
                         title=_('Cambios - {name}').format(name=name),
                         default_width=820, default_height=560)
        _ui.apply_theme(self)
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

        limit = 2 * 1024 * 1024

        def read(p):
            try:
                if os.path.getsize(p) > limit:
                    return 'too-big'
                with open(p, encoding='utf-8', errors='replace') as f:
                    return f.readlines()
            except OSError:
                return None

        new_lines = read(new_path) if new_path else None
        old_lines = read(old_path) if old_path else None

        if new_lines == 'too-big' or old_lines == 'too-big':
            buf.set_text(
                _('El archivo es demasiado grande para el diff interno '
                  '(más de 2 MB).'))
            return
        if new_lines is None:
            buf.set_text(_('No se puede leer el archivo en la instantánea.'))
            return
        if prev_sid is None:
            buf.set_text(
                _('No hay una instantánea anterior con la que comparar: '
                  'este es el punto más antiguo.'))
            return
        if old_lines is None:
            buf.set_text(_('El archivo no existía en la instantánea anterior '
                           '(es nuevo).'))
            return

        diff = difflib.unified_diff(old_lines, new_lines,
                                    fromfile=_('anterior ({sid})').format(
                                        sid=prev_sid),
                                    tofile=_('actual ({sid})').format(sid=sid),
                                    lineterm='')
        out = list(diff)
        if not out:
            buf.set_text(_('Sin diferencias con la instantánea anterior.'))
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


class RetentionDialog(Adw.Window):
    """What the retention policy would remove, and what that would cost."""

    def __init__(self, parent, result, on_settings=None):
        super().__init__(transient_for=parent, modal=True, title=_('Retención'),
                         default_width=760, default_height=620)
        _ui.apply_theme(self)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(box)
        box.append(Adw.HeaderBar())

        scroll = Gtk.ScrolledWindow(vexpand=True)
        box.append(scroll)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        body.set_margin_top(16)
        body.set_margin_bottom(16)
        body.set_margin_start(18)
        body.set_margin_end(18)
        scroll.set_child(body)

        if result.get('engine_inert'):
            if result.get('unsupported_unit'):
                intro_text = _(
                    'La retención está configurada en {label}, una unidad que '
                    'el motor no soporta: solo aplica días, semanas y años. '
                    'No se borrará ninguna instantánea.').format(
                        label=result["label"])
                note_text = _(
                    'Cambia la conservación a 2 semanas, 1 año o 2 años para '
                    'que el borrado automático funcione.')
            else:
                intro_text = _(
                    'La retención está configurada en {label}, pero este '
                    'núcleo de Back In Time no la aplica: '
                    'removeOldSnapshotsDate() devuelve 1/1/1, así que no se '
                    'borrará ninguna instantánea.').format(label=result["label"])
                note_text = _(
                    'No es un problema de esta aplicación: el motor instalado '
                    'no ejecuta la retención. No hay archivos en riesgo por '
                    'borrado automático.')
            intro = Gtk.Label(label=intro_text, xalign=0, wrap=True)
            intro.add_css_class('heading')
            body.append(intro)
            note = Gtk.Label(label=note_text, xalign=0, wrap=True)
            note.add_css_class('dim-label')
            body.append(note)
            self._buttons(body, on_settings)
            return

        if not result.get('complete', True) and result.get('reason') == \
                'unreadable' and not result.get('doomed'):
            intro = Gtk.Label(
                label=_(
                    'La retención está en {label}, pero ahora mismo no se '
                    'puede leer el destino, así que no se puede saber qué '
                    'instantáneas se borrarían ni qué archivos se '
                    'perderían.').format(label=result["label"]),
                xalign=0, wrap=True)
            intro.add_css_class('heading')
            body.append(intro)
            note = Gtk.Label(xalign=0, wrap=True)
            note.add_css_class('dim-label')
            note.set_text(
                _('Conecta el destino y vuelve a revisar la retención. Las '
                  'instantáneas con nombre no se borran nunca.'))
            body.append(note)
            self._buttons(body, on_settings)
            return

        cutoff = result['cutoff']
        count = len(result['doomed'])
        intro = Gtk.Label(
            label=_(
                '{count} instantáneas anteriores al {cutoff:%d/%m/%Y} '
                '(retención: {label}) se eliminarán en la próxima '
                'copia.').format(count=count, cutoff=cutoff,
                                 label=result["label"]),
            xalign=0, wrap=True)
        intro.add_css_class('heading')
        body.append(intro)

        lost = result['lost']
        complete = result.get('complete', True)
        title = Gtk.Label(xalign=0, wrap=True)
        title.set_markup(
            _('<b>Solo existen ahí: {n} archivos</b>').format(n=len(lost)))
        body.append(title)
        if not complete:
            warn = Gtk.Label(xalign=0, wrap=True)
            warn.add_css_class('dim-label')
            if result.get('reason') == 'unreadable':
                warn.set_text(
                    _('No se pudo leer todo el contenido (permisos o destino '
                      'sin montar). No se puede asegurar qué se perdería.'))
            else:
                warn.set_text(
                    _('Hay más archivos de los que se pueden analizar de una '
                      'vez. No se puede asegurar qué se perdería; lista '
                      'incompleta.'))
            body.append(warn)
        elif lost:
            hint = Gtk.Label(xalign=0, wrap=True)
            hint.add_css_class('dim-label')
            hint.set_text(
                _('Si se borran esas instantáneas, estos archivos se pierden. '
                  'No hay versión más reciente de ellos.'))
            body.append(hint)
            body.append(self._path_list(lost))
        else:
            ok = Gtk.Label(xalign=0, wrap=True)
            ok.add_css_class('dim-label')
            ok.set_text(
                _('Todos los archivos de esas instantáneas existen en alguna '
                  'instantánea que se conserva.'))
            body.append(ok)

        mine = Gtk.Label(xalign=0, wrap=True)
        mine.set_markup(
            _('Con versión en una instantánea que se conserva: <b>{n}</b>')
            .format(n=result["superseded"]))
        body.append(mine)
        self._buttons(body, on_settings)

    def _buttons(self, body, on_settings):
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        if on_settings is not None:
            settings_btn = Gtk.Button(label=_('Abrir ajustes'))
            settings_btn.add_css_class('rw-soft')
            settings_btn.connect('clicked', lambda *_: (
                self.close(), on_settings()))
            buttons.append(settings_btn)
        close = Gtk.Button(label=_('Entendido'))
        close.add_css_class('rw-primary')
        close.connect('clicked', lambda *_: self.close())
        buttons.append(close)
        body.append(buttons)

    @staticmethod
    def _path_list(paths, limit=300):
        text = '\n'.join('/' + path for path in paths[:limit])
        if len(paths) > limit:
            text += _('\n… y {n} más').format(n=len(paths) - limit)
        return _text_view(text)


class PasswordDialog(Adw.Window):
    """Ask for the password of an encrypted profile.

    The core would otherwise read it from stdin (``getpass``), which in a GTK
    app hangs or fails the mount.
    """

    def __init__(self, parent, on_accept):
        super().__init__(transient_for=parent, modal=True,
                         title=_('Desbloquear el perfil cifrado'),
                         default_width=420, resizable=False)
        _ui.apply_theme(self)
        self.on_accept = on_accept

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(20)
        box.set_margin_bottom(18)
        box.set_margin_start(20)
        box.set_margin_end(20)
        self.set_content(box)

        title = Gtk.Label(label=_('Este perfil guarda las copias cifradas'),
                          xalign=0, wrap=True)
        title.add_css_class('heading')
        box.append(title)
        hint = Gtk.Label(label=_('Escribe la contraseña del perfil para leer '
                                 'las instantáneas.'), xalign=0, wrap=True)
        hint.add_css_class('dim-label')
        box.append(hint)

        self.entry = Gtk.PasswordEntry(show_peek_icon=True)
        self.entry.set_property('placeholder-text', _('Contraseña'))
        self.entry.connect('activate', lambda *_: self._accept())
        box.append(self.entry)

        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancel = Gtk.Button(label=_('Cancelar'))
        cancel.connect('clicked', lambda *_: self.close())
        accept = Gtk.Button(label=_('Desbloquear'))
        accept.add_css_class('suggested-action')
        accept.connect('clicked', lambda *_: self._accept())
        buttons.append(cancel)
        buttons.append(accept)
        box.append(buttons)

    def _accept(self):
        password = self.entry.get_text()
        if not password:
            self.entry.add_css_class('error')
            return
        self.entry.remove_css_class('error')
        callback, self.on_accept = self.on_accept, None
        self.close()
        if callback:
            callback(password)


def _text_view(text):
    view = Gtk.TextView(editable=False, monospace=True, top_margin=6,
                        bottom_margin=6, left_margin=8, right_margin=8)
    view.get_buffer().set_text(text)
    view.add_css_class('rw-mono')
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroll.set_size_request(-1, 260)
    scroll.set_child(view)
    return scroll
