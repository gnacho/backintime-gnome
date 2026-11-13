"""Adw.Application wiring: actions, shortcuts, about."""
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from bitgnome import APP_ID, __version__  # noqa: E402
from bitgnome.backend import Backend  # noqa: E402
from bitgnome.i18n import _  # noqa: E402


class BackInTimeApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.backend = Backend()
        self._add_action('quit', self._quit, ['<Control>q'])
        self._add_action('preferences', lambda *_: self._win('open_settings'),
                         ['<Control>comma'])
        self._add_action('advanced-preferences', self._advanced_preferences)
        self._add_action('about', self._about)
        self._add_action('shortcuts', self._shortcuts)
        self._add_action('first-run', lambda *_: self._win('open_wizard'))
        self._add_action('delete-snapshot',
                         lambda *_: self._win('delete_current_snapshot'))
        self._add_action('retention', lambda *_: self._win('review_retention'))
        self._add_action('take-snapshot', lambda *_: self._win('take_snapshot'),
                         ['<Control>s'])
        self._add_action('restore', lambda *_: self._win('restore_selection'),
                         ['<Control>r'])
        self._add_action('restore-to', lambda *_: self._win('restore_selection', True))
        self._add_action('log-view', lambda *_: self._win('show_log'))
        self._add_action('diff', lambda *_: self._win('show_diff'))

    def _quit(self, *_):
        win = self.get_active_window()
        if win is not None:
            win.close()
        else:
            self.quit()

    def _win(self, method, *args):
        win = self.get_active_window()
        if win is None:
            # Without focus get_active_window() is None and menu actions
            # would silently do nothing.
            windows = self.get_windows()
            win = windows[0] if windows else None
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
        # get_active_window() is None without focus, and a second launch
        # (another `rewind`, or `rewind --first-run`) used to create a second
        # main window instead of presenting the one already open.
        win = self.get_active_window()
        if win is None:
            windows = self.get_windows()
            win = windows[0] if windows else MainWindow(self)
        win.present()

    def do_command_line(self, command_line):
        args = command_line.get_arguments() or []
        self.activate()
        if '--first-run' in args:
            win = self.get_active_window()
            if win is not None:
                win.open_wizard()
        return 0

    def _preferences(self, *_):
        self._win('open_settings')

    def _advanced_preferences(self, *_):
        from bitgnome.prefs import PreferencesWindow
        PreferencesWindow(self).present()

    def _about(self, *_args):
        from bitgnome.ui import has_icon
        icon = 'rewind' if has_icon('rewind') else 'document-revert-symbolic'
        dialog = Adw.AboutWindow(
            transient_for=self.get_active_window(),
            application_name='Rewind',
            application_icon=icon,
            version=__version__,
            developer_name=_('Equipo de Back In Time · front GNOME'),
            license_type=Gtk.License.GPL_2_0,
            website='https://github.com/bit-team/backintime',
            issue_url='https://github.com/bit-team/backintime/issues',
            comments=_('Interfaz GTK4 + libadwaita sobre el núcleo de Back In Time.'),
        )
        dialog.present()

    def _shortcuts(self, *_args):
        builder = Gtk.Builder()
        # The window is built from XML, so every title goes through gettext
        # before it reaches the builder.
        builder.add_from_string('''
<interface>
  <object class="GtkShortcutsWindow" id="win">
    <property name="modal">True</property>
    <child>
      <object class="GtkShortcutsSection">
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title">{general}</property>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">{snapshot}</property>
              <property name="accelerator">&lt;Control&gt;s</property>
            </object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">{restore}</property>
              <property name="accelerator">&lt;Control&gt;r</property>
            </object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">{settings}</property>
              <property name="accelerator">&lt;Control&gt;comma</property>
            </object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">{previous}</property>
              <property name="accelerator">Left</property>
            </object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">{next_point}</property>
              <property name="accelerator">Right</property>
            </object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">{back_to_present}</property>
              <property name="accelerator">Escape</property>
            </object></child>
            <child><object class="GtkShortcutsShortcut">
              <property name="title">{quit}</property>
              <property name="accelerator">&lt;Control&gt;q</property>
            </object></child>
          </object>
        </child>
      </object>
    </child>
  </object>
</interface>'''.format(general=_('General'),
                       snapshot=_('Tomar instantánea'),
                       restore=_('Restaurar'),
                       settings=_('Ajustes'),
                       previous=_('Punto anterior'),
                       next_point=_('Punto siguiente'),
                       back_to_present=_('Volver al presente'),
                       quit=_('Salir')))
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
    # loaded, so GUI flags must be gone before the app is built. The flag is
    # still passed to GApplication so a running instance receives it.
    sys.argv = [argv[0]] + [flag for flag in flags if flag != '--first-run']

    app = BackInTimeApp()
    app.force_wizard = force_wizard
    return app.run([argv[0]] + flags)
