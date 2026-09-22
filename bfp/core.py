# -*- coding: utf-8 -*-
"""
bfp.core — logique pure du greffon « Traitement par lots d'un dossier ».

Ce module ne dépend d'AUCUNE API GIMP / GTK / GEGL.  Tout ce qui est calcul
géométrique, nommage de fichiers, parcours de dossiers et validation de
réglages vit ici, ce qui le rend testable sans GIMP (voir tests/).
"""

from __future__ import annotations

import datetime
import os
import re
import string

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

#: Extensions reconnues en entrée (minuscules, sans point).
DEFAULT_EXTENSIONS = (
    "jpg", "jpeg", "jpe", "png", "tif", "tiff", "bmp", "webp",
    "gif", "tga", "xcf", "psd", "ppm", "pgm", "pbm", "pnm", "ico", "avif",
)

#: Ancrages sur une grille 3x3.
ANCHORS = (
    "top-left", "top", "top-right",
    "left", "center", "right",
    "bottom-left", "bottom", "bottom-right",
)

#: Modes de redimensionnement.
RESIZE_MODES = ("fit", "fill", "exact", "percent", "width", "height")

#: Modes de recadrage.
CROP_MODES = ("manual", "ratio", "margins", "autocrop")

#: Politiques en cas de fichier de sortie existant.
OVERWRITE_POLICIES = ("overwrite", "skip", "rename")

#: Formats de sortie gérés.  « keep » = conserver le format d'origine.
OUTPUT_FORMATS = ("keep", "jpeg", "png", "webp", "tiff", "bmp", "xcf")

#: Extension canonique associée à chaque format.
FORMAT_EXTENSION = {
    "jpeg": ".jpg",
    "png": ".png",
    "webp": ".webp",
    "tiff": ".tif",
    "bmp": ".bmp",
    "xcf": ".xcf",
}

#: Formats sans canal alpha : il faut aplatir sur une couleur de fond.
FORMATS_WITHOUT_ALPHA = frozenset({"jpeg", "bmp"})


class BatchError(Exception):
    """Erreur métier, affichable telle quelle à l'utilisateur."""


# ---------------------------------------------------------------------------
# Petits utilitaires
# ---------------------------------------------------------------------------

def clamp(value, low, high):
    """Ramène ``value`` dans l'intervalle [low, high]."""
    if low > high:
        low, high = high, low
    return max(low, min(high, value))


def parse_ratio(text):
    """Convertit « 16:9 », « 16/9 », « 1.777 » en float.

    Lève :class:`BatchError` si la chaîne est inexploitable.
    """
    if text is None:
        raise BatchError("Rapport d'aspect vide.")
    raw = str(text).strip().replace(",", ".")
    if not raw:
        raise BatchError("Rapport d'aspect vide.")
    match = re.match(r"^\s*([0-9]*\.?[0-9]+)\s*[:/x×]\s*([0-9]*\.?[0-9]+)\s*$", raw)
    if match:
        width = float(match.group(1))
        height = float(match.group(2))
        if height <= 0 or width <= 0:
            raise BatchError("Rapport d'aspect invalide : %s" % text)
        return width / height
    try:
        value = float(raw)
    except ValueError:
        raise BatchError("Rapport d'aspect invalide : %s" % text)
    if value <= 0:
        raise BatchError("Rapport d'aspect invalide : %s" % text)
    return value


def hex_to_rgba(text, default_alpha=1.0):
    """« #rrggbb », « #rgb », « #rrggbbaa » → tuple (r, g, b, a) dans [0, 1]."""
    if not text:
        raise BatchError("Couleur vide.")
    raw = str(text).strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) == 6:
        raw += "%02x" % int(round(clamp(default_alpha, 0.0, 1.0) * 255))
    if len(raw) != 8 or not re.match(r"^[0-9a-fA-F]{8}$", raw):
        raise BatchError("Couleur invalide : %s" % text)
    return tuple(int(raw[i:i + 2], 16) / 255.0 for i in (0, 2, 4, 6))


