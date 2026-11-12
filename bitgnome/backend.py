"""Backend adapter: thin async-friendly layer over Back In Time's ``common``.

The whole point of this frontend is that the backup engine is *not*
reimplemented: we import ``common/`` from an installed Back In Time (or a
checkout pointed by ``BACKINTIME_COMMON``) and drive ``config.Config``,
``snapshots.Snapshots`` and friends exactly like the Qt GUI does.

Works with Back In Time 1.6.x (EncFS modes) and 2.0.x (Gocryptfs modes,
mount manager). The differences are detected at runtime.
"""
import configparser
import contextlib
import inspect
import os
import shutil
import subprocess
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
        self.busy_backup = False      # shared with the wizard
        self.busy_owner = None
        self._automation_lock = threading.Lock()
        self.last_automation_error = None
        self._snapshots = None        # cached: Snapshots() costs ~260 ms
        self._mount_manager = None    # persistent mount for the app lifetime
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

    def _state_path(self):
        base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(
            os.path.expanduser('~'), '.config')
        return os.path.join(base, 'backintime-gnome', 'state.ini')

    def _saved_profile(self):
        try:
            parser = configparser.ConfigParser()
            parser.read(self._state_path())
            return parser.get('ui', 'profile', fallback=None)
        except Exception:                 # noqa: BLE001
            return None

    def _remember_profile(self, pid):
        try:
            path = self._state_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            parser = configparser.ConfigParser()
            if os.path.exists(path):
                parser.read(path)
            if not parser.has_section('ui'):
                parser.add_section('ui')
            parser.set('ui', 'profile', str(pid))
            with open(path, 'w', encoding='utf-8') as fh:
                parser.write(fh)
        except Exception:                 # noqa: BLE001
            pass

    def _restore_profile(self):
        saved = self._saved_profile()
        if not saved:
            return
        try:
            if saved in [str(pid) for pid in self.cfg.profiles()]:
                self.cfg.setCurrentProfile(saved)
        except Exception:                 # noqa: BLE001
            pass

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
            self._restore_profile()
            return
        if not self.cfg.profiles():
            pid = self.cfg.addProfile('Main profile')
            self.cfg.setCurrentProfile(pid)
            self.cfg.save()
        else:
            self._restore_profile()

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
        self._snapshots = None
        self._remember_profile(pid)

    def _snapshots_obj(self):
        if self._snapshots is None:
            self._snapshots = snapshots.Snapshots(self.cfg)
        return self._snapshots

    def profile_mode(self):
        try:
            return str(self.cfg.snapshotsMode())
        except Exception:                 # noqa: BLE001
            return 'local'

    def set_destination_path(self, path):
        """Write the destination of the *current* mode.

        ``set_snapshots_path`` only covers local profiles; SSH and encrypted
        profiles keep their path in different settings.
        """
        mode = self.profile_mode()
        if 'ssh' in mode:
            self.cfg.setSshSnapshotsPath(path)
        elif 'gocryptfs' in mode:
            setter = getattr(self.cfg, 'setLocalGocryptfsPath', None)
            if setter is not None:
                setter(path, self.current_profile())
            else:
                self.cfg.set_snapshots_path(path)
        elif 'encfs' in mode:
            setter = getattr(self.cfg, 'setLocalEncfsPath', None)
            if setter is not None:
                setter(path)
            else:
                self.cfg.set_snapshots_path(path)
        else:
            self.cfg.set_snapshots_path(path)

    def setup_local_gocryptfs(self, destination, password,
                              save_in_keyring=False, use_ram_cache=True):
        """Initialize and validate a local Gocryptfs profile safely.

        The password is kept in the core's in-memory store while mounting.
        Keyring or RAM-cache persistence is only enabled after validation.
        """
        from pathlib import Path
        import core_events
        from .encryption import destination_is_empty_or_initialized

        if self.cfg is None:
            raise RuntimeError(self.error or 'Núcleo no disponible')
        previous_mode = self.profile_mode()
        if previous_mode not in ('local', 'local_gocryptfs'):
            raise RuntimeError(
                'El alta guiada solo está disponible para perfiles locales')
        previous_path = self.destination_path()
        same_path = (os.path.realpath(destination) ==
                     os.path.realpath(previous_path or ''))
        if previous_mode == 'local_gocryptfs' and not same_path:
            raise RuntimeError(
                'No cambies el destino de un perfil cifrado desde aquí. '
                'Crea un perfil nuevo para conservar las copias anteriores.')
        if previous_mode != 'local_gocryptfs' and self.list_snapshots():
            raise RuntimeError(
                'El perfil actual ya tiene copias. Crea un perfil nuevo '
                'para no ocultar las instantáneas sin cifrar.')
        if not destination_is_empty_or_initialized(destination):
            raise RuntimeError(
                'Elige una carpeta nueva o vacía, o un destino Gocryptfs '
                'ya inicializado')
        if tools.which('gocryptfs') is None:
            raise RuntimeError('Gocryptfs no está instalado')

        previous_save = self.cfg.passwordSave()
        previous_cache = self.cfg.passwordUseCache()
        previous_password = None
        if previous_mode == 'local_gocryptfs' and not password:
            previous_password = self.cfg.get_encryption_password()
            password = previous_password
        if not password:
            raise RuntimeError('Escribe una contraseña para el cifrado')

        mode = 'local_gocryptfs'
        manager = None
        try:
            self.release_mount()
            self._snapshots = None
            Path(destination).mkdir(parents=True, exist_ok=True)
            self.cfg.setSnapshotsMode(mode)
            self.cfg.setLocalGocryptfsPath(
                str(destination), self.current_profile())
            self.cfg.setPasswordSave(False, mode=mode)
            self.cfg.setPasswordUseCache(False, mode=mode)
            self.cfg.setPassword(password, mode=mode, pw_id=1)

            manager = mount.MountManager.create(self.cfg)
            manager.mount()
            errors = []

            def collect_error(message):
                errors.append(str(message))

            core_events.event_error.register(collect_error)
            try:
                valid = tools.validate_and_prepare_snapshots_path(
                    path=manager.path,
                    host_user_profile=self.cfg.hostUserProfile(),
                    mode=mode,
                    copy_links=self.cfg.copyLinks(),
                    error_event=core_events.event_error,
                )
            finally:
                core_events.event_error.deregister(collect_error)
            if not valid:
                detail = errors[-1] if errors else 'Destino no válido'
                raise RuntimeError(detail)

            self.cfg.setPasswordSave(save_in_keyring, mode=mode)
            self.cfg.setPasswordUseCache(use_ram_cache, mode=mode)
            self.cfg.setPassword(password, mode=mode, pw_id=1)
            self.cfg.save()
        except Exception as exc:
            try:
                self.cfg.setSnapshotsMode(previous_mode)
                self.set_destination_path(previous_path or '')
                self.cfg.setPasswordSave(previous_save, mode=previous_mode)
                self.cfg.setPasswordUseCache(
                    previous_cache, mode=previous_mode)
                if previous_password:
                    self.cfg.setPassword(
                        previous_password, mode=previous_mode, pw_id=1)
            except Exception as restore_error:  # noqa: BLE001
                logger.debug(f'Could not restore profile: {restore_error}')
            self._snapshots = None
            self.release_mount()
            message = getattr(exc, 'gui_msg', None) or str(exc)
            raise RuntimeError(message) from exc
        finally:
            if manager is not None:
                try:
                    self.unmount_manager(manager)
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f'Could not unmount setup destination: {exc}')

    def automation_supported(self):
        """Whether the loaded core can install the schedule for this mode."""
        try:
            mode = getattr(self.cfg.scheduleMode(), 'name', '')
        except Exception:                 # noqa: BLE001
            return True
        if mode in ('DISABLED', 'UDEV', 'AT_EVERY_BOOT'):
            return True
        return bool(shutil.which('crontab') or shutil.which('fcrontab'))

    def apply_automation(self):
        """Install or refresh the cron/udev entry for the schedule.

        The Qt client does this on startup; without it a schedule chosen in
        the GUI is never executed. ``setup_automation`` swallows write
        failures, so the cron binary is checked first.
        """
        if self.cfg is None:
            return False
        with self._automation_lock:
            if not self.automation_supported():
                self.last_automation_error = 'no crontab installed'
                return False
            try:
                self.cfg.setup_automation()
            except Exception as exc:      # noqa: BLE001
                self.last_automation_error = str(exc)
                return False
            if not self._cron_installed():
                self.last_automation_error = 'crontab not updated'
                return False
            self.last_automation_error = None
            return True

    def _cron_installed(self):
        """The core swallows crontab write errors; check the result."""
        try:
            mode = getattr(self.cfg.scheduleMode(), 'name', '')
        except Exception:                 # noqa: BLE001
            return True
        if mode in ('DISABLED', 'UDEV', 'AT_EVERY_BOOT'):
            return True
        binary = shutil.which('crontab') or shutil.which('fcrontab')
        if binary is None:
            return False
        try:
            out = subprocess.run([binary, '-l'], capture_output=True,
                                 text=True, timeout=10)
        except Exception:                 # noqa: BLE001
            return False
        if out.returncode not in (0, 1):
            return False
        return 'backintime' in out.stdout.lower()

    @staticmethod
    def _clean_path(path):
        """Empty or relative '.' destinations mean 'not configured yet'."""
        if path is None:
            return None
        path = str(path).strip()
        return None if path in ('', '.') else path

    def destination_path(self, pid=None):
        """Backup destination of a profile, whatever the core version.

        For SSH profiles the answer is the remote path; ``snapshotsFullPath``
        would return the internal mountpoint instead.
        """
        mode = ''
        try:
            mode = str(self.cfg.snapshotsMode(pid))
        except Exception:                 # noqa: BLE001
            pass
        if 'ssh' in mode:
            try:
                return self._clean_path(self.cfg.sshSnapshotsPath(pid))
            except Exception:             # noqa: BLE001
                return None
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

    def ensure_mounted(self):
        """Mount the destination once and keep it for the app lifetime.

        Returns ``(ok, error)``. A plain local profile needs no mount; 1.6.x
        has no mount manager at all.
        """
        if self.cfg is None:
            return True, None
        if str(self.profile_mode()) == 'local':
            return True, None
        manager_cls = getattr(mount, 'MountManager', None) if mount else None
        if manager_cls is None:
            return True, None
        if self._mount_manager is not None:
            return True, None
        try:
            manager = manager_cls.create(self.cfg)
            manager.mount()
        except Exception as exc:          # noqa: BLE001
            return False, str(exc)
        self._mount_manager = manager
        return True, None

    @staticmethod
    def unmount_manager(manager):
        """Unmount a core manager, including the FUSE3-only Arch layout."""
        try:
            manager.umount()
            return
        except Exception as original:      # noqa: BLE001
            path = getattr(manager, 'path', None)
            if path is None or not tools.is_mounted(path):
                raise
            binary = shutil.which('fusermount3') or shutil.which('fusermount')
            if binary is None:
                raise
            result = subprocess.run(
                [binary, '-u', str(path)], capture_output=True, text=True,
                check=False)
            if result.returncode != 0:
                raise original
            logger.debug(f'Unmounted with fallback binary: {binary}')

    def release_mount(self):
        if self._mount_manager is None:
            return
        try:
            self.unmount_manager(self._mount_manager)
        except Exception:                 # noqa: BLE001
            pass
        finally:
            self._mount_manager = None

    @contextlib.contextmanager
    def mounted(self):
        """Yield the path where the destination is mounted, or None.

        When the app holds a persistent mount, reuse it instead of
        mounting and unmounting around every call.
        """
        if self._mount_manager is not None:
            yield self._mount_manager.path
            return
        manager_cls = getattr(mount, 'MountManager', None) if mount else None
        if manager_cls is None:
            yield None
            return
        manager = manager_cls.create(self.cfg)
        manager.mount()
        try:
            yield manager.path
        finally:
            self.unmount_manager(manager)

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
        """Run a backup. Returns error string or None.

        ``Snapshots.backup()`` owns its mount lifecycle. Keeping an outer
        persistent mount double-mounts encrypted destinations. The core also
        calls the legacy ``fusermount`` binary, absent on FUSE3-only systems.
        """
        before = [sid.path() for sid in self.list_snapshots()]
        if self.profile_mode() != 'local':
            self.release_mount()
        sn = snapshots.Snapshots(self.cfg)
        try:
            ret_error = sn.backup(force=True)
            msg = sn.takeSnapshotMessage()
        except FileNotFoundError as exc:
            if 'fusermount' not in str(exc):
                self._snapshots = None
                return str(exc)
            manager = getattr(sn, 'mount_manager', None)
            if manager is not None:
                try:
                    self.unmount_manager(manager)
                except Exception as unmount_error:  # noqa: BLE001
                    logger.debug(f'Backup unmount fallback: {unmount_error}')
            self._snapshots = None
            after = [sid.path() for sid in self.list_snapshots()]
            if after and (not before or after[0] != before[0]):
                return None
            return str(exc)
        except Exception as exc:          # noqa: BLE001
            self._snapshots = None
            return str(exc)
        return None if not ret_error else (msg[1] if msg else 'Error desconocido')

    @staticmethod
    def descendant_pids(root=None):
        """PIDs below ``root`` (default: this process), deepest last."""
        root = root or os.getpid()
        children = {}
        for entry in os.listdir('/proc'):
            if not entry.isdigit():
                continue
            try:
                with open(f'/proc/{entry}/stat', encoding='utf-8') as fh:
                    fields = fh.read().rsplit(')', 1)[1].split()
                ppid = int(fields[1])
            except (OSError, IndexError, ValueError):
                continue
            children.setdefault(ppid, []).append(int(entry))
        out, stack = [], [root]
        while stack:
            parent = stack.pop()
            for child in children.get(parent, []):
                out.append(child)
                stack.append(child)
        return out

    @staticmethod
    def _argv0(pid):
        try:
            with open(f'/proc/{pid}/cmdline', 'rb') as fh:
                argv0 = fh.read().split(b'\0', 1)[0].decode(errors='replace')
        except OSError:
            return ''
        return os.path.basename(argv0)

    def stop_snapshot(self, sig=None):
        """Kill the rsync (and its ssh helper) spawned by a backup.

        The core reacts to a non-zero rsync exit by removing the incomplete
        snapshot, so this is a clean cancel. Only rsync/ssh are signalled:
        killing every descendant would also kill the gocryptfs mount and the
        password cache, leaving the profile unusable. Returns the PIDs.
        """
        import signal
        sig = sig or signal.SIGTERM
        signalled = []
        for pid in self.descendant_pids():
            if self._argv0(pid) not in ('rsync', 'ssh'):
                continue
            try:
                os.kill(pid, sig)
                signalled.append(pid)
            except (ProcessLookupError, PermissionError):
                continue
        return signalled

    def take_snapshot_message(self):
        try:
            return self._snapshots_obj().takeSnapshotMessage()
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
