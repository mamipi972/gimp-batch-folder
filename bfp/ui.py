# -*- coding: utf-8 -*-
"""
bfp.ui — boîte de dialogue GTK 3 du greffon.

L'interface est construite à la main (plutôt qu'avec
``GimpUi.ProcedureDialog``) afin d'obtenir des onglets, une grille
d'ancrage 3×3, une barre de préréglages et un journal — ce que la
génération automatique ne permet pas.

Toutes les commandes sont reliées au dictionnaire de réglages par
:class:`Binder`, qui sait lire et écrire chaque widget. Ajouter une option
revient donc à ajouter une clé dans ``core.default_settings()`` et un widget
ici, sans toucher au reste.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gimp", "3.0")
gi.require_version("GimpUi", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gimp, GimpUi, GLib, Gtk  # noqa: E402

from . import paths, presets as presets_mod
from .core import (  # noqa: E402
    ANCHORS,
    BatchError,
    OVERWRITE_POLICIES,
    coerce_settings,
    default_settings,
    hex_to_rgba,
    rgba_to_hex,
    validate_settings,
)
from .looks import find_look, load_looks
from .runner import BatchRunner
from .gimpops import Reporter

RESPONSE_RUN = 1
RESPONSE_DRY_RUN = 2
RESPONSE_STOP = 3

#: Libellés des ancrages, dans l'ordre de la grille 3×3.
_ANCHOR_LABELS = ("↖", "↑", "↗", "←", "•", "→", "↙", "↓", "↘")

_TOKEN_HELP = (
    "Jetons : <tt>{name}</tt> nom sans extension · <tt>{ext}</tt> extension · "
    "<tt>{index}</tt> numéro (<tt>{index:03d}</tt>) · <tt>{parent}</tt> dossier · "
    "<tt>{width}</tt> · <tt>{height}</tt>\n"
    "Dates : <tt>{date}</tt> 2026-09-22 · <tt>{date_fr}</tt> 22-09-2026 · "
    "<tt>{time}</tt> 14-32-05 · <tt>{year}</tt> <tt>{month}</tt> "
    "<tt>{day}</tt> <tt>{hour}</tt> <tt>{minute}</tt>"
)


# ---------------------------------------------------------------------------
# Liaison widgets ↔ réglages
# ---------------------------------------------------------------------------

class Binder(object):
    """Relie des widgets GTK aux clés du dictionnaire de réglages."""

    def __init__(self, on_change=None):
        self._entries = {}
        self._on_change = on_change

    def add(self, key, widget, getter, setter, signal=None):
        self._entries[key] = (widget, getter, setter)
        if signal and self._on_change:
            widget.connect(signal, lambda *a: self._on_change(key))
        return widget

    def widget(self, key):
        entry = self._entries.get(key)
        return entry[0] if entry else None

    def collect(self, base=None):
        settings = dict(base or default_settings())
        for key, (widget, getter, _setter) in self._entries.items():
            try:
                settings[key] = getter(widget)
            except Exception:
                pass
        return coerce_settings(settings)

    def apply(self, settings):
        for key, (widget, _getter, setter) in self._entries.items():
            if key not in settings:
                continue
            try:
                setter(widget, settings[key])
            except Exception:
                pass


class AnchorChooser(Gtk.Grid):
    """Grille 3×3 de boutons radio pour choisir un ancrage."""

    def __init__(self):
        super(AnchorChooser, self).__init__()
        self.set_row_spacing(2)
        self.set_column_spacing(2)
        self._buttons = {}
        group = None
        for position, anchor in enumerate(ANCHORS):
            button = Gtk.RadioButton.new_with_label_from_widget(
                group, _ANCHOR_LABELS[position])
            button.set_mode(False)  # apparence de bouton bascule
            button.set_tooltip_text(anchor)
            group = group or button
            self.attach(button, position % 3, position // 3, 1, 1)
            self._buttons[anchor] = button

    def get_anchor(self):
        for anchor, button in self._buttons.items():
            if button.get_active():
                return anchor
        return "center"

    def set_anchor(self, anchor):
        button = self._buttons.get(anchor)
        if button is not None:
            button.set_active(True)


# ---------------------------------------------------------------------------
# Fabriques de widgets
# ---------------------------------------------------------------------------

def _label(text, markup=False, dim=False):
    widget = Gtk.Label()
    if markup:
        widget.set_markup(text)
    else:
        widget.set_text(text)
    widget.set_xalign(0.0)
    widget.set_line_wrap(True)
    if dim:
        widget.get_style_context().add_class("dim-label")
    return widget


def _frame(title, child):
    frame = Gtk.Frame()
    frame.set_label_widget(_label("<b>%s</b>" % GLib.markup_escape_text(title),
                                  markup=True))
    frame.set_shadow_type(Gtk.ShadowType.NONE)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.set_margin_start(12)
    box.set_margin_top(6)
    box.set_margin_bottom(6)
    box.add(child)
    frame.add(box)
    return frame


def _row(*widgets, **kwargs):
    """Ligne horizontale. ``expand=True`` étire les champs, pas les étiquettes."""
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                  spacing=kwargs.get("spacing", 6))
    expand = kwargs.get("expand", False)
    for widget in widgets:
        is_text = isinstance(widget, str)
        if is_text:
            widget = _label(widget)
        box.pack_start(widget, expand and not is_text, True, 0)
    return box


def _vbox(*widgets, **kwargs):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                  spacing=kwargs.get("spacing", 6))
    for widget in widgets:
        box.pack_start(widget, False, False, 0)
    return box


def _page(*widgets):
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.set_border_width(12)
    for widget in widgets:
        box.pack_start(widget, False, False, 0)
    scroller.add(box)
    return scroller


# ---------------------------------------------------------------------------
# Boîte de dialogue principale
# ---------------------------------------------------------------------------

class BatchDialog(Gtk.Dialog):
    """Fenêtre principale du greffon."""

    def __init__(self, settings=None):
        use_header_bar = False
        try:
            use_header_bar = bool(Gtk.Settings.get_default().get_property(
                "gtk-dialogs-use-header"))
        except Exception:
            pass

        super(BatchDialog, self).__init__(
            title="Traitement par lots d'un dossier",
            use_header_bar=use_header_bar)

        self.set_default_size(820, 760)
        self.set_resizable(True)

        self._settings = coerce_settings(settings or default_settings())
        self._looks, look_errors = load_looks(paths.look_directories())
        self._built = False
        self._running = False
        self._cancel = False
        self._reporter = None

        self.binder = Binder(on_change=self._on_setting_changed)

        content = self.get_content_area()
        content.set_spacing(6)
        content.pack_start(self._build_preset_bar(), False, False, 0)

        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        content.pack_start(self.notebook, True, True, 0)

        self.notebook.append_page(self._page_source(), _label("Source"))
        self.notebook.append_page(self._page_transform(), _label("Transformations"))
        self.notebook.append_page(self._page_watermark(), _label("Filigrane"))
        self.notebook.append_page(self._page_look(), _label("Look"))
        self.notebook.append_page(self._page_output(), _label("Sortie"))
        self.notebook.append_page(self._page_log(), _label("Journal"))

        content.pack_start(self._build_status_bar(), False, False, 0)

        self.add_button("Fermer", Gtk.ResponseType.CLOSE)
        self._dry_button = self.add_button("Simuler", RESPONSE_DRY_RUN)
        self._dry_button.set_tooltip_text(
            "Parcourt le dossier et affiche ce qui serait écrit, sans rien "
            "modifier.")
        self._stop_button = self.add_button("Arrêter", RESPONSE_STOP)
        self._stop_button.set_sensitive(False)
        self._run_button = self.add_button("Lancer", RESPONSE_RUN)
        self._run_button.get_style_context().add_class("suggested-action")
        self.set_default_response(RESPONSE_RUN)

        self.connect("response", self._on_response)
        self.connect("delete-event", lambda *a: self._on_close())

        self._built = True
        self.binder.apply(self._settings)
        self._refresh_look_description()
        self._update_sensitivity()
        for message in look_errors:
            self._log(message)
        self._schedule_count()

    # -- barre de préréglages ---------------------------------------------

    def _build_preset_bar(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_border_width(6)

        box.pack_start(_label("Préréglage :"), False, False, 0)

        self.preset_combo = Gtk.ComboBoxText.new_with_entry()
        self.preset_combo.set_size_request(240, -1)
        self._reload_presets()
        box.pack_start(self.preset_combo, True, True, 0)

        load_button = Gtk.Button(label="Charger")
        load_button.connect("clicked", self._on_load_preset)
        box.pack_start(load_button, False, False, 0)

        save_button = Gtk.Button(label="Enregistrer")
        save_button.connect("clicked", self._on_save_preset)
        box.pack_start(save_button, False, False, 0)

        delete_button = Gtk.Button(label="Supprimer")
        delete_button.connect("clicked", self._on_delete_preset)
        box.pack_start(delete_button, False, False, 0)

        reset_button = Gtk.Button(label="Réinitialiser")
        reset_button.set_tooltip_text("Revenir aux réglages par défaut.")
        reset_button.connect("clicked", self._on_reset)
        box.pack_start(reset_button, False, False, 0)

        return box

    def _reload_presets(self, select=None):
        self.preset_combo.remove_all()
        for name in presets_mod.list_presets():
            self.preset_combo.append_text(name)
        if select:
            self.preset_combo.get_child().set_text(select)

    def _preset_name(self):
        return (self.preset_combo.get_child().get_text() or "").strip()

    def _on_load_preset(self, _button):
        name = self._preset_name()
        if not name:
            self._message("Choisissez un préréglage à charger.")
            return
        try:
            loaded = presets_mod.load_preset(name, base=self.binder.collect())
        except BatchError as exc:
            self._message(str(exc), error=True)
            return
        self.binder.apply(loaded)
        self._refresh_look_description()
        self._update_sensitivity()
        self._schedule_count()
        self._log("Préréglage chargé : %s" % name)

    def _on_save_preset(self, _button):
        name = self._preset_name()
        if not name:
            self._message("Donnez un nom au préréglage avant d'enregistrer.")
            return
        try:
            path = presets_mod.save_preset(name, self.binder.collect())
        except BatchError as exc:
            self._message(str(exc), error=True)
            return
        self._reload_presets(select=name)
        self._log("Préréglage enregistré : %s" % path)

    def _on_delete_preset(self, _button):
        name = self._preset_name()
        if not name:
            return
        try:
            removed = presets_mod.delete_preset(name)
        except BatchError as exc:
            self._message(str(exc), error=True)
            return
        self._reload_presets()
        self._log("Préréglage supprimé : %s" % name if removed
                  else "Aucun préréglage nommé « %s »." % name)

    def _on_reset(self, _button):
        self.binder.apply(default_settings())
        self._refresh_look_description()
        self._update_sensitivity()
        self._schedule_count()

    # -- page « Source » ---------------------------------------------------

    def _page_source(self):
        bind = self.binder.add

        self.source_chooser = Gtk.FileChooserButton(
            title="Dossier à traiter",
            action=Gtk.FileChooserAction.SELECT_FOLDER)
        self.source_chooser.set_size_request(340, -1)
        bind("source_folder", self.source_chooser,
             lambda w: w.get_filename() or "",
             lambda w, v: w.set_filename(v) if v and os.path.isdir(v) else None,
             "file-set")

        recursive = Gtk.CheckButton(label="Inclure les sous-dossiers")
        bind("recursive", recursive, Gtk.CheckButton.get_active,
             Gtk.CheckButton.set_active, "toggled")

        extensions = Gtk.Entry()
        extensions.set_tooltip_text(
            "Extensions traitées, séparées par des virgules.")
        bind("extensions", extensions, Gtk.Entry.get_text,
             lambda w, v: w.set_text(str(v)), "changed")

        source_frame = _frame("Dossier source", _vbox(
            _row("Dossier :", self.source_chooser, expand=False),
            recursive,
            _row("Extensions :", extensions),
        ))

        self.dest_inplace = Gtk.RadioButton.new_with_label_from_widget(
            None, "À côté des originaux, dans un sous-dossier :")
        self.dest_elsewhere = Gtk.RadioButton.new_with_label_from_widget(
            self.dest_inplace, "Dans un dossier choisi :")
        bind("output_in_place", self.dest_inplace,
             Gtk.RadioButton.get_active,
             self._set_inplace, "toggled")

        subfolder = Gtk.Entry()
        subfolder.set_width_chars(18)
        bind("output_subfolder", subfolder, Gtk.Entry.get_text,
             lambda w, v: w.set_text(str(v)), "changed")

        self.output_chooser = Gtk.FileChooserButton(
            title="Dossier de destination",
            action=Gtk.FileChooserAction.SELECT_FOLDER)
        self.output_chooser.set_size_request(340, -1)
        bind("output_folder", self.output_chooser,
             lambda w: w.get_filename() or "",
             lambda w, v: w.set_filename(v) if v and os.path.isdir(v) else None,
             "file-set")

        keep_tree = Gtk.CheckButton(
            label="Recréer l'arborescence des sous-dossiers")
        bind("keep_tree", keep_tree, Gtk.CheckButton.get_active,
             Gtk.CheckButton.set_active, "toggled")

        dest_frame = _frame("Destination", _vbox(
            _row(self.dest_inplace, subfolder),
            _row(self.dest_elsewhere, self.output_chooser),
            keep_tree,
        ))

        template = Gtk.Entry()
        bind("name_template", template, Gtk.Entry.get_text,
             lambda w, v: w.set_text(str(v)), "changed")

        overwrite = Gtk.ComboBoxText()
        for value, text in (("rename", "Renommer (ajouter _1, _2…)"),
                            ("skip", "Ignorer le fichier"),
                            ("overwrite", "Écraser")):
            overwrite.append(value, text)
        bind("overwrite_policy", overwrite,
             lambda w: w.get_active_id() or "rename",
             lambda w, v: w.set_active_id(v if v in OVERWRITE_POLICIES
                                          else "rename"),
             "changed")

        naming_frame = _frame("Nommage", _vbox(
            _row("Modèle :", template, expand=True),
            _label(_TOKEN_HELP, markup=True, dim=True),
            _row("Si le fichier existe déjà :", overwrite),
        ))

        return _page(source_frame, dest_frame, naming_frame)

    # -- page « Transformations » ------------------------------------------

    def _page_transform(self):
        bind = self.binder.add

        rotate_enabled = Gtk.CheckButton(label="Activer rotation / miroir")
        bind("rotate_enabled", rotate_enabled, Gtk.CheckButton.get_active,
             Gtk.CheckButton.set_active, "toggled")

        degrees = Gtk.ComboBoxText()
        for value, text in (("0", "Aucune"), ("90", "90° horaire"),
                            ("180", "180°"), ("270", "270° (90° antihoraire)")):
            degrees.append(value, text)
        bind("rotate_degrees", degrees,
             lambda w: int(w.get_active_id() or 0),
             lambda w, v: w.set_active_id(str(int(v))), "changed")

        flip_h = Gtk.CheckButton(label="Miroir horizontal")
        bind("flip_horizontal", flip_h, Gtk.CheckButton.get_active,
             Gtk.CheckButton.set_active, "toggled")
        flip_v = Gtk.CheckButton(label="Miroir vertical")
        bind("flip_vertical", flip_v, Gtk.CheckButton.get_active,
             Gtk.CheckButton.set_active, "toggled")

        self._rotate_widgets = [degrees, flip_h, flip_v]
        rotate_frame = _frame("Rotation et miroir", _vbox(
            rotate_enabled,
            _row("Rotation :", degrees),
            _row(flip_h, flip_v),
        ))

        # --- recadrage
        crop_enabled = Gtk.CheckButton(label="Activer le recadrage")
        bind("crop_enabled", crop_enabled, Gtk.CheckButton.get_active,
             Gtk.CheckButton.set_active, "toggled")

        crop_mode = Gtk.ComboBoxText()
        for value, text in (("ratio", "Rapport d'aspect, centré sur un ancrage"),
                            ("manual", "Rectangle précis (x, y, largeur, hauteur)"),
                            ("margins", "Rogner des marges sur chaque bord"),
                            ("autocrop", "Automatique (retirer les bords unis)")):
            crop_mode.append(value, text)
        bind("crop_mode", crop_mode, lambda w: w.get_active_id() or "ratio",
             lambda w, v: w.set_active_id(v), "changed")

        crop_ratio = Gtk.Entry()
        crop_ratio.set_width_chars(8)
        crop_ratio.set_placeholder_text("16:9")
        bind("crop_ratio", crop_ratio, Gtk.Entry.get_text,
             lambda w, v: w.set_text(str(v)), "changed")

        self.crop_anchor = AnchorChooser()
        bind("crop_anchor", self.crop_anchor, AnchorChooser.get_anchor,
             AnchorChooser.set_anchor)

        crop_x = self._spin("crop_x", 0, 100000, 1)
        crop_y = self._spin("crop_y", 0, 100000, 1)
        crop_w = self._spin("crop_width", 0, 100000, 1)
        crop_h = self._spin("crop_height", 0, 100000, 1)

        margins = [self._spin("crop_margin_%s" % side, 0, 100000, 1)
                   for side in ("left", "right", "top", "bottom")]

        self._crop_ratio_widgets = [crop_ratio, self.crop_anchor]
        self._crop_manual_widgets = [crop_x, crop_y, crop_w, crop_h]
        self._crop_margin_widgets = list(margins)
        self._crop_widgets = ([crop_mode] + self._crop_ratio_widgets +
                              self._crop_manual_widgets +
                              self._crop_margin_widgets)

        crop_frame = _frame("Recadrage", _vbox(
            crop_enabled,
            _row("Mode :", crop_mode, expand=True),
            _row("Rapport :", crop_ratio, _label("   Ancrage :"),
                 self.crop_anchor),
            _row("x :", crop_x, "y :", crop_y, "largeur :", crop_w,
                 "hauteur :", crop_h),
            _row("Marges — gauche :", margins[0], "droite :", margins[1],
                 "haut :", margins[2], "bas :", margins[3]),
            _label("0 en largeur ou hauteur = jusqu'au bord de l'image.",
                   dim=True),
        ))

        # --- redimensionnement
        resize_enabled = Gtk.CheckButton(label="Activer le redimensionnement")
        bind("resize_enabled", resize_enabled, Gtk.CheckButton.get_active,
             Gtk.CheckButton.set_active, "toggled")

        resize_mode = Gtk.ComboBoxText()
        for value, text in (
                ("fit", "Tenir dans une boîte (proportions gardées)"),
                ("fill", "Remplir la boîte puis recadrer au centre"),
                ("width", "Largeur imposée, hauteur proportionnelle"),
                ("height", "Hauteur imposée, largeur proportionnelle"),
                ("percent", "Pourcentage"),
                ("exact", "Dimensions exactes (déforme l'image)")):
            resize_mode.append(value, text)
        bind("resize_mode", resize_mode, lambda w: w.get_active_id() or "fit",
             lambda w, v: w.set_active_id(v), "changed")

        resize_w = self._spin("resize_width", 1, 100000, 10)
        resize_h = self._spin("resize_height", 1, 100000, 10)
        resize_pct = self._spin("resize_percent", 1.0, 1000.0, 5.0, digits=1)

        upscale = Gtk.CheckButton(
            label="Autoriser l'agrandissement des images plus petites")
        bind("resize_allow_upscale", upscale, Gtk.CheckButton.get_active,
             Gtk.CheckButton.set_active, "toggled")

        interpolation = Gtk.ComboBoxText()
        for value, text in (("cubic", "Cubique (recommandé)"),
                            ("nohalo", "NoHalo"), ("lohalo", "LoHalo"),
                            ("linear", "Linéaire"), ("none", "Aucune")):
            interpolation.append(value, text)
        bind("resize_interpolation", interpolation,
             lambda w: w.get_active_id() or "cubic",
             lambda w, v: w.set_active_id(v), "changed")

        self._resize_widgets = [resize_mode, resize_w, resize_h, resize_pct,
                                upscale, interpolation]

        resize_frame = _frame("Redimensionnement", _vbox(
            resize_enabled,
            _row("Mode :", resize_mode, expand=True),
            _row("Largeur :", resize_w, "Hauteur :", resize_h,
                 "Pourcentage :", resize_pct),
            upscale,
            _row("Interpolation :", interpolation),
        ))

        return _page(rotate_frame, crop_frame, resize_frame)

    # -- page « Filigrane » ------------------------------------------------

    def _page_watermark(self):
        bind = self.binder.add

        text_enabled = Gtk.CheckButton(label="Ajouter un filigrane texte")
        bind("wm_text_enabled", text_enabled, Gtk.CheckButton.get_active,
             Gtk.CheckButton.set_active, "toggled")

        text_entry = Gtk.Entry()
        bind("wm_text", text_entry, Gtk.Entry.get_text,
             lambda w, v: w.set_text(str(v)), "changed")

        font_entry = Gtk.Entry()
        font_entry.set_width_chars(18)
        font_entry.set_tooltip_text(
            "Nom exact de la police tel qu'il apparaît dans GIMP, "
            "p. ex. « Sans Bold ».")
        bind("wm_text_font", font_entry, Gtk.Entry.get_text,
             lambda w, v: w.set_text(str(v)), "changed")

        text_size = self._spin("wm_text_size", 0.1, 10000.0, 0.5, digits=1)
        text_size_unit = self._unit_combo("wm_text_size_is_percent",
                                          "% du petit côté", "pixels")

        text_color = Gtk.ColorButton()
        text_color.set_use_alpha(False)
        bind("wm_text_color", text_color, self._get_color, self._set_color,
             "color-set")

        text_opacity = self._spin("wm_text_opacity", 0.0, 100.0, 5.0, digits=0)
        text_angle = self._spin("wm_text_angle", -180.0, 180.0, 5.0, digits=1)

        self.wm_text_anchor = AnchorChooser()
        bind("wm_text_anchor", self.wm_text_anchor, AnchorChooser.get_anchor,
             AnchorChooser.set_anchor)

        text_margin = self._spin("wm_text_margin", 0.0, 10000.0, 0.5, digits=1)
        text_margin_unit = self._unit_combo("wm_text_margin_is_percent",
                                            "% du petit côté", "pixels")

        self._wm_text_widgets = [text_entry, font_entry, text_size,
                                 text_size_unit, text_color, text_opacity,
                                 text_angle, self.wm_text_anchor, text_margin,
                                 text_margin_unit]

        text_frame = _frame("Filigrane texte", _vbox(
            text_enabled,
            _row("Texte :", text_entry, expand=True),
            _label("Les mêmes jetons que pour le nommage sont acceptés "
                   "(<tt>{name}</tt>, <tt>{date}</tt>…).", markup=True, dim=True),
            _row("Police :", font_entry, "Taille :", text_size,
                 text_size_unit),
            _row("Couleur :", text_color, "Opacité % :", text_opacity,
                 "Angle ° :", text_angle),
            _row("Position :", self.wm_text_anchor, "Marge :", text_margin,
                 text_margin_unit),
        ))

        image_enabled = Gtk.CheckButton(label="Ajouter un filigrane image")
        bind("wm_image_enabled", image_enabled, Gtk.CheckButton.get_active,
             Gtk.CheckButton.set_active, "toggled")

        image_chooser = Gtk.FileChooserButton(
            title="Image de filigrane", action=Gtk.FileChooserAction.OPEN)
        image_chooser.set_size_request(320, -1)
        image_filter = Gtk.FileFilter()
        image_filter.set_name("Images")
        for pattern in ("*.png", "*.PNG", "*.webp", "*.xcf", "*.tif", "*.tiff"):
            image_filter.add_pattern(pattern)
        image_chooser.add_filter(image_filter)
        bind("wm_image_path", image_chooser,
             lambda w: w.get_filename() or "",
             lambda w, v: w.set_filename(v) if v and os.path.isfile(v) else None,
             "file-set")

        image_scale = self._spin("wm_image_scale", 1.0, 500.0, 5.0, digits=1)
        image_opacity = self._spin("wm_image_opacity", 0.0, 100.0, 5.0, digits=0)
        self.wm_image_anchor = AnchorChooser()
        bind("wm_image_anchor", self.wm_image_anchor, AnchorChooser.get_anchor,
             AnchorChooser.set_anchor)
        image_margin = self._spin("wm_image_margin", 0.0, 10000.0, 0.5, digits=1)
        image_margin_unit = self._unit_combo("wm_image_margin_is_percent",
                                             "% du petit côté", "pixels")

        self._wm_image_widgets = [image_chooser, image_scale, image_opacity,
                                  self.wm_image_anchor, image_margin,
                                  image_margin_unit]

        image_frame = _frame("Filigrane image", _vbox(
            image_enabled,
            _row("Fichier :", image_chooser, expand=False),
            _row("Largeur (% de l'image) :", image_scale,
                 "Opacité % :", image_opacity),
            _row("Position :", self.wm_image_anchor, "Marge :", image_margin,
                 image_margin_unit),
            _label("Un PNG à fond transparent donne le meilleur résultat.",
                   dim=True),
        ))

        return _page(text_frame, image_frame)

    # -- page « Look » -----------------------------------------------------

    def _page_look(self):
        bind = self.binder.add

        look_enabled = Gtk.CheckButton(
            label="Appliquer une recette de look (filtres GEGL)")
        bind("look_enabled", look_enabled, Gtk.CheckButton.get_active,
             Gtk.CheckButton.set_active, "toggled")

        self.look_combo = Gtk.ComboBoxText()
        for look in self._looks:
            self.look_combo.append(look.name, look.name)
        bind("look_name", self.look_combo,
             lambda w: w.get_active_id() or "",
             lambda w, v: w.set_active_id(v) if v else None, "changed")
        self.look_combo.connect("changed",
                                lambda *a: self._refresh_look_description())

        self.look_description = _label("", dim=True)

        look_opacity = self._spin("look_opacity", 0.0, 100.0, 5.0, digits=0)

        self._look_widgets = [self.look_combo, look_opacity]

        reload_button = Gtk.Button(label="Recharger")
        reload_button.set_tooltip_text(
            "Relire les fichiers de recettes sur le disque.")
        reload_button.connect("clicked", self._on_reload_looks)

        new_button = Gtk.Button(label="Nouvelle…")
        new_button.set_tooltip_text("Composer une recette de zéro.")
        new_button.connect("clicked", lambda *a: self._open_look_editor("new"))

        edit_button = Gtk.Button(label="Modifier…")
        edit_button.set_tooltip_text(
            "Ouvrir la recette sélectionnée dans l'éditeur.")
        edit_button.connect("clicked", lambda *a: self._open_look_editor("edit"))

        duplicate_button = Gtk.Button(label="Dupliquer…")
        duplicate_button.set_tooltip_text(
            "Partir de la recette sélectionnée sous un autre nom.")
        duplicate_button.connect("clicked",
                                 lambda *a: self._open_look_editor("copy"))

        frame = _frame("Look", _vbox(
            look_enabled,
            _row("Recette :", self.look_combo, expand=True),
            self.look_description,
            _row("Dosage (%) :", look_opacity),
            _row(new_button, edit_button, duplicate_button, reload_button),
        ))

        info = _frame("Vos propres recettes", _vbox(
            _label("L'éditeur compose la recette pour vous : il propose les "
                   "opérations GEGL de votre installation, fabrique les "
                   "réglages à partir de l'opération choisie et montre un "
                   "aperçu avant/après sur une image témoin."),
            _label("Les recettes que vous enregistrez vont dans :"),
            _label("<tt>%s</tt>" % GLib.markup_escape_text(
                paths.user_looks_directory()), markup=True),
            _label("Ce sont de simples fichiers JSON : pour partager une "
                   "recette, envoyez le fichier. Celles livrées avec le "
                   "greffon ne sont jamais écrasées — une recette du même nom "
                   "enregistrée par vos soins a la priorité.", dim=True),
        ))

        return _page(frame, info)

    def _open_look_editor(self, mode="new"):
        """Ouvre l'éditeur de recettes en création, modification ou copie."""
        try:
            from .lookeditor import edit_look
        except Exception as exc:
            self._message("Éditeur indisponible : %s" % exc, error=True)
            return

        look = None
        if mode in ("edit", "copy"):
            look = find_look(self._looks, self.look_combo.get_active_id())
            if look is None:
                self._message("Sélectionnez d'abord une recette.")
                return
            if mode == "copy":
                look = look.copy(name="%s (copie)" % look.name)

        try:
            saved = edit_look(self, look, self._first_source_image())
        except Exception as exc:
            self._log("ERREUR  éditeur de recettes : %s" % exc)
            self._message("L'éditeur a rencontré une erreur : %s" % exc,
                          error=True)
            return

        if saved:
            self._on_reload_looks(None)
            self.look_combo.set_active_id(saved)
            self.binder.widget("look_enabled").set_active(True)
            self._update_sensitivity()
            self._log("Recette enregistrée : %s" % saved)

    def _first_source_image(self):
        """Première image du dossier source, comme témoin d'aperçu."""
        try:
            files = BatchRunner(self.binder.collect(), self._looks).files()
        except Exception:
            return None
        return files[0] if files else None

    def _on_reload_looks(self, _button):
        current = self.look_combo.get_active_id()
        self._looks, errors = load_looks(paths.look_directories())
        self.look_combo.remove_all()
        for look in self._looks:
            self.look_combo.append(look.name, look.name)
        if current:
            self.look_combo.set_active_id(current)
        for message in errors:
            self._log(message)
        self._log("%d recette(s) disponible(s)." % len(self._looks))
        self._refresh_look_description()

    def _refresh_look_description(self):
        if not hasattr(self, "look_combo"):
            return
        # Sans sélection, activer la case « look » n'aurait aucun effet :
        # on présélectionne la première recette disponible.
        if not self.look_combo.get_active_id() and self._looks:
            self.look_combo.set_active_id(self._looks[0].name)
        name = self.look_combo.get_active_id()
        for look in self._looks:
            if look.name == name:
                self.look_description.set_text(
                    "%s  (%d étape%s)" % (look.description, len(look.steps),
                                          "s" if len(look.steps) > 1 else ""))
                return
        self.look_description.set_text("")

    # -- page « Sortie » ---------------------------------------------------

    def _page_output(self):
        bind = self.binder.add

        fmt = Gtk.ComboBoxText()
        for value, text in (("keep", "Conserver le format d'origine"),
                            ("jpeg", "JPEG"), ("png", "PNG"), ("webp", "WebP"),
                            ("tiff", "TIFF"), ("bmp", "BMP"),
                            ("xcf", "XCF (projet GIMP)")):
            fmt.append(value, text)
        bind("output_format", fmt, lambda w: w.get_active_id() or "keep",
             lambda w, v: w.set_active_id(v), "changed")

        jpeg_quality = self._spin("jpeg_quality", 0.0, 100.0, 1.0, digits=0)
        jpeg_progressive = Gtk.CheckButton(label="Progressif")
        bind("jpeg_progressive", jpeg_progressive, Gtk.CheckButton.get_active,
             Gtk.CheckButton.set_active, "toggled")

        png_compression = self._spin("png_compression", 0, 9, 1)
        webp_quality = self._spin("webp_quality", 0.0, 100.0, 1.0, digits=0)
        webp_lossless = Gtk.CheckButton(label="Sans perte")
        bind("webp_lossless", webp_lossless, Gtk.CheckButton.get_active,
             Gtk.CheckButton.set_active, "toggled")

        tiff_compression = Gtk.ComboBoxText()
        for value in ("none", "lzw", "packbits", "deflate", "jpeg"):
            tiff_compression.append(value, value.upper())
        bind("tiff_compression", tiff_compression,
             lambda w: w.get_active_id() or "lzw",
             lambda w, v: w.set_active_id(str(v)), "changed")

        strip = Gtk.CheckButton(label="Retirer les métadonnées (EXIF, XMP…)")
        bind("strip_metadata", strip, Gtk.CheckButton.get_active,
             Gtk.CheckButton.set_active, "toggled")

        format_frame = _frame("Format de sortie", _vbox(
            _row("Format :", fmt, expand=True),
            _row("JPEG — qualité :", jpeg_quality, jpeg_progressive),
            _row("PNG — compression :", png_compression,
                 "   WebP — qualité :", webp_quality, webp_lossless),
            _row("TIFF — compression :", tiff_compression),
            strip,
        ))

        color_enabled = Gtk.CheckButton(label="Forcer un mode colorimétrique")
        bind("color_enabled", color_enabled, Gtk.CheckButton.get_active,
             Gtk.CheckButton.set_active, "toggled")

        color_mode = Gtk.ComboBoxText()
        color_mode.append("rgb", "RVB")
        color_mode.append("grayscale", "Niveaux de gris")
        bind("color_mode", color_mode, lambda w: w.get_active_id() or "rgb",
             lambda w, v: w.set_active_id(v), "changed")

        flatten = Gtk.CheckButton(label="Aplatir les calques avant l'export")
        bind("flatten", flatten, Gtk.CheckButton.get_active,
             Gtk.CheckButton.set_active, "toggled")

        background = Gtk.ColorButton()
        background.set_use_alpha(False)
        bind("background_color", background, self._get_color, self._set_color,
             "color-set")

        self._color_widgets = [color_mode]

        color_frame = _frame("Couleur et calques", _vbox(
            _row(color_enabled, color_mode),
            flatten,
            _row("Fond utilisé à l'aplatissement :", background),
            _label("Le JPEG et le BMP n'ont pas de transparence : "
                   "l'aplatissement sur ce fond y est automatique.", dim=True),
        ))

        export_layers = Gtk.CheckButton(
            label="Exporter aussi chaque calque dans un fichier séparé")
        bind("export_layers", export_layers, Gtk.CheckButton.get_active,
             Gtk.CheckButton.set_active, "toggled")

        layers_only = Gtk.CheckButton(
            label="Uniquement les calques (ne pas écrire l'image complète)")
        bind("export_layers_only", layers_only, Gtk.CheckButton.get_active,
             Gtk.CheckButton.set_active, "toggled")

        layers_visible = Gtk.CheckButton(label="Calques visibles seulement")
        bind("export_layers_visible_only", layers_visible,
             Gtk.CheckButton.get_active, Gtk.CheckButton.set_active, "toggled")

        layers_crop = Gtk.CheckButton(
            label="Rogner chaque fichier aux limites du calque")
        bind("export_layers_crop", layers_crop, Gtk.CheckButton.get_active,
             Gtk.CheckButton.set_active, "toggled")

        layers_template = Gtk.Entry()
        bind("export_layers_template", layers_template, Gtk.Entry.get_text,
             lambda w, v: w.set_text(str(v)), "changed")

        self._layer_widgets = [layers_only, layers_visible, layers_crop,
                               layers_template]

        layers_frame = _frame("Export des calques", _vbox(
            export_layers,
            layers_only,
            _row(layers_visible, layers_crop),
            _row("Modèle :", layers_template, expand=True),
            _label("Jetons supplémentaires : <tt>{layer}</tt> nom du calque · "
                   "<tt>{layer_index}</tt> son rang (du bas vers le haut).",
                   markup=True, dim=True),
        ))

        stop_on_error = Gtk.CheckButton(label="S'arrêter à la première erreur")
        bind("stop_on_error", stop_on_error, Gtk.CheckButton.get_active,
             Gtk.CheckButton.set_active, "toggled")

        return _page(format_frame, color_frame, layers_frame,
                     _frame("Divers", _vbox(stop_on_error)))

    # -- page « Journal » --------------------------------------------------

    def _page_log(self):
        self.log_buffer = Gtk.TextBuffer()
        view = Gtk.TextView(buffer=self.log_buffer)
        view.set_editable(False)
        view.set_monospace(True)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.add(view)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_border_width(12)
        box.pack_start(scroller, True, True, 0)
        box.pack_start(_label("Le journal complet du dernier lot est aussi "
                              "écrit dans %s" % paths.log_path(), dim=True),
                       False, False, 0)
        self._log_view = view
        return box

    def _log(self, message):
        if not hasattr(self, "log_buffer"):
            return
        end = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end, message.rstrip() + "\n")
        try:
            mark = self.log_buffer.create_mark(None,
                                               self.log_buffer.get_end_iter(),
                                               False)
            self._log_view.scroll_mark_onscreen(mark)
            self.log_buffer.delete_mark(mark)
        except Exception:
            pass

    # -- barre d'état ------------------------------------------------------

    def _build_status_bar(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_border_width(6)
        self.count_label = _label("")
        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        self.progress.set_text("Prêt")
        box.pack_start(self.count_label, False, False, 0)
        box.pack_start(self.progress, False, False, 0)
        return box

    # -- helpers widgets ---------------------------------------------------

    def _spin(self, key, minimum, maximum, step, digits=0):
        adjustment = Gtk.Adjustment(value=0, lower=minimum, upper=maximum,
                                    step_increment=step,
                                    page_increment=step * 10)
        spin = Gtk.SpinButton(adjustment=adjustment, climb_rate=step,
                              digits=digits)
        spin.set_numeric(True)
        spin.set_width_chars(7 if digits else 6)
        if digits:
            self.binder.add(key, spin, Gtk.SpinButton.get_value,
                            lambda w, v: w.set_value(float(v)), "value-changed")
        else:
            self.binder.add(key, spin,
                            lambda w: int(w.get_value()),
                            lambda w, v: w.set_value(float(v)), "value-changed")
        return spin

    def _unit_combo(self, key, percent_label, pixel_label):
        combo = Gtk.ComboBoxText()
        combo.append("percent", percent_label)
        combo.append("pixels", pixel_label)
        self.binder.add(key, combo,
                        lambda w: w.get_active_id() == "percent",
                        lambda w, v: w.set_active_id("percent" if v
                                                     else "pixels"),
                        "changed")
        return combo

    def _set_inplace(self, _widget, value):
        """Un bouton radio ne se « décoche » pas : on active l'autre."""
        if value:
            self.dest_inplace.set_active(True)
        else:
            self.dest_elsewhere.set_active(True)

    @staticmethod
    def _get_color(button):
        rgba = button.get_rgba()
        return rgba_to_hex((rgba.red, rgba.green, rgba.blue, 1.0))

    @staticmethod
    def _set_color(button, value):
        try:
            red, green, blue, alpha = hex_to_rgba(value)
        except BatchError:
            red = green = blue = 1.0
            alpha = 1.0
        rgba = Gdk.RGBA()
        rgba.red, rgba.green, rgba.blue, rgba.alpha = red, green, blue, alpha
        button.set_rgba(rgba)

    # -- réactions ---------------------------------------------------------

    def _on_setting_changed(self, key):
        if key in ("source_folder", "recursive", "extensions",
                   "output_in_place", "output_subfolder"):
            self._schedule_count()
        self._update_sensitivity()

    def _update_sensitivity(self):
        if not getattr(self, "_built", False):
            return

        def enabled(key):
            widget = self.binder.widget(key)
            try:
                return bool(widget.get_active())
            except Exception:
                return False

        for widget in self._rotate_widgets:
            widget.set_sensitive(enabled("rotate_enabled"))
        for widget in self._resize_widgets:
            widget.set_sensitive(enabled("resize_enabled"))
        for widget in self._wm_text_widgets:
            widget.set_sensitive(enabled("wm_text_enabled"))
        for widget in self._wm_image_widgets:
            widget.set_sensitive(enabled("wm_image_enabled"))
        for widget in self._look_widgets:
            widget.set_sensitive(enabled("look_enabled"))
        for widget in self._color_widgets:
            widget.set_sensitive(enabled("color_enabled"))
        for widget in self._layer_widgets:
            widget.set_sensitive(enabled("export_layers"))

        crop_on = enabled("crop_enabled")
        mode_widget = self.binder.widget("crop_mode")
        mode = mode_widget.get_active_id() if mode_widget else "ratio"
        for widget in self._crop_widgets:
            widget.set_sensitive(crop_on)
        if crop_on:
            for widget in self._crop_ratio_widgets:
                widget.set_sensitive(mode == "ratio")
            for widget in self._crop_manual_widgets:
                widget.set_sensitive(mode == "manual")
            for widget in self._crop_margin_widgets:
                widget.set_sensitive(mode == "margins")

        in_place = enabled("output_in_place")
        self.binder.widget("output_subfolder").set_sensitive(in_place)
        self.output_chooser.set_sensitive(not in_place)

    _count_source = None

    def _schedule_count(self):
        if self._count_source is not None:
            try:
                GLib.source_remove(self._count_source)
            except Exception:
                pass
        self._count_source = GLib.timeout_add(350, self._do_count)

    def _do_count(self):
        self._count_source = None
        settings = self.binder.collect()
        folder = settings.get("source_folder") or ""
        if not folder or not os.path.isdir(folder):
            self.count_label.set_text("Aucun dossier source sélectionné.")
            return False
        try:
            runner = BatchRunner(settings, self._looks)
            files = runner.files()
        except Exception as exc:
            self.count_label.set_text("Impossible de lire le dossier : %s" % exc)
            return False
        self.count_label.set_text(
            "%d image(s) trouvée(s) dans %s%s."
            % (len(files), os.path.basename(folder.rstrip(os.sep)) or folder,
               " et ses sous-dossiers" if settings.get("recursive") else ""))
        return False

    # -- exécution ---------------------------------------------------------

    def _pump(self):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    def _on_progress(self, fraction, message):
        if fraction is None or fraction < 0:
            self.progress.pulse()
        else:
            self.progress.set_fraction(max(0.0, min(1.0, fraction)))
        self.progress.set_text(message)
        self._pump()

    def _on_response(self, _dialog, response):
        if response in (Gtk.ResponseType.CLOSE, Gtk.ResponseType.DELETE_EVENT):
            self._on_close()
        elif response == RESPONSE_STOP:
            self._cancel = True
        elif response in (RESPONSE_RUN, RESPONSE_DRY_RUN):
            self._start(dry_run=(response == RESPONSE_DRY_RUN))

    def _on_close(self):
        if self._running:
            self._cancel = True
            return True
        try:
            presets_mod.save_last_settings(self.binder.collect())
        except Exception:
            pass
        Gtk.main_quit()
        return False

    def _start(self, dry_run=False):
        if self._running:
            return
        settings = self.binder.collect()
        settings["dry_run"] = bool(dry_run)

        problems = validate_settings(settings)
        if problems:
            self._message("\n".join("• " + p for p in problems), error=True)
            self.notebook.set_current_page(0)
            return

        self._settings = settings
        self._running = True
        self._cancel = False
        self._run_button.set_sensitive(False)
        self._dry_button.set_sensitive(False)
        self._stop_button.set_sensitive(True)
        self.notebook.set_current_page(self.notebook.get_n_pages() - 1)
        self._log("── %s ──" % ("Simulation" if dry_run else "Traitement"))

        reporter = Reporter(echo=self._log)
        runner = BatchRunner(settings, self._looks, reporter=reporter,
                             progress=self._on_progress,
                             is_cancelled=lambda: self._cancel)
        try:
            Gimp.progress_init("Traitement par lots…")
        except Exception:
            pass

        try:
            result = runner.run()
        except BatchError as exc:
            self._log("ERREUR  %s" % exc)
            self._message(str(exc), error=True)
            result = None
        except Exception as exc:
            self._log("ERREUR  %s" % exc)
            self._message("Erreur inattendue : %s" % exc, error=True)
            result = None
        finally:
            try:
                Gimp.progress_end()
            except Exception:
                pass
            self._running = False
            self._run_button.set_sensitive(True)
            self._dry_button.set_sensitive(True)
            self._stop_button.set_sensitive(False)

        if result is not None:
            self._log(result.summary())
            self.progress.set_fraction(1.0 if not result.cancelled else 0.0)
            self.progress.set_text(result.summary())
            try:
                presets_mod.save_last_settings(settings)
            except Exception:
                pass

    # -- messages ----------------------------------------------------------

    def _message(self, text, error=False):
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.ERROR if error else Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Traitement par lots")
        dialog.format_secondary_text(text)
        dialog.run()
        dialog.destroy()


def run_dialog(settings=None):
    """Ouvre la fenêtre et rend la main quand elle se ferme."""
    GimpUi.init("gimp-batch-folder")
    dialog = BatchDialog(settings)
    dialog.show_all()
    dialog._update_sensitivity()
    Gtk.main()
    collected = None
    try:
        collected = dialog.binder.collect()
    except Exception:
        pass
    dialog.destroy()
    return collected
