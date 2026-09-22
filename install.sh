#!/usr/bin/env bash
#
# Installe le greffon « Traitement par lots d'un dossier » dans le dossier
# personnel des greffons de GIMP 3.
#
#   ./install.sh                 installation à l'emplacement habituel
#   ./install.sh /autre/chemin   installation dans un dossier plug-ins précis
#
set -euo pipefail

NOM="gimp-batch-folder"
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$#" -ge 1 ]; then
    PLUGINS="$1"
elif [ "$(uname -s)" = "Darwin" ]; then
    PLUGINS="$HOME/Library/Application Support/GIMP/3.0/plug-ins"
else
    PLUGINS="${XDG_CONFIG_HOME:-$HOME/.config}/GIMP/3.0/plug-ins"
fi

CIBLE="$PLUGINS/$NOM"

if [ "$SOURCE" = "$CIBLE" ]; then
    echo "Le greffon est déjà en place dans $CIBLE."
else
    echo "Installation dans $CIBLE"
    mkdir -p "$PLUGINS"
    rm -rf "$CIBLE"
    mkdir -p "$CIBLE"
    cp -R "$SOURCE/$NOM.py" "$SOURCE/bfp" "$SOURCE/looks" "$SOURCE/README.md" \
          "$CIBLE/"
    find "$CIBLE" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
fi

chmod +x "$CIBLE/$NOM.py"

echo
echo "Fait. Relancez GIMP : l'entrée apparaît dans le menu Fichier,"
echo "sous « Traitement par lots d'un dossier… »."
echo
echo "Si elle n'apparaît pas, lancez GIMP depuis un terminal pour voir les"
echo "erreurs d'enregistrement du greffon."
