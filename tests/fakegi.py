# -*- coding: utf-8 -*-
"""
Faux module ``gi`` : simule juste assez de GIMP 3, GEGL et GObject pour
exécuter ``bfp.gimpops`` et ``bfp.runner`` hors de GIMP.

Le but n'est pas de reproduire GIMP, mais de vérifier que le greffon appelle
les bonnes fonctions, dans le bon ordre, avec des valeurs plausibles — et que
les chemins de repli (procédure absente, propriété inconnue, opération GEGL
manquante) se comportent comme prévu.

Usage ::

    import fakegi
    calls = fakegi.install()        # avant d'importer bfp.gimpops
"""

from __future__ import annotations

import os
import sys
import types


# ---------------------------------------------------------------------------
# Journal des appels
# ---------------------------------------------------------------------------

class CallLog(list):
    def record(self, name, *args, **kwargs):
        self.append((name, args, kwargs))
        return self

    def names(self):
        return [entry[0] for entry in self]

    def of(self, name):
        return [entry for entry in self if entry[0] == name]

    def count(self, name):
        return len(self.of(name))


CALLS = CallLog()


# ---------------------------------------------------------------------------
# GObject
# ---------------------------------------------------------------------------

class GType(str):
    @property
    def name(self):
        return str(self)


class ParamSpec(object):
    def __init__(self, name, value_type, minimum=None, maximum=None,
                 default=None, nick=None, blurb=""):
        self.name = name
        self.value_type = value_type
        self.nick = nick or name.replace("-", " ")
        self.blurb = blurb
        self.default_value = default
        if minimum is not None:
            self.minimum = minimum
        if maximum is not None:
            self.maximum = maximum


class Value(object):
    def __init__(self, value_type, value):
        self.value_type = value_type
        self.value = value


class ParamFlags(object):
    READWRITE = 3


GObjectModule = types.SimpleNamespace(
    TYPE_BOOLEAN=GType("gboolean"),
    TYPE_INT=GType("gint"),
    TYPE_UINT=GType("guint"),
    TYPE_INT64=GType("gint64"),
    TYPE_UINT64=GType("guint64"),
    TYPE_LONG=GType("glong"),
    TYPE_ULONG=GType("gulong"),
    TYPE_DOUBLE=GType("gdouble"),
    TYPE_FLOAT=GType("gfloat"),
    TYPE_STRING=GType("gchararray"),
    Value=Value,
    ParamFlags=ParamFlags,
)


class Config(object):
    """Imite un GimpProcedureConfig / GeglConfig."""

    def __init__(self, specs):
        self._specs = {spec.name: spec for spec in specs}
        self.values = {}

    def find_property(self, name):
        return self._specs.get(name)

    def set_property(self, name, value):
        if name not in self._specs:
            raise AttributeError(name)
        self.values[name] = value

    def get_property(self, name):
        return self.values.get(name)


# ---------------------------------------------------------------------------
# GLib / Gio
# ---------------------------------------------------------------------------

class GFile(object):
    def __init__(self, path):
        self._path = path

    def get_path(self):
        return self._path

    def __repr__(self):
        return "<GFile %s>" % self._path


GioModule = types.SimpleNamespace(
    File=types.SimpleNamespace(new_for_path=lambda path: GFile(path)))

GLibModule = types.SimpleNamespace(Error=Exception, MAXINT=2 ** 31 - 1,
                                   markup_escape_text=lambda s: s,
                                   timeout_add=lambda delay, fn: 0,
                                   source_remove=lambda handle: None)

GimpUiModule = types.SimpleNamespace(init=lambda name: CALLS.record(
    "GimpUi.init", name))


# ---------------------------------------------------------------------------
# GEGL
# ---------------------------------------------------------------------------