def rgba_to_hex(rgba):
    """Tuple (r, g, b[, a]) dans [0, 1] → « #rrggbb » ou « #rrggbbaa »."""
    values = list(rgba)
    if len(values) == 3:
        values.append(1.0)
    ints = [int(round(clamp(v, 0.0, 1.0) * 255)) for v in values[:4]]
    if ints[3] == 255:
        return "#%02x%02x%02x" % tuple(ints[:3])
    return "#%02x%02x%02x%02x" % tuple(ints)


def percent_or_pixels(value, reference, is_percent):
    """Renvoie une valeur en pixels, que ``value`` soit un % ou des pixels."""
    if is_percent:
        return int(round(reference * float(value) / 100.0))
    return int(round(float(value)))


# ---------------------------------------------------------------------------
# Géométrie : redimensionnement
# ---------------------------------------------------------------------------

def compute_resize(src_w, src_h, mode="fit", target_w=0, target_h=0,
                   percent=100.0, allow_upscale=False):
    """Calcule les dimensions cibles d'une image.

    Renvoie ``(width, height, needs_cover_crop)``.  ``needs_cover_crop`` n'est
    vrai qu'en mode ``fill`` : l'appelant doit alors recadrer au centre après
    la mise à l'échelle.

    Les modes :

    ``fit``      tient dans la boîte ``target_w`` × ``target_h`` (ratio gardé)
    ``fill``     couvre la boîte puis recadrage centré (ratio gardé)
    ``exact``    dimensions imposées, ratio non conservé
    ``percent``  mise à l'échelle proportionnelle
    ``width``    largeur imposée, hauteur déduite
    ``height``   hauteur imposée, largeur déduite
    """
    if src_w <= 0 or src_h <= 0:
        raise BatchError("Dimensions source invalides : %sx%s" % (src_w, src_h))
    if mode not in RESIZE_MODES:
        raise BatchError("Mode de redimensionnement inconnu : %s" % mode)

    src_w = int(src_w)
    src_h = int(src_h)

    if mode == "percent":
        factor = float(percent) / 100.0
        if factor <= 0:
            raise BatchError("Pourcentage de redimensionnement invalide.")
        if not allow_upscale:
            factor = min(factor, 1.0)
        return max(1, int(round(src_w * factor))), max(1, int(round(src_h * factor))), False

    if mode == "width":
        if target_w <= 0:
            raise BatchError("Largeur cible invalide.")
        factor = float(target_w) / src_w
        if not allow_upscale:
            factor = min(factor, 1.0)
        return max(1, int(round(src_w * factor))), max(1, int(round(src_h * factor))), False

    if mode == "height":
        if target_h <= 0:
            raise BatchError("Hauteur cible invalide.")
        factor = float(target_h) / src_h
        if not allow_upscale:
            factor = min(factor, 1.0)
        return max(1, int(round(src_w * factor))), max(1, int(round(src_h * factor))), False

    if target_w <= 0 or target_h <= 0:
        raise BatchError("Dimensions cibles invalides : %sx%s" % (target_w, target_h))

    if mode == "exact":
        if not allow_upscale:
            return (max(1, min(int(target_w), src_w)),
                    max(1, min(int(target_h), src_h)), False)
        return max(1, int(target_w)), max(1, int(target_h)), False

    factor_w = float(target_w) / src_w
    factor_h = float(target_h) / src_h
    factor = min(factor_w, factor_h) if mode == "fit" else max(factor_w, factor_h)
    if not allow_upscale:
        factor = min(factor, 1.0)

    width = max(1, int(round(src_w * factor)))
    height = max(1, int(round(src_h * factor)))
    return width, height, (mode == "fill")


# ---------------------------------------------------------------------------
# Géométrie : recadrage
# ---------------------------------------------------------------------------

