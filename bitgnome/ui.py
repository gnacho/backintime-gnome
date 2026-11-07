"""Small widget helpers shared by the window, the settings sheet and the wizard."""
import datetime

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Pango  # noqa: E402

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


MONTHS = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
          'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
DAYS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']


def save(cfg):
    try:
        cfg.save()
        return True
    except Exception:  # noqa: BLE001
        return False


def day_label(when):
    """'martes 4 de noviembre'."""
    return f'{DAYS[when.weekday()]} {when.day} de {MONTHS[when.month - 1]}'


def short_label(when):
    """'4 nov, 09:00'."""
    return f'{when.day} {MONTHS[when.month - 1][:3]}, {when:%H:%M}'


def time_label(when):
    return f'{when:%H:%M}'


def ago_label(when):
    today = datetime.date.today()
    days = (today - when.date()).days
    if days <= 0:
        when_time = 'hoy'
    elif days == 1:
        when_time = 'ayer'
    elif days < 7:
        when_time = f'hace {days} días'
    elif days < 30:
        weeks = days // 7
        when_time = f'hace {weeks} semana' + ('' if weeks == 1 else 's')
    elif days < 365:
        months = days // 30
        when_time = f'hace {months} mes' + ('' if months == 1 else 'es')
    else:
        years = days // 365
        when_time = f'hace {years} año' + ('' if years == 1 else 's')
    return when_time


def chip(label, tooltip, on_close):
    box = Gtk.Box(spacing=4)
    box.add_css_class('rw-chip')
    text = Gtk.Label(label=label, ellipsize=Pango.EllipsizeMode.END)
    text.set_tooltip_text(tooltip or label)
    close = Gtk.Button(icon_name='window-close-symbolic')
    close.add_css_class('rw-chip-close')
    close.set_tooltip_text('Quitar')
    a11y(close, f'Quitar {label}')
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


def segmented(options, current, on_change):
    """Pill segment control. ``options`` is [(value, label), ...]."""
    box = Gtk.Box(spacing=2)
    box.add_css_class('rw-seg')
    buttons = {}

    def select(value, notify=True):
        for val, btn in buttons.items():
            btn.set_active(val == value)
        if notify:
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
