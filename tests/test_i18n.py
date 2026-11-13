"""The catalogue has to resolve, fall back and never lie about coverage.

Spanish is the source language, so the fallback is the string written in the
code. A wrong ``localedir`` or a stale ``.mo`` would silently leave an English
session reading Spanish, which is the bug this file exists to catch.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bitgnome import i18n

ROOT = Path(__file__).resolve().parents[1]
MO = ROOT / 'po' / 'locale' / 'en' / 'LC_MESSAGES' / 'backintime-gnome.mo'


class LanguageTests(unittest.TestCase):
    def test_the_override_wins_over_the_session(self):
        old = os.environ.get(i18n.LANGUAGE_VAR)
        os.environ[i18n.LANGUAGE_VAR] = 'en_GB.UTF-8'
        try:
            self.assertEqual(i18n.language(), 'en')
            os.environ[i18n.LANGUAGE_VAR] = 'es'
            self.assertEqual(i18n.language(), 'es')
        finally:
            if old is None:
                del os.environ[i18n.LANGUAGE_VAR]
            else:
                os.environ[i18n.LANGUAGE_VAR] = old

    def test_english_catalogue_translates(self):
        translation = i18n.load('en')
        self.assertEqual(translation.gettext('Ajustes'), 'Settings')

    def test_unknown_language_falls_back_to_the_source(self):
        translation = i18n.load('xx')
        self.assertEqual(translation.gettext('Ajustes'), 'Ajustes')

    def test_spanish_uses_the_source_strings(self):
        translation = i18n.load('es')
        self.assertEqual(translation.gettext('Ajustes'), 'Ajustes')

    def test_plurals_follow_the_catalogue(self):
        translation = i18n.load('en')
        self.assertEqual(translation.ngettext('{n} día', '{n} días', 1),
                         '{n} day')
        self.assertEqual(translation.ngettext('{n} día', '{n} días', 4),
                         '{n} days')


class CatalogueTests(unittest.TestCase):
    def test_the_compiled_catalogue_is_committed_next_to_the_po(self):
        self.assertTrue(MO.exists(),
                        'run ./po/update-po.sh --build after editing a .po')

    def test_the_committed_catalogue_matches_the_po(self):
        import shutil
        import subprocess
        import tempfile

        if shutil.which('msgfmt') is None:
            self.skipTest('msgfmt is not installed')
        with tempfile.TemporaryDirectory() as folder:
            fresh = Path(folder) / 'en.mo'
            subprocess.run(['msgfmt', '--output-file', str(fresh),
                            str(ROOT / 'po' / 'en.po')], check=True)
            self.assertEqual(fresh.read_bytes(), MO.read_bytes(),
                             'the committed catalog is stale, run '
                             './po/update-po.sh --build')

    def test_every_translated_entry_has_text(self):
        po = (ROOT / 'po' / 'en.po').read_text(encoding='utf-8')
        blocks = [block for block in po.split('\n\n')
                  if 'msgid "' in block and 'msgstr' in block]
        empty = [block for block in blocks
                 if 'msgstr ""\n\n' in block + '\n\n']
        self.assertEqual(empty, [], 'untranslated entries in po/en.po')

    def test_no_entry_is_marked_fuzzy(self):
        po = (ROOT / 'po' / 'en.po').read_text(encoding='utf-8')
        self.assertNotIn('#, fuzzy', po)


if __name__ == '__main__':
    unittest.main(verbosity=2)
