"""An SSH destination has to be checked before it is saved.

Back In Time copies over SSH with rsync on the remote side, so a reachable
host is not enough. These tests drive the probe through fake ssh output and
one real host is checked by hand (citadel-01, rsync 3.5.0, 823 GiB free).
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bitgnome import backend as _backend  # noqa: E402

from helpers import TestApp, isolated_home  # noqa: E402

OK_OUTPUT = ('rsync=rsync  version 3.5.0  protocol version 32\n'
             'state=dir-writable\n'
             'free=1024\n')


class SshCheckTests(unittest.TestCase):
    def setUp(self):
        self.home = isolated_home(self)
        self.backend = TestApp().backend

    def run_check(self, output='', returncode=0, stderr='', **kwargs):
        fake = mock.Mock(returncode=returncode, stdout=output, stderr=stderr)
        argv = kwargs.pop('argv', ('host', 22, 'root', '/backup'))
        with mock.patch.object(_backend.shutil, 'which', lambda name: '/usr/bin/sshfs'), \
                mock.patch.object(_backend.subprocess, 'run', return_value=fake):
            return self.backend.check_ssh_destination(*argv, **kwargs)

    def test_healthy_destination_is_accepted(self):
        ok, message = self.run_check(OK_OUTPUT)
        self.assertTrue(ok, message)
        self.assertIn('Conexión correcta', message)
        self.assertIn('32', message)

    def test_remote_without_rsync_is_refused(self):
        ok, message = self.run_check(
            'rsync=MISSING\nstate=dir-writable\nfree=1024\n')
        self.assertFalse(ok)
        self.assertIn('rsync', message)

    def test_read_only_remote_path_is_refused(self):
        ok, message = self.run_check(
            'rsync=rsync  version 3.5.0\nstate=dir-readonly\nfree=1\n')
        self.assertFalse(ok)
        self.assertIn('no se puede escribir', message)

    def test_missing_parent_is_refused(self):
        ok, message = self.run_check(
            'rsync=rsync  version 3.5.0\nstate=parent-missing\nfree=\n')
        self.assertFalse(ok)
        self.assertIn('carpeta padre', message)

    def test_rejected_key_is_explained(self):
        ok, message = self.run_check(
            returncode=255, stderr='root@host: Permission denied (publickey).')
        self.assertFalse(ok)
        self.assertIn('llave', message.lower())

    def test_unreachable_host_is_explained(self):
        ok, message = self.run_check(
            returncode=255, stderr='ssh: connect to host h port 22: '
                                   'No route to host')
        self.assertFalse(ok)
        self.assertIn('No se puede conectar', message)

    def test_missing_fields_are_named(self):
        ok, message = self.run_check(argv=('', 22, 'root', '/backup'))
        self.assertFalse(ok)
        self.assertIn('servidor', message.lower())
        ok, message = self.run_check(argv=('host', 22, 'root', ''))
        self.assertFalse(ok)
        self.assertIn('ruta remota', message.lower())

    def test_path_that_will_be_created_is_accepted(self):
        ok, message = self.run_check(
            'rsync=rsync  version 3.5.0\nstate=parent-writable\nfree=2048\n')
        self.assertTrue(ok, message)
        self.assertIn('se creará', message)


if __name__ == '__main__':
    unittest.main(verbosity=2)
