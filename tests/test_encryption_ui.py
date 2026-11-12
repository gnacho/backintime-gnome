"""UI acceptance tests for guided encrypted profiles."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bitgnome.prefs import PreferencesWindow, _MODES
from bitgnome.settings_sheet import SettingsSheet
from bitgnome.wizard import FirstRunWizard

from helpers import TestApp, find_widget_by_label, isolated_home, widget_texts


class EncryptionUiTests(unittest.TestCase):
    def setUp(self):
        self.home = isolated_home(self)
        self.app = TestApp()
        self.wizard = FirstRunWizard(self.app)
        self.wizard.step = 3
        self.wizard._render()

    def test_destination_offers_an_encryption_choice(self):
        texts = ' '.join(widget_texts(self.wizard.card)).lower()
        self.assertIn('cifrar', texts)

    def test_enabling_encryption_requests_password_and_confirmation(self):
        toggle = find_widget_by_label(self.wizard.card, 'Cifrar la copia')
        self.assertIsNotNone(toggle)
        toggle.set_active(True)
        texts = ' '.join(widget_texts(self.wizard.card)).lower()
        self.assertIn('contraseña', texts)
        self.assertIn('confirmar contraseña', texts)
        self.assertIn('gocryptfs', texts)

    def test_mismatched_passwords_stop_apply(self):
        toggle = find_widget_by_label(self.wizard.card, 'Cifrar la copia')
        toggle.set_active(True)
        self.wizard.folders = {'/tmp'}
        self.wizard.destination = self.home.name + '/destination'
        self.wizard.password_entry.set_text('first-secret')
        self.wizard.password_confirm.set_text('different-secret')
        self.app.backend.apply_automation = lambda: True
        self.assertFalse(self.wizard._apply())
        texts = ' '.join(widget_texts(self.wizard.card)).lower()
        self.assertIn('no coinciden', texts)

    def test_wizard_keeps_encryption_fields_when_navigating_back(self):
        toggle = find_widget_by_label(self.wizard.card, 'Cifrar la copia')
        toggle.set_active(True)
        self.wizard.password_entry.set_text('keep-secret')
        self.wizard.password_confirm.set_text('keep-secret')
        self.wizard.step = 4
        self.wizard._render()
        self.wizard.step = 3
        self.wizard._render()
        self.assertEqual(self.wizard.password_entry.get_text(), 'keep-secret')
        self.assertEqual(self.wizard.password_confirm.get_text(), 'keep-secret')

    def test_wizard_applies_the_guided_encrypted_profile(self):
        toggle = find_widget_by_label(self.wizard.card, 'Cifrar la copia')
        toggle.set_active(True)
        self.wizard.folders = {'/tmp'}
        self.wizard.destination = self.home.name + '/new-encrypted'
        self.wizard.password_entry.set_text('same-secret')
        self.wizard.password_confirm.set_text('same-secret')
        calls = []

        def setup(destination, password, **options):
            calls.append((destination, password, options))

        self.app.backend.setup_local_gocryptfs = setup
        self.app.backend.apply_automation = lambda: True
        self.assertTrue(self.wizard._apply())
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], self.wizard.destination)
        self.assertEqual(calls[0][1], 'same-secret')
        self.assertTrue(self.wizard._applied)

    def test_profile_with_snapshots_is_not_silently_switched_to_encryption(self):
        toggle = find_widget_by_label(self.wizard.card, 'Cifrar la copia')
        toggle.set_active(True)
        self.wizard.folders = {'/tmp'}
        self.wizard.destination = self.home.name + '/new-encrypted'
        self.wizard.password_entry.set_text('same-secret')
        self.wizard.password_confirm.set_text('same-secret')
        self.app.backend.list_snapshots = lambda: [object()]
        original = self.app.backend.profile_mode()
        self.assertFalse(self.wizard._apply())
        self.assertEqual(self.app.backend.profile_mode(), original)
        self.assertIn('perfil nuevo',
                      ' '.join(widget_texts(self.wizard.card)).lower())

    def test_nonempty_destination_is_rejected_for_guided_encryption(self):
        toggle = find_widget_by_label(self.wizard.card, 'Cifrar la copia')
        toggle.set_active(True)
        destination = self.home.name + '/existing'
        os.mkdir(destination)
        Path(destination, 'snapshot').write_text('keep me')
        self.wizard.folders = {'/tmp'}
        self.wizard.destination = destination
        self.wizard.password_entry.set_text('same-secret')
        self.wizard.password_confirm.set_text('same-secret')
        self.app.backend.apply_automation = lambda: True
        self.assertFalse(self.wizard._apply())
        self.assertTrue(Path(destination, 'snapshot').exists())
        texts = ' '.join(widget_texts(self.wizard.card)).lower()
        self.assertIn('vacía', texts)

    def test_summary_shows_the_encryption_backend(self):
        toggle = find_widget_by_label(self.wizard.card, 'Cifrar la copia')
        toggle.set_active(True)
        self.wizard.step = 4
        self.wizard._render()
        texts = ' '.join(widget_texts(self.wizard.card)).lower()
        self.assertIn('cifrado', texts)
        self.assertIn('gocryptfs', texts)

    def test_settings_offers_encryption_setup(self):
        settings = SettingsSheet(self.app)
        texts = ' '.join(widget_texts(settings.body)).lower()
        self.assertIn('cifrar', texts)

    def test_encryption_warns_that_the_password_is_required_for_restore(self):
        toggle = find_widget_by_label(self.wizard.card, 'Cifrar la copia')
        toggle.set_active(True)
        texts = ' '.join(widget_texts(self.wizard.card)).lower()
        self.assertIn('sin ella no podrás restaurar', texts)

    def test_advanced_editor_routes_encrypted_mode_through_guided_setup(self):
        original = self.app.backend.profile_mode()
        window = PreferencesWindow(self.app)
        window.mode_row.set_selected(_MODES.index('local_gocryptfs'))
        window._on_mode_changed(window.mode_row)
        self.assertEqual(self.app.backend.profile_mode(), original)
        dialog = getattr(window, 'encryption_dialog', None)
        self.assertIsNotNone(dialog)
        self.assertTrue(dialog.get_visible())

    def test_settings_ssh_encrypted_profile_points_to_profile_manager(self):
        self.app.backend.profile_mode = lambda: 'ssh_gocryptfs'
        self.app.backend.profile_summary = lambda: (
            'ssh_gocryptfs', '/remote/encrypted')
        settings = SettingsSheet(self.app)
        button = find_widget_by_label(settings.body, 'Configurar cifrado…')
        self.assertFalse(button.get_sensitive())
        self.assertIn('ssh',
                      ' '.join(widget_texts(settings.body)).lower())

    def test_wizard_does_not_offer_local_encryption_for_ssh_profile(self):
        self.app.backend.profile_mode = lambda: 'ssh_gocryptfs'
        wizard = FirstRunWizard(self.app)
        wizard.step = 3
        wizard._render()
        self.assertIsNone(find_widget_by_label(wizard.card, 'Cifrar la copia'))

    def test_settings_shows_an_active_encrypted_profile(self):
        self.app.backend.profile_mode = lambda: 'local_gocryptfs'
        self.app.backend.profile_summary = lambda: (
            'local_gocryptfs', '/tmp/encrypted')
        settings = SettingsSheet(self.app)
        texts = ' '.join(widget_texts(settings.body)).lower()
        self.assertIn('cifrado activo', texts)
        self.assertIn('gocryptfs', texts)

    def test_encrypted_settings_use_guided_destination_instead_of_plain_picker(self):
        self.app.backend.profile_mode = lambda: 'local_gocryptfs'
        self.app.backend.profile_summary = lambda: (
            'local_gocryptfs', '/tmp/encrypted')
        settings = SettingsSheet(self.app)
        change = find_widget_by_label(settings.destination_row, 'Cambiar…')
        self.assertFalse(change.get_sensitive())

    def test_existing_encrypted_profile_can_keep_its_password(self):
        self.app.backend.profile_mode = lambda: 'local_gocryptfs'
        self.app.backend.profile_summary = lambda: (
            'local_gocryptfs', '/tmp/encrypted')
        self.app.backend.destination_path = lambda: '/tmp/encrypted'
        settings = SettingsSheet(self.app)
        button = find_widget_by_label(settings.body, 'Configurar cifrado…')
        button.emit('clicked')
        dialog = settings.encryption_dialog
        self.assertEqual(dialog.destination, '/tmp/encrypted')
        self.assertFalse(dialog.destination_button.get_sensitive())
        calls = []

        def setup(destination, password, **options):
            calls.append((destination, password))

        self.app.backend.setup_local_gocryptfs = setup
        dialog.password_entry.set_text('')
        dialog.password_confirm.set_text('')
        find_widget_by_label(dialog, 'Activar cifrado').emit('clicked')
        self.assertEqual(calls, [('/tmp/encrypted', '')])

    def test_settings_button_opens_guided_encryption_dialog(self):
        settings = SettingsSheet(self.app)
        button = find_widget_by_label(settings.body, 'Cifrar copia…')
        self.assertIsNotNone(button)
        button.emit('clicked')
        dialog = getattr(settings, 'encryption_dialog', None)
        self.assertIsNotNone(dialog)
        self.assertTrue(dialog.get_visible())
        texts = ' '.join(widget_texts(dialog)).lower()
        self.assertIn('contraseña', texts)
        self.assertIn('destino', texts)

    def _open_encryption_dialog(self):
        settings = SettingsSheet(self.app)
        settings._open_encryption_setup(None)
        return settings, settings.encryption_dialog

    def test_settings_dialog_rejects_mismatched_passwords(self):
        _settings, dialog = self._open_encryption_dialog()
        dialog.destination = self.home.name + '/guided'
        dialog.password_entry.set_text('first-secret')
        dialog.password_confirm.set_text('different-secret')
        calls = []
        self.app.backend.setup_local_gocryptfs = (
            lambda *args, **kwargs: calls.append((args, kwargs)))
        find_widget_by_label(dialog, 'Activar cifrado').emit('clicked')
        self.assertEqual(calls, [])
        self.assertIn('no coinciden',
                      ' '.join(widget_texts(dialog)).lower())

    def test_settings_dialog_applies_valid_encryption(self):
        _settings, dialog = self._open_encryption_dialog()
        dialog.destination = self.home.name + '/guided'
        dialog.password_entry.set_text('same-secret')
        dialog.password_confirm.set_text('same-secret')
        calls = []

        def setup(destination, password, **options):
            calls.append((destination, password, options))

        self.app.backend.setup_local_gocryptfs = setup
        find_widget_by_label(dialog, 'Activar cifrado').emit('clicked')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], dialog.destination)
        self.assertEqual(calls[0][1], 'same-secret')
        self.assertFalse(dialog.get_visible())


if __name__ == '__main__':
    result = unittest.main(exit=False)
    if result.result.wasSuccessful():
        print('ENCRYPTION_UI_TESTS_PASSED')
    else:
        raise SystemExit(1)