#: Opérations que le faux GEGL déclare connaître.
KNOWN_OPERATIONS = {
    "gegl:saturation": [ParamSpec("scale", GObjectModule.TYPE_DOUBLE, 0.0, 10.0)],
    "gegl:brightness-contrast": [
        ParamSpec("brightness", GObjectModule.TYPE_DOUBLE, -1.0, 1.0),
        ParamSpec("contrast", GObjectModule.TYPE_DOUBLE, 0.0, 2.0)],
    "gegl:levels": [ParamSpec(n, GObjectModule.TYPE_DOUBLE, 0.0, 1.0)
                    for n in ("in-low", "in-high", "out-low", "out-high")],
    "gegl:unsharp-mask": [
        ParamSpec("std-dev", GObjectModule.TYPE_DOUBLE, 0.0, 100.0),
        ParamSpec("scale", GObjectModule.TYPE_DOUBLE, 0.0, 300.0),
        ParamSpec("threshold", GObjectModule.TYPE_DOUBLE, 0.0, 1.0)],
    "gegl:vignette": [
        ParamSpec("color", GType("GeglColor")),
        ParamSpec("radius", GObjectModule.TYPE_DOUBLE, 0.0, 3.0),
        ParamSpec("softness", GObjectModule.TYPE_DOUBLE, 0.0, 1.0),
        ParamSpec("gamma", GObjectModule.TYPE_DOUBLE, 1.0, 20.0),
        ParamSpec("proportion", GObjectModule.TYPE_DOUBLE, 0.0, 1.0)],
    "gegl:noise-rgb": [
        ParamSpec("independent", GObjectModule.TYPE_BOOLEAN),
        ParamSpec("correlated", GObjectModule.TYPE_BOOLEAN),
        ParamSpec("red", GObjectModule.TYPE_DOUBLE, 0.0, 1.0),
        ParamSpec("green", GObjectModule.TYPE_DOUBLE, 0.0, 1.0),
        ParamSpec("blue", GObjectModule.TYPE_DOUBLE, 0.0, 1.0),
        ParamSpec("alpha", GObjectModule.TYPE_DOUBLE, 0.0, 1.0),
        ParamSpec("seed", GObjectModule.TYPE_INT, 0, 2 ** 31 - 1)],
    "gegl:shadows-highlights": [
        ParamSpec("shadows", GObjectModule.TYPE_DOUBLE, -100.0, 100.0),
        ParamSpec("highlights", GObjectModule.TYPE_DOUBLE, -100.0, 100.0),
        ParamSpec("radius", GObjectModule.TYPE_DOUBLE, 0.1, 1500.0),
        ParamSpec("compress", GObjectModule.TYPE_DOUBLE, 0.0, 100.0)],
    "gegl:softglow": [
        ParamSpec("glow-radius", GObjectModule.TYPE_DOUBLE, 1.0, 50.0),
        ParamSpec("brightness", GObjectModule.TYPE_DOUBLE, 0.0, 1.0),
        ParamSpec("sharpness", GObjectModule.TYPE_DOUBLE, 0.0, 1.0)],
    "gegl:color-temperature": [
        ParamSpec("original-temperature", GObjectModule.TYPE_DOUBLE, 1000.0, 12000.0),
        ParamSpec("intended-temperature", GObjectModule.TYPE_DOUBLE, 1000.0, 12000.0)],
    "gegl:invert-gamma": [],
    "gegl:invert-linear": [],
}


class Color(object):
    def __init__(self, name="black"):
        self.rgba = (0.0, 0.0, 0.0, 1.0)

    @staticmethod
    def new(name="black"):
        return Color(name)

    def set_rgba(self, red, green, blue, alpha):
        self.rgba = (red, green, blue, alpha)

    def __repr__(self):
        return "<Color %r>" % (self.rgba,)


#: Métadonnées renvoyées par le faux ``Gegl.Operation.get_key``.
OPERATION_KEYS = {
    "gegl:saturation": {"title": "Saturation",
                        "description": "Modifie la saturation des couleurs.",
                        "categories": "color"},
    "gegl:vignette": {"title": "Vignettage",
                      "description": "Assombrit les bords de l'image.",
                      "categories": "render"},
}


class Operation(object):
    """Imite les fonctions statiques de GeglOperation."""

    @staticmethod
    def list_properties(name):
        if name not in KNOWN_OPERATIONS:
            raise RuntimeError("opération inconnue : %s" % name)
        return list(KNOWN_OPERATIONS[name])

    @staticmethod
    def get_key(name, key):
        return OPERATION_KEYS.get(name, {}).get(key, "")


class Node(object):
    def __init__(self):
        self._operation = None

    def set_property(self, name, value):
        if name == "operation":
            self._operation = value

    def list_properties(self):
        return list(KNOWN_OPERATIONS.get(self._operation, []))


