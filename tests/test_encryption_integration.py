"""Real Gocryptfs integration against an isolated HOME and destination."""
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bitgnome.backend import Backend, tools

from helpers import isolated_home


class EncryptionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.home = isolated_home(self)
        self.backend = Backend()
        self.addCleanup(self.backend.release_mount)
        self.addCleanup(self._force_cleanup_mounts)
        self.destination = Path(self.home.name, 'encrypted-backup')

    def _force_cleanup_mounts(self):
        mount_root = Path(self.home.name, '.local/share/backintime/mnt')
        for path in mount_root.glob('*/mountpoint'):
            if tools.is_mounted(path):
                subprocess.run(['fusermount3', '-u', str(path)], check=False)

    def test_initialize_an_encrypted_profile_without_plaintext_secrets(self):
        self.backend.setup_local_gocryptfs(
            str(self.destination), 'integration-secret',
            save_in_keyring=False, use_ram_cache=False)
        self.assertTrue((self.destination / 'gocryptfs.conf').exists())
        self.assertEqual(self.backend.cfg.snapshotsMode(), 'local_gocryptfs')
        config = Path(self.home.name, '.config/backintime/config').read_text()
        self.assertNotIn('integration-secret', config)
        self.assertIsNone(self.backend._mount_manager)
        mountpoints = Path(
            self.home.name, '.local/share/backintime/mnt').glob('*/mountpoint')
        self.assertFalse([path for path in mountpoints
                          if tools.is_mounted(path)])

    def test_wrong_password_does_not_replace_the_encrypted_store(self):
        self.backend.setup_local_gocryptfs(
            str(self.destination), 'correct-secret',
            save_in_keyring=False, use_ram_cache=False)
        conf = self.destination / 'gocryptfs.conf'
        original = conf.read_bytes()
        with self.assertRaises(RuntimeError):
            self.backend.setup_local_gocryptfs(
                str(self.destination), 'wrong-secret',
                save_in_keyring=False, use_ram_cache=False)
        self.assertEqual(conf.read_bytes(), original)
        self.assertEqual(self.backend.profile_mode(), 'local_gocryptfs')

    def test_first_snapshot_survives_a_second_mount(self):
        source = Path(self.home.name, 'source')
        source.mkdir()
        marker = source / 'marker.txt'
        marker.write_text('encrypted marker')
        self.backend.cfg.setInclude([(str(source), 0)])
        self.backend.setup_local_gocryptfs(
            str(self.destination), 'snapshot-secret',
            save_in_keyring=False, use_ram_cache=False)
        ok, error = self.backend.ensure_mounted()
        self.assertTrue(ok, error)
        self.assertIsNone(self.backend.take_snapshot())
        immediate = self.backend.list_snapshots()
        self.assertEqual(len(immediate), 1)
        self.backend.release_mount()

        restarted = Backend()
        self.addCleanup(restarted.release_mount)
        restarted.cfg.setPassword(
            'snapshot-secret', mode='local_gocryptfs', pw_id=1)
        ok, error = restarted.ensure_mounted()
        self.assertTrue(ok, error)
        snapshots = restarted.list_snapshots()
        self.assertEqual(len(snapshots), 1)
        found = list(Path(snapshots[0].pathBackup()).rglob('marker.txt'))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].read_text(), 'encrypted marker')


if __name__ == '__main__':
    result = unittest.main(exit=False)
    if result.result.wasSuccessful():
        print('ENCRYPTION_INTEGRATION_PASSED')
    else:
        raise SystemExit(1)