def anchor_offset(outer_w, outer_h, inner_w, inner_h, anchor="center",
                  margin_x=0, margin_y=0):
    """Position (x, y) d'un rectangle ``inner`` ancré dans ``outer``.

    ``margin_x`` / ``margin_y`` éloignent le rectangle du bord concerné ; ils
    sont ignorés sur l'axe où l'ancrage est centré.
    """
    if anchor not in ANCHORS:
        raise BatchError("Ancrage inconnu : %s" % anchor)

    if anchor.endswith("left") or anchor == "left":
        x = margin_x
    elif anchor.endswith("right") or anchor == "right":
        x = outer_w - inner_w - margin_x
    else:
        x = (outer_w - inner_w) // 2

    if anchor.startswith("top"):
        y = margin_y
    elif anchor.startswith("bottom"):
        y = outer_h - inner_h - margin_y
    else:
        y = (outer_h - inner_h) // 2

    return int(round(x)), int(round(y))


def compute_crop_ratio(src_w, src_h, ratio, anchor="center"):
    """Plus grand rectangle de rapport ``ratio`` tenant dans l'image."""
    if ratio <= 0:
        raise BatchError("Rapport d'aspect invalide.")
    src_ratio = float(src_w) / float(src_h)
    if src_ratio > ratio:
        height = src_h
        width = int(round(src_h * ratio))
    else:
        width = src_w
        height = int(round(src_w / ratio))
    width = clamp(width, 1, src_w)
    height = clamp(height, 1, src_h)
    x, y = anchor_offset(src_w, src_h, width, height, anchor)
    return int(x), int(y), int(width), int(height)


def compute_crop(src_w, src_h, mode="manual", x=0, y=0, width=0, height=0,
                 ratio=1.0, anchor="center",
                 margin_left=0, margin_right=0, margin_top=0, margin_bottom=0):
    """Calcule un rectangle de recadrage ``(x, y, w, h)`` validé.

    ``mode == "autocrop"`` renvoie ``None`` : le détourage automatique est
    délégué à GIMP (procédure ``plug-in-autocrop``).
    """
    if mode not in CROP_MODES:
        raise BatchError("Mode de recadrage inconnu : %s" % mode)
    if mode == "autocrop":
        return None

    if mode == "ratio":
        return compute_crop_ratio(src_w, src_h, ratio, anchor)

    if mode == "margins":
        x0 = int(margin_left)
        y0 = int(margin_top)
        x1 = src_w - int(margin_right)
        y1 = src_h - int(margin_bottom)
        if x1 - x0 < 1 or y1 - y0 < 1:
            raise BatchError(
                "Les marges de recadrage dépassent la taille de l'image "
                "(%dx%d)." % (src_w, src_h))
        return x0, y0, x1 - x0, y1 - y0

    # mode == "manual"
    x0 = clamp(int(x), 0, max(0, src_w - 1))
    y0 = clamp(int(y), 0, max(0, src_h - 1))
    w = int(width) if int(width) > 0 else src_w - x0
    h = int(height) if int(height) > 0 else src_h - y0
    w = clamp(w, 1, src_w - x0)
    h = clamp(h, 1, src_h - y0)
    return x0, y0, w, h


# ---------------------------------------------------------------------------
# Nommage des fichiers
# ---------------------------------------------------------------------------

class _SafeDict(dict):
    """Dictionnaire qui renvoie « {clé} » littéralement pour une clé absente."""

    def __missing__(self, key):
        return "{%s}" % key


_FORMATTER = string.Formatter()