GeglModule = types.SimpleNamespace(
    init=lambda _args: CALLS.record("Gegl.init"),
    has_operation=lambda name: name in KNOWN_OPERATIONS,
    list_operations=lambda: sorted(KNOWN_OPERATIONS),
    Color=Color,
    Operation=Operation,
    Node=Node,
)


# ---------------------------------------------------------------------------
# GIMP
# ---------------------------------------------------------------------------

class _Enum(object):
    def __init__(self, name):
        self._name = name

    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        value = "%s.%s" % (self._name, item)
        setattr(self, item, value)
        return value


class Drawable(object):
    def __init__(self, name="Calque", width=0, height=0, image=None):
        self._name = name
        self._width = width
        self._height = height
        self._image = image
        # GIMP détruit les calques fusionnés : on reproduit fidèlement cette
        # invalidation, c'est elle qui faisait échouer gimp-drawable-filter-new.
        self._valid = True
        self._visible = True
        self._opacity = 100.0
        self._offsets = (0, 0)
        self._mode = "NORMAL"
        self._mask = None
        self.filters = []

    # -- lecture
    def get_name(self):
        return self._name

    def get_width(self):
        return self._width

    def get_height(self):
        return self._height

    def get_visible(self):
        return self._visible

    def get_offsets(self):
        return (True,) + self._offsets

    def get_parent(self):
        return None

    def is_valid(self):
        return self._valid

    # -- écriture
    def set_name(self, name):
        self._name = name

    def set_visible(self, visible):
        self._visible = visible

    def set_opacity(self, value):
        CALLS.record("layer.set_opacity", self._name, value)
        self._opacity = value

    def set_offsets(self, x, y):
        CALLS.record("layer.set_offsets", self._name, x, y)
        self._offsets = (x, y)

    def set_mode(self, mode):
        CALLS.record("layer.set_mode", self._name, mode)
        self._mode = mode

    def copy(self):
        CALLS.record("layer.copy", self._name)
        clone = Drawable(self._name + " copie", self._width, self._height,
                         self._image)
        return clone

    def create_mask(self, mask_type):
        CALLS.record("layer.create_mask", self._name, mask_type)
        return Drawable(self._name + " masque", self._width, self._height,
                        self._image)

    def add_mask(self, mask):
        CALLS.record("layer.add_mask", self._name)
        self._mask = mask

    def edit_fill(self, fill_type):
        CALLS.record("layer.edit_fill", self._name, fill_type)

    def scale(self, width, height, local_origin=False):
        CALLS.record("layer.scale", self._name, width, height)
        self._width, self._height = width, height

    def merge_filter(self, drawable_filter):
        CALLS.record("layer.merge_filter", self._name,
                     drawable_filter.operation,
                     dict(drawable_filter.config.values))
        self.filters.append(drawable_filter)

    def append_filter(self, drawable_filter):
        self.merge_filter(drawable_filter)

    def transform_rotate(self, angle, auto_center, cx, cy):
        CALLS.record("layer.transform_rotate", self._name, angle)
        return self


class DrawableFilter(object):
    def __init__(self, drawable, operation, name=""):
        if operation not in KNOWN_OPERATIONS:
            raise RuntimeError("opération inconnue : %s" % operation)
        if not drawable.is_valid():
            # Message équivalent à celui que GIMP affiche réellement.
            CALLS.record("erreur.drawable-invalide", operation)
            raise RuntimeError(
                "gimp-drawable-filter-new : ID erroné pour « drawable »")
        self.drawable = drawable
        self.operation = operation
        self.config = Config(KNOWN_OPERATIONS[operation])
        self.opacity = 1.0
        self.blend_mode = None

    @staticmethod
    def new(drawable, operation, name=""):
        return DrawableFilter(drawable, operation, name)

    def get_config(self):
        return self.config

    def set_opacity(self, value):
        self.opacity = value

    def set_blend_mode(self, mode):
        self.blend_mode = mode

    def update(self):
        pass


