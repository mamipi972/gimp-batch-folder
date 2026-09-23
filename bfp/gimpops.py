# -*- coding: utf-8 -*-
"""
bfp.gimpops — toutes les interactions avec l'API GIMP 3 / GEGL.

Principe de robustesse retenu dans ce module : l'API PDB de GIMP 3 a encore
bougé entre les versions 3.0.x, et les opérations GEGL disponibles dépendent
de la build.  Plutôt que de coder en dur des noms de propriétés qui peuvent
manquer, on **introspecte** systématiquement :

* une propriété est écrite seulement si ``config.find_property()`` la trouve ;
* une valeur numérique est ramenée dans les bornes déclarées par le
  ``GParamSpec`` (c'est ainsi que « qualité 90 » devient ``0.9`` pour le JPEG
  et reste ``90`` pour le WebP, sans supposer l'échelle) ;
* une opération GEGL absente est signalée dans le journal, puis ignorée ;
* les procédures d'export sont cherchées sous leur nom GIMP 3
  (``file-jpeg-export``) puis sous l'ancien (``file-jpeg-save``).

Résultat : une étape qui échoue dégrade le rendu, elle n'interrompt pas le lot.
"""

from __future__ import annotations

import math
import os

import gi

gi.require_version("Gimp", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gegl, Gimp, Gio, GObject  # noqa: E402

from .core import (  # noqa: E402
    BatchError,
    FORMATS_WITHOUT_ALPHA,
    anchor_offset,
    clamp,
    compute_crop,
    compute_resize,
    hex_to_rgba,
    parse_ratio,
    percent_or_pixels,
)

# ---------------------------------------------------------------------------
# Journalisation
# ---------------------------------------------------------------------------


class Reporter(object):
    """Collecte les messages du traitement (affichés et écrits dans un log)."""

    def __init__(self, echo=None):
        self.lines = []
        self.warnings = 0
        self.errors = 0
        self._echo = echo

    def _add(self, level, message):
        line = "%-7s %s" % (level, message)
        self.lines.append(line)
        if self._echo:
            try:
                self._echo(line)
            except Exception:
                pass
        return line

    def info(self, message):
        return self._add("", message)

    def warn(self, message):
        self.warnings += 1
        return self._add("ATTENTION", message)

    def error(self, message):
        self.errors += 1
        return self._add("ERREUR", message)

    def text(self):
        return "\n".join(self.lines)


_NULL_REPORTER = Reporter()


# ---------------------------------------------------------------------------
# Initialisation et petits ponts vers GEGL
# ---------------------------------------------------------------------------

_gegl_ready = False


def init_gegl():
    global _gegl_ready
    if not _gegl_ready:
        Gegl.init(None)
        _gegl_ready = True


def gegl_color(spec, default_alpha=1.0):
    """« #rrggbb » (ou tuple RVBA) → :class:`Gegl.Color`."""
    init_gegl()
    if isinstance(spec, Gegl.Color):
        return spec
    if isinstance(spec, (tuple, list)):
        rgba = tuple(spec) + (default_alpha,) * (4 - len(spec))
    else:
        rgba = hex_to_rgba(spec, default_alpha)
    color = Gegl.Color.new("black")
    color.set_rgba(rgba[0], rgba[1], rgba[2], rgba[3])
    return color


def gegl_has_operation(name):
    init_gegl()
    try:
        return bool(Gegl.has_operation(name))
    except Exception:
        # Certaines builds n'exposent pas has_operation : on laissera
        # DrawableFilter.new() échouer proprement à la place.
        return True


# ---------------------------------------------------------------------------
# Écriture « prudente » des propriétés de configuration
# ---------------------------------------------------------------------------

def _param_bounds(pspec):
    """Bornes numériques d'un GParamSpec, ou ``(None, None)``."""
    low = getattr(pspec, "minimum", None)
    high = getattr(pspec, "maximum", None)
    if isinstance(low, (int, float)) and isinstance(high, (int, float)):
        return low, high
    return None, None


def _coerce_value(pspec, value):
    """Adapte ``value`` au type réel attendu par la propriété."""
    value_type = pspec.value_type

    if value_type == GObject.TYPE_BOOLEAN:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "vrai", "yes", "oui", "on")
        return bool(value)

    if value_type in (GObject.TYPE_INT, GObject.TYPE_UINT,
                      GObject.TYPE_INT64, GObject.TYPE_UINT64,
                      GObject.TYPE_LONG, GObject.TYPE_ULONG):
        number = int(round(float(value)))
        low, high = _param_bounds(pspec)
        if low is not None:
            number = int(clamp(number, low, high))
        return number

    if value_type in (GObject.TYPE_DOUBLE, GObject.TYPE_FLOAT):
        number = float(value)
        low, high = _param_bounds(pspec)
        if low is not None:
            number = clamp(number, low, high)
        return number

    if value_type == GObject.TYPE_STRING:
        return str(value)

    # Couleurs GEGL : on accepte les chaînes « #rrggbb ».
    if value_type.name in ("GeglColor", "GimpColor") and isinstance(value, str):
        return gegl_color(value)

    return value