def render_template(template, context):
    """Applique un modèle de nom de fichier.

    Jetons disponibles : ``{name}``, ``{ext}``, ``{index}``, ``{parent}``,
    ``{width}``, ``{height}``, ``{date}``, ``{time}``, ``{layer}``,
    ``{layer_index}``.  Un jeton inconnu est laissé tel quel plutôt que de
    faire échouer le traitement, et la mise en forme ``{index:03d}`` marche.
    """
    if not template:
        template = "{name}{ext}"
    try:
        return _FORMATTER.vformat(template, (), _SafeDict(context))
    except (ValueError, IndexError, KeyError, TypeError) as exc:
        raise BatchError(
            "Modèle de nom invalide (%s) : %s" % (exc, template))


def build_name_context(source_path, index=1, width=0, height=0,
                       extension=None, layer_name="", layer_index=0,
                       now=None):
    """Construit le dictionnaire de jetons pour :func:`render_template`."""
    now = now or datetime.datetime.now()
    base = os.path.basename(source_path)
    stem, src_ext = os.path.splitext(base)
    parent = os.path.basename(os.path.dirname(os.path.abspath(source_path)))
    return {
        "name": stem,
        "ext": extension if extension is not None else src_ext,
        "srcext": src_ext,
        "index": int(index),
        "parent": parent,
        "width": int(width),
        "height": int(height),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H-%M-%S"),
        "layer": sanitize_filename(layer_name),
        "layer_index": int(layer_index),
    }


_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name):
    """Retire d'un nom les caractères interdits par les systèmes de fichiers."""
    if not name:
        return ""
    cleaned = _ILLEGAL_CHARS.sub("_", str(name)).strip().strip(".")
    return cleaned or "_"


def target_extension(output_format, source_path):
    """Extension à utiliser en sortie pour un format donné."""
    if output_format == "keep" or not output_format:
        return os.path.splitext(source_path)[1] or ".png"
    try:
        return FORMAT_EXTENSION[output_format]
    except KeyError:
        raise BatchError("Format de sortie inconnu : %s" % output_format)


def format_from_extension(path):
    """Déduit un format de sortie à partir d'une extension de fichier."""
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    mapping = {
        "jpg": "jpeg", "jpeg": "jpeg", "jpe": "jpeg",
        "png": "png",
        "webp": "webp",
        "tif": "tiff", "tiff": "tiff",
        "bmp": "bmp",
        "xcf": "xcf",
    }
    return mapping.get(ext)


def resolve_collision(path, policy="overwrite", exists=os.path.exists):
    """Applique la politique d'écrasement.

    Renvoie le chemin à utiliser, ou ``None`` si le fichier doit être ignoré.
    ``exists`` est injectable pour les tests.
    """
    if policy not in OVERWRITE_POLICIES:
        raise BatchError("Politique d'écrasement inconnue : %s" % policy)
    if not exists(path):
        return path
    if policy == "overwrite":
        return path
    if policy == "skip":
        return None
    stem, ext = os.path.splitext(path)
    for counter in range(1, 10000):
        candidate = "%s_%d%s" % (stem, counter, ext)
        if not exists(candidate):
            return candidate
    raise BatchError("Impossible de trouver un nom libre pour %s" % path)


# ---------------------------------------------------------------------------
# Parcours du dossier source
# ---------------------------------------------------------------------------

def normalise_extensions(extensions):
    """Nettoie une liste d'extensions saisie par l'utilisateur."""
    if isinstance(extensions, str):
        parts = re.split(r"[,;\s]+", extensions)
    else:
        parts = list(extensions or ())
    cleaned = []
    for part in parts:
        part = str(part).strip().lower().lstrip("*").lstrip(".")
        if part and part not in cleaned:
            cleaned.append(part)
    return tuple(cleaned) or DEFAULT_EXTENSIONS


def iter_images(root, recursive=False, extensions=DEFAULT_EXTENSIONS,
                walker=None, skip_dirs=("_sortie", ".git", "__pycache__")):
    """Liste les images d'un dossier, triées, en évitant les dossiers cachés.

    ``walker`` est injectable (par défaut :func:`os.walk`) pour les tests.
    """
    walker = walker or os.walk
    exts = tuple("." + e for e in normalise_extensions(extensions))
    found = []

    for dirpath, dirnames, filenames in walker(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(".") and d not in skip_dirs
        )
        for filename in sorted(filenames):
            if filename.startswith("."):
                continue
            if os.path.splitext(filename)[1].lower() in exts:
                found.append(os.path.join(dirpath, filename))
        if not recursive:
            dirnames[:] = []

    return found


