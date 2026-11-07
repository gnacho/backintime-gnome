# Back In Time - front GNOME (GTK4 + libadwaita)

Frontend GNOME nativo para [Back In Time](https://github.com/bit-team/backintime).
No reimplementa nada del motor: importa `common/` del propio Back In Time y usa
`config.Config`, `snapshots.Snapshots` y `snapshotlog.SnapshotLog` exactamente
igual que el cliente Qt. Probado contra **Back In Time 1.6.2** (la rama `dev` 2.0
está a medio refactorizar y no arranca sin configuración previa).

## Requisitos

- Python 3.11+ con PyGObject (`python3-gi`)
- GTK 4.8+ y libadwaita 1.2+ (`gir1.2-gtk-4.0`, `libadwaita-1-0`, `gir1.2-adw-1`)
- `backintime-common` instalado (recomendado), o un checkout del repo

## Instalación (Arch Linux)

El paquete vive en `packaging/aur` y depende de `backintime-cli` (AUR), que
aporta `common/` sin arrastrar la interfaz Qt6.

```bash
git clone https://github.com/gnacho/backintime-gnome
cd backintime-gnome/packaging/aur
paru -S --asdeps backintime-cli
makepkg -si
```

Instala `/usr/bin/backintime-gnome` y la entrada de menú *Back In Time (GNOME)*.

## Ejecutar

```bash
./backintime-gnome
```

Si el núcleo no está instalado en la ruta estándar:

```bash
BACKINTIME_COMMON=/ruta/a/backintime/common ./backintime-gnome
```

Rutas donde busca `common/`, en orden: `$BACKINTIME_COMMON`,
`~/.local/share/backintime-gnome/common`, `/usr/share/backintime/common`,
`/usr/local/share/backintime/common`, y `../backintime/common` (desarrollo).

## Funcionalidad (hito 1)

- **Ventana principal**: lista de instantáneas reales agrupada (Hoy/Ayer/Esta
  semana/Más antiguas) con entrada «Ahora»; explorador del contenido de cada
  instantánea o del sistema actual, con migas de pan, subir nivel y búsqueda.
- **Solo cambios**: compara la instantánea seleccionada con la anterior
  (badges Nuevo/Modificado).
- **Tomar instantánea**: ejecuta `Snapshots.backup(force=True)` en un hilo con
  diálogo de progreso que lee `takeSnapshotMessage()`; toast al terminar y
  recarga de la lista. Ctrl+S.
- **Restaurar**: diálogo con ubicación original u otra carpeta, copia
  `.backup.YYYYMMDD`, `--update` y borrado de archivos nuevos; llama a
  `Snapshots.restore()` real. Ctrl+R.
- **Eliminar instantánea**: confirmación destructiva + `Snapshots.remove()`.
- **Registro**: visor del `takesnapshot_N.log` con filtro Info/Avisos/Errores.
- **Comparar**: diff unificado interno entre el archivo en la instantánea y la
  anterior (doble clic en un archivo viendo una instantánea).
- **Perfiles**: selector en la header bar (cambia `currentProfile` real) y
  ventana *Gestionar perfiles* con las 6 páginas del cliente Qt:
  General (modo Local/SSH/cifrados, destino, SSH, programación cron/udev),
  Incluir y Excluir (listas editables con diálogo de archivos real),
  Autoeliminar (antigüedad + eliminación inteligente), Opciones y Avanzado.
  Todo escribe con los setters de `config.Config` y `cfg.save()`.
- Tema claro/oscuro, F9 para el lateral, atajos (Ctrl+, Ctrl+S Ctrl+R Ctrl+Q).

## Capturas

![Ventana principal](capturas/ventana-principal.png)

![Preferencias](capturas/preferencias.png)

## Pendiente (próximos hitos)

- Asistente de primer arranque (restaurar config desde copia antigua).
- Vista de diff con herramienta externa configurable (`meld`).
- Barra de progreso real durante la restauración.
- Empaquetado Meson/Flatpak y traducciones (reusar `common/po`).

## Notas

- Baseline **GTK 4.8 / libadwaita 1.2** (Debian 12): no usa `AdwDialog`,
  `AdwToolbarView`, `AdwAlertDialog` ni `AdwSpinRow` (requieren GTK 4.10 /
  Adw 1.4+). Migración trivial cuando se suba el baseline.
- Licencia: GPL-2.0+ (igual que Back In Time).