def set_props(config, props, reporter=_NULL_REPORTER, context=""):
    """Écrit les propriétés qui existent réellement ; signale les autres.

    Renvoie la liste des noms effectivement écrits.
    """
    written = []
    for name, value in props.items():
        if value is None:
            continue
        try:
            pspec = config.find_property(name)
        except Exception:
            pspec = None
        if pspec is None:
            reporter.warn("%s : propriété « %s » absente de cette version de "
                          "GIMP, ignorée." % (context or "réglage", name))
            continue
        try:
            config.set_property(name, _coerce_value(pspec, value))
            written.append(name)
        except Exception as exc:
            reporter.warn("%s : impossible de régler « %s » (%s)."
                          % (context or "réglage", name, exc))
    return written


def scale_percent_to_property(config, name, percent_value):
    """Traduit une valeur 0–100 dans l'échelle réelle de la propriété.

    Le JPEG attend une qualité entre 0 et 1, le WebP entre 0 et 100 ; plutôt
    que de deviner, on lit les bornes déclarées.
    """
    try:
        pspec = config.find_property(name)
    except Exception:
        pspec = None
    if pspec is None:
        return None
    low, high = _param_bounds(pspec)
    if high is None:
        return float(percent_value)
    if high <= 1.5:
        return clamp(float(percent_value) / 100.0, low or 0.0, high)
    return clamp(float(percent_value), low or 0.0, high)


# ---------------------------------------------------------------------------
# Appel de procédures PDB
# ---------------------------------------------------------------------------

def lookup_procedure(*names):
    """Première procédure existante parmi ``names``, ou ``(None, None)``."""
    pdb = Gimp.get_pdb()
    for name in names:
        try:
            procedure = pdb.lookup_procedure(name)
        except Exception:
            procedure = None
        if procedure is not None:
            return procedure, name
    return None, None


def run_procedure(names, props, reporter=_NULL_REPORTER, quality_props=None):
    """Exécute une procédure PDB en n'écrivant que les propriétés connues.

    ``quality_props`` est un dict ``{nom: valeur_en_pourcent}`` dont l'échelle
    est déduite du ``GParamSpec`` (voir :func:`scale_percent_to_property`).

    Renvoie ``(succès, valeurs_de_retour)``.
    """
    if isinstance(names, str):
        names = (names,)
    procedure, used = lookup_procedure(*names)
    if procedure is None:
        reporter.warn("Procédure introuvable : %s" % " / ".join(names))
        return False, None

    config = procedure.create_config()
    set_props(config, props, reporter, context=used)

    for name, percent in (quality_props or {}).items():
        scaled = scale_percent_to_property(config, name, percent)
        if scaled is None:
            continue
        set_props(config, {name: scaled}, reporter, context=used)

    try:
        result = procedure.run(config)
    except Exception as exc:
        reporter.error("Échec de %s : %s" % (used, exc))
        return False, None

    try:
        status = result.index(0)
    except Exception:
        return True, result

    if status == Gimp.PDBStatusType.SUCCESS:
        return True, result

    detail = ""
    try:
        detail = Gimp.get_pdb().get_last_error() or ""
    except Exception:
        pass
    reporter.error("%s a échoué (%s) %s" % (used, status, detail).strip())
    return False, result


# ---------------------------------------------------------------------------
# Chargement
# ---------------------------------------------------------------------------

def quiet_messages():
    """Envoie les messages de GIMP vers la console d'erreurs.

    Sans cela, une seule opération refusée par GIMP ouvre une fenêtre modale
    — multipliée par le nombre d'images du lot. Renvoie ``True`` si le
    basculement a eu lieu, pour savoir s'il faut le défaire.
    """
    kinds = getattr(Gimp, "MessageHandlerType", None)
    if kinds is None:
        return False
    for attribute in ("ERROR_CONSOLE", "CONSOLE"):
        handler = getattr(kinds, attribute, None)
        if handler is None:
            continue
        try:
            Gimp.message_set_handler(handler)
            return True
        except Exception:
            continue
    return False


def restore_messages():
    """Rétablit les fenêtres de message de GIMP."""
    kinds = getattr(Gimp, "MessageHandlerType", None)
    handler = getattr(kinds, "MESSAGE_BOX", None) if kinds else None
    if handler is None:
        return
    try:
        Gimp.message_set_handler(handler)
    except Exception:
        pass


