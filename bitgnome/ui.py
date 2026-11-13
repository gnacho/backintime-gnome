"""Small widget helpers shared by the window, the settings sheet and the wizard."""
import datetime

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gtk, Pango  # noqa: E402

from bitgnome.i18n import _, ngettext  # noqa: E402


def has_icon(name):
    """Whether the icon theme knows ``name`` (the app artwork may be absent)."""
    try:
        display = Gdk.Display.get_default()
        if display is None:
            return False
        return Gtk.IconTheme.get_for_display(display).has_icon(name)
    except Exception:  # noqa: BLE001
        return False


def a11y(widget, label):
    """Accessible name, with the tooltip as a fallback on GTK 4.8."""
    if not widget.get_tooltip_text():
        widget.set_tooltip_text(label)
    try:
        prop = getattr(Gtk, 'AccessibleProperty', None)
        if prop is not None:
            widget.update_property([prop.LABEL], [label])
    except Exception:  # noqa: BLE001
        pass


MONTHS = [_('enero'), _('febrero'), _('marzo'), _('abril'), _('mayo'),
          _('junio'), _('julio'), _('agosto'), _('septiembre'), _('octubre'),
          _('noviembre'), _('diciembre')]
DAYS = [_('lunes'), _('martes'), _('miércoles'), _('jueves'), _('viernes'),
        _('sábado'), _('domingo')]


def save(cfg):
    try:
        cfg.save()
        return True
    except Exception:  # noqa: BLE001
        return False


def day_label(when):
    """'martes 4 de noviembre'."""
    return _('{day} {number} de {month}').format(
        day=DAYS[when.weekday()], number=when.day,
        month=MONTHS[when.month - 1])


def short_label(when):
    """'4 nov, 09:00'."""
    return _('{number} {month}, {time}').format(
        number=when.day, month=MONTHS[when.month - 1][:3],
        time=f'{when:%H:%M}')


def time_label(when):
    return f'{when:%H:%M}'


def ago_label(when):
    today = datetime.date.today()
    days = (today - when.date()).days
    if days <= 0:
        when_time = _('hoy')
    elif days == 1:
        when_time = _('ayer')
    elif days < 7:
        when_time = ngettext('hace {n} día', 'hace {n} días',
                             days).format(n=days)
    elif days < 30:
        weeks = days // 7
        when_time = ngettext('hace {n} semana', 'hace {n} semanas',
                             weeks).format(n=weeks)
    elif days < 365:
        months = days // 30
        when_time = ngettext('hace {n} mes', 'hace {n} meses',
                             months).format(n=months)
    else:
        years = days // 365
        when_time = ngettext('hace {n} año', 'hace {n} años',
                             years).format(n=years)
    return when_time


def chip(label, tooltip, on_close):
    box = Gtk.Box(spacing=4)
    box.add_css_class('rw-chip')
    text = Gtk.Label(label=label, ellipsize=Pango.EllipsizeMode.END)
    text.set_tooltip_text(tooltip or label)
    close = Gtk.Button(icon_name='window-close-symbolic')
    close.add_css_class('rw-chip-close')
    close.set_tooltip_text(_('Quitar'))
    a11y(close, _('Quitar {label}').format(label=label))
    close.connect('clicked', lambda *_: on_close())
    box.append(text)
    box.append(close)
    return box


def row(title, subtitle=None, suffix=None):
    """Plain settings row: title (and subtitle) on the left, widget right."""
    box = Gtk.Box(spacing=12)
    box.add_css_class('rw-srow')
    box.title_label = None
    box.subtitle = None
    labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
    labels.set_hexpand(True)
    head = Gtk.Label(label=title, xalign=0)
    head.add_css_class('rw-srow-title')
    labels.append(head)
    box.title_label = head
    if subtitle:
        sub = Gtk.Label(label=subtitle, xalign=0, wrap=True)
        sub.add_css_class('rw-srow-sub')
        labels.append(sub)
        box.subtitle = sub
    box.append(labels)
    if suffix is not None:
        box.append(suffix)
    return box


def group_title(text):
    label = Gtk.Label(label=text.upper(), xalign=0)
    label.add_css_class('rw-group-title')
    return label


def apply_theme(window):
    """Make any window follow the desktop theme.

    The stylesheet is written for a light desktop, so the dark surfaces hang
    off the ``rw-dark`` class. Doing it only on the main window left the
    settings sheet, the profile manager and every dialog painted light over
    dark libadwaita widgets.
    """
    manager = Adw.StyleManager.get_default()

    def sync(*_args):
        if manager.get_dark():
            window.add_css_class('rw-dark')
        else:
            window.remove_css_class('rw-dark')

    sync()
    handler = manager.connect('notify::dark', sync)
    window.connect('destroy', lambda *_: manager.disconnect(handler))
    window._theme_handler = handler
    return window


def segmented(options, current, on_change):
    """Pill segment control. ``options`` is [(value, label), ...]."""
    box = Gtk.Box(spacing=2)
    box.add_css_class('rw-seg')
    buttons = {}
    state = {'value': current}

    def select(value, notify=True):
        changed = value != state['value']
        state['value'] = value
        for val, btn in buttons.items():
            btn.set_active(val == value)
        if notify and changed:
            on_change(value)

    for value, label in options:
        btn = Gtk.ToggleButton(label=label)
        btn.connect('clicked', lambda b, v=value: (
            b.set_active(True), select(v)))
        box.append(btn)
        buttons[value] = btn
    select(current, notify=False)
    box.set_halign(Gtk.Align.END)
    return box


def choose_folder(parent, title, on_path):
    """Native folder chooser; calls on_path(path) on accept."""
    dialog = Gtk.FileChooserNative(title=title, transient_for=parent,
                                   action=Gtk.FileChooserAction.SELECT_FOLDER)

    def on_response(dlg, response):
        if response == Gtk.ResponseType.ACCEPT:
            folder = dlg.get_file()
            if folder:
                on_path(folder.get_path())

    dialog.connect('response', on_response)
    dialog.show()


def choose_path(parent, title, action, on_path):
    dialog = Gtk.FileChooserNative(title=title, transient_for=parent,
                                   action=action)

    def on_response(dlg, response):
        if response == Gtk.ResponseType.ACCEPT:
            folder = dlg.get_file()
            if folder:
                on_path(folder.get_path())

    dialog.connect('response', on_response)
    dialog.show()
