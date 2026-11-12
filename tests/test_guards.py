"""Nothing changes while an operation is running.

Two related defects: the settings sheet wrote the new profile (config and
state.ini) before the window could refuse to reload, and the long operations
did not exclude each other.
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
from bitgnome.settings_sheet import SettingsSheet  # noqa: E402
from bitgnome.window import MainWindow  # noqa: E402

from helpers import isolated_home  # noqa: E402


class WindowTests(unittest.TestCase):
    _ids = itertools.count()

    def setUp(self):
        self.home = isolated_home(self)
        self.app = Adw.Application(
            application_id=f'net.local.RewindGuard{next(self._ids)}',
            flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.app.backend = Backend()
        self.app.force_wizard = False
        self.app.register(None)
        self.window = MainWindow(self.app)

    def tearDown(self):
        self.window.destroy()

    def test_idle_window_has_no_reason_to_wait(self):
        self.assertIsNone(self.window.busy_reason())

    def test_running_operation_blocks_with_a_reason(self):
        self.window._busy_ops = 1
        self.assertIsNotNone(self.window.busy_reason())
        self.assertTrue(self.window._busy_guard('restaurar'))

    def test_restore_and_delete_refuse_while_busy(self):
        self.window._busy_ops = 1
        self.window.selected = {'rel': 'x', 'is_dir': False}
        self.window.restore_selection()
        self.window.delete_current_snapshot()
        self.assertIsNone(getattr(self.window, '_close_dialog', None))

    def test_copy_refuses_while_busy(self):
        self.window._busy_ops = 1
        self.window.take_snapshot()
        self.assertFalse(self.window._snap_worker_running)


class Parent(Adw.Window):
    """A real window, because transient_for demands one."""

    def __init__(self, reason=None):
        super().__init__()
        self.reason = reason

    def busy_reason(self):
        return self.reason


class FakeApp:
    def __init__(self, backend, parent):
        self.backend = backend
        self._parent = parent

    def get_active_window(self):
        return self._parent


class SheetGuardTests(unittest.TestCase):
    def setUp(self):
        self.home = isolated_home(self)
        self.backend = Backend()

    def sheet(self, parent):
        return SettingsSheet(FakeApp(self.backend, parent))

    def test_busy_parent_keeps_the_current_profile(self):
        called = []
        self.backend.set_current_profile = lambda pid: called.append(pid)
        sheet = self.sheet(Parent('Espera a que termine la operación'))
        sheet._on_profile_chosen(None, '2')
        self.assertEqual(called, [])

    def test_free_parent_allows_the_change(self):
        called = []
        self.backend.set_current_profile = lambda pid: called.append(pid)
        sheet = self.sheet(Parent())
        sheet._on_profile_chosen(None, '2')
        self.assertEqual(called, ['2'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