def load_image(path, reporter=_NULL_REPORTER):
    """Ouvre un fichier ; renvoie une :class:`Gimp.Image` ou ``None``."""
    gio_file = Gio.File.new_for_path(path)
    try:
        image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, gio_file)
    except Exception as exc:
        reporter.error("Ouverture impossible de %s : %s" % (path, exc))
        return None
    if image is None:
        reporter.error("Ouverture impossible de %s." % path)
        return None
    try:
        image.undo_disable()
    except Exception:
        pass
    return image


def discard_image(image):
    """Ferme une image sans l'afficher."""
    if image is None:
        return
    try:
        image.undo_enable()
    except Exception:
        pass
    try:
        image.delete()
    except Exception:
        pass


def duplicate_image(image, reporter=_NULL_REPORTER):
    """Copie de travail d'une image (renvoie ``None`` en cas d'échec)."""
    try:
        copy = image.duplicate()
    except Exception as exc:
        reporter.warn("Duplication de l'image impossible : %s" % exc)
        return None
    if copy is None:
        return None
    try:
        copy.undo_disable()
    except Exception:
        pass
    return copy


def top_drawables(image):
    """Calques de premier niveau, du haut vers le bas."""
    try:
        return list(image.get_layers())
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Transformations géométriques
# ---------------------------------------------------------------------------

_ROTATIONS = {
    90: "DEGREES90",
    180: "DEGREES180",
    270: "DEGREES270",
}


def apply_rotation(image, degrees=0, flip_h=False, flip_v=False,
                   reporter=_NULL_REPORTER):
    """Rotation par quarts de tour puis miroirs."""
    degrees = int(degrees) % 360
    if degrees in _ROTATIONS:
        rotation = getattr(Gimp.RotationType, _ROTATIONS[degrees], None)
        if rotation is None:
            reporter.warn("Rotation %d° non gérée par cette version." % degrees)
        else:
            try:
                image.rotate(rotation)
            except Exception as exc:
                reporter.warn("Rotation impossible : %s" % exc)
    elif degrees:
        reporter.warn("Seules les rotations de 90, 180 et 270° sont gérées "
                      "(%d° ignoré)." % degrees)

    for enabled, orientation in ((flip_h, "HORIZONTAL"), (flip_v, "VERTICAL")):
        if not enabled:
            continue
        value = getattr(Gimp.OrientationType, orientation, None)
        if value is None:
            reporter.warn("Miroir %s non géré." % orientation)
            continue
        try:
            image.flip(value)
        except Exception as exc:
            reporter.warn("Miroir impossible : %s" % exc)


def apply_autocrop(image, reporter=_NULL_REPORTER):
    """Détourage automatique des bords uniformes."""
    drawables = top_drawables(image)
    drawable = drawables[0] if drawables else None
    ok, _ = run_procedure(
        ("plug-in-autocrop",),
        {"run-mode": Gimp.RunMode.NONINTERACTIVE,
         "image": image,
         "drawable": drawable},
        reporter)
    if not ok:
        reporter.warn("Détourage automatique indisponible, étape ignorée.")
    return ok


def apply_crop(image, settings, reporter=_NULL_REPORTER):
    """Recadrage selon les réglages (manuel, rapport, marges ou automatique)."""
    mode = settings.get("crop_mode", "ratio")
    if mode == "autocrop":
        return apply_autocrop(image, reporter)

    src_w = image.get_width()
    src_h = image.get_height()
    ratio = 1.0
    if mode == "ratio":
        ratio = parse_ratio(settings.get("crop_ratio", "1:1"))

    rect = compute_crop(
        src_w, src_h, mode,
        x=settings.get("crop_x", 0), y=settings.get("crop_y", 0),
        width=settings.get("crop_width", 0),
        height=settings.get("crop_height", 0),
        ratio=ratio, anchor=settings.get("crop_anchor", "center"),
        margin_left=settings.get("crop_margin_left", 0),
        margin_right=settings.get("crop_margin_right", 0),
        margin_top=settings.get("crop_margin_top", 0),
        margin_bottom=settings.get("crop_margin_bottom", 0))
    if rect is None:
        return False

    x, y, width, height = rect
    if (x, y, width, height) == (0, 0, src_w, src_h):
        return False
    try:
        image.crop(width, height, x, y)
    except Exception as exc:
        reporter.warn("Recadrage impossible : %s" % exc)
        return False
    return True


_INTERPOLATIONS = {
    "none": "NONE",
    "linear": "LINEAR",
    "cubic": "CUBIC",
    "nohalo": "NOHALO",
    "lohalo": "LOHALO",
}


def set_interpolation(name, reporter=_NULL_REPORTER):
    value = getattr(Gimp.InterpolationType,
                    _INTERPOLATIONS.get(str(name).lower(), "CUBIC"), None)
    if value is None:
        return
    try:
        Gimp.context_set_interpolation(value)
    except Exception as exc:
        reporter.warn("Interpolation non réglable : %s" % exc)