class Image(object):
    _counter = 0

    def __init__(self, width=1600, height=1200, base_type="RGB"):
        Image._counter += 1
        self.identifier = Image._counter
        self._width = width
        self._height = height
        self._base_type = base_type
        self._file = None
        self.deleted = False
        self.layers = [Drawable("Arrière-plan", width, height, self)]

    # -- lecture
    def get_width(self):
        return self._width

    def get_height(self):
        return self._height

    def get_base_type(self):
        return self._base_type

    def get_layers(self):
        return list(self.layers)

    def get_item_position(self, item):
        try:
            return self.layers.index(item)
        except ValueError:
            return 0

    def get_file(self):
        return self._file

    # -- écriture
    def set_file(self, gfile):
        self._file = gfile

    def undo_disable(self):
        pass

    def undo_enable(self):
        pass

    def delete(self):
        self.deleted = True

    def duplicate(self):
        CALLS.record("image.duplicate", self.identifier)
        clone = Image(self._width, self._height, self._base_type)
        clone.layers = [Drawable(l.get_name(), l.get_width(), l.get_height(),
                                 clone) for l in self.layers]
        return clone

    def scale(self, width, height):
        CALLS.record("image.scale", width, height)
        self._width, self._height = width, height
        for layer in self.layers:
            layer._width, layer._height = width, height

    def crop(self, width, height, offx, offy):
        CALLS.record("image.crop", width, height, offx, offy)
        self._width, self._height = width, height

    def resize(self, width, height, offx, offy):
        CALLS.record("image.resize", width, height, offx, offy)
        self._width, self._height = width, height

    def rotate(self, rotation):
        CALLS.record("image.rotate", rotation)
        if rotation in ("RotationType.DEGREES90", "RotationType.DEGREES270"):
            self._width, self._height = self._height, self._width

    def flip(self, orientation):
        CALLS.record("image.flip", orientation)

    def flatten(self):
        CALLS.record("image.flatten")
        for existing in self.layers:
            existing._valid = False
        self.layers = [Drawable("Aplati", self._width, self._height, self)]
        return self.layers[0]

    def merge_visible_layers(self, merge_type):
        CALLS.record("image.merge_visible_layers", merge_type)
        for existing in self.layers[1:]:
            existing._valid = False
        self.layers = [self.layers[0]]
        return self.layers[0]

    def merge_down(self, layer, merge_type):
        """Comme GIMP : détruit les deux calques et en renvoie un nouveau."""
        CALLS.record("image.merge_down", layer.get_name())
        if layer not in self.layers:
            raise RuntimeError("calque absent de l'image")
        position = self.layers.index(layer)
        if position + 1 >= len(self.layers):
            raise RuntimeError("aucun calque en dessous")
        below = self.layers[position + 1]
        merged = Drawable(below.get_name(), below.get_width(),
                          below.get_height(), self)
        layer._valid = False
        below._valid = False
        self.layers[position:position + 2] = [merged]
        return merged

    def remove_layer(self, layer):
        if layer in self.layers:
            self.layers.remove(layer)

    def insert_layer(self, layer, parent, position):
        CALLS.record("image.insert_layer", layer.get_name(), position)
        self.layers.insert(max(0, min(position, len(self.layers))), layer)

    def convert_grayscale(self):
        CALLS.record("image.convert_grayscale")
        self._base_type = "GRAY"

    def convert_rgb(self):
        CALLS.record("image.convert_rgb")
        self._base_type = "RGB"

    def set_metadata(self, metadata):
        CALLS.record("image.set_metadata", metadata)

    @staticmethod
    def new(width, height, base_type):
        CALLS.record("Image.new", width, height, base_type)
        image = Image(width, height, base_type)
        image.layers = []
        return image


class ValueArray(list):
    def index(self, position):          # noqa: A003 - imite l'API GIMP
        return list.__getitem__(self, position)

    def length(self):
        return len(self)


