"""Guided encrypted-profile setup shared by settings and the wizard."""
import json
import os
from pathlib import Path

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from . import backend as _backend
from . import ui


def destination_is_empty_or_initialized(path):
    """Allow new directories, empty directories and existing Gocryptfs stores."""
    if not path:
        return False
    destination = Path(path)
    if not destination.exists():
        return True
    if not destination.is_dir():
        return False
    if not any(destination.iterdir()):
        return True
    try:
        with (destination / 'gocryptfs.conf').open(encoding='utf-8') as conf:
            json.load(conf)
        return True
    except (OSError, ValueError):
        return False


DEVICE_GONE_MARKERS = (
    'no such device', 'input/output error', 'read-only file system',
    'transport endpoint is not connected', 'device not configured',
)


def device_problem_text(text):
    """Point at the disk when the error is the disk.

    A USB drive that drops off the bus makes gocryptfs fail with rc 24 and a
    raw ENODEV while fsyncing. Showing that to the user hides the only thing
    that matters: the destination is gone, not the password or the folder.
    """
    lowered = (text or '').lower()
    if 'permission denied' in lowered or 'permiso denegado' in lowered:
        return ('La carpeta de destino no admite escritura con tu usuario. '
                'Elige otra carpeta o revisa los permisos.')
    if any(marker in lowered for marker in DEVICE_GONE_MARKERS):
        return ('El disco de destino se ha desconectado o ha pasado a solo '
                'lectura durante la operación. Comprueba el cable y el puerto, '
                'vuelve a conectarlo y revisa el estado del disco (por ejemplo '
                'con `sudo smartctl -a` y un `fsck` de la partición) antes de '
                'reintentar.')
    return text or 'Error desconocido del motor'


def write_probe(path):
    """Write, fsync, rename and delete a tiny file in the destination.

    Returns ``(ok, message)``. Doing this before ``gocryptfs -init`` fails
    early and in plain words when the disk is gone, instead of leaving a half
    initialized store behind.
    """
    try:
        destination = Path(path)
        destination.mkdir(parents=True, exist_ok=True)
        probe = destination / '.rewind-write-probe'
        with probe.open('wb') as handle:
            handle.write(b'probe')
            handle.flush()
            os.fsync(handle.fileno())
        os.rename(probe, probe.with_suffix('.probe'))
        probe.with_suffix('.probe').unlink()
        return True, ''
    except OSError as exc:
        return False, device_problem_text(f'{exc.strerror or exc}: {exc}')


class EncryptionSetupDialog(Adw.Window):

    def __init__(self, parent, on_done=None):
        super().__init__(transient_for=parent, modal=True,
                         title='Cifrar copia', default_width=520,
                         default_height=480)
        self.backend = parent.backend
        self.on_done = on_done
        mode = self.backend.profile_mode()
        self.existing = 'gocryptfs' in mode or 'encfs' in mode
        # Start from the current destination so the user does not have to
        # browse to a /run/media/<uuid>/ path by hand. It is still validated:
        # an encrypted profile needs an empty or already initialised folder.
        self.destination = self.backend.destination_path() or ''
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        root.set_margin_top(22)
        root.set_margin_bottom(22)
        root.set_margin_start(22)
        root.set_margin_end(22)
        self.set_content(root)

        title = Gtk.Label(label='Protege tus copias con una contraseña', xalign=0)
        title.add_css_class('rw-wiz-title')
        title.set_wrap(True)
        root.append(title)

        self.destination_button = Gtk.Button(
            label=self.destination or 'Elegir destino…')
        self.destination_button.add_css_class('rw-choice')
        self.destination_button.set_sensitive(not self.existing)
        self.destination_button.connect('clicked', self._choose_destination)
        destination_hint = ('Destino cifrado actual' if self.existing
                            else 'Usa una carpeta nueva o vacía')
        root.append(ui.row('Destino', destination_hint,
                           self.destination_button))

        password_title = ('Contraseña (vacía para conservar la actual)'
                          if self.existing else 'Contraseña')
        root.append(Gtk.Label(label=password_title, xalign=0))
        self.password_entry = Gtk.PasswordEntry()
        ui.a11y(self.password_entry, 'Contraseña del cifrado')
        root.append(self.password_entry)
        root.append(Gtk.Label(label='Confirmar contraseña', xalign=0))
        self.password_confirm = Gtk.PasswordEntry()
        ui.a11y(self.password_confirm, 'Confirmar contraseña del cifrado')
        root.append(self.password_confirm)

        warning = Gtk.Label(
            label='Guarda la contraseña en un lugar seguro: sin ella no '
                  'podrás restaurar tus copias.', xalign=0, wrap=True)
        warning.add_css_class('rw-wiz-sub')
        root.append(warning)

        self.error_label = Gtk.Label(xalign=0, wrap=True)
        self.error_label.add_css_class('rw-wiz-sub')
        root.append(self.error_label)

        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancel = Gtk.Button(label='Cancelar')
        cancel.add_css_class('rw-soft')
        cancel.connect('clicked', lambda *_: self.close())
        actions.append(cancel)
        apply = Gtk.Button(label='Activar cifrado')
        apply.add_css_class('rw-primary')
        apply.connect('clicked', self._apply)
        actions.append(apply)
        root.append(actions)

    def _choose_destination(self, _btn):
        def selected(path):
            self.destination = path
            self.destination_button.set_label(path)

        ui.choose_folder(self, 'Elegir destino cifrado', selected)

    def _apply(self, _btn=None):
        destination = self.destination
        password = self.password_entry.get_text()
        if not destination_is_empty_or_initialized(destination):
            self.error_label.set_text(
                'Elige una carpeta nueva o vacía, o un destino Gocryptfs '
                'ya inicializado')
            return
        if not password and not self.existing:
            self.error_label.set_text('Escribe una contraseña para el cifrado')
            return
        if password != self.password_confirm.get_text():
            self.error_label.set_text('Las contraseñas no coinciden')
            return
        try:
            self.backend.setup_local_gocryptfs(
                destination, password,
                save_in_keyring=bool(getattr(
                    _backend.tools, 'KEYRING_SUPPORTED', False)),
                use_ram_cache=True)
        except Exception as exc:  # noqa: BLE001
            self.error_label.set_text(str(exc))
            return
        if self.on_done is not None:
            self.on_done(destination)
        self.close()