def apply_resize(image, settings, reporter=_NULL_REPORTER):
    """Mise à l'échelle, avec recadrage centré en mode « remplir »."""
    src_w = image.get_width()
    src_h = image.get_height()

    width, height, needs_cover_crop = compute_resize(
        src_w, src_h,
        settings.get("resize_mode", "fit"),
        settings.get("resize_width", 0),
        settings.get("resize_height", 0),
        settings.get("resize_percent", 100.0),
        settings.get("resize_allow_upscale", False))

    set_interpolation(settings.get("resize_interpolation", "cubic"), reporter)

    if (width, height) != (src_w, src_h):
        try:
            image.scale(width, height)
        except Exception as exc:
            reporter.warn("Mise à l'échelle impossible : %s" % exc)
            return False

    if needs_cover_crop:
        target_w = int(settings.get("resize_width", width))
        target_h = int(settings.get("resize_height", height))
        target_w = int(clamp(target_w, 1, image.get_width()))
        target_h = int(clamp(target_h, 1, image.get_height()))
        x, y = anchor_offset(image.get_width(), image.get_height(),
                             target_w, target_h, "center")
        try:
            image.crop(target_w, target_h, max(0, x), max(0, y))
        except Exception as exc:
            reporter.warn("Recadrage de remplissage impossible : %s" % exc)
    return True


# ---------------------------------------------------------------------------
# Couleur
# ---------------------------------------------------------------------------

def apply_color_mode(image, mode="rgb", reporter=_NULL_REPORTER):
    """Conversion en niveaux de gris ou retour en RVB."""
    try:
        base = image.get_base_type()
    except Exception:
        base = None
    try:
        if mode == "grayscale":
            if base != Gimp.ImageBaseType.GRAY:
                image.convert_grayscale()
        else:
            if base != Gimp.ImageBaseType.RGB:
                image.convert_rgb()
    except Exception as exc:
        reporter.warn("Conversion colorimétrique impossible : %s" % exc)


def flatten_image(image, background="#ffffff", reporter=_NULL_REPORTER):
    """Aplatit l'image sur la couleur de fond indiquée."""
    try:
        Gimp.context_set_background(gegl_color(background))
    except Exception as exc:
        reporter.warn("Couleur de fond non appliquée : %s" % exc)
    try:
        image.flatten()
        return True
    except Exception as exc:
        reporter.warn("Aplatissement impossible : %s" % exc)
        return False


def merge_visible(image, reporter=_NULL_REPORTER):
    """Fusionne les calques visibles en conservant la transparence."""
    try:
        merge_type = getattr(Gimp.MergeType, "CLIP_TO_IMAGE",
                             getattr(Gimp.MergeType, "EXPAND_AS_NECESSARY"))
        image.merge_visible_layers(merge_type)
        return True
    except Exception as exc:
        reporter.warn("Fusion des calques visibles impossible : %s" % exc)
        return False


# ---------------------------------------------------------------------------
# Filtres GEGL (recettes de look)
# ---------------------------------------------------------------------------

_BLEND_MODES = {
    "replace": "REPLACE",
    "normal": "NORMAL",
    "multiply": "MULTIPLY",
    "screen": "SCREEN",
    "overlay": "OVERLAY",
    "softlight": "SOFTLIGHT",
    "soft-light": "SOFTLIGHT",
    "hardlight": "HARDLIGHT",
    "difference": "DIFFERENCE",
    "addition": "ADDITION",
    "subtract": "SUBTRACT",
    "darken-only": "DARKEN_ONLY",
    "lighten-only": "LIGHTEN_ONLY",
}


def apply_gegl_step(drawable, operation, params=None, opacity=100.0,
                    blend="replace", reporter=_NULL_REPORTER):
    """Applique une opération GEGL à un calque, de façon destructive."""
    if not gegl_has_operation(operation):
        reporter.warn("Opération GEGL « %s » absente de cette installation, "
                      "étape ignorée." % operation)
        return False

    # Une fusion de calques (virage partiel, dosage) remplace le calque par un
    # nouvel objet : travailler sur l'ancien ferait échouer
    # gimp-drawable-filter-new avec un identifiant invalide.
    if not drawable_is_valid(drawable):
        reporter.warn("Étape « %s » sautée : le calque visé n'existe plus "
                      "(il a été fusionné par une étape précédente)."
                      % operation)
        return False

    try:
        drawable_filter = Gimp.DrawableFilter.new(drawable, operation, "")
    except Exception as exc:
        reporter.warn("Filtre « %s » non créé : %s" % (operation, exc))
        return False

    config = drawable_filter.get_config()
    if params:
        set_props(config, params, reporter, context=operation)

    mode_name = _BLEND_MODES.get(str(blend).lower(), "REPLACE")
    mode = getattr(Gimp.LayerMode, mode_name, None)
    if mode is not None:
        try:
            drawable_filter.set_blend_mode(mode)
        except Exception:
            pass
    try:
        drawable_filter.set_opacity(clamp(float(opacity), 0.0, 100.0) / 100.0)
    except Exception:
        pass

    try:
        drawable_filter.update()
    except Exception:
        pass

    try:
        drawable.merge_filter(drawable_filter)
    except Exception as exc:
        reporter.warn("Application de « %s » impossible : %s" % (operation, exc))
        return False
    return True


