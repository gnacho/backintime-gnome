"""The schedule has to say whether it can actually run.

`crontab` existing is not the same as the cron daemon running: on Arch the
package is installed without enabling the service, so every check that only
looks for the binary reports success while no backup ever runs.
"""
import contextlib
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bitgnome import backend as _backend
from bitgnome.window import (AUTOMATION_ERRORS, cron_fix_text, cron_warning,
                             next_copy_text)

from helpers import TestApp, isolated_home

CRON_UNITS = ('cronie.service', 'cron.service', 'crond.service',
              'fcron.service')


class CronStatusTests(unittest.TestCase):
    def setUp(self):
        self.home = isolated_home(self)
        self.app = TestApp()
        self.backend = self.app.backend
        self.set_mode('DAY')

    def tearDown(self):
        self.set_mode('DISABLED')

    def set_mode(self, name):
        mode = _backend.bitbase.ScheduleMode[name]
        self.backend.cfg.setScheduleMode(mode)

    def _run(self, binary, systemctl='/usr/bin/systemctl',
             active='active', load='loaded'):
        """Patch shutil.which and subprocess.run, return the run mock."""
        def which(name):
            if name == 'systemctl':
                return systemctl
            if name in ('crontab', 'fcrontab'):
                return binary
            return None

        def show(command, **_kwargs):
            unit = command[-1]
            state = load if unit in CRON_UNITS else 'not-found'
            return mock.Mock(
                returncode=0,
                stdout=f'LoadState={state}\nActiveState={active}\n')

        run = mock.Mock(side_effect=show)
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                _backend.shutil, 'which', which))
            stack.enter_context(mock.patch.object(
                _backend.subprocess, 'run', run))
            status = self.backend.cron_status()
        return status, run

    def test_missing_binary_is_reported_as_binary_problem(self):
        status, run = self._run(None)
        self.assertEqual(status['problem'], 'binary')
        self.assertIsNone(status['binary'])
        run.assert_not_called()

    def test_installed_but_stopped_service_is_a_problem(self):
        status, _ = self._run('/usr/bin/crontab', active='inactive')
        self.assertEqual(status['problem'], 'service')
        self.assertEqual(status['service'], 'inactive')
        self.assertIn('parado', cron_warning(status))

    def test_running_service_has_no_problem(self):
        status, _ = self._run('/usr/bin/crontab')
        self.assertIsNone(status['problem'])
        self.assertEqual(status['service'], 'active')
        self.assertIsNone(cron_warning(status))

    def test_missing_systemctl_is_unknown_not_broken(self):
        status, _ = self._run('/usr/bin/crontab', systemctl=None)
        self.assertEqual(status['service'], 'unknown')
        self.assertIsNone(status['problem'])

    def test_manual_schedule_does_not_need_cron(self):
        self.set_mode('DISABLED')
        status, run = self._run(None)
        self.assertFalse(status['needs_cron'])
        self.assertIsNone(status['problem'])
        run.assert_not_called()

    def test_warning_and_fix_name_the_exact_command(self):
        self.assertIn('cron instalado',
                      cron_warning({'problem': 'binary'}).lower())
        self.assertIn('systemctl enable --now cronie.service',
                      cron_fix_text({'problem': 'binary'}))
        self.assertIn('systemctl enable --now cronie.service',
                      cron_fix_text({'problem': 'service'}))
        self.assertEqual(cron_fix_text({'problem': None}), '')
        self.assertIn('cron service not running', AUTOMATION_ERRORS)

    def test_debian_unit_is_not_a_false_alarm(self):
        """Debian and Ubuntu run cron.service, not cronie.service."""
        status, _ = self._run('/usr/bin/crontab')
        self.assertEqual(status['service'], 'active')
        self.assertIsNone(status['problem'])

    def test_no_loaded_unit_is_unknown_not_a_claim(self):
        status, _ = self._run('/usr/bin/crontab', load='not-found')
        self.assertEqual(status['service'], 'unknown')
        self.assertIsNone(status['problem'])
        self.assertIsNone(cron_warning(status))

    def test_broken_systemctl_is_unknown(self):
        run = mock.Mock(side_effect=OSError('dbus is on fire'))
        with mock.patch.object(_backend.shutil, 'which',
                              lambda name: '/usr/bin/systemctl'
                              if name == 'systemctl' else '/usr/bin/crontab'), \
                mock.patch.object(_backend.subprocess, 'run', run):
            status = self.backend.cron_status()
        self.assertEqual(status['service'], 'unknown')
        self.assertIsNone(status['problem'])

    def test_at_every_boot_needs_cron(self):
        """The core writes a crontab @reboot line for this mode."""
        self.set_mode('AT_EVERY_BOOT')
        status, _ = self._run(None)
        self.assertTrue(status['needs_cron'])
        self.assertEqual(status['problem'], 'binary')

    def test_udev_mode_reports_the_missing_service_helper(self):
        self.set_mode('UDEV')
        status, _ = self._run(None)
        self.assertFalse(status['needs_cron'])
        if self.backend._udev_ready() is False:
            self.assertEqual(status['problem'], 'udev')
            self.assertIn('udev', cron_warning(status).lower())
            self.assertIn('cron', cron_fix_text(status).lower())

    def test_next_copy_text_for_every_mode_that_used_to_lie(self):
        self.assertEqual(next_copy_text('MINUTES_5'), 'la próxima, en menos de 5 minutos')
        self.assertEqual(next_copy_text('HOURS_2'), 'la próxima, en menos de 2 horas')
        self.assertEqual(next_copy_text('HOURS_12'), 'la próxima, en menos de 12 horas')
        self.assertEqual(next_copy_text('DAY', (7, 30)), 'la próxima, mañana a las 07:30')
        self.assertEqual(next_copy_text('AT_EVERY_BOOT'),
                         'la próxima, cuando arranques el equipo')
        self.assertEqual(next_copy_text('DISABLED'), 'la próxima, sin copias automáticas')
        self.assertEqual(next_copy_text(''), 'sin programación')

    def test_automation_keeps_the_entry_when_only_the_service_is_down(self):
        """The crontab write succeeds, so enabling the daemon is enough."""
        with mock.patch.object(_backend.shutil, 'which',
                               lambda name: '/usr/bin/systemctl'
                               if name == 'systemctl' else '/usr/bin/crontab'), \
                mock.patch.object(_backend, 'subprocess', mock.Mock(
                    run=mock.Mock(return_value=mock.Mock(
                        returncode=0, text='', stdout='')))):
            self.backend.cfg.setup_automation = lambda: None
            self.backend._cron_installed = lambda: True
            self.backend._cron_service_state = lambda *a, **k: 'inactive'
            applied = self.backend.apply_automation()
        self.assertTrue(applied)
        self.assertEqual(self.backend.last_automation_error,
                         'cron service not running')


if __name__ == '__main__':
    unittest.main(verbosity=2)
