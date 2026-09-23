# -*- coding: utf-8 -*-
"""
bfp.opinfo — introspection des opérations GEGL et des procédures du PDB.

C'est ce qui permet à l'éditeur de recettes de se construire tout seul : il
demande à GEGL la liste de ses opérations, puis, pour celle qu'on choisit, la
liste de ses propriétés avec leur type, leurs bornes et leur valeur par
défaut. Aucun catalogue n'est codé en dur, donc l'éditeur expose exactement
ce que votre installation sait faire — ni plus, ni moins.

Tout est enveloppé : une build de GEGL qui n'expose pas telle fonction fait
retomber sur une solution de repli plutôt que de casser l'interface.
"""

from __future__ import annotations

import gi

gi.require_version("Gimp", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gegl, Gimp, GObject  # noqa: E402

from .core import clamp  # noqa: E402

#: Opérations sans intérêt dans une recette de look : sources, sorties,
#: entrées/sorties de tampon, méta-opérations de GIMP.
_HIDDEN_PREFIXES = (
    "gegl:load", "gegl:save", "gegl:buffer", "gegl:nop", "gegl:clone",
    "gegl:layer", "gegl:introspect", "gegl:json", "gegl:exr", "gegl:jpg",
    "gegl:jpeg", "gegl:png", "gegl:svg", "gegl:tiff", "gegl:webp",
    "gegl:ppm", "gegl:rgbe", "gegl:gif", "gegl:magick", "gegl:raw",
    "gegl:path", "gegl:text", "gegl:icc", "gegl:display", "gegl:write",
    "gimp:", "gegl:git",
)

#: Quelques opérations que l'on met en avant dans l'éditeur, par thème.
SUGGESTED_OPERATIONS = (
    ("Couleur", (
        "gegl:brightness-contrast", "gegl:saturation", "gegl:levels",
        "gegl:exposure", "gegl:hue-chroma", "gegl:color-temperature",
        "gegl:shadows-highlights", "gegl:threshold",
    )),
    ("Netteté et flou", (
        "gegl:unsharp-mask", "gegl:gaussian-blur", "gegl:median-blur",
        "gegl:bilateral-filter",
    )),
    ("Ambiance", (
        "gegl:vignette", "gegl:softglow", "gegl:noise-rgb", "gegl:noise-hsv",
        "gegl:dropshadow", "gegl:bloom",
    )),
    ("Artistique", (
        "gegl:cartoon", "gegl:oilify", "gegl:photocopy", "gegl:c2g",
        "gegl:edge", "gegl:pixelize",
    )),
)


# ---------------------------------------------------------------------------
# GEGL
# ---------------------------------------------------------------------------

def _gegl_ready():
    try:
        Gegl.init(None)
    except Exception:
        pass


def list_operations(include_hidden=False):
    """Toutes les opérations GEGL utilisables, triées."""
    _gegl_ready()
    try:
        operations = list(Gegl.list_operations())
    except Exception:
        # Repli : au moins les opérations que l'on sait nommer.
        operations = [op for _theme, ops in SUGGESTED_OPERATIONS for op in ops]

    if include_hidden:
        return sorted(set(operations))

    kept = []
    for name in operations:
        lowered = str(name).lower()
        if lowered.startswith(_HIDDEN_PREFIXES):
            continue
        kept.append(str(name))
    return sorted(set(kept))


def operation_exists(name):
    _gegl_ready()
    try:
        return bool(Gegl.has_operation(name))
    except Exception:
        return True


def operation_key(name, key):
    """Métadonnée d'une opération (``title``, ``description``, ``categories``)."""
    _gegl_ready()
    try:
        value = Gegl.Operation.get_key(name, key)
    except Exception:
        value = None
    return value or ""


def operation_title(name):
    return operation_key(name, "title") or str(name)


def operation_description(name):
    return operation_key(name, "description")


def operation_properties(name):
    """Liste des ``GParamSpec`` d'une opération GEGL.

    Trois voies successives, de la plus propre à la plus rustique, parce que
    toutes les builds n'exposent pas les mêmes fonctions.
    """
    _gegl_ready()

    try:
        specs = Gegl.Operation.list_properties(name)
        if specs:
            return [s for s in specs if not _is_pad(s)]
    except Exception:
        pass

    try:
        node = Gegl.Node()
        node.set_property("operation", name)
        specs = node.list_properties()
        if specs:
            return [s for s in specs if not _is_pad(s)
                    and s.name not in ("operation", "name")]
    except Exception:
        pass

    return []


def _is_pad(pspec):
    """Les entrées/sorties de tampon ne sont pas des réglages."""
    try:
        return pspec.value_type.name in ("GeglBuffer", "GeglNode")
    except Exception:
        return False


# ---------------------------------------------------------------------------
# PDB
# ---------------------------------------------------------------------------

#: Procédures qu'il n'est pas raisonnable d'appeler dans un lot.
_PDB_BLOCKLIST_PREFIXES = (
    "gimp-quit", "gimp-display", "gimp-progress", "gimp-message",
    "gimp-help", "gimp-plugins-menu", "gimp-procedural-db",
    "gimp-pdb", "file-", "gimp-file-", "gimp-image-delete",
    "python-fu-batch-folder",
)


def list_procedures(query=""):
    """Procédures du PDB plausibles comme étape de recette.

    On garde les greffons (``plug-in-…``), les scripts (``script-fu-…``) et
    G'MIC ; on écarte tout ce qui ouvre une fenêtre, quitte GIMP ou touche aux
    fichiers — l'export est le travail du greffon, pas d'une étape de look.
    """
    try:
        names = Gimp.get_pdb().query_procedures(
            query or "", "", "", "", "", "", "", "")
    except Exception:
        try:
            names = Gimp.get_pdb().query_procedures("", "", "", "", "", "", "")
        except Exception:
            names = []

    kept = []
    for name in names or []:
        lowered = str(name).lower()
        if lowered.startswith(_PDB_BLOCKLIST_PREFIXES):
            continue
        if lowered.startswith(("plug-in-", "script-fu-", "gimp-drawable-",
                               "gimp-image-", "gegl-")) or "gmic" in lowered:
            kept.append(str(name))
    return sorted(set(kept))


#: Arguments que le greffon remplit lui-même : ils n'apparaissent pas dans
#: l'éditeur et ne sont pas enregistrés dans la recette.
AUTO_FILLED_ARGUMENTS = ("run-mode", "image", "drawable", "drawables",
                         "num-drawables")


def procedure_arguments(name):
    """``GParamSpec`` des arguments d'une procédure, hors arguments d'office."""
    try:
        procedure = Gimp.get_pdb().lookup_procedure(name)
    except Exception:
        procedure = None
    if procedure is None:
        return []
    try:
        specs = procedure.get_arguments()
    except Exception:
        return []
    return [s for s in specs or [] if s.name not in AUTO_FILLED_ARGUMENTS]


def procedure_blurb(name):
    try:
        procedure = Gimp.get_pdb().lookup_procedure(name)
        return procedure.get_blurb() or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Description normalisée d'un réglage, pour construire les widgets
# ---------------------------------------------------------------------------

class ParamInfo(object):
    """Ce que l'éditeur a besoin de savoir pour fabriquer un widget."""

    __slots__ = ("name", "label", "tooltip", "kind", "minimum", "maximum",
                 "default", "choices")

    def __init__(self, name, label, tooltip, kind, minimum=None, maximum=None,
                 default=None, choices=()):
        self.name = name
        self.label = label
        self.tooltip = tooltip
        self.kind = kind          # bool | int | double | string | color | enum
        self.minimum = minimum
        self.maximum = maximum
        self.default = default
        self.choices = tuple(choices)

    def __repr__(self):  # pragma: no cover
        return "<ParamInfo %s %s>" % (self.name, self.kind)

    def coerce(self, value):
        """Ramène une valeur dans le domaine du réglage."""
        try:
            if self.kind == "bool":
                if isinstance(value, str):
                    return value.strip().lower() in ("1", "true", "vrai",
                                                     "yes", "oui", "on")
                return bool(value)
            if self.kind == "int":
                number = int(round(float(value)))
                if self.minimum is not None:
                    number = int(clamp(number, self.minimum, self.maximum))
                return number
            if self.kind == "double":
                number = float(value)
                if self.minimum is not None:
                    number = clamp(number, self.minimum, self.maximum)
                return number
        except (TypeError, ValueError):
            return self.default
        return value


_NUMERIC_INT = (GObject.TYPE_INT, GObject.TYPE_UINT, GObject.TYPE_INT64,
                GObject.TYPE_UINT64, GObject.TYPE_LONG, GObject.TYPE_ULONG)
_NUMERIC_DOUBLE = (GObject.TYPE_DOUBLE, GObject.TYPE_FLOAT)


def _bounds(pspec, fallback_min, fallback_max):
    low = getattr(pspec, "minimum", None)
    high = getattr(pspec, "maximum", None)
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        return fallback_min, fallback_max
    # GEGL déclare souvent des bornes « infinies » ; un curseur de -1e308 à
    # +1e308 est inutilisable, on le resserre sur quelque chose de manœuvrable.
    if low < -1e9:
        low = fallback_min
    if high > 1e9:
        high = fallback_max
    return low, high


def describe_param(pspec):
    """Traduit un ``GParamSpec`` en :class:`ParamInfo`, ou ``None``."""
    try:
        name = pspec.name
    except Exception:
        return None

    label = (getattr(pspec, "nick", None) or name).replace("-", " ")
    tooltip = getattr(pspec, "blurb", None) or ""
    default = getattr(pspec, "default_value", None)

    try:
        value_type = pspec.value_type
        type_name = value_type.name
    except Exception:
        return None

    if value_type == GObject.TYPE_BOOLEAN:
        return ParamInfo(name, label, tooltip, "bool",
                         default=bool(default) if default is not None else False)

    if value_type in _NUMERIC_INT:
        low, high = _bounds(pspec, -10000, 10000)
        return ParamInfo(name, label, tooltip, "int", low, high,
                         int(default) if isinstance(default, (int, float)) else 0)

    if value_type in _NUMERIC_DOUBLE:
        low, high = _bounds(pspec, -1000.0, 1000.0)
        return ParamInfo(name, label, tooltip, "double", low, high,
                         float(default) if isinstance(default, (int, float))
                         else 0.0)

    if value_type == GObject.TYPE_STRING:
        return ParamInfo(name, label, tooltip, "string",
                         default=str(default) if default is not None else "")

    if type_name in ("GeglColor", "GimpColor"):
        return ParamInfo(name, label, tooltip, "color", default="#000000")

    choices = _enum_choices(value_type)
    if choices:
        return ParamInfo(name, label, tooltip, "enum", default=choices[0][0],
                         choices=choices)

    # Type que l'éditeur ne sait pas représenter : on l'ignore plutôt que
    # d'afficher un champ qui ne marcherait pas.
    return None


def _enum_choices(value_type):
    """Valeurs d'une énumération, sous forme ``[(valeur, libellé), …]``."""
    try:
        enum_class = value_type.pytype
        if enum_class is None:
            return ()
        values = []
        for attribute in dir(enum_class):
            if attribute.isupper():
                item = getattr(enum_class, attribute)
                if isinstance(item, int) or hasattr(item, "real"):
                    values.append((int(item), attribute.replace("_", " ").title()))
        return tuple(sorted(set(values)))
    except Exception:
        return ()


def describe_params(pspecs):
    """Applique :func:`describe_param` à une liste, en éliminant les ``None``."""
    infos = []
    for pspec in pspecs or []:
        info = describe_param(pspec)
        if info is not None:
            infos.append(info)
    return infos


def step_params(step):
    """Réglages disponibles pour une étape de recette."""
    if step.kind == "special":
        return SPLIT_TONE_PARAMS
    if step.kind == "proc":
        return describe_params(procedure_arguments(step.proc))
    return describe_params(operation_properties(step.op))


#: Le virage partiel est écrit en Python : ses réglages sont déclarés ici.
SPLIT_TONE_PARAMS = (
    ParamInfo("shadows", "Teinte des ombres",
              "Couleur appliquée aux zones sombres.", "color",
              default="#1d3b57"),
    ParamInfo("highlights", "Teinte des hautes lumières",
              "Couleur appliquée aux zones claires.", "color",
              default="#e9cd93"),
    ParamInfo("amount", "Intensité", "0 à 100. Au-delà de 35, cela cesse de "
              "ressembler à une couleur de film.", "double", 0.0, 100.0, 25.0),
)
