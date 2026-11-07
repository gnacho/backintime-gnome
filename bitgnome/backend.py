"""Backend adapter: thin async-friendly layer over Back In Time's ``common``.

The whole point of this frontend is that the backup engine is *not*
reimplemented: we import ``common/`` from an installed Back In Time (or a
checkout pointed by ``BACKINTIME_COMMON``) and drive ``config.Config``,
``snapshots.Snapshots`` and friends exactly like the Qt GUI does.

Works with Back In Time 1.6.x (EncFS modes) and 2.0.x (Gocryptfs modes,
mount manager). The differences are detected at runtime.
"""
import contextlib
import inspect
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
bitbase_module = None   # raw module in both 1.x and 2.x (TimeUnit lives here)


def _locate_common():
    """Find and import Back In Time's ``common`` package."""
    global _LOCATED, _IMPORT_ERROR
    global config, snapshots, snapshotlog, mount, tools, logger, bitbase
    global bitbase_module

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
            import bitbase as _bitbase
        except ImportError:
            bitbase = None
        else:
            bitbase_module = _bitbase
            # 1.x has bitbase too, but without the CLI globals dict
            if getattr(_bitbase, 'context', None) is None or \
                    getattr(_bitbase, 'DEFAULT_CONFIG_FILE_PATH', None) is None:
                bitbase = None
            else:
                bitbase = _bitbase
                # 2.0 keeps CLI globals in bitbase.context, normally filled
                # by Bit's own argument parser. Fill what Config.save() needs.
                if bitbase.context.get('--config') is None:
                    bitbase.context['--config'] = \
                        bitbase.DEFAULT_CONFIG_FILE_PATH
                if bitbase.context.get('--quiet') is None:
                    bitbase.context['--quiet'] = True
                bitbase.context['--config'].parent.mkdir(parents=True,
                                                         exist_ok=True)
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


def bit_version():
    """Version string of the loaded core (``2.0.0-rc2``), or None."""
    path = common_path()
    if not path:
        return None
    for base in (path, os.path.dirname(path)):
        try:
            with open(os.path.join(base, 'VERSION'), encoding='utf-8') as fh:
                version = fh.read().strip()
            if version:
                return version
        except OSError:
            continue
    return None


def supports_encfs():
    """True on 1.x cores, where the EncFS modes still exist."""
    return config is not None and hasattr(config.Config, 'localEncfsPath')


def snapshot_modes():
    """(mode ids, labels) offered by the loaded core."""
    if supports_encfs():
        return (['local', 'ssh', 'local_encfs', 'ssh_encfs'],
                ['Local', 'SSH', 'Local cifrado (encfs)',
                 'SSH cifrado (encfs)'])
    return (['local', 'ssh', 'local_gocryptfs', 'ssh_gocryptfs'],
            ['Local', 'SSH', 'Local cifrado (gocryptfs)',
             'SSH cifrado (gocryptfs)'])


def _list_snapshots_needs_mount_path():
    try:
        params = inspect.signature(snapshots.listSnapshots).parameters
    except (TypeError, ValueError):
        return False
    return 'mounted_path' in params


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
            self._load_config()
        except SystemExit as exc:         # cli.get_config_and_select_profile
            self.error = f'Config no válida (exit {exc.code})'
        except Exception as exc:          # noqa: BLE001
            self.error = str(exc)

    def _load_config(self):
        """Load the config the same way Bit's own CLI does.

        2.0 needs an explicit Konfig load (it also migrates old EncFS
        profiles and fixes enum values). 1.6.x loads itself in Config().
        """
        loader = None
        if bitbase is not None:
            try:
                import cli as _cli
                loader = getattr(_cli, 'get_config_and_select_profile', None)
            except Exception:             # noqa: BLE001
                loader = None
        if loader is not None:
            loader(config_path=bitbase.context['--config'], pid_or_name=None)
            self.cfg = config.Config()
            return
        if not self.cfg.profiles():
            pid = self.cfg.addProfile('Main profile')
            self.cfg.setCurrentProfile(pid)
            self.cfg.save()

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

    @staticmethod
    def _clean_path(path):
        """Empty or relative '.' destinations mean 'not configured yet'."""
        if path is None:
            return None
        path = str(path).strip()
        return None if path in ('', '.') else path

    def destination_path(self, pid=None):
        """Backup destination of a profile, whatever the core version."""
        try:
            getter = getattr(self.cfg, 'get_backup_destination_path', None)
            if getter is not None:
                return self._clean_path(getter(pid))
        except Exception:                 # noqa: BLE001
            pass
        try:
            return self._clean_path(self.cfg.snapshotsFullPath(pid))
        except Exception:                 # noqa: BLE001
            return None

    def profile_summary(self, pid=None):
        try:
            mode = self.cfg.snapshotsMode(pid)
            if str(mode).startswith('local'):
                path = self.destination_path(pid)
            else:
                path = self._clean_path(self.cfg.sshSnapshotsPath(pid))
            return mode, path or 'sin configurar'
        except Exception:                 # noqa: BLE001
            return '?', '?'

    @contextlib.contextmanager
    def mounted(self):
        """Mount the destination when the core requires it (2.0+).

        1.6.x has no mount manager, so this is a no-op there. Yields the
        path where the destination is mounted, or None.
        """
        manager_cls = getattr(mount, 'MountManager', None) if mount else None
        if manager_cls is None:
            yield None
            return
        manager = manager_cls.create(self.cfg)
        with manager.mounted():
            yield manager.path

    # -- snapshots ------------------------------------------------------
    def list_snapshots(self):
        """List of SID objects, newest first. Empty list on error."""
        if not self.cfg:
            return []
        try:
            kwargs = {'includeNewSnapshot': False, 'reverse': True}
            with self.mounted() as mounted_path:
                if mounted_path is not None:
                    kwargs['mounted_path'] = mounted_path
                return list(snapshots.listSnapshots(self.cfg, **kwargs))
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
        with self.mounted():
            sn.remove(sid)

    # -- long operations (call from worker thread) ----------------------
    def take_snapshot(self):
        """Run a backup. Returns error string or None."""
        sn = snapshots.Snapshots(self.cfg)
        with self.mounted():
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
        with self.mounted():
            sn.restore(sid, paths, restore_to=restore_to, delete=delete,
                       backup=backup, only_new=only_new)

    def snapshot_log(self):
        """Yield log lines for the current profile."""
        log = snapshotlog.SnapshotLog(self.cfg)
        try:
            with self.mounted():
                yield from log.get()
        except Exception:                 # noqa: BLE001
            return

    def free_space(self):
        try:
            from diskusage import get_free_space
            p = self.destination_path()
            if p and os.path.isdir(p):
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
        return '-'
    for unit in ('B', 'KiB', 'MiB', 'GiB'):
        if n < 1024:
            return f'{int(n)} {unit}' if n == int(n) else f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} TiB'
