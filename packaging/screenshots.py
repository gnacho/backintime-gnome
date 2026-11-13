#!/usr/bin/env python3
"""Render the README screenshots from a throwaway profile.

Nothing here touches the real profile: ``HOME``, ``XDG_CONFIG_HOME`` and
``XDG_DATA_HOME`` point at a temporary directory, three real snapshots are taken
with the installed engine and the window states are painted with
``Gtk.WidgetPaintable``. That skips ``org.gnome.Shell.Screenshot`` and needs no
compositor portal, which is what made the first captures impossible.

    ./packaging/screenshots.py --lang en --out capturas/en

Requires a running session (a display and GTK) and the Back In Time engine
installed. The output names are fixed: main-window, time-travel, search and
settings.
"""
import argparse
import datetime
import os
import shutil
import sys
import tempfile
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk, Gsk, Graphene  # noqa: E402

SIZE = (1160, 780)
SHEET_SIZE = (660, 760)
# Shown in the settings capture instead of the temporary folder, which would
# leak a throwaway path. Only the label is affected: no snapshot is taken
# after this point.
DEMO_DESTINATION = '/media/backup/Rewind'


def pump(seconds):
    """Iterate the main loop for a while so workers and layout settle."""
    context = GLib.MainContext.default()
    deadline = time.time() + seconds
    while time.time() < deadline:
        while context.iteration(False):
            pass
        time.sleep(0.02)


def wait_for(predicate, timeout=20.0):
    context = GLib.MainContext.default()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        while context.iteration(False):
            pass
        time.sleep(0.02)
    return False


def render(widget, path, size, tries=4):
    width, height = size
    node = None
    for _attempt in range(tries):
        paintable = Gtk.WidgetPaintable.new(widget)
        snapshot = Gtk.Snapshot.new()
        paintable.snapshot(snapshot, width, height)
        node = snapshot.to_node()
        if node is not None:
            break
        pump(0.5)
    if node is None:
        print('nothing to paint in', path, file=sys.stderr)
        return False
    native = widget.get_native()
    renderer = native.get_renderer() if native is not None else None
    if renderer is None:
        renderer = Gsk.CairoRenderer.new()
    texture = renderer.render_texture(node, Graphene.Rect().init(0, 0, width,
                                                                height))
    texture.save_to_png(path)
    print('wrote', path)
    return True


FOLDER_NAMES = {
    'en': ('Documents', 'Pictures', 'Projects', 'invoices'),
    'es': ('Documentos', 'Imágenes', 'Proyectos', 'facturas'),
}


def seed_files(root, lang='en'):
    """A small tree with something to change and something to remove."""
    documents_name, pictures_name, projects_name, invoices_name = FOLDER_NAMES.get(
        lang, FOLDER_NAMES['en'])
    documents = os.path.join(root, documents_name)
    pictures = os.path.join(root, pictures_name)
    projects = os.path.join(root, projects_name)
    invoices = os.path.join(documents, invoices_name)
    for folder in (documents, pictures, projects, invoices):
        os.makedirs(folder, exist_ok=True)
    lines = ['Presupuesto del trimestre', '', 'Servidor: 1.200 EUR',
             'Soporte: 480 EUR', '']
    with open(os.path.join(documents, 'informe.odt'), 'w') as handle:
        handle.write('\n'.join(lines))
    with open(os.path.join(documents, 'notas.txt'), 'w') as handle:
        handle.write('Llamar al gestor el martes\n')
    with open(os.path.join(invoices, '2026-01.pdf'), 'w') as handle:
        handle.write('%PDF-1.4 factura de enero\n')
    with open(os.path.join(pictures, 'obra.jpg'), 'w') as handle:
        handle.write('foto\n')
    return documents, invoices


def touch_round(document, invoices, round_number):
    if round_number == 1:
        with open(document, 'a') as handle:
            handle.write('Revisión del cliente: 1.450 EUR\n')
        with open(os.path.join(invoices, '2026-02.pdf'), 'w') as handle:
            handle.write('%PDF-1.4 factura de febrero\n')
    else:
        os.unlink(os.path.join(os.path.dirname(document), 'notas.txt'))
        with open(document, 'a') as handle:
            handle.write('Cierre: pendiente de firma\n')