#: Procédures que le faux PDB déclare connaître, avec leurs propriétés.
PDB_PROCEDURES = {
    "file-jpeg-export": [
        ParamSpec("run-mode", GType("GimpRunMode")),
        ParamSpec("image", GType("GimpImage")),
        ParamSpec("file", GType("GFile")),
        # Le JPEG attend une qualité entre 0 et 1 : c'est ce que le greffon
        # doit déduire tout seul des bornes.
        ParamSpec("quality", GObjectModule.TYPE_DOUBLE, 0.0, 1.0),
        ParamSpec("progressive", GObjectModule.TYPE_BOOLEAN),
        ParamSpec("optimize", GObjectModule.TYPE_BOOLEAN),
        ParamSpec("include-exif", GObjectModule.TYPE_BOOLEAN),
        ParamSpec("include-xmp", GObjectModule.TYPE_BOOLEAN),
    ],
    "file-png-export": [
        ParamSpec("run-mode", GType("GimpRunMode")),
        ParamSpec("image", GType("GimpImage")),
        ParamSpec("file", GType("GFile")),
        ParamSpec("compression", GObjectModule.TYPE_INT, 0, 9),
        ParamSpec("interlaced", GObjectModule.TYPE_BOOLEAN),
    ],
    "file-webp-export": [
        ParamSpec("run-mode", GType("GimpRunMode")),
        ParamSpec("image", GType("GimpImage")),
        ParamSpec("file", GType("GFile")),
        # Le WebP, lui, attend 0–100.
        ParamSpec("quality", GObjectModule.TYPE_DOUBLE, 0.0, 100.0),
        ParamSpec("lossless", GObjectModule.TYPE_BOOLEAN),
    ],
    "plug-in-autocrop": [
        ParamSpec("run-mode", GType("GimpRunMode")),
        ParamSpec("image", GType("GimpImage")),
        ParamSpec("drawable", GType("GimpDrawable")),
    ],
    # Un greffon plausible, pour les étapes « proc » des recettes.
    "plug-in-unsharp-mask": [
        ParamSpec("run-mode", GType("GimpRunMode")),
        ParamSpec("image", GType("GimpImage")),
        ParamSpec("drawables", GType("GimpCoreObjectArray")),
        ParamSpec("radius", GObjectModule.TYPE_DOUBLE, 0.0, 120.0, 5.0),
        ParamSpec("amount", GObjectModule.TYPE_DOUBLE, 0.0, 10.0, 0.5),
        ParamSpec("threshold", GObjectModule.TYPE_INT, 0, 255, 0),
    ],
    "script-fu-drop-shadow": [
        ParamSpec("run-mode", GType("GimpRunMode")),
        ParamSpec("image", GType("GimpImage")),
        ParamSpec("drawable", GType("GimpDrawable")),
        ParamSpec("opacity", GObjectModule.TYPE_DOUBLE, 0.0, 100.0, 80.0),
    ],
}

#: Procédures que le faux PDB annonce à ``query_procedures``.
PDB_QUERY_RESULT = sorted(PDB_PROCEDURES) + [
    "gimp-quit", "gimp-displays-flush", "file-png-load",
]

#: Fichiers écrits par le faux export (chemin → format).
WRITTEN = []


class Procedure(object):
    def __init__(self, name):
        self.name = name

    def create_config(self):
        return Config(PDB_PROCEDURES[self.name])

    def get_arguments(self):
        return list(PDB_PROCEDURES[self.name])

    def get_blurb(self):
        return "Fait quelque chose (%s)." % self.name

    def run(self, config):
        CALLS.record("pdb.run", self.name, dict(config.values))
        if self.name.startswith("file-") and self.name.endswith("-export"):
            gfile = config.values.get("file")
            if gfile is not None:
                path = gfile.get_path()
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as handle:
                    handle.write(b"fake-image")
                WRITTEN.append(path)
        return ValueArray(["PDBStatusType.SUCCESS"])


class PDB(object):
    def lookup_procedure(self, name):
        return Procedure(name) if name in PDB_PROCEDURES else None

    def query_procedures(self, *args):
        return list(PDB_QUERY_RESULT)

    def get_last_error(self):
        return ""


_PDB = PDB()


def _file_load(run_mode, gfile):
    CALLS.record("Gimp.file_load", gfile.get_path())
    return Image()


def _file_save(run_mode, image, gfile, options):
    CALLS.record("Gimp.file_save", gfile.get_path())
    path = gfile.get_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"fake-image")
    WRITTEN.append(path)
    return True


def _file_load_layer(run_mode, image, gfile):
    CALLS.record("Gimp.file_load_layer", gfile.get_path())
    return Drawable("filigrane", 400, 200, image)


def _text_font(image, drawable, x, y, text, border, antialias, size, font):
    CALLS.record("Gimp.text_font", text, size)
    width = max(1, int(len(text) * size * 0.55))
    return Drawable("Texte: %s" % text, width, int(size * 1.3), image)


class Font(object):
    def __init__(self, name):
        self.name = name

    @staticmethod
    def get_by_name(name):
        return Font(name) if name else None


