# -*- coding: utf-8 -*-
"""
bfp.presets — enregistrement et relecture des jeux de réglages (JSON).

Les préréglages sont de simples fichiers ``.json`` déposés dans
``<config GIMP>/batch-folder/presets/``.  Ils sont donc partageables : il
suffit de copier le fichier.
"""

from __future__ import annotations

import json
import os
import re

from . import paths
from .core import BatchError, coerce_settings, default_settings

#: Clés volontairement non enregistrées : elles décrivent un lot précis, pas
#: une façon de travailler.
VOLATILE_KEYS = ("source_folder", "output_folder", "dry_run")


def preset_filename(name):
    """Nom de fichier sûr pour un préréglage."""
    cleaned = re.sub(r"[^\w\-. ]+", "_", str(name), flags=re.UNICODE).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    if not cleaned:
        raise BatchError("Nom de préréglage vide.")
    return cleaned + ".json"


def preset_path(name):
    return os.path.join(paths.presets_directory(), preset_filename(name))


def list_presets():
    """Noms des préréglages disponibles, triés."""
    directory = paths.presets_directory()
    names = []
    if os.path.isdir(directory):
        for filename in os.listdir(directory):
            if not filename.lower().endswith(".json"):
                continue
            full = os.path.join(directory, filename)
            try:
                with open(full, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                name = data.get("preset_name") or os.path.splitext(filename)[0]
            except (OSError, ValueError):
                name = os.path.splitext(filename)[0]
            names.append(str(name))
    return sorted(set(names), key=lambda s: s.lower())


def save_preset(name, settings, keep_folders=False):
    """Écrit un préréglage.  Renvoie son chemin."""
    name = str(name or "").strip()
    if not name:
        raise BatchError("Donnez un nom au préréglage.")

    payload = dict(settings)
    if not keep_folders:
        for key in VOLATILE_KEYS:
            payload.pop(key, None)
    payload["preset_name"] = name

    path = preset_path(name)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2,
                      sort_keys=True)
            handle.write("\n")
    except OSError as exc:
        raise BatchError("Enregistrement impossible : %s" % exc)
    return path


def load_preset(name, base=None):
    """Relit un préréglage et le fusionne avec ``base`` (ou les défauts)."""
    path = preset_path(name)
    if not os.path.isfile(path):
        # Recherche tolérante : le fichier peut avoir été renommé à la main.
        directory = paths.presets_directory()
        target = str(name).strip().lower()
        for filename in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
            if not filename.lower().endswith(".json"):
                continue
            candidate = os.path.join(directory, filename)
            try:
                with open(candidate, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, ValueError):
                continue
            if str(data.get("preset_name", "")).strip().lower() == target:
                path = candidate
                break
        else:
            raise BatchError("Préréglage introuvable : %s" % name)

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise BatchError("Lecture impossible de %s : %s" % (path, exc))

    merged = dict(base if base is not None else default_settings())
    for key, value in data.items():
        if key == "preset_name":
            continue
        merged[key] = value
    return coerce_settings(merged)


def delete_preset(name):
    """Supprime un préréglage.  Renvoie True si un fichier a été retiré."""
    path = preset_path(name)
    if os.path.isfile(path):
        try:
            os.remove(path)
            return True
        except OSError as exc:
            raise BatchError("Suppression impossible : %s" % exc)
    return False


def save_last_settings(settings):
    """Mémorise les derniers réglages utilisés (rouverts au prochain appel)."""
    try:
        with open(paths.last_settings_path(), "w", encoding="utf-8") as handle:
            json.dump(settings, handle, ensure_ascii=False, indent=2,
                      sort_keys=True)
    except OSError:
        pass  # jamais bloquant


def load_last_settings():
    """Relit les derniers réglages, ou renvoie les valeurs par défaut."""
    path = paths.last_settings_path()
    if not os.path.isfile(path):
        return default_settings()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return coerce_settings(json.load(handle))
    except (OSError, ValueError):
        return default_settings()