def output_path_for(source_path, source_root, output_root, filename,
                    keep_tree=True):
    """Chemin de sortie complet, en recréant l'arborescence si demandé."""
    if keep_tree:
        relative = os.path.relpath(os.path.dirname(os.path.abspath(source_path)),
                                   os.path.abspath(source_root))
        if relative in (".", ""):
            relative = ""
    else:
        relative = ""
    return os.path.join(output_root, relative, filename) if relative \
        else os.path.join(output_root, filename)


# ---------------------------------------------------------------------------
# Réglages : valeurs par défaut et validation
# ---------------------------------------------------------------------------

def default_settings():
    """Dictionnaire complet des réglages, avec les valeurs par défaut."""
    return {
        "version": 1,

        # Source / destination
        "source_folder": "",
        "recursive": False,
        "extensions": ",".join(DEFAULT_EXTENSIONS),
        "output_folder": "",
        "output_in_place": True,
        "output_subfolder": "_sortie",
        "keep_tree": True,
        "name_template": "{name}{ext}",
        "overwrite_policy": "rename",

        # Rotation / miroir
        "rotate_enabled": False,
        "rotate_degrees": 0,          # 0, 90, 180, 270
        "flip_horizontal": False,
        "flip_vertical": False,

        # Recadrage
        "crop_enabled": False,
        "crop_mode": "ratio",
        "crop_x": 0,
        "crop_y": 0,
        "crop_width": 0,
        "crop_height": 0,
        "crop_ratio": "1:1",
        "crop_anchor": "center",
        "crop_margin_left": 0,
        "crop_margin_right": 0,
        "crop_margin_top": 0,
        "crop_margin_bottom": 0,

        # Redimensionnement
        "resize_enabled": False,
        "resize_mode": "fit",
        "resize_width": 1920,
        "resize_height": 1080,
        "resize_percent": 50.0,
        "resize_allow_upscale": False,
        "resize_interpolation": "cubic",

        # Couleur
        "color_enabled": False,
        "color_mode": "rgb",          # rgb | grayscale
        "flatten": False,
        "background_color": "#ffffff",

        # Look (recettes GEGL)
        "look_enabled": False,
        "look_name": "",
        "look_opacity": 100.0,

        # Filigrane texte
        "wm_text_enabled": False,
        "wm_text": "© {parent} — {name}",
        "wm_text_font": "Sans Bold",
        "wm_text_size": 5.0,
        "wm_text_size_is_percent": True,
        "wm_text_color": "#ffffff",
        "wm_text_opacity": 60.0,
        "wm_text_anchor": "bottom-right",
        "wm_text_margin": 2.0,
        "wm_text_margin_is_percent": True,
        "wm_text_angle": 0.0,

        # Filigrane image
        "wm_image_enabled": False,
        "wm_image_path": "",
        "wm_image_scale": 20.0,       # % de la largeur de l'image
        "wm_image_opacity": 60.0,
        "wm_image_anchor": "bottom-right",
        "wm_image_margin": 2.0,
        "wm_image_margin_is_percent": True,

        # Sortie
        "output_format": "keep",
        "jpeg_quality": 90.0,
        "jpeg_progressive": True,
        "png_compression": 9,
        "webp_quality": 90.0,
        "webp_lossless": False,
        "tiff_compression": "lzw",
        "strip_metadata": False,

        # Export des calques
        "export_layers": False,
        "export_layers_only": False,
        "export_layers_visible_only": True,
        "export_layers_crop": False,
        "export_layers_template": "{name}_{layer_index:02d}_{layer}{ext}",

        # Divers
        "dry_run": False,
        "stop_on_error": False,
    }


