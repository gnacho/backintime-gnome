"""Interface translations for the GNOME frontend.

Source strings are written in Spanish, so a Spanish session needs no catalog
at all. Any other language resolves through gettext, both from the catalogue
the package installs in ``/usr/share/locale`` and from the one shipped next to
this module, which is what a checkout uses.

``BACKINTIME_GNOME_LANG`` pins the language regardless of the session locale.
That is what the screenshot script and the tests use to get a stable language.
"""
import gettext
import os

DOMAIN = 'backintime-gnome'
LANGUAGE_VAR = 'BACKINTIME_GNOME_LANG'
_HERE = os.path.dirname(os.path.abspath(__file__))
_LOCALEDIRS = (os.path.join(_HERE, 'locale'),
               os.path.join(_HERE, os.pardir, 'po', 'locale'),
               '/usr/share/locale',
               '/usr/local/share/locale')


def _clean(code):
    """Reduce a locale name (``es_ES.UTF-8``) to its language (``es``)."""
    for separator in ('.', '@'):
        code = (code or '').split(separator)[0]
    return code.split('_')[0].strip()


def language():
    explicit = _clean(os.environ.get(LANGUAGE_VAR, ''))
    if explicit:
        return explicit
    names = []
    try:
        from gi.repository import GLib
        names = GLib.get_language_names() or []
    except Exception:                     # noqa: BLE001
        import locale as _locale
        names = [_locale.getlocale()[0] or '']
    for name in names:
        code = _clean(name)
        if code and code not in ('C', 'POSIX'):
            return code
    return 'en'


def load(lang=None):
    """Return the translation for ``lang`` or a catalogue-less fallback."""
    lang = _clean(lang) or language()
    for base in _LOCALEDIRS:
        try:
            return gettext.translation(DOMAIN, localedir=base,
                                       languages=[lang], fallback=False)
        except (OSError, FileNotFoundError, ValueError):
            continue
    return gettext.NullTranslations()


_TRANSLATION = load()
_ = _TRANSLATION.gettext
ngettext = _TRANSLATION.ngettext
