"""An unreachable destination is not "no copies yet".

The window used to paint "Sin copias todavía" whenever the snapshot list came
back empty, which is what happens when the destination is on a disk that is
not plugged in. The core already writes a user facing message for that case
(MountError.gui_msg) and it was being dropped.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bitgnome import backend as _backend  # noqa: E402

from helpers import TestApp, isolated_home  # noqa: E402


class DestinationTests(unittest.TestCase):
    def setUp(self):
        self.home = isolated_home(self)
        self.backend = TestApp().backend

    def test_missing_local_destination_is_reported_not_swallowed(self):
        missing = Path(self.home.name) / 'no-existe'
        self.backend.set_destination_path(str(missing))
        ok, error = self.backend.ensure_mounted()
        self.assertFalse(ok)
        self.assertIn('destination', error.lower())
        self.assertIsNotNone(self.backend.destination_problem)

    def test_existing_local_destination_is_fine(self):
        existing = Path(self.home.name) / 'destino'
        existing.mkdir()
        self.backend.set_destination_path(str(existing))
        ok, error = self.backend.ensure_mounted()
        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertIsNone(self.backend.destination_problem)

    def test_snapshot_log_does_not_need_a_mount(self):
        """The log is local: it has to survive an unplugged destination."""
        # The core decides the file name: takeSnapshotLogFile() returns
        # takesnapshot_.log for every profile in 2.0.
        log_path = Path(self.backend.cfg.takeSnapshotLogFile(None))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text('ERROR algo fallo\n', encoding='utf-8')

        def boom(*_args, **_kwargs):
            raise AssertionError('snapshot_log must not mount anything')

        with mock.patch.object(self.backend, 'mounted', boom):
            lines = list(self.backend.snapshot_log())
        self.assertTrue(any('algo fallo' in line for line in lines))

    def test_copy_uses_the_core_message_when_the_backup_fails(self):
        class FakeSnapshots:
            def __init__(self, _cfg):
                pass

            def backup(self, force=True):
                raise _BackupFailure()

        class _BackupFailure(Exception):
            gui_msg = 'Conecta el disco de copias, por favor.'

        with mock.patch.object(_backend, 'snapshots',
                              mock.Mock(Snapshots=FakeSnapshots)):
            self.assertEqual(self.backend.take_snapshot(),
                             'Conecta el disco de copias, por favor.')

    def test_copy_remounts_the_destination_afterwards(self):
        calls = []

        class FakeSnapshots:
            def __init__(self, _cfg):
                pass

            def backup(self, force=True):
                return False

            def takeSnapshotMessage(self):
                return None

        self.backend.profile_mode = lambda: 'ssh'
        self.backend.release_mount = lambda: calls.append('release')
        self.backend.ensure_mounted = lambda: (calls.append('mount'), (True, None))[1]
        with mock.patch.object(_backend, 'snapshots',
                              mock.Mock(Snapshots=FakeSnapshots)):
            self.assertIsNone(self.backend.take_snapshot())
        self.assertEqual(calls, ['release', 'mount'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
