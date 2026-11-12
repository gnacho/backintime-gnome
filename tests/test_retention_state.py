"""Retention has to distinguish three different "nothing gets deleted".

A unit the core does not support (months, hours), an engine that never applies
retention, and a destination that cannot be read right now. The dialog used to
blame the engine version for all three and to promise deletions of named
snapshots whose names it could not read.
"""
import datetime
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bitgnome import backend as _backend  # noqa: E402
from bitgnome import retention  # noqa: E402

from helpers import TestApp, isolated_home  # noqa: E402


class FakeSid:
    def __init__(self, date, root, name=None):
        self.date = datetime.datetime(date.year, date.month, date.day, 12, 0)
        self._root = Path(root)
        self._name = name
        self.displayName = date.strftime('%Y%m%d-%H%M%S')

    @property
    def name(self):
        return self._name or ''

    def pathBackup(self, *_args):
        return str(self._root / 'backup')


class RetentionStateTests(unittest.TestCase):
    def setUp(self):
        self.home = isolated_home(self)
        self.backend = TestApp().backend
        self.cfg = self.backend.cfg

    def set_retention(self, value, unit):
        self.cfg.setRemoveOldSnapshots(
            True, value, _backend.bitbase.TimeUnit[unit])

    def test_unsupported_unit_is_not_blamed_on_the_engine(self):
        self.set_retention(6, 'MONTH')
        info = retention.state(self.cfg)
        self.assertTrue(info['engine_inert'])
        self.assertTrue(info['unsupported_unit'])

    def test_supported_unit_is_not_flagged(self):
        self.set_retention(2, 'YEAR')
        info = retention.state(self.cfg)
        self.assertFalse(info['engine_inert'])
        self.assertFalse(info['unsupported_unit'])
        self.assertNotEqual(info['cutoff'], retention.NEVER)

    def test_unreadable_destination_promises_nothing(self):
        self.set_retention(2, 'DAY')
        sids = [FakeSid(datetime.date.today(), self.home.name + '/no-existe')]
        result = retention.audit(self.cfg, sids)
        self.assertIsNotNone(result)
        self.assertEqual(result['reason'], 'unreadable')
        self.assertFalse(result['complete'])
        self.assertEqual(result['doomed'], [])

    def test_readable_destination_plans_the_old_snapshots(self):
        self.set_retention(2, 'DAY')
        sids = []
        for delta in (0, 10):
            root = Path(self.home.name) / f'snap{delta}'
            (root / 'backup').mkdir(parents=True)
            (root / 'backup' / 'file.txt').write_text('x', encoding='utf-8')
            sids.append(FakeSid(datetime.date.today()
                                - datetime.timedelta(days=delta), root))
        result = retention.audit(self.cfg, sids)
        self.assertIsNotNone(result)
        self.assertTrue(result['complete'])
        self.assertEqual(len(result['doomed']), 1)

    def test_named_snapshot_is_kept_when_readable(self):
        self.set_retention(2, 'DAY')
        roots = []
        for delta in (0, 10):
            root = Path(self.home.name) / f'named{delta}'
            (root / 'backup').mkdir(parents=True)
            roots.append(root)
        old = FakeSid(datetime.date.today() - datetime.timedelta(days=10),
                      roots[1], name='hito')
        new = FakeSid(datetime.date.today(), roots[0])
        result = retention.audit(self.cfg, [new, old])
        self.assertTrue(result is None or result['doomed'] == [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
