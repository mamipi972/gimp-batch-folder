# -*- coding: utf-8 -*-
"""
bfp.paths — emplacements de stockage (préréglages, recettes de look, journal).

Le dossier de configuration de GIMP est demandé à ``Gimp.directory()`` quand
GIMP est là ; sinon on retombe sur ``~/.config/GIMP/3.0``, ce qui permet
d'exécuter et de tester ces modules hors de GIMP.
"""

from __future__ import annotations

import os
import sys

#: Nom du dossier créé dans le répertoire de configuration de GIMP.
DATA_DIRNAME = "batch-folder"


def _fallback_gimp_directory():
    env = os.environ.get("GIMP3_DIRECTORY")
    if env:
        return env
    home = os.path.expanduser("~")
    if os.name == "nt":
        appdata = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        return os.path.join(appdata, "GIMP", "3.0")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "GIMP", "3.0")
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return os.path.join(config, "GIMP", "3.0")


def gimp_directory():
    """Répertoire de configuration utilisateur de GIMP."""
    try:
        import gi
        gi.require_version("Gimp", "3.0")
        from gi.repository import Gimp
        directory = Gimp.directory()
        if directory:
            return directory
    except Exception:
        pass
    return _fallback_gimp_directory()


def data_directory():
    """Dossier de données du greffon, créé au besoin."""
    path = os.path.join(gimp_directory(), DATA_DIRNAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def presets_directory():
    path = os.path.join(data_directory(), "presets")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def user_looks_directory():
    path = os.path.join(data_directory(), "looks")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def bundled_looks_directory():
    """Dossier ``looks/`` livré avec le greffon."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "looks")


def look_directories():
    """Dossiers de recettes, du plus général au plus prioritaire."""
    return [bundled_looks_directory(), user_looks_directory()]


def log_path():
    return os.path.join(data_directory(), "dernier-traitement.log")


def last_settings_path():
    return os.path.join(data_directory(), "derniers-reglages.json")
