"""The app follows the desktop theme, and the wizard is reachable again.

The stylesheet is written for a light desktop, so a `rw-dark` class repaints
it when the desktop is dark. Before this, the window was painted light even
with the system in dark mode and the only control was a menu entry that
forced dark, which read as "the theme does not work".
"""
import itertools
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gi

gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gio  # noqa: E402

from bitgnome.backend import Backend  # noqa: E402
from bitgnome.window import MainWindow  # noqa: E402

from helpers import isolated_home  # noqa: E402


class ThemeTestApplication(Adw.Application):
    """A real Adw.Application: MainWindow needs one to attach to."""

    _ids = itertools.count()

    def __init__(self):
        super().__init__(application_id=f'net.local.RewindThemeTest{next(self._ids)}',
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.backend = Backend()
        self.force_wizard = False


def menu_actions(model, found=None):
    """Collect every action name in a Gio.Menu, recursing into sections."""
    from gi.repository import GLib

    found = [] if found is None else found
    for index in range(model.get_n_items()):
        action = model.get_item_attribute_value(
            index, 'action', GLib.VariantType.new('s'))
        if action is not None:
            found.append(action.get_string())
        link = model.get_item_link(index, Gio.MENU_LINK_SECTION)
        if link is not None:
            menu_actions(link, found)
    return found


class ThemeAndWizardTests(unittest.TestCase):
    def setUp(self):
        self.home = isolated_home(self)
        self.app = ThemeTestApplication()
        self.app.register(None)   # windows need the startup signal emitted
        self.manager = Adw.StyleManager.get_default()
        self.win = MainWindow(self.app)

    def tearDown(self):
        self.manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
        self.win.destroy()

    def test_dark_desktop_paints_the_dark_palette(self):
        self.manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        self.assertTrue(self.win.has_css_class('rw-dark'))

    def test_light_desktop_keeps_the_light_palette(self):
        self.manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        self.assertFalse(self.win.has_css_class('rw-dark'))

    def test_dialogs_follow_the_theme_too(self):
        from bitgnome.dialogs import RetentionDialog

        self.manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        dialog = RetentionDialog(None, {'engine_inert': True, 'label': '2 años'})
        self.assertTrue(dialog.has_css_class('rw-dark'))
        dialog.destroy()
        self.manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        dialog = RetentionDialog(None, {'engine_inert': True, 'label': '2 años'})
        self.assertFalse(dialog.has_css_class('rw-dark'))
        dialog.destroy()

    def test_theme_is_not_forced_by_the_app(self):
        self.assertFalse(hasattr(self.win, '_toggle_dark'))
        self.assertIn(self.manager.get_color_scheme(),
                      (Adw.ColorScheme.DEFAULT, Adw.ColorScheme.FORCE_LIGHT,
                       Adw.ColorScheme.FORCE_DARK))

    def test_menu_reopens_the_wizard_and_drops_the_theme_toggle(self):
        actions = menu_actions(self.win.menu)
        self.assertIn('app.first-run', actions)
        self.assertNotIn('app.toggle-theme', actions)

    def test_the_wizard_can_be_opened_after_the_first_run(self):
        wizard = self.win.open_wizard()
        self.assertIsNotNone(wizard)
        self.assertTrue(wizard.get_visible())


if __name__ == '__main__':
    unittest.main(verbosity=2)
