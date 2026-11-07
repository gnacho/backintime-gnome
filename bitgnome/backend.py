"""Backend adapter: thin async-friendly layer over Back In Time's ``common``.

The whole point of this frontend is that the backup engine is *not*
reimplemented: we import ``common/`` from an installed Back In Time (or a
checkout pointed by ``BACKINTIME_COMMON``) and drive ``config.Config``,
``snapshots.Snapshots`` and friends exactly like the Qt GUI does.

Tested against Back In Time 1.6.2.
"""
import os
import sys
import threading

_LOCATED = False
_IMPORT_ERROR = None

# BiT modules, populated by _locate_common()
config = None
snapshots = None
snapshotlog = None
mount = None
tools = None
logger = None
bitbase = None


def _locate_common():
    """Find and import Back In Time's ``common`` package."""
    global _LOCATED, _IMPORT_ERROR
    global config, snapshots, snapshotlog, mount, tools, logger, bitbase

    if _LOCATED:
        return
    _LOCATED = True

    candidates = [
        os.environ.get('BACKINTIME_COMMON', ''),
        os.path.expanduser('~/.local/share/backintime-gnome/common'),
        '/usr/share/backintime/common',
        '/usr/local/share/backintime/common',
        # development convenience: a checkout next to this project
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     os.pardir, os.pardir, 'backintime', 'common'),
    ]
    for path in candidates:
        path = os.path.normpath(path) if path else ''
        if path and os.path.isfile(os.path.join(path, 'config.py')):
            if path not in sys.path:
                sys.path.insert(0, path)
            break
    else:
        _IMPORT_ERROR = 'No se encontró el núcleo de Back In Time (common/).'
        return

    try:
        import config as _config          # noqa: F401
        import snapshots as _snapshots    # noqa: F401
        import snapshotlog as _snapshotlog  # noqa: F401
        import mount as _mount            # noqa: F401
        import tools as _tools            # noqa: F401
        import logger as _logger          # noqa: F401
        config, snapshots, snapshotlog = _config, _snapshots, _snapshotlog
        mount, tools, logger = _mount, _tools, _logger
        try:
            import bitbase as _bitbase    # 2.x only
            bitbase = _bitbase
        except ImportError:
            bitbase = None
    except Exception as exc:              # noqa: BLE001
        _IMPORT_ERROR = f'Error importando el núcleo de Back In Time: {exc}'


_locate_common()


def available():
    return _IMPORT_ERROR is None


def import_error():
    return _IMPORT_ERROR


def common_path():
    for p in sys.path:
        if os.path.isfile(os.path.join(p, 'config.py')) and \
                os.path.isfile(os.path.join(p, 'snapshots.py')):
            return p
    return None


class Backend:
    """Holds the Config instance and serialises long operations.

    All BiT core objects are kept here so the GUI never touches ``common``
    directly. Long operations (take snapshot, restore, delete) run on a
    worker thread and report through callbacks posted with
    ``GLib.idle_add`` by the caller.
    """

    def __init__(self):
        self.cfg = None
        self.error = None
        if not available():
            self.error = _IMPORT_ERROR
            return
        try:
            self.cfg = config.Config()
        except Exception as exc:          # noqa: BLE001
            self.error = str(exc)

    # -- profiles ------------------------------------------------------
    def profiles(self):
        """list of (profile_id, name)"""
        if not self.cfg:
            return []
        try:
            return [(pid, self.cfg.profileName(pid)) for pid in self.cfg.profiles()]
        except Exception:                 # noqa: BLE001
            return []

    def current_profile(self):
        try:
            return self.cfg.currentProfile()
        except Exception:                 # noqa: BLE001
            return None

    def set_current_profile(self, pid):
        self.cfg.setCurrentProfile(pid)

    def profile_summary(self, pid=None):
        try:
            mode = self.cfg.snapshotsMode(pid)
            path = (self.cfg.snapshotsFullPath(pid)
                    if mode == 'local' else self.cfg.sshSnapshotsPath(pid))
            return mode, path
        except Exception:                 # noqa: BLE001
            return '?', '?'

    # -- snapshots ------------------------------------------------------
    def list_snapshots(self):
        """List of SID objects, newest first. Empty list on error."""
        if not self.cfg:
            return []
        try:
            return list(snapshots.listSnapshots(self.cfg, includeNewSnapshot=False,
                                                reverse=True))
        except Exception as exc:          # noqa: BLE001
            logger.debug(f'listSnapshots failed: {exc}')
            return []

    def snapshot_label(self, sid):
        """Human-readable 'lun 2 nov, 09:00' style label."""
        try:
            import datetime
            d = sid.date
            if isinstance(d, str):
                d = datetime.datetime.strptime(d[:15], '%Y%m%d-%H%M%S')
            return d
        except Exception:                 # noqa: BLE001
            return None

    def delete_snapshot(self, sid):
        sn = snapshots.Snapshots(self.cfg)
        sn.remove(sid)

    # -- long operations (call from worker thread) ----------------------
    def take_snapshot(self):
        """Run a backup. Returns error string or None."""
        sn = snapshots.Snapshots(self.cfg)
        ret_error = sn.backup(force=True)
        msg = sn.takeSnapshotMessage()
        return None if not ret_error else (msg[1] if msg else 'Error desconocido')

    def take_snapshot_message(self):
        try:
            return snapshots.Snapshots(self.cfg).takeSnapshotMessage()
        except Exception:                 # noqa: BLE001
            return None

    def restore(self, sid, paths, restore_to='', delete=False,
                backup=True, only_new=False):
        sn = snapshots.Snapshots(self.cfg)
        sn.restore(sid, paths, restore_to=restore_to, delete=delete,
                   backup=backup, only_new=only_new)

    def snapshot_log(self):
        """Yield log lines for the current profile."""
        log = snapshotlog.SnapshotLog(self.cfg)
        try:
            yield from log.get()
        except Exception:                 # noqa: BLE001
            return

    def free_space(self):
        try:
            from diskusage import get_free_space
            p = self.cfg.snapshotsFullPath()
            if os.path.isdir(p):
                usage = os.statvfs(p)
                return usage.f_bavail * usage.f_frsize, usage.f_blocks * usage.f_frsize
        except Exception:                 # noqa: BLE001
            pass
        return None, None


class Worker:
    """Run ``fn`` on a thread; call ``done(result, error)`` on the GTK loop."""

    def __init__(self, fn, done):
        from gi.repository import GLib

        def run():
            try:
                result, error = fn(), None
            except Exception as exc:      # noqa: BLE001
                result, error = None, exc
            GLib.idle_add(done, result, error)

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()


def format_size(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return '—'
    for unit in ('B', 'KiB', 'MiB', 'GiB'):
        if n < 1024:
            return f'{int(n)} {unit}' if n == int(n) else f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} TiB'
