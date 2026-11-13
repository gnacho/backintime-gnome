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
import shlex
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


def _init_translations(tools_module):
    """Load the core's catalogue with the language of the session.

    Modules bind ``_`` when they are imported, so this has to run before
    ``config`` and ``snapshots``. Without it every message the core writes
    reaches the window in English, which is what the destination warning did.
    """
    if tools_module is None:
        return None
    code = ''
    try:
        from gi.repository import GLib
        for name in GLib.get_language_names() or []:
            base = name.split('.')[0].split('_')[0]
            if base and base not in ('C', 'POSIX'):
                code = base
                break
    except Exception:                     # noqa: BLE001
        code = ''
    if not code:
        import locale as _locale
        code = (_locale.getlocale()[0] or 'en').split('_')[0]
    try:
        return tools_module.initiate_translation(code)
    except Exception:                     # noqa: BLE001
        return None


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
        import tools as _tools_first      # noqa: F401
    except ImportError:
        _tools_first = None
    _init_translations(_tools_first)

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
        self.destination_problem = None   # user facing text when unreachable
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
        from .encryption import (destination_is_empty_or_initialized,
                                 device_problem_text, write_probe)

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
            writable, probe_error = write_probe(destination)
            if not writable:
                raise RuntimeError(probe_error)
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
            raise RuntimeError(device_problem_text(message)) from exc
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
        if mode in ('DISABLED', 'UDEV'):
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
            if self._cron_service_state() == 'inactive':
                # The entry is written, so enabling the daemon later is
                # enough. Reported as a warning, not as a write failure.
                self.last_automation_error = 'cron service not running'
                return True
            self.last_automation_error = None
            return True

    def _cron_service_state(self, binary=None):
        """State of the cron daemon: active, inactive or unknown.

        A package installed is not a daemon running. On Arch nothing enables
        ``cronie.service`` on install, so a crontab entry can be written and
        never executed. Units of the usual implementations are checked and
        an absent unit is reported as inactive only when no implementation
        is installed at all, which is the caller's business.
        """
        if self.cfg is None or not self._needs_cron():
            return 'unknown'
        systemctl = shutil.which('systemctl')
        if systemctl is None:
            return 'unknown'
        binary = binary or self._cron_binary()
        if binary and os.path.basename(binary) == 'fcrontab':
            units = ('fcron.service',)
        else:
            units = ('cronie.service', 'cron.service', 'crond.service',
                     'fcron.service')
        for unit in units:
            try:
                out = subprocess.run(
                    [systemctl, 'show', '--property=LoadState',
                     '--property=ActiveState', unit],
                    capture_output=True, text=True, timeout=5)
            except Exception:                 # noqa: BLE001
                continue
            values = {}
            for line in out.stdout.splitlines():
                key, _, value = line.partition('=')
                values[key.strip()] = value.strip()
            if values.get('LoadState') != 'loaded':
                continue
            return 'active' if values.get('ActiveState') == 'active' \
                else 'inactive'
        # No unit loaded, or systemctl hung: nothing can be claimed. Saying
        # "stopped" here would show a banner over a working cron.
        return 'unknown'

    def _udev_ready(self):
        """Whether the core can install udev rules right now.

        The rules are written by a D-Bus serviceHelper that ships with the Qt
        package. With only the CLI installed the mode is dead, and the UI used
        to present it as if it worked.
        """
        setup = getattr(self.cfg, 'setupUdev', None)
        return getattr(setup, 'isReady', None)

    def _needs_cron(self):
        """Whether the current schedule mode relies on cron."""
        try:
            mode = getattr(self.cfg.scheduleMode(), 'name', '')
        except Exception:                     # noqa: BLE001
            return False
        # AT_EVERY_BOOT is a crontab @reboot line (common/schedule.py), so
        # it needs cron as much as the time based modes do. Only DISABLED and
        # UDEV are outside cron.
        return mode not in ('DISABLED', 'UDEV')

    @staticmethod
    def _cron_binary():
        return shutil.which('crontab') or shutil.which('fcrontab')

    def cron_status(self):
        """Report whether the configured schedule can actually run.

        Returns a dict with the schedule mode, the crontab binary, the state
        of the cron service and a ``problem`` key: ``None`` when nothing is
        wrong, ``'binary'`` when no implementation is installed and
        ``'service'`` when one is installed but its daemon is not running.
        """
        status = {'mode': '', 'needs_cron': False, 'binary': None,
                  'service': 'unknown', 'problem': None}
        if self.cfg is None:
            return status
        try:
            status['mode'] = getattr(self.cfg.scheduleMode(), 'name', '')
        except Exception:                     # noqa: BLE001
            status['mode'] = ''
        status['needs_cron'] = self._needs_cron()
        if not status['needs_cron']:
            if status['mode'] == 'UDEV' and self._udev_ready() is False:
                status['problem'] = 'udev'
            return status
        status['binary'] = self._cron_binary()
        if status['binary'] is None:
            status['problem'] = 'binary'
            return status
        status['service'] = self._cron_service_state(status['binary'])
        if status['service'] == 'inactive':
            status['problem'] = 'service'
        return status

    def _cron_installed(self):
        """The core swallows crontab write errors; check the result."""
        try:
            mode = getattr(self.cfg.scheduleMode(), 'name', '')
        except Exception:                 # noqa: BLE001
            return True
        if mode in ('DISABLED', 'UDEV'):
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

    SSH_REMOTE_CHECK = (
        'rsync="MISSING"; command -v rsync >/dev/null 2>&1 && '
        'rsync=$(rsync --version 2>/dev/null | head -1); '
        'echo "rsync=$rsync"; '
        'if [ -d {path} ]; then '
        'if [ -w {path} ]; then echo "state=dir-writable"; '
        'else echo "state=dir-readonly"; fi; '
        'else parent=$(dirname {path}); '
        'if [ -d "$parent" ] && [ -w "$parent" ]; then '
        'echo "state=parent-writable"; '
        'else echo "state=parent-missing"; fi; fi; '
        'target={path}; [ -d {path} ] || target=$(dirname {path}); '
        'echo "free=$(df -Pk "$target" 2>/dev/null | awk \'NR==2{{print $4}}\')"'
    )

    def check_ssh_destination(self, host, port, user, path):
        """Probe an SSH destination before saving it.

        Back In Time copies over SSH with rsync on the *remote* side, so a
        reachable host is not enough: rsync has to be installed and the path
        writable. Checking this here avoids saving a profile whose first
        snapshot fails.
        """
        host = (host or '').strip()
        user = (user or '').strip()
        path = (path or '').strip()
        if not host:
            return False, 'Escribe el servidor SSH'
        if not path:
            return False, 'Escribe la ruta remota de las copias'
        if shutil.which('sshfs') is None:
            return False, ('Falta sshfs en este equipo: instálalo para usar '
                           'destinos SSH')
        target = f'{user}@{host}' if user else host
        command = self.SSH_REMOTE_CHECK.format(path=shlex.quote(path))
        argv = ['ssh', '-p', str(port or 22), '-o', 'BatchMode=yes',
                '-o', 'ConnectTimeout=8',
                '-o', 'StrictHostKeyChecking=accept-new',
                target, command]
        try:
            out = subprocess.run(argv, capture_output=True, text=True,
                                 timeout=25)
        except subprocess.TimeoutExpired:
            return False, f'El servidor {target} no responde en 25 segundos'
        except OSError as exc:
            return False, f'No se pudo lanzar ssh: {exc}'
        stderr = (out.stderr or '').strip().splitlines()
        detail = stderr[-1] if stderr else ''
        values = {}
        for line in (out.stdout or '').splitlines():
            key, _, value = line.partition('=')
            values[key.strip()] = value.strip()
        if out.returncode != 0 or 'state' not in values:
            lowered = detail.lower()
            if 'permission denied' in lowered or 'publickey' in lowered:
                return False, ('La llave SSH no ha sido aceptada por '
                               f'{target}. Añádela al usuario remoto.')
            if 'could not resolve' in lowered:
                return False, f'No se puede resolver el nombre {host}'
            if ('connection refused' in lowered or 'timed out' in lowered
                    or 'no route to host' in lowered):
                return False, (f'No se puede conectar a {target} en el puerto '
                               f'{port or 22}')
            return False, f'La comprobación falló: {detail or out.returncode}'
        state = values.get('state', '')
        if state == 'dir-readonly':
            return False, f'La ruta {path} existe pero no se puede escribir'
        if state == 'parent-missing':
            return False, (f'La carpeta padre de {path} no existe o no se '
                           'puede escribir')
        if values.get('rsync', 'MISSING') == 'MISSING':
            return False, ('El servidor no tiene rsync instalado y Back In '
                           'Time lo necesita allí para copiar por SSH')
        space = ''
        try:
            space = f' · {format_size(int(values.get("free") or 0) * 1024)} libres'
        except (TypeError, ValueError):
            space = ''
        where = path if state == 'dir-writable' else f'{path} (se creará)'
        return True, (f'Conexión correcta con {target} · '
                      f'{values.get("rsync", "rsync")}{space} en {where}')

    def password_available(self):
        """Whether an encrypted profile has its password at hand.

        The core falls back to ``getpass`` (stdin) when nothing is in the
        cache or the keyring, which in a GTK app means a hung or failed
        mount. This only looks, it never asks.
        """
        mode = str(self.profile_mode())
        if 'gocryptfs' not in mode and 'encfs' not in mode:
            return True
        try:
            from password import Password
            pw_id = 2 if mode.startswith('ssh') else 1
            pid = self.cfg.currentProfile()
            # Reuse the core's own container: the password set during this
            # session lives in its in-memory db, not in a new instance.
            pw = getattr(self.cfg, 'pw', None) or Password(self.cfg)
            service = self.cfg.keyringServiceName(pid, mode, pw_id)
            user = self.cfg.keyringUserName(pid)
            if pw.db.get(f'{service}/{user}'):
                return True
            if self.cfg.passwordUseCache(pid) \
                    and pw.passwordFromCache(service, user):
                return True
            if self.cfg.passwordSave(pid) \
                    and pw.passwordFromKeyring(service, user):
                return True
        except Exception:                 # noqa: BLE001
            return True     # unknown: never block the mount on a guess
        return False

    def set_profile_password(self, password):
        """Store the encryption password where the core will find it."""
        mode = self.profile_mode()
        pw_id = 2 if str(mode).startswith('ssh') else 1
        self.cfg.setPassword(password, mode=mode, pw_id=pw_id)
        self.cfg.save()

    def ensure_mounted(self):
        """Mount the destination once and keep it for the app lifetime.

        Returns ``(ok, error)`` with the error ready to show: the core's
        MountError carries a ``gui_msg`` written for the user. A local
        profile needs no mount, but the destination still has to exist,
        otherwise the window claims "no copies yet" over a disk that is
        simply unplugged.
        """
        if self.cfg is None:
            return True, None
        manager_cls = getattr(mount, 'MountManager', None) if mount else None
        if manager_cls is None:
            return True, None
        if self._mount_manager is not None:
            return True, None
        if not self.password_available():
            self.destination_problem = (
                'El perfil cifrado necesita su contraseña: desbloquéalo para '
                'poder leer las copias.')
            return False, self.destination_problem
        try:
            if str(self.profile_mode()) == 'local':
                manager_cls.create(self.cfg).validate()
                self.destination_problem = None
                return True, None
            manager = manager_cls.create(self.cfg)
            manager.mount()
        except Exception as exc:          # noqa: BLE001
            self.destination_problem = self._mount_error_text(exc)
            return False, self.destination_problem
        self.destination_problem = None
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
                return self._mount_error_text(exc)
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
            return self._mount_error_text(exc)
        except Exception as exc:          # noqa: BLE001
            self._snapshots = None
            return self._mount_error_text(exc)
        finally:
            # The core owns the mount during a backup and unmounts at the
            # end, so the persistent mount has to be restored or every
            # snapshot path the window holds points at a dead mountpoint.
            if self.profile_mode() != 'local':
                ok, error = self.ensure_mounted()
                if not ok:
                    logger.debug(f'Could not remount after the copy: {error}')
        if not ret_error:
            return None
        reason = self.engine_message(msg)
        after = [sid.path() for sid in self.list_snapshots()]
        created = bool(after) and (not before or after[0] != before[0])
        if created:
            # rsync can finish fine and the core still fail afterwards
            # (retention, free space). Saying "the snapshot failed" there is
            # wrong: the copy exists.
            detail = f': {reason}' if reason else ''
            return f'La copia se creó, pero el motor falló al terminar{detail}'
        return f'La copia falló{": " + reason if reason else ""}'

    @staticmethod
    def engine_message(msg):
        """The useful part of ``takeSnapshotMessage()``, without filler.

        The core returns tuples and lists whose last element is often an
        informative "please be patient, finishing" line, which used to reach
        the window as the error text.
        """
        def flatten(value):
            if isinstance(value, (list, tuple)):
                for item in value:
                    yield from flatten(item)
            elif value:
                yield str(value)

        text = ' '.join(part.strip() for part in flatten(msg)).strip()
        while text.startswith('[]'):
            text = text[2:].strip()
        lowered = text.lower()
        if not text or lowered.startswith(('por favor', 'finalizando')):
            return ''
        return text

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
        """Yield log lines for the current profile.

        The log lives under the local data directory, so it needs no mount.
        Wrapping it in ``mounted()`` left it empty exactly when it mattered:
        the copy failed because the destination was unreachable.
        """
        try:
            log = snapshotlog.SnapshotLog(self.cfg)
            yield from log.get()
        except Exception as exc:          # noqa: BLE001
            logger.debug(f'Could not read the snapshot log: {exc}')
            return

    @staticmethod
    def _mount_error_text(exc):
        """The core writes user facing messages in MountError.gui_msg."""
        text = getattr(exc, 'gui_msg', None) or str(exc)
        return text.strip() or exc.__class__.__name__

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