_INT_KEYS = (
    "rotate_degrees", "crop_x", "crop_y", "crop_width", "crop_height",
    "crop_margin_left", "crop_margin_right", "crop_margin_top",
    "crop_margin_bottom", "resize_width", "resize_height", "png_compression",
)
_FLOAT_KEYS = (
    "resize_percent", "look_opacity", "wm_text_size", "wm_text_opacity",
    "wm_text_margin", "wm_text_angle", "wm_image_scale", "wm_image_opacity",
    "wm_image_margin", "jpeg_quality", "webp_quality",
)
_BOOL_KEYS = (
    "recursive", "output_in_place", "keep_tree", "rotate_enabled",
    "flip_horizontal", "flip_vertical", "crop_enabled", "resize_enabled",
    "resize_allow_upscale", "color_enabled", "flatten", "look_enabled",
    "wm_text_enabled", "wm_text_size_is_percent", "wm_text_margin_is_percent",
    "wm_image_enabled", "wm_image_margin_is_percent", "jpeg_progressive",
    "webp_lossless", "strip_metadata", "export_layers", "export_layers_only",
    "export_layers_visible_only", "export_layers_crop", "dry_run",
    "stop_on_error",
)


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "vrai", "yes", "oui", "on")


def coerce_settings(raw):
    """Fusionne des réglages venus d'un JSON avec les valeurs par défaut.

    Les clés inconnues sont ignorées et les types sont forcés, de sorte qu'un
    préréglage ancien ou bricolé à la main ne fasse jamais planter le greffon.
    """
    settings = default_settings()
    if not isinstance(raw, dict):
        return settings

    for key, value in raw.items():
        if key not in settings:
            continue
        try:
            if key in _BOOL_KEYS:
                settings[key] = _as_bool(value)
            elif key in _INT_KEYS:
                settings[key] = int(round(float(value)))
            elif key in _FLOAT_KEYS:
                settings[key] = float(value)
            else:
                settings[key] = value if value is not None else settings[key]
        except (TypeError, ValueError):
            continue  # on garde la valeur par défaut

    # Bornes et énumérations
    settings["rotate_degrees"] = {0: 0, 90: 90, 180: 180, 270: 270}.get(
        int(settings["rotate_degrees"]) % 360, 0)
    if settings["resize_mode"] not in RESIZE_MODES:
        settings["resize_mode"] = "fit"
    if settings["crop_mode"] not in CROP_MODES:
        settings["crop_mode"] = "ratio"
    if settings["crop_anchor"] not in ANCHORS:
        settings["crop_anchor"] = "center"
    if settings["wm_text_anchor"] not in ANCHORS:
        settings["wm_text_anchor"] = "bottom-right"
    if settings["wm_image_anchor"] not in ANCHORS:
        settings["wm_image_anchor"] = "bottom-right"
    if settings["overwrite_policy"] not in OVERWRITE_POLICIES:
        settings["overwrite_policy"] = "rename"
    if settings["output_format"] not in OUTPUT_FORMATS:
        settings["output_format"] = "keep"
    if settings["color_mode"] not in ("rgb", "grayscale"):
        settings["color_mode"] = "rgb"

    settings["png_compression"] = int(clamp(settings["png_compression"], 0, 9))
    settings["jpeg_quality"] = clamp(settings["jpeg_quality"], 0.0, 100.0)
    settings["webp_quality"] = clamp(settings["webp_quality"], 0.0, 100.0)
    for key in ("look_opacity", "wm_text_opacity", "wm_image_opacity"):
        settings[key] = clamp(settings[key], 0.0, 100.0)

    return settings


