"""Adw.Application wiring: actions, shortcuts, about."""
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from bitgnome import APP_ID, __version__  # noqa: E402
from bitgnome.backend import Backend  # noqa: E402


class BackInTimeApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.backend = Backend()
        self._add_action('quit', lambda *_: self.quit(), ['<Control>q'])
        self._add_action('preferences', lambda *_: self._win('open_settings'),
                         ['<Control>comma'])
        self._add_action('advanced-preferences', self._advanced_preferences)
        self._add_action('about', self._about)
        self._add_action('shortcuts', self._shortcuts)
        self._add_action('toggle-theme', lambda *_: self._win('_toggle_dark'))
        self._add_action('first-run', lambda *_: self._win('open_wizard'))
        self._add_action('delete-snapshot',
                         lambda *_: self._win('delete_current_snapshot'))
        self._add_action('take-snapshot', lambda *_: self._win('take_snapshot'),
                         ['<Control>s'])
        self._add_action('restore', lambda *_: self._win('restore_selection'),
                         ['<Control>r'])
        self._add_action('restore-to', lambda *_: self._win('restore_selection', True))
        self._add_action('log-view', lambda *_: self._win('show_log'))
        self._add_action('diff', lambda *_: self._win('show_diff'))

    def _win(self, method, *args):
        win = self.get_active_window()
        if win:
            getattr(win, method)(*args)

    def _add_action(self, name, callback, shortcuts=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect('activate', callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f'app.{name}', shortcuts)

    def do_activate(self):
        from bitgnome.window import MainWindow
        win = self.get_active_window() or MainWindow(self)
        win.present()

    def _preferences(self, *_):
        self._win('open_settings')

    def _advanced_preferences(self, *_):
        from bitgnome.prefs import PreferencesWindow
        PreferencesWindow(self).present()

    def _about(self, *_):
        dialog = Adw.AboutWindow(
            transient_for=self.get_active_window(),
            application_name='Rewind',
            application_icon='camera-photo-symbolic',
            version=__version__,
            developer_name='Equipo de Back In Time · front GNOME',
            license_type=Gtk.License.GPL_2_0,
            website='https://github.com/bit-team/backintime',
            issue_url='https://github.com/bit-team/backintime/issues',
            comments='Interfaz GTK4 + libadwaita sobre el núcleo de Back In Time.',
        )
        dialog.present()

    def _shortcuts(self, *_):
        builder = Gtk.Builder()
        builder.add_from_string('''
<interface>
  <object class="GtkShortcutsWindow" id="win">
    <property name="modal">True</property>
    <child>
      <object class="GtkShortcutsSection">
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title">General</property>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">Tomar instantánea</property>
              <property name="accelerator">&lt;Control&gt;s</property>
            </object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">Restaurar</property>
              <property name="accelerator">&lt;Control&gt;r</property>
            </object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">Ajustes</property>
              <property name="accelerator">&lt;Control&gt;comma</property>
            </object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">Punto anterior / siguiente</property>
              <property name="accelerator">Left / Right</property>
            </object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">Volver al presente</property>
              <property name="accelerator">Escape</property>
            </object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">Salir</property>
              <property name="accelerator">&lt;Control&gt;q</property>
            </object></child>
          </object>
        </child>
      </object>
    </child>
  </object>
</interface>''')
        win = builder.get_object('win')
        win.set_transient_for(self.get_active_window())
        win.present()


USAGE = """Usage: backintime-gnome [OPTION]

Options:
  --first-run   Open the first-run wizard even if the profile is configured
  --help        Show this help and exit
"""


def main():
    argv = list(sys.argv)
    flags = argv[1:]
    if '--help' in flags or '-h' in flags:
        print(USAGE, end='')
        return 0

    force_wizard = '--first-run' in flags
    # Back In Time's own argument parser reads sys.argv while the config is
    # loaded, so GUI flags must be gone before the app is built.
    sys.argv = [argv[0]] + [flag for flag in flags if flag != '--first-run']

    app = BackInTimeApp()
    app.force_wizard = force_wizard
    return app.run(sys.argv)
