#!/bin/sh
# Refresh the translation template and merge it into the English catalogue.
#
# Spanish is the source language, so there is no es.po: a Spanish session uses
# the strings written in the code. Every other language needs a catalogue, and
# only English ships today.
#
#   ./po/update-po.sh          regenerate the template and merge
#   ./po/update-po.sh --build  also compile every catalogue into po/locale/,
#                              the tree a checkout reads at runtime
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=$(dirname -- "$here")
version=$(sed -n "s/^__version__ = '\(.*\)'/\1/p" "$root/bitgnome/__init__.py")

cd "$root"
xgettext \
  --language=Python \
  --keyword=_ \
  --keyword=ngettext:1,2 \
  --from-code=UTF-8 \
  --add-location=file \
  --package-name=backintime-gnome \
  --package-version="$version" \
  --output="$here/backintime-gnome.pot" \
  bitgnome/*.py

if [ -f "$here/en.po" ]; then
  msgmerge --quiet --update --backup=none "$here/en.po" \
    "$here/backintime-gnome.pot"
fi

for catalogue in "$here"/*.po; do
  [ -e "$catalogue" ] || continue
  lang=$(basename "$catalogue" .po)
  msgfmt --check --output-file=/dev/null "$catalogue"
  if [ "${1:-}" = "--build" ]; then
    target="$here/locale/$lang/LC_MESSAGES/backintime-gnome.mo"
    install -d "$(dirname "$target")"
    msgfmt --output-file="$target" "$catalogue"
  fi
done