def drawable_is_valid(drawable):
    """``True`` si le calque existe encore côté GIMP."""
    if drawable is None:
        return False
    try:
        return bool(drawable.is_valid())
    except Exception:
        # Les versions qui n'exposent pas is_valid() : on fait confiance.
        return True


def _merge_down(image, layer, reporter=_NULL_REPORTER):
    """Fusionne ``layer`` dans le calque du dessous et renvoie le résultat.

    ``gimp_image_merge_down()`` détruit les deux calques d'origine et en crée
    un nouveau : c'est cet objet-là qu'il faut réutiliser ensuite. Oublier de
    le faire produit l'erreur « gimp-drawable-filter-new a été appelée avec un
    ID erroné pour le paramètre drawable ».
    """
    merge_type = getattr(Gimp.MergeType, "CLIP_TO_IMAGE",
                         getattr(Gimp.MergeType, "EXPAND_AS_NECESSARY"))
    merged = image.merge_down(layer, merge_type)
    if merged is not None:
        return merged
    # Repli : certaines versions ne renvoient rien. On reprend le calque qui
    # occupe désormais la position visée.
    layers = top_drawables(image)
    return layers[0] if layers else None


#: Arguments qu'une étape « proc » n'a pas à déclarer : le greffon les
#: remplit lui-même s'ils existent dans la signature de la procédure.
_AUTO_ARGUMENTS = ("run-mode", "image", "drawable", "drawables",
                   "num-drawables")


def apply_proc_step(image, drawable, proc_name, params=None,
                    reporter=_NULL_REPORTER):
    """Exécute une procédure du PDB sur un calque, comme étape de recette.

    Contrairement à une opération GEGL, une procédure peut faire à peu près
    n'importe quoi à l'image — ajouter des calques, l'aplatir, ouvrir une
    fenêtre. On lui passe donc le mode non interactif quand elle l'accepte, et
    l'appelant doit revalider le calque après coup.
    """
    procedure, used = lookup_procedure(proc_name)
    if procedure is None:
        reporter.warn("Procédure « %s » introuvable : étape ignorée."
                      % proc_name)
        return False

    try:
        argument_names = set(spec.name for spec in
                             (procedure.get_arguments() or []))
    except Exception:
        argument_names = set()

    props = dict(params or {})
    available = {
        "run-mode": Gimp.RunMode.NONINTERACTIVE,
        "image": image,
        "drawable": drawable,
        "drawables": [drawable],
        "num-drawables": 1,
    }
    for name in _AUTO_ARGUMENTS:
        if name in argument_names and name not in props:
            props[name] = available[name]

    config = procedure.create_config()
    set_props(config, props, reporter, context=used)

    try:
        result = procedure.run(config)
    except Exception as exc:
        reporter.warn("Étape « %s » impossible : %s" % (used, exc))
        return False

    try:
        status = result.index(0)
    except Exception:
        return True
    if status == Gimp.PDBStatusType.SUCCESS:
        return True

    detail = ""
    try:
        detail = Gimp.get_pdb().get_last_error() or ""
    except Exception:
        pass
    reporter.warn(("Étape « %s » a échoué (%s) %s"
                   % (used, status, detail)).strip())
    return False


def _invert_drawable(drawable, reporter=_NULL_REPORTER):
    for operation in ("gegl:invert-gamma", "gegl:invert-linear", "gegl:invert"):
        if gegl_has_operation(operation) and apply_gegl_step(
                drawable, operation, reporter=reporter):
            return True
    return False


