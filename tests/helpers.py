"""Shared helpers for isolated GTK tests using the real Back In Time backend."""
import os
import tempfile

import gi

gi.require_version('Adw', '1')
from gi.repository import Adw

from bitgnome.backend import Backend


class TestApp:
    def __init__(self):
        self.backend = Backend()

    @staticmethod
    def get_active_window():
        return None

    @staticmethod
    def activate_action(_name, _parameter):
        return None


def isolated_home(test):
    """Create a temporary HOME and restore it during test cleanup."""
    # A worker thread may still be writing into HOME when the test
    # ends: the cleanup must not turn that into a test failure.
    home = tempfile.TemporaryDirectory(
        prefix='rewind-encryption-home-', ignore_cleanup_errors=True)
    old_home = os.environ.get('HOME')
    old_config = os.environ.get('XDG_CONFIG_HOME')
    old_data = os.environ.get('XDG_DATA_HOME')
    os.environ['HOME'] = home.name
    os.environ['XDG_CONFIG_HOME'] = home.name + '/.config'
    os.environ['XDG_DATA_HOME'] = home.name + '/.local/share'
    from pathlib import Path
    import bitbase
    bitbase.XDG_DATA_HOME = Path(home.name, '.local/share')
    bitbase.XDG_CONFIG_HOME = Path(home.name, '.config')
    bitbase.BIT_DATA_HOME = bitbase.XDG_DATA_HOME / bitbase.BINARY_NAME_BASE
    bitbase.DEFAULT_CONFIG_FILE_PATH = (
        bitbase.XDG_CONFIG_HOME / bitbase.BINARY_NAME_BASE / 'config')
    bitbase.CRON_ENV_PATH = bitbase.XDG_DATA_HOME / 'cron_env.json'
    bitbase.context['--config'] = bitbase.DEFAULT_CONFIG_FILE_PATH
    bitbase.DEFAULT_CONFIG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    test.addCleanup(lambda: os.environ.__setitem__('HOME', old_home or ''))
    test.addCleanup(lambda: os.environ.__setitem__(
        'XDG_CONFIG_HOME', old_config or ''))
    test.addCleanup(lambda: os.environ.__setitem__(
        'XDG_DATA_HOME', old_data or ''))
    test.addCleanup(home.cleanup)
    Adw.init()
    return home


def find_widget_by_label(widget, label):
    """Find the first widget with an exact label or text."""
    for method in ('get_label', 'get_text'):
        try:
            if getattr(widget, method)() == label:
                return widget
        except (AttributeError, TypeError):
            pass
    child = widget.get_first_child()
    while child is not None:
        found = find_widget_by_label(child, label)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def widget_texts(widget):
    """Return visible label/button text for assertions about user choices."""
    texts = []
    for method in ('get_label', 'get_text'):
        try:
            value = getattr(widget, method)()
        except (AttributeError, TypeError):
            continue
        if value:
            texts.append(str(value))
    child = widget.get_first_child()
    while child is not None:
        texts.extend(widget_texts(child))
        child = child.get_next_sibling()
    return texts