def validate_settings(settings):
    """Contrôles avant lancement.  Renvoie une liste de messages d'erreur."""
    problems = []

    source = settings.get("source_folder") or ""
    if not source:
        problems.append("Aucun dossier source sélectionné.")
    elif not os.path.isdir(source):
        problems.append("Le dossier source n'existe pas : %s" % source)

    if not settings.get("output_in_place"):
        out = settings.get("output_folder") or ""
        if not out:
            problems.append("Aucun dossier de destination sélectionné.")
        elif os.path.abspath(out) == os.path.abspath(source or ""):
            problems.append(
                "Le dossier de destination est identique au dossier source. "
                "Utilisez plutôt « à côté des originaux ».")

    if settings.get("resize_enabled"):
        try:
            compute_resize(1000, 800,
                           settings["resize_mode"],
                           settings["resize_width"],
                           settings["resize_height"],
                           settings["resize_percent"],
                           settings["resize_allow_upscale"])
        except BatchError as exc:
            problems.append(str(exc))

    if settings.get("crop_enabled") and settings.get("crop_mode") == "ratio":
        try:
            parse_ratio(settings.get("crop_ratio"))
        except BatchError as exc:
            problems.append(str(exc))

    if settings.get("wm_image_enabled"):
        path = settings.get("wm_image_path") or ""
        if not path:
            problems.append("Filigrane image activé mais aucun fichier choisi.")
        elif not os.path.isfile(path):
            problems.append("Fichier de filigrane introuvable : %s" % path)

    if settings.get("wm_text_enabled") and not (settings.get("wm_text") or "").strip():
        problems.append("Filigrane texte activé mais le texte est vide.")

    for key in ("wm_text_color", "background_color"):
        try:
            hex_to_rgba(settings.get(key))
        except BatchError as exc:
            problems.append("%s : %s" % (key, exc))

    try:
        render_template(settings.get("name_template"),
                        build_name_context("/tmp/exemple.jpg"))
    except BatchError as exc:
        problems.append(str(exc))

    if settings.get("export_layers"):
        try:
            render_template(settings.get("export_layers_template"),
                            build_name_context("/tmp/exemple.jpg",
                                               layer_name="Calque",
                                               layer_index=1))
        except BatchError as exc:
            problems.append(str(exc))

    return problems


def output_directory(settings, source_path):
    """Dossier dans lequel écrire les résultats d'une image donnée."""
    if settings.get("output_in_place"):
        base_dir = os.path.dirname(os.path.abspath(source_path))
        subfolder = (settings.get("output_subfolder") or "").strip()
        return os.path.join(base_dir, subfolder) if subfolder else base_dir

    output_root = settings.get("output_folder") or ""
    if not settings.get("keep_tree", True):
        return output_root
    relative = os.path.relpath(
        os.path.dirname(os.path.abspath(source_path)),
        os.path.abspath(settings.get("source_folder") or ""))
    if relative in (".", ""):
        return output_root
    return os.path.join(output_root, relative)


def plan_output(settings, source_path, index, width, height,
                exists=os.path.exists):
    """Calcule le chemin de sortie final d'une image (hors export de calques).

    Renvoie ``(chemin, format)`` ou ``(None, format)`` si le fichier doit être
    ignoré à cause de la politique d'écrasement.
    """
    output_format = settings.get("output_format", "keep")
    if output_format == "keep":
        resolved_format = format_from_extension(source_path) or "png"
    else:
        resolved_format = output_format

    extension = target_extension(output_format, source_path)
    context = build_name_context(source_path, index=index, width=width,
                                 height=height, extension=extension)
    filename = sanitize_filename(render_template(settings.get("name_template"),
                                                 context))

    path = os.path.join(output_directory(settings, source_path), filename)

    # Ne jamais écraser le fichier source lui-même.
    if os.path.abspath(path) == os.path.abspath(source_path):
        stem, ext = os.path.splitext(path)
        path = "%s_traite%s" % (stem, ext)

    return resolve_collision(path, settings.get("overwrite_policy", "rename"),
                             exists=exists), resolved_format