def apply_split_tone(image, layer, shadows="#000040", highlights="#ffe0a0",
                     amount=25.0, reporter=_NULL_REPORTER):
    """Virage partiel : teinte les ombres et les hautes lumières séparément.

    Technique : pour chaque tonalité, on duplique le calque, on en tire un
    masque « copie en niveaux de gris » (inversé pour les ombres), on remplit
    le calque de la couleur voulue et on le fusionne en mode lumière douce.

    Renvoie **le calque résultant** — pas un booléen : après les fusions,
    l'appelant doit impérativement continuer sur ce nouvel objet.
    """
    amount = clamp(float(amount), 0.0, 100.0)
    if amount <= 0 or not drawable_is_valid(layer):
        return layer

    soft_light = getattr(Gimp.LayerMode, "SOFTLIGHT",
                         getattr(Gimp.LayerMode, "NORMAL"))
    copy_mask = getattr(Gimp.AddMaskType, "COPY", None)
    if copy_mask is None:
        reporter.warn("Masques de calque indisponibles : virage partiel ignoré.")
        return layer

    for color_hex, invert in ((highlights, False), (shadows, True)):
        if not color_hex or not drawable_is_valid(layer):
            continue
        tint = None
        try:
            tint = layer.copy()
            image.insert_layer(tint, layer.get_parent(),
                               image.get_item_position(layer))

            mask = tint.create_mask(copy_mask)
            tint.add_mask(mask)
            if invert:
                _invert_drawable(mask, reporter)

            Gimp.context_set_foreground(gegl_color(color_hex))
            tint.edit_fill(Gimp.FillType.FOREGROUND)

            tint.set_mode(soft_light)
            tint.set_opacity(amount)

            merged = _merge_down(image, tint, reporter)
            if merged is not None:
                layer = merged
        except Exception as exc:
            reporter.warn("Virage partiel (%s) impossible : %s"
                          % ("ombres" if invert else "hautes lumières", exc))
            try:
                if drawable_is_valid(tint):
                    image.remove_layer(tint)
            except Exception:
                pass
    return layer


def apply_look(image, look, opacity=100.0, reporter=_NULL_REPORTER):
    """Applique une recette de look à tous les calques de premier niveau."""
    if look is None:
        return False

    opacity = clamp(float(opacity), 0.0, 100.0)
    if opacity <= 0:
        return False

    any_applied = False
    layer_count = len(top_drawables(image))

    for index in range(layer_count):
        layers = top_drawables(image)
        if index >= len(layers):
            break
        base = layers[index]

        # Dosage : la recette est appliquée sur une copie dont on règle
        # ensuite l'opacité, puis qu'on fusionne dans l'original.
        partial = opacity < 100.0
        target = base
        if partial:
            try:
                copy = base.copy()
                image.insert_layer(copy, base.get_parent(),
                                   image.get_item_position(base))
                target = copy
            except Exception as exc:
                reporter.warn("Dosage du look impossible (%s) : "
                              "appliqué à 100 %%." % exc)
                partial = False
                target = base

        for step in look.steps:
            kind = getattr(step, "kind", "op")

            if kind == "special" and step.special == "split-tone":
                params = step.params
                before = target
                target = apply_split_tone(
                    image, target,
                    shadows=params.get("shadows", "#000040"),
                    highlights=params.get("highlights", "#ffe0a0"),
                    amount=params.get("amount", 25.0),
                    reporter=reporter)
                if target is not before:
                    any_applied = True
                continue

            if kind == "proc":
                if apply_proc_step(image, target, step.proc, step.params,
                                   reporter):
                    any_applied = True
                # Une procédure peut avoir remplacé ou fusionné des calques :
                # on revalide la cible avant l'étape suivante.
                if not drawable_is_valid(target):
                    layers = top_drawables(image)
                    if not layers:
                        break
                    target = layers[0]
                    reporter.warn(
                        "L'étape « %s » a modifié la structure des calques ; "
                        "la suite de la recette repart du calque du dessus."
                        % step.proc)
                continue

            if apply_gegl_step(target, step.op, step.params, step.opacity,
                               step.blend, reporter):
                any_applied = True

        if partial and drawable_is_valid(target) and target is not base:
            try:
                target.set_opacity(opacity)
                _merge_down(image, target, reporter)
            except Exception as exc:
                reporter.warn("Fusion du look impossible : %s" % exc)

    return any_applied


# ---------------------------------------------------------------------------
# Filigranes
# ---------------------------------------------------------------------------

def _resolve_font(name, reporter=_NULL_REPORTER):
    """Retrouve une :class:`Gimp.Font` par son nom, avec repli sur le contexte."""
    if name:
        for getter in ("get_by_name",):
            try:
                font = getattr(Gimp.Font, getter)(name)
            except Exception:
                font = None
            if font is not None:
                return font
        reporter.warn("Police « %s » introuvable : police courante utilisée."
                      % name)
    try:
        return Gimp.context_get_font()
    except Exception:
        return None


