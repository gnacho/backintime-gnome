"""Leaving the first-run wizard has to be possible from any setup page.

It used to be offered only on the welcome page, which made the six step setup
feel mandatory even though the window manager close button worked all along.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers import TestApp, find_widget_by_label, isolated_home

from bitgnome import backend as _backend
from bitgnome.wizard import FirstRunWizard

SETUP_STEPS = (0, 1, 2, 3, 4)


class WizardDismissTests(unittest.TestCase):
    def setUp(self):
        self.home = isolated_home(self)
        self.app = TestApp()
        self.wizard = FirstRunWizard(self.app)

    def dismiss_button(self):
        return find_widget_by_label(self.wizard.card, 'Ahora no')

    def config_path(self):
        return Path(str(_backend.bitbase.DEFAULT_CONFIG_FILE_PATH))

    def test_every_setup_page_offers_the_dismiss_action(self):
        for step in SETUP_STEPS:
            with self.subTest(step=step):
                self.wizard.step = step
                self.wizard._render()
                button = self.dismiss_button()
                self.assertIsNotNone(button, f'no dismiss action on step {step}')
                self.assertTrue(button.get_visible())
                self.assertTrue(button.get_sensitive())

    def test_dismissing_after_the_welcome_page_writes_no_config(self):
        self.assertFalse(self.config_path().exists())
        self.wizard.step = 2
        self.wizard._render()
        self.dismiss_button().emit('clicked')
        self.assertFalse(self.config_path().exists())
        self.assertEqual(list(self.wizard.cfg.include()), [])
        self.assertFalse(self.wizard._applied)
        self.assertFalse(self.wizard.get_visible())

    def test_dismissing_a_later_page_saves_nothing(self):
        from unittest import mock

        saved = mock.Mock()
        self.wizard.cfg.save = saved
        self.wizard.step = 3
        self.wizard._render()
        self.dismiss_button().emit('clicked')
        saved.assert_not_called()

    def test_dismiss_does_not_bypass_the_running_backup_guard(self):
        self.wizard._running = True
        self.assertTrue(self.wizard._on_close_request())
        self.assertFalse(self.wizard._closing)


if __name__ == '__main__':
    unittest.main(verbosity=2)
