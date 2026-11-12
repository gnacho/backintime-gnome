"""An encrypted profile without a password must ask, not read stdin.

The core falls back to ``getpass`` when the password is not in the cache or
the keyring, which in a GTK app hangs the mount or fails with EOF. The window
now checks first and opens a password dialog.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bitgnome import backend as _backend  # noqa: E402
from bitgnome.dialogs import PasswordDialog  # noqa: E402

from helpers import TestApp, isolated_home  # noqa: E402


class PasswordTests(unittest.TestCase):
    def setUp(self):
        self.home = isolated_home(self)
        self.backend = TestApp().backend

    def test_plain_profiles_need_no_password(self):
        self.assertTrue(self.backend.password_available())

    def test_encrypted_profile_without_password_is_detected(self):
        self.backend.cfg.setSnapshotsMode('local_gocryptfs')
        self.assertFalse(self.backend.password_available())

    def test_mount_refuses_instead_of_calling_getpass(self):
        self.backend.cfg.setSnapshotsMode('local_gocryptfs')
        ok, error = self.backend.ensure_mounted()
        self.assertFalse(ok)
        self.assertIn('contraseña', error.lower())
        self.assertEqual(self.backend.destination_problem, error)

    def test_dialog_returns_what_was_typed(self):
        received = []
        dialog = PasswordDialog(None, received.append)
        dialog.entry.set_text('secreto')
        dialog._accept()
        self.assertEqual(received, ['secreto'])
        self.assertFalse(dialog.get_visible())

    def test_empty_password_is_refused(self):
        received = []
        dialog = PasswordDialog(None, received.append)
        dialog._accept()
        self.assertEqual(received, [])
        self.assertTrue(dialog.entry.has_css_class('error'))
        dialog.destroy()


if __name__ == '__main__':
    unittest.main(verbosity=2)