def add_text_watermark(image, settings, text, reporter=_NULL_REPORTER):
    """Ajoute un filigrane texte et renvoie le calque créé (ou ``None``)."""
    text = (text or "").strip()
    if not text:
        return None

    img_w = image.get_width()
    img_h = image.get_height()
    reference = min(img_w, img_h)

    size = percent_or_pixels(settings.get("wm_text_size", 5.0), reference,
                             settings.get("wm_text_size_is_percent", True))
    size = max(4, int(size))

    font = _resolve_font(settings.get("wm_text_font"), reporter)

    try:
        Gimp.context_set_foreground(gegl_color(settings.get("wm_text_color",
                                                            "#ffffff")))
    except Exception as exc:
        reporter.warn("Couleur du filigrane non appliquée : %s" % exc)

    try:
        layer = Gimp.text_font(image, None, 0, 0, text, -1, True,
                               float(size), font)
    except Exception as exc:
        reporter.warn("Filigrane texte impossible : %s" % exc)
        return None
    if layer is None:
        reporter.warn("Filigrane texte : GIMP n'a pas créé de calque.")
        return None

    angle = float(settings.get("wm_text_angle", 0.0) or 0.0)
    if abs(angle) > 0.01:
        try:
            resize_mode = getattr(Gimp.TransformResize, "ADJUST", None)
            if resize_mode is not None:
                Gimp.context_set_transform_resize(resize_mode)
            rotated = layer.transform_rotate(math.radians(angle), True, 0, 0)
            layer = rotated or layer
        except Exception as exc:
            reporter.warn("Rotation du filigrane impossible : %s" % exc)

    margin = percent_or_pixels(settings.get("wm_text_margin", 2.0), reference,
                               settings.get("wm_text_margin_is_percent", True))
    x, y = anchor_offset(img_w, img_h, layer.get_width(), layer.get_height(),
                         settings.get("wm_text_anchor", "bottom-right"),
                         margin, margin)
    try:
        layer.set_offsets(int(x), int(y))
        layer.set_opacity(clamp(float(settings.get("wm_text_opacity", 60.0)),
                                0.0, 100.0))
    except Exception as exc:
        reporter.warn("Placement du filigrane texte : %s" % exc)
    return layer


def add_image_watermark(image, settings, reporter=_NULL_REPORTER):
    """Superpose un fichier image (PNG à fond transparent, typiquement)."""
    path = settings.get("wm_image_path") or ""
    if not path or not os.path.isfile(path):
        reporter.warn("Fichier de filigrane introuvable : %s" % path)
        return None

    try:
        layer = Gimp.file_load_layer(Gimp.RunMode.NONINTERACTIVE, image,
                                     Gio.File.new_for_path(path))
    except Exception as exc:
        reporter.warn("Filigrane image non chargé : %s" % exc)
        return None
    if layer is None:
        reporter.warn("Filigrane image non chargé : %s" % path)
        return None

    try:
        image.insert_layer(layer, None, 0)
    except Exception as exc:
        reporter.warn("Insertion du filigrane impossible : %s" % exc)
        return None

    img_w = image.get_width()
    img_h = image.get_height()

    scale = float(settings.get("wm_image_scale", 20.0) or 0.0)
    if scale > 0:
        target_w = max(1, int(round(img_w * scale / 100.0)))
        source_w = max(1, layer.get_width())
        target_h = max(1, int(round(layer.get_height() * target_w / source_w)))
        try:
            layer.scale(target_w, target_h, False)
        except Exception as exc:
            reporter.warn("Mise à l'échelle du filigrane : %s" % exc)

    reference = min(img_w, img_h)
    margin = percent_or_pixels(settings.get("wm_image_margin", 2.0), reference,
                               settings.get("wm_image_margin_is_percent", True))
    x, y = anchor_offset(img_w, img_h, layer.get_width(), layer.get_height(),
                         settings.get("wm_image_anchor", "bottom-right"),
                         margin, margin)
    try:
        layer.set_offsets(int(x), int(y))
        layer.set_opacity(clamp(float(settings.get("wm_image_opacity", 60.0)),
                                0.0, 100.0))
    except Exception as exc:
        reporter.warn("Placement du filigrane image : %s" % exc)
    return layer


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

#: Nom GIMP 3 d'abord, ancien nom GIMP 2.10 ensuite.
EXPORT_PROCEDURES = {
    "jpeg": ("file-jpeg-export", "file-jpeg-save"),
    "png": ("file-png-export", "file-png-save"),
    "webp": ("file-webp-export", "file-webp-save"),
    "tiff": ("file-tiff-export", "file-tiff-save"),
    "bmp": ("file-bmp-export", "file-bmp-save"),
}

#: Propriétés de métadonnées à désactiver quand « retirer les métadonnées »
#: est coché.  Les absentes sont simplement ignorées.
_METADATA_PROPS = ("include-exif", "include-xmp", "include-iptc",
                   "include-thumbnail", "include-color-profile",
                   "save-exif", "save-xmp", "save-iptc", "save-thumbnail",
                   "save-color-profile")


def _format_props(output_format, settings):
    """Propriétés spécifiques au format, hors qualité (échelle à déduire)."""
    props = {}
    if output_format == "jpeg":
        props["progressive"] = settings.get("jpeg_progressive", True)
        props["optimize"] = True
    elif output_format == "png":
        props["compression"] = int(settings.get("png_compression", 9))
        props["interlaced"] = False
    elif output_format == "webp":
        props["lossless"] = settings.get("webp_lossless", False)
    elif output_format == "tiff":
        props["compression"] = settings.get("tiff_compression", "lzw")
    return props


