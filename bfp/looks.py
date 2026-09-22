# -*- coding: utf-8 -*-
"""
bfp.looks — « recettes » de look : enchaînements d'opérations GEGL décrits en
JSON, réutilisables d'un lot à l'autre.

Le format d'une recette::

    {
      "name": "Argentique doux",
      "description": "Contraste adouci, grain fin, léger vignettage.",
      "steps": [
        {"op": "gegl:levels", "params": {"in-low": 0.0, "out-low": 0.04}},
        {"op": "gegl:saturation", "params": {"scale": 0.85}},
        {"special": "split-tone",
         "params": {"shadows": "#1b3a5c", "highlights": "#e8c98f",
                    "amount": 25.0}}
      ]
    }

Chaque étape accepte en plus ``"opacity"`` (0–100) et ``"blend"`` (nom d'un
mode de fusion GIMP, p. ex. ``"overlay"``).

Ce module reste sans dépendance GIMP : il ne fait que charger et valider la
structure.  L'application réelle des filtres est dans :mod:`bfp.gimpops`.
"""

from __future__ import annotations

import json
import os

from .core import BatchError, clamp, hex_to_rgba

#: Étapes « spéciales » implémentées en Python plutôt qu'en une opération GEGL.
SPECIAL_STEPS = ("split-tone",)


class LookStep(object):
    """Une étape d'une recette."""

    __slots__ = ("op", "special", "params", "opacity", "blend")

    def __init__(self, op=None, special=None, params=None, opacity=100.0,
                 blend="replace"):
        self.op = op
        self.special = special
        self.params = dict(params or {})
        self.opacity = clamp(float(opacity), 0.0, 100.0)
        self.blend = str(blend or "replace").lower()

    def __repr__(self):  # pragma: no cover - confort de débogage
        return "<LookStep %s %r>" % (self.special or self.op, self.params)


class Look(object):
    """Une recette complète."""

    __slots__ = ("name", "description", "steps", "path")

    def __init__(self, name, description="", steps=(), path=None):
        self.name = name
        self.description = description
        self.steps = list(steps)
        self.path = path

    def __repr__(self):  # pragma: no cover
        return "<Look %r (%d étapes)>" % (self.name, len(self.steps))

    def to_dict(self):
        steps = []
        for step in self.steps:
            data = {"params": dict(step.params)}
            if step.special:
                data["special"] = step.special
            else:
                data["op"] = step.op
            if step.opacity != 100.0:
                data["opacity"] = step.opacity
            if step.blend != "replace":
                data["blend"] = step.blend
            steps.append(data)
        return {"name": self.name, "description": self.description,
                "steps": steps}


def parse_look(data, path=None):
    """Transforme un dictionnaire JSON en :class:`Look` validé."""
    if not isinstance(data, dict):
        raise BatchError("Recette invalide (objet JSON attendu) : %s" % path)

    name = str(data.get("name") or
               (os.path.splitext(os.path.basename(path))[0] if path else "")).strip()
    if not name:
        raise BatchError("Recette sans nom : %s" % path)

    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise BatchError("La recette « %s » ne contient aucune étape." % name)

    steps = []
    for position, raw in enumerate(raw_steps, start=1):
        if not isinstance(raw, dict):
            raise BatchError("Étape %d invalide dans « %s »." % (position, name))
        op = raw.get("op")
        special = raw.get("special")
        if special:
            if special not in SPECIAL_STEPS:
                raise BatchError(
                    "Étape spéciale inconnue « %s » dans « %s »." % (special, name))
        elif not op or ":" not in str(op):
            raise BatchError(
                "Étape %d de « %s » : « op » doit être une opération GEGL "
                "comme « gegl:saturation »." % (position, name))
        params = raw.get("params") or {}
        if not isinstance(params, dict):
            raise BatchError("Étape %d de « %s » : « params » doit être un objet."
                             % (position, name))
        if special == "split-tone":
            # Validation précoce des couleurs pour ne pas échouer en plein lot.
            hex_to_rgba(params.get("shadows", "#000000"))
            hex_to_rgba(params.get("highlights", "#ffffff"))
        steps.append(LookStep(op=op, special=special, params=params,
                              opacity=raw.get("opacity", 100.0),
                              blend=raw.get("blend", "replace")))

    return Look(name=name, description=str(data.get("description") or ""),
                steps=steps, path=path)


def load_look_file(path):
    """Charge une recette depuis un fichier JSON."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise BatchError("Lecture impossible de %s : %s" % (path, exc))
    return parse_look(data, path=path)


def load_looks(directories):
    """Charge toutes les recettes des dossiers donnés.

    Renvoie ``(looks, erreurs)``.  Un fichier cassé n'empêche pas les autres
    de se charger ; son message est renvoyé dans ``erreurs``.
    """
    looks = []
    errors = []
    seen = set()

    for directory in directories:
        if not directory or not os.path.isdir(directory):
            continue
        for filename in sorted(os.listdir(directory)):
            if not filename.lower().endswith(".json"):
                continue
            full = os.path.join(directory, filename)
            try:
                look = load_look_file(full)
            except BatchError as exc:
                errors.append(str(exc))
                continue
            if look.name in seen:
                # Les dossiers utilisateur passent après : ils écrasent.
                looks = [l for l in looks if l.name != look.name]
            seen.add(look.name)
            looks.append(look)

    looks.sort(key=lambda l: l.name.lower())
    return looks, errors


def save_look(look, path):
    """Écrit une recette sur disque."""
    directory = os.path.dirname(os.path.abspath(path))
    try:
        os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(look.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except OSError as exc:
        raise BatchError("Écriture impossible de %s : %s" % (path, exc))
    return path


def find_look(looks, name):
    """Retrouve une recette par son nom (sans tenir compte de la casse)."""
    if not name:
        return None
    target = str(name).strip().lower()
    for look in looks:
        if look.name.lower() == target:
            return look
    return None
