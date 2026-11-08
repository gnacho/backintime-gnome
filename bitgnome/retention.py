"""Retention audit: what would be lost when old snapshots are removed.

Back In Time removes snapshots older than the configured age on the next
backup. This module answers, before that happens, which files exist only in
those doomed snapshots (they would be lost) and which ones already have a
newer version in the latest snapshot.
"""
import datetime
import os

MAX_FILES = 20000


def _sid_date(sid):
    value = getattr(sid, 'date', None)
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        try:
            return datetime.datetime.strptime(value[:15],
                                              '%Y%m%d-%H%M%S').date()
        except ValueError:
            pass
    try:
        return datetime.datetime.strptime(
            str(getattr(sid, 'displayName', sid))[:15], '%Y%m%d-%H%M%S').date()
    except ValueError:
        return None


def cutoff_date(value, unit_name, today=None):
    """Same arithmetic as ``Config.removeOldSnapshotsDate``."""
    today = today or datetime.date.today()
    if unit_name == 'DAY':
        return today - datetime.timedelta(days=value)
    if unit_name == 'WEEK':
        return today - datetime.timedelta(days=today.weekday() + 7 * value)
    if unit_name == 'YEAR':
        return today.replace(day=1, year=today.year - value)
    return None


def state(cfg, today=None):
    """Retention setting in a shape the UI can show, or None if disabled."""
    try:
        enabled, value, unit = cfg.removeOldSnapshots()
    except Exception:  # noqa: BLE001
        return None
    if not enabled:
        return None
    unit_name = getattr(unit, 'name', None)
    if unit_name is None:
        unit_name = {10: 'HOUR', 20: 'DAY', 30: 'WEEK', 40: 'MONTH',
                     80: 'YEAR'}.get(unit, '')
    cutoff = cutoff_date(int(value), unit_name, today)
    if cutoff is None:
        return None
    labels = {'DAY': ('día', 'días'), 'WEEK': ('semana', 'semanas'),
              'YEAR': ('año', 'años')}
    singular, plural = labels.get(unit_name, ('día', 'días'))
    return {'value': value, 'unit': unit_name, 'cutoff': cutoff,
            'label': f'{value} {singular if value == 1 else plural}'}


def walk_files(root, limit=MAX_FILES):
    """Relative paths of the files under a snapshot backup root."""
    files = set()
    truncated = False
    if not root or not os.path.isdir(root):
        return files, truncated
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            files.add(rel)
            if len(files) >= limit:
                return files, True
    return files, truncated


def audit(cfg, sids, today=None, limit=MAX_FILES):
    """Which files would disappear if the retention policy runs now.

    ``sids`` newest first. Returns None when retention is off or uses a unit
    the core does not support (months, hours).
    """
    info = state(cfg, today)
    if info is None or not sids:
        return None
    cutoff = info['cutoff']
    doomed = []
    for sid in sids:
        date = _sid_date(sid)
        if date is not None and date < cutoff:
            doomed.append((sid, date))
    if not doomed:
        return None

    reference = sids[0] if sids[0] not in [d[0] for d in doomed] else None
    reference_files, ref_truncated = walk_files(
        _backup_root(reference), limit) if reference is not None else (set(), False)

    lost, superseded = [], 0
    scanned = 0
    truncated = ref_truncated
    for sid, _date in doomed:
        files, cut = walk_files(_backup_root(sid), limit)
        truncated = truncated or cut
        for rel in files:
            scanned += 1
            if rel in reference_files:
                superseded += 1
            elif rel not in lost:
                lost.append(rel)
        if len(lost) >= limit:
            truncated = True
            break

    return {
        'label': info['label'],
        'cutoff': cutoff,
        'doomed': [(str(sid.displayName), date) for sid, date in doomed],
        'reference': str(getattr(reference, 'displayName', reference))
        if reference is not None else None,
        'lost': sorted(lost),
        'superseded': superseded,
        'scanned': scanned,
        'truncated': truncated,
    }


def _backup_root(sid):
    if sid is None:
        return None
    try:
        return sid.pathBackup() if hasattr(sid, 'pathBackup') else None
    except Exception:  # noqa: BLE001
        return None