def _quality_props(output_format, settings):
    if output_format == "jpeg":
        return {"quality": float(settings.get("jpeg_quality", 90.0))}
    if output_format == "webp":
        return {"quality": float(settings.get("webp_quality", 90.0))}
    return {}


def strip_metadata(image, reporter=_NULL_REPORTER):
    """Retire les métadonnées portées par l'image."""
    try:
        image.set_metadata(None)
    except Exception as exc:
        reporter.warn("Métadonnées non retirées : %s" % exc)


def export_image(image, path, output_format, settings,
                 reporter=_NULL_REPORTER):
    """Écrit l'image dans ``path`` au format demandé.

    Aplatit si le format ne gère pas la transparence.  Renvoie un booléen.
    """
    directory = os.path.dirname(os.path.abspath(path))
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        reporter.error("Dossier de sortie inaccessible (%s) : %s"
                       % (directory, exc))
        return False

    if output_format in FORMATS_WITHOUT_ALPHA:
        flatten_image(image, settings.get("background_color", "#ffffff"),
                      reporter)
    elif settings.get("flatten"):
        flatten_image(image, settings.get("background_color", "#ffffff"),
                      reporter)

    if settings.get("strip_metadata"):
        strip_metadata(image, reporter)

    gio_file = Gio.File.new_for_path(path)

    if output_format == "xcf" or output_format not in EXPORT_PROCEDURES:
        try:
            image.set_file(gio_file)
        except Exception:
            pass
        try:
            ok = Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, image, gio_file,
                                None)
        except Exception as exc:
            reporter.error("Enregistrement impossible de %s : %s" % (path, exc))
            return False
        if not ok:
            reporter.error("Enregistrement refusé par GIMP : %s" % path)
        return bool(ok)

    props = {"run-mode": Gimp.RunMode.NONINTERACTIVE,
             "image": image,
             "file": gio_file}
    props.update(_format_props(output_format, settings))
    if settings.get("strip_metadata"):
        for name in _METADATA_PROPS:
            props.setdefault(name, False)

    ok, _ = run_procedure(EXPORT_PROCEDURES[output_format], props, reporter,
                          quality_props=_quality_props(output_format, settings))

    if not ok:
        # Dernier recours : laisser GIMP choisir par l'extension.
        reporter.warn("Export spécialisé indisponible pour %s : repli sur "
                      "l'enregistrement générique." % output_format)
        try:
            return bool(Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, image,
                                       gio_file, None))
        except Exception as exc:
            reporter.error("Enregistrement impossible de %s : %s" % (path, exc))
            return False
    return True


def render_preview(source_path, output_path, look=None, opacity=100.0,
                   max_size=480, reporter=_NULL_REPORTER):
    """Fabrique un PNG d'aperçu : image réduite, recette appliquée.

    La réduction a lieu **avant** les filtres : un aperçu doit être instantané,
    et l'on regarde une teinte ou un vignettage, pas du grain au pixel près.
    Renvoie ``(largeur, hauteur)`` ou ``None``.
    """
    image = load_image(source_path, reporter)
    if image is None:
        return None
    try:
        width, height, _ = compute_resize(
            image.get_width(), image.get_height(), "fit",
            int(max_size), int(max_size), allow_upscale=False)
        set_interpolation("cubic", reporter)
        if (width, height) != (image.get_width(), image.get_height()):
            image.scale(width, height)

        if look is not None:
            apply_look(image, look, opacity, reporter)

        settings = {"png_compression": 1, "flatten": False,
                    "background_color": "#ffffff"}
        if not export_image(image, output_path, "png", settings, reporter):
            return None
        return image.get_width(), image.get_height()
    except Exception as exc:
        reporter.warn("Aperçu impossible : %s" % exc)
        return None
    finally:
        discard_image(image)


def image_from_layer(source_image, layer, crop_to_layer=False,
                     reporter=_NULL_REPORTER):
    """Crée une image autonome contenant un seul calque."""
    try:
        if crop_to_layer:
            width, height = layer.get_width(), layer.get_height()
        else:
            width, height = source_image.get_width(), source_image.get_height()
        new_image = Gimp.Image.new(max(1, width), max(1, height),
                                   source_image.get_base_type())
        new_image.undo_disable()
        copy = Gimp.Layer.new_from_drawable(layer, new_image)
        new_image.insert_layer(copy, None, 0)
        copy.set_opacity(100.0)
        copy.set_visible(True)
        if crop_to_layer:
            copy.set_offsets(0, 0)
        else:
            ok, offx, offy = layer.get_offsets()
            copy.set_offsets(offx if ok else 0, offy if ok else 0)
        return new_image
    except Exception as exc:
        reporter.error("Extraction du calque impossible : %s" % exc)
        return None
