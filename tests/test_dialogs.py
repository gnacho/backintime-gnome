"""Retention dialog buttons have to do something when clicked.

`_buttons` was a `staticmethod` whose lambdas called `self.close()`, so both
buttons raised NameError and the dialog could not be closed from its own
action. Clicking them here is the regression test.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bitgnome.dialogs import RetentionDialog  # noqa: E402

from helpers import find_widget_by_label, isolated_home  # noqa: E402


class RetentionDialogTests(unittest.TestCase):
    def setUp(self):
        self.home = isolated_home(self)
        self.opened = []
        self.dialog = RetentionDialog(
            None, {'engine_inert': True, 'label': '2 años'},
            on_settings=lambda: self.opened.append(True))

    def test_the_dialog_can_be_closed_from_its_own_button(self):
        button = find_widget_by_label(self.dialog.get_content(), 'Entendido')
        self.assertIsNotNone(button)
        button.emit('clicked')
        self.assertFalse(self.dialog.get_visible())

    def test_the_settings_button_calls_back_and_closes(self):
        self.dialog.present()
        button = find_widget_by_label(self.dialog.get_content(),
                                      'Abrir ajustes')
        self.assertIsNotNone(button)
        button.emit('clicked')
        self.assertEqual(self.opened, [True])
        self.assertFalse(self.dialog.get_visible())

    def test_the_buttons_are_not_a_static_helper(self):
        import inspect

        self.assertNotIsInstance(
            inspect.getattr_static(RetentionDialog, '_buttons'), staticmethod)


if __name__ == '__main__':
    unittest.main(verbosity=2)