class Layer(object):
    @staticmethod
    def new_from_drawable(drawable, image):
        CALLS.record("Layer.new_from_drawable", drawable.get_name())
        return Drawable(drawable.get_name(), drawable.get_width(),
                        drawable.get_height(), image)


GimpModule = types.SimpleNamespace(
    # énumérations
    RunMode=_Enum("RunMode"),
    RotationType=_Enum("RotationType"),
    OrientationType=_Enum("OrientationType"),
    InterpolationType=_Enum("InterpolationType"),
    ImageBaseType=types.SimpleNamespace(RGB="RGB", GRAY="GRAY", INDEXED="INDEXED"),
    MergeType=types.SimpleNamespace(CLIP_TO_IMAGE="CLIP_TO_IMAGE",
                                    EXPAND_AS_NECESSARY="EXPAND"),
    LayerMode=_Enum("LayerMode"),
    AddMaskType=types.SimpleNamespace(COPY="COPY", WHITE="WHITE", BLACK="BLACK"),
    FillType=_Enum("FillType"),
    TransformResize=types.SimpleNamespace(ADJUST="ADJUST"),
    MessageHandlerType=types.SimpleNamespace(MESSAGE_BOX="MESSAGE_BOX",
                                             CONSOLE="CONSOLE",
                                             ERROR_CONSOLE="ERROR_CONSOLE"),
    message_set_handler=lambda handler: CALLS.record("message_set_handler",
                                                     handler),
    PDBStatusType=types.SimpleNamespace(SUCCESS="PDBStatusType.SUCCESS",
                                        CANCEL="PDBStatusType.CANCEL",
                                        EXECUTION_ERROR="PDBStatusType.ERROR",
                                        CALLING_ERROR="PDBStatusType.CALLING"),
    # classes
    Image=Image,
    Layer=Layer,
    Font=Font,
    DrawableFilter=DrawableFilter,
    # fonctions
    get_pdb=lambda: _PDB,
    file_load=_file_load,
    file_save=_file_save,
    file_load_layer=_file_load_layer,
    text_font=_text_font,
    context_set_foreground=lambda color: CALLS.record("context_set_foreground",
                                                      color.rgba),
    context_set_background=lambda color: CALLS.record("context_set_background",
                                                      color.rgba),
    context_get_font=lambda: Font("Sans"),
    context_set_interpolation=lambda value: CALLS.record("set_interpolation",
                                                         value),
    context_set_transform_resize=lambda value: None,
    context_push=lambda: None,
    context_pop=lambda: None,
    displays_flush=lambda: None,
    message=lambda text: CALLS.record("Gimp.message", text),
    progress_init=lambda text: None,
    progress_update=lambda fraction: None,
    progress_end=lambda: None,
    directory=lambda: os.environ.get("FAKE_GIMP_DIR", "/tmp/fake-gimp"),
)


# ---------------------------------------------------------------------------
# Installation dans sys.modules
# ---------------------------------------------------------------------------

def install(with_gtk=False):
    """Installe le faux ``gi`` et renvoie le journal des appels.

    ``with_gtk=True`` ajoute les faux Gtk/Gdk/GimpUi nécessaires pour
    construire la boîte de dialogue sans serveur X.
    """
    repository = types.ModuleType("gi.repository")
    repository.Gimp = GimpModule
    repository.Gegl = GeglModule
    repository.Gio = GioModule
    repository.GLib = GLibModule
    repository.GObject = GObjectModule

    names = ["Gimp", "Gegl", "Gio", "GLib", "GObject"]
    if with_gtk:
        import fakegtk

        repository.Gtk = fakegtk.GtkModule
        repository.Gdk = fakegtk.GdkModule
        repository.GimpUi = GimpUiModule
        names += ["Gtk", "Gdk", "GimpUi"]

    gi_module = types.ModuleType("gi")
    gi_module.require_version = lambda *args, **kwargs: None
    gi_module.repository = repository

    sys.modules["gi"] = gi_module
    sys.modules["gi.repository"] = repository
    for name in names:
        sys.modules["gi.repository.%s" % name] = getattr(repository, name)

    CALLS.clear()
    del WRITTEN[:]
    return CALLS


def reset():
    CALLS.clear()
    del WRITTEN[:]
    Image._counter = 0
