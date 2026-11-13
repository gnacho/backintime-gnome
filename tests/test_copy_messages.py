"""A copy that worked must not be reported as failed.

Reported from a real run: rsync exited 0 and copied 2.53 GB, then the disk
dropped off the bus during the core's post-processing. The window said
"Instantánea terminada con errores: [] Por favor, ten paciencia. Finalizando…"
which is both false and unreadable.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bitgnome import backend as _backend  # noqa: E402

from helpers import TestApp, isolated_home  # noqa: E402


class EngineMessageTests(unittest.TestCase):
    def setUp(self):
        self.home = isolated_home(self)
        self.backend = TestApp().backend

    def test_filler_is_dropped(self):
        self.assertEqual(
            self.backend.engine_message(
                ([''], 'Por favor, ten paciencia. Finalizando…')), '')
        self.assertEqual(self.backend.engine_message('[] Por favor, ten '
                                                     'paciencia. Finalizando…'),
                         '')

    def test_real_error_survives(self):
        self.assertIn('rsync', self.backend.engine_message(
            ('I', 'rsync: connection unexpectedly closed')))


class CopyResultTests(unittest.TestCase):
    def setUp(self):
        self.home = isolated_home(self)
        self.backend = TestApp().backend

    def run_copy(self, ret_error, msg, after):
        class FakeSnapshots:
            def __init__(self, _cfg):
                pass

            def backup(self, force=True):
                return ret_error

            def takeSnapshotMessage(self):
                return msg

        self.backend.profile_mode = lambda: 'local'
        self.backend.list_snapshots = mock.Mock(side_effect=[
            [mock.Mock(path=lambda: 'old')],   # before
            after,                             # after
        ])
        with mock.patch.object(_backend, 'snapshots',
                               mock.Mock(Snapshots=FakeSnapshots)):
            return self.backend.take_snapshot()

    def test_success_is_silent(self):
        self.assertIsNone(self.run_copy(False, None, []))

    def test_post_processing_failure_with_new_snapshot_says_so(self):
        new = mock.Mock(path=lambda: 'new')
        message = self.run_copy(True, ([''], 'Por favor, ten paciencia. '
                                             'Finalizando…'), [new])
        self.assertIn('se creó', message)
        self.assertNotIn('paciencia', message.lower())

    def test_failure_without_snapshot_says_it_failed(self):
        message = self.run_copy(True, ('E', 'rsync: no space left'), [])
        self.assertIn('falló', message)
        self.assertIn('no space left', message)


if __name__ == '__main__':
    unittest.main(verbosity=2)