def current_lang():
    from bitgnome import i18n
    return i18n.language()


def home_root(path):
    return os.path.dirname(path)


def configure(backend, documents, backend_module):
    cfg = backend.cfg
    destination = os.path.join(os.environ['HOME'], 'Respaldos')
    os.makedirs(destination, exist_ok=True)
    cfg.setSnapshotsMode('local')
    backend.set_destination_path(destination)
    _documents, pictures, projects, _invoices = FOLDER_NAMES.get(
        current_lang(), FOLDER_NAMES['en'])
    root = home_root(documents)
    cfg.setInclude([(documents, 0),
                    (os.path.join(root, pictures), 0),
                    (os.path.join(root, projects), 0)])
    cfg.setExclude([])
    cfg.setScheduleMode(backend_module.bitbase.ScheduleMode['DISABLED'])
    cfg.setRemoveOldSnapshots(True, 2,
                              backend_module.bitbase.TimeUnit['WEEK'])
    cfg.save()
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lang', default='en')
    parser.add_argument('--out', default='capturas/en')
    parser.add_argument('--keep', action='store_true',
                        help='do not delete the temporary profile')
    args = parser.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)

    home = tempfile.mkdtemp(prefix='rewind-shots-')
    os.environ['HOME'] = home
    os.environ['XDG_CONFIG_HOME'] = os.path.join(home, '.config')
    os.environ['XDG_DATA_HOME'] = os.path.join(home, '.local', 'share')
    os.environ['BACKINTIME_GNOME_LANG'] = args.lang
    os.environ['LANGUAGE'] = args.lang

    documents, invoices = seed_files(home, args.lang)

    from bitgnome import backend as backend_module
    from bitgnome import i18n
    from bitgnome.backend import Backend
    from bitgnome.window import MainWindow

    backend = Backend()
    if backend.error:
        print('engine error:', backend.error, file=sys.stderr)
        return 1
    configure(backend, documents, backend_module)

    for round_number in range(3):
        if round_number:
            touch_round(os.path.join(documents, 'informe.odt'), invoices,
                        round_number)
        error = backend.take_snapshot()
        if error:
            print('snapshot error:', error, file=sys.stderr)
            return 1
    print('snapshots:', len(backend.list_snapshots()))

    app = Adw.Application(application_id='com.github.bitteam.'
                                         'BackInTimeGNOME.Shots')
    app.backend = backend
    app.register(None)
    window = MainWindow(app)
    window.set_default_size(*SIZE)
    window.present()
    if not wait_for(lambda: bool(window.chrono)):
        print('the window never loaded the snapshots', file=sys.stderr)
        return 1
    pump(1.5)

    out = args.out if os.path.isabs(args.out) else os.path.join(root, args.out)
    os.makedirs(out, exist_ok=True)

    window.index = None
    window._apply_mode()
    pump(1.0)
    render(window, os.path.join(out, 'main-window.png'), SIZE)

    window.set_snapshot(1)
    window.rel = ''
    window.render_files()
    pump(0.8)
    target = os.path.basename(documents)
    for entry in window._root_entries():
        if entry['name'] == target:
            window._activate(entry)
            break
    pump(1.5)
    render(window, os.path.join(out, 'time-travel.png'), SIZE)

    window.search.set_text('informe')
    pump(2.0)
    render(window, os.path.join(out, 'search.png'), SIZE)

    window.search.set_text('')
    window.backend.set_destination_path(DEMO_DESTINATION)
    window.open_settings()
    sheet = getattr(window, '_settings_sheet', None)
    if sheet is None:
        print('the settings sheet did not open', file=sys.stderr)
        return 1
    sheet.set_default_size(*SHEET_SIZE)
    pump(2.0)
    render(sheet, os.path.join(out, 'settings.png'), SHEET_SIZE)

    sheet.close()
    window._close_now()
    pump(0.5)
    if not args.keep:
        shutil.rmtree(home, ignore_errors=True)
    else:
        print('profile kept in', home)
    return 0


if __name__ == '__main__':
    sys.exit(main())
