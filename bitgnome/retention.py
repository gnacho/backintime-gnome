"""Retention audit: what the engine would remove, and what that would cost.

The cutoff and the deletion rules are taken from the core, not re-invented:
``Config.removeOldSnapshotsDate()`` decides the date, the oldest snapshot is
never removed, and named snapshots are kept when the profile says so.

Two different things are reported separately: a unit the core does not
support (months and hours, which fall through to ``date(1, 1, 1)``) and an
engine that never applies retention at all (2.0.0-rc2 before the backport).
Blaming the version for an unsupported unit was wrong, and so was promising
deletions of named snapshots whose names could not be read.
"""
import datetime
import os

from bitgnome.i18n import _, ngettext

MAX_PATHS = 200000
NEVER = datetime.date(1, 1, 1)


def _unit_label(unit_name, value):
    """'2 años' for the unit the profile uses, singular or plural."""
    if unit_name == 'WEEK':
        return ngettext('{n} semana', '{n} semanas', value).format(n=value)
    if unit_name == 'YEAR':
        return ngettext('{n} año', '{n} años', value).format(n=value)
    if unit_name == 'MONTH':
        return ngettext('{n} mes', '{n} meses', value).format(n=value)
    if unit_name == 'HOUR':
        return ngettext('{n} hora', '{n} horas', value).format(n=value)
    return ngettext('{n} día', '{n} días', value).format(n=value)


def _sid_date(sid):
    value = getattr(sid, 'date', None)
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    for fmt in ('%Y%m%d-%H%M%S', '%Y-%m-%d %H:%M:%S'):
        if isinstance(value, str):
            try:
                return datetime.datetime.strptime(value[:19], fmt).date()
            except ValueError:
                continue
        for attr in ('displayID', 'displayName'):
            text = getattr(sid, attr, None)
            if not isinstance(text, str):
                continue
            try:
                return datetime.datetime.strptime(text[:19], fmt).date()
            except ValueError:
                continue
    return None


def _sid_name(sid):
    try:
        return getattr(sid, 'name', None) or None
    except Exception:  # noqa: BLE001
        return None


def state(cfg):
    """Retention as the core will apply it, or None when disabled."""
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
    try:
        cutoff = cfg.removeOldSnapshotsDate()
    except Exception:  # noqa: BLE001
        cutoff = NEVER
    if not isinstance(cutoff, datetime.date):
        cutoff = NEVER
    supported = unit_name in ('DAY', 'WEEK', 'YEAR')
    return {'value': value, 'unit': unit_name,
            'label': _unit_label(unit_name, value),
            'cutoff': cutoff, 'engine_inert': cutoff <= NEVER,
            'unsupported_unit': not supported,
            'supported_units': sorted((_('días'), _('semanas'), _('años')))}


def plan(sids, cutoff, keep_named):
    """Snapshots the engine would remove, mirroring ``removeOldSnapshots``."""
    remaining = list(reversed(sids))
    doomed, kept = [], []
    while len(remaining) > 1:
        sid = remaining[0]
        date = _sid_date(sid)
        if date is None or date >= cutoff:
            break
        if keep_named and _sid_name(sid):
            kept.append(remaining.pop(0))
            continue
        doomed.append(remaining.pop(0))
    return doomed, remaining + kept


def _collect(sids, budget):
    """Union of file paths under the given snapshots, with a hard budget."""
    paths = set()
    unreadable = False
    limited = False

    def onerror(_exc):
        nonlocal unreadable
        unreadable = True

    for sid in sids:
        root = _backup_root(sid)
        if not root or not os.path.isdir(root):
            unreadable = True
            continue
        for dirpath, dirnames, filenames in os.walk(root, onerror=onerror,
                                                    followlinks=False):
            for name in filenames:
                paths.add(os.path.relpath(os.path.join(dirpath, name), root))
            for name in dirnames:
                full = os.path.join(dirpath, name)
                if os.path.islink(full):
                    paths.add(os.path.relpath(full, root))
            if len(paths) >= budget:
                limited = True
                break
        if limited:
            break
    reason = 'unreadable' if unreadable else ('limit' if limited else None)
    return paths, reason


def _backup_root(sid):
    try:
        return sid.pathBackup() if hasattr(sid, 'pathBackup') else None
    except Exception:  # noqa: BLE001
        return None


def audit(cfg, sids, budget=MAX_PATHS):
    """What the retention policy would remove and what would be lost.

    ``sids`` newest first. Returns None when retention is off.
    """
    info = state(cfg)
    if info is None or not sids:
        return None
    if info['engine_inert']:
        return {'engine_inert': True, 'label': info['label'],
                'unsupported_unit': info.get('unsupported_unit', False),
                'cutoff': None, 'doomed': [], 'lost': [], 'superseded': 0,
                'scanned': 0, 'complete': True, 'reason': None}

    try:
        keep_named = bool(cfg.dontRemoveNamedSnapshots())
    except Exception:  # noqa: BLE001
        keep_named = True
    # A snapshot whose directory cannot be read may well carry a name, and
    # named snapshots are never removed. Saying "these will be deleted" from
    # that state was a promise the engine would not keep.
    readable = all(_backup_root(sid) and os.path.isdir(_backup_root(sid))
                   for sid in sids)
    doomed_sids, survivors = plan(sids, info['cutoff'],
                                  keep_named or not readable)
    if not readable:
        return {'engine_inert': False,
                'unsupported_unit': info.get('unsupported_unit', False),
                'label': info['label'], 'cutoff': info['cutoff'],
                'doomed': [], 'lost': [], 'superseded': 0, 'scanned': 0,
                'complete': False, 'reason': 'unreadable'}
    if not doomed_sids:
        return None

    survivor_paths, survivor_reason = _collect(survivors, budget)
    doomed_paths, doomed_reason = _collect(doomed_sids, budget)
    reason = survivor_reason or doomed_reason
    complete = reason is None

    # With an incomplete picture, a path missing from the survivor set is not
    # proof of loss, so nothing is claimed as lost.
    lost = sorted(doomed_paths - survivor_paths) if complete else []
    superseded = len(doomed_paths & survivor_paths)

    return {
        'engine_inert': False,
        'label': info['label'],
        'cutoff': info['cutoff'],
        'doomed': [(str(getattr(sid, 'displayName', sid)), _sid_date(sid))
                   for sid in doomed_sids],
        'reference': str(getattr(survivors[-1], 'displayName', ''))
        if survivors else None,
        'lost': lost,
        'superseded': superseded,
        'scanned': len(doomed_paths),
        'complete': complete,
        'reason': reason,
    }
