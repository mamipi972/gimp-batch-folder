# -*- coding: utf-8 -*-
"""
bfp.lookeditor — éditeur visuel de recettes de look.

Aucun catalogue n'est codé en dur : les opérations proposées viennent de
``Gegl.list_operations()``, les procédures de ``Gimp.get_pdb()``, et les
réglages de chaque étape sont fabriqués à partir des ``GParamSpec`` réels
(voir :mod:`bfp.opinfo`). L'éditeur expose donc exactement ce que votre
installation sait faire, et il suivra les versions de GEGL sans retouche.

L'aperçu applique la recette à une copie réduite d'une image témoin. La
réduction précède les filtres : on regarde une teinte, un contraste, un
vignettage — pas du grain au pixel près.
"""

from __future__ import annotations

import os
import re
import time

import gi

gi.require_version("Gimp", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import gimpops, opinfo, paths
from .core import BatchError, clamp, hex_to_rgba, rgba_to_hex
from .looks import Look, LookStep, save_look

#: Modes de fusion proposés pour une étape.
BLEND_MODES = (
    ("replace", "Remplacer (normal)"),
    ("normal", "Normal"),
    ("overlay", "Superposer"),
    ("softlight", "Lumière douce"),
    ("multiply", "Multiplier"),
    ("screen", "Écran"),
    ("darken-only", "Assombrir seulement"),
    ("lighten-only", "Éclaircir seulement"),
    ("difference", "Différence"),
)

_PREVIEW_SIZE = 420
_PREVIEW_DELAY_MS = 700


def slugify(name):
    """Nom de fichier sûr pour une recette."""
    cleaned = re.sub(r"[^\w\-. ]+", "_", str(name), flags=re.UNICODE).strip()
    cleaned = re.sub(r"\s+", "-", cleaned).strip("-_.")
    return (cleaned or "recette").lower()


# ---------------------------------------------------------------------------
# Petites fabriques de widgets
# ---------------------------------------------------------------------------

def _label(text, markup=False, dim=False, wrap=True):
    widget = Gtk.Label()
    if markup:
        widget.set_markup(text)
    else:
        widget.set_text(text)
    widget.set_xalign(0.0)
    widget.set_line_wrap(wrap)
    if dim:
        widget.get_style_context().add_class("dim-label")
    return widget


def _box(orientation, spacing=6):
    return Gtk.Box(orientation=orientation, spacing=spacing)


def _frame(title, child):
    frame = Gtk.Frame()
    frame.set_label_widget(_label("<b>%s</b>" % GLib.markup_escape_text(title),
                                  markup=True))
    frame.set_shadow_type(Gtk.ShadowType.NONE)
    inner = _box(Gtk.Orientation.VERTICAL)
    inner.set_margin_start(10)
    inner.set_margin_top(4)
    inner.add(child)
    frame.add(inner)
    return frame


def _clear(container):
    try:
        for child in container.get_children():
            container.remove(child)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Choix d'une étape à ajouter
# ---------------------------------------------------------------------------

class StepChooser(Gtk.Dialog):
    """Liste filtrable des opérations GEGL, procédures et étapes spéciales."""

    MAX_ROWS = 80

    def __init__(self, parent):
        super(StepChooser, self).__init__(title="Ajouter une étape",
                                          transient_for=parent, modal=True)
        self.set_default_size(560, 520)
        self.add_button("Annuler", Gtk.ResponseType.CANCEL)
        self._add_button = self.add_button("Ajouter", Gtk.ResponseType.OK)
        self._add_button.get_style_context().add_class("suggested-action")

        self._entries = self._build_catalogue()
        self.chosen = None

        content = self.get_content_area()
        content.set_spacing(6)
        content.set_border_width(10)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text(
            "Chercher : saturation, vignette, flou, netteté…")
        self.search.connect("search-changed", lambda *a: self._refill())
        content.pack_start(self.search, False, False, 0)

        self.listbox = Gtk.ListBox()
        self.listbox.connect("row-selected", self._on_selected)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self.listbox)
        content.pack_start(scroller, True, True, 0)

        self.hint = _label("", dim=True)
        content.pack_start(self.hint, False, False, 0)

        self.description = _label("", dim=True)
        content.pack_start(self.description, False, False, 0)

        self._rows = []
        self._refill()

    # -- catalogue
    def _build_catalogue(self):
        """``[(clé de recherche, libellé, fabrique d'étape)]``."""
        entries = [(
            "virage partiel split tone ombres hautes lumières",
            "Virage partiel — teinte les ombres et les hautes lumières",
            lambda: LookStep(special="split-tone",
                             params={"shadows": "#1d3b57",
                                     "highlights": "#e9cd93",
                                     "amount": 25.0}),
        )]

        suggested = []
        for _theme, operations in opinfo.SUGGESTED_OPERATIONS:
            suggested.extend(operations)

        def add_operation(name, starred=False):
            title = opinfo.operation_title(name)
            label = "%s%s — %s" % ("★ " if starred else "", name, title)
            entries.append((("%s %s" % (name, title)).lower(), label,
                            lambda n=name: LookStep(op=n)))

        seen = set()
        for name in suggested:
            if opinfo.operation_exists(name):
                add_operation(name, starred=True)
                seen.add(name)
        for name in opinfo.list_operations():
            if name not in seen:
                add_operation(name)

        for name in opinfo.list_procedures():
            blurb = opinfo.procedure_blurb(name)
            label = "greffon : %s%s" % (name, " — %s" % blurb if blurb else "")
            entries.append((("%s %s" % (name, blurb)).lower(), label,
                            lambda n=name: LookStep(proc=n)))
        return entries

    # -- remplissage
    def _refill(self):
        needle = (self.search.get_text() or "").strip().lower()
        _clear(self.listbox)
        self._rows = []

        matches = [e for e in self._entries if not needle or needle in e[0]]
        for entry in matches[:self.MAX_ROWS]:
            row = Gtk.ListBoxRow()
            row.add(_label(entry[1], wrap=False))
            self.listbox.add(row)
            self._rows.append(entry)

        hidden = len(matches) - len(self._rows)
        if hidden > 0:
            self.hint.set_text("%d autres résultats — affinez la recherche."
                               % hidden)
        elif not matches:
            self.hint.set_text("Aucune opération ne correspond.")
        else:
            self.hint.set_text("%d résultat(s)." % len(matches))
        try:
            self.listbox.show_all()
        except Exception:
            pass

    def _on_selected(self, _listbox, row):
        if row is None:
            self.chosen = None
            return
        try:
            index = row.get_index()
        except Exception:
            index = -1
        if 0 <= index < len(self._rows):
            self.chosen = self._rows[index][2]
            label = self._rows[index][1]
            name = label.split(" — ")[0].replace("★ ", "").strip()
            if name.startswith("gegl:"):
                self.description.set_text(opinfo.operation_description(name))
            else:
                self.description.set_text("")

    def build_step(self):
        """Étape choisie, avec ses valeurs par défaut renseignées."""
        if self.chosen is None:
            return None
        step = self.chosen()
        for info in opinfo.step_params(step):
            if info.default is not None:
                step.params.setdefault(info.name, info.default)
        return step


# ---------------------------------------------------------------------------
# Éditeur principal
# ---------------------------------------------------------------------------

class LookEditor(Gtk.Dialog):
    """Fenêtre d'édition d'une recette."""

    def __init__(self, parent=None, look=None, sample_path=None):
        super(LookEditor, self).__init__(title="Éditeur de recettes de look",
                                         transient_for=parent, modal=True)
        self.set_default_size(980, 720)

        self.look = (look.copy() if look is not None
                     else Look(name="Ma recette", description="", steps=[]))
        self.saved_name = None
        self._sample_path = sample_path
        self._preview_source = None
        self._refresh_handle = None
        self._building = False

        content = self.get_content_area()
        content.set_spacing(6)

        columns = _box(Gtk.Orientation.HORIZONTAL, 12)
        columns.set_border_width(12)
        columns.pack_start(self._build_left_column(), False, False, 0)
        columns.pack_start(self._build_right_column(), True, True, 0)
        content.pack_start(columns, True, True, 0)

        self.add_button("Fermer", Gtk.ResponseType.CLOSE)
        self._save_button = self.add_button("Enregistrer",
                                            Gtk.ResponseType.APPLY)
        self._save_button.get_style_context().add_class("suggested-action")

        self._reload_steps()
        self._refresh_step_panel()

    # -- colonne de gauche -------------------------------------------------

    def _build_left_column(self):
        column = _box(Gtk.Orientation.VERTICAL, 8)
        column.set_size_request(320, -1)

        self.name_entry = Gtk.Entry()
        self.name_entry.set_text(self.look.name)
        self.name_entry.connect("changed", self._on_name_changed)

        self.description_entry = Gtk.Entry()
        self.description_entry.set_text(self.look.description)
        self.description_entry.set_placeholder_text(
            "Ce qu'elle fait, en une phrase.")
        self.description_entry.connect(
            "changed",
            lambda *a: setattr(self.look, "description",
                               self.description_entry.get_text()))

        identity = _box(Gtk.Orientation.VERTICAL, 4)
        identity.pack_start(_label("Nom"), False, False, 0)
        identity.pack_start(self.name_entry, False, False, 0)
        identity.pack_start(_label("Description"), False, False, 0)
        identity.pack_start(self.description_entry, False, False, 0)
        column.pack_start(identity, False, False, 0)

        self.step_list = Gtk.ListBox()
        self.step_list.connect("row-selected",
                               lambda *a: self._refresh_step_panel())
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self.step_list)
        column.pack_start(_frame("Étapes, dans l'ordre d'application",
                                 scroller), True, True, 0)

        buttons = _box(Gtk.Orientation.HORIZONTAL, 4)
        for label, tooltip, handler in (
                ("Ajouter…", "Choisir une opération GEGL ou un greffon",
                 self._on_add),
                ("Dupliquer", "Copier l'étape sélectionnée",
                 self._on_duplicate),
                ("Supprimer", "Retirer l'étape sélectionnée", self._on_remove),
                ("↑", "Monter l'étape", self._on_move_up),
                ("↓", "Descendre l'étape", self._on_move_down)):
            button = Gtk.Button(label=label)
            button.set_tooltip_text(tooltip)
            button.connect("clicked", handler)
            buttons.pack_start(button, False, False, 0)
        column.pack_start(buttons, False, False, 0)

        column.pack_start(
            _label("Les recettes sont enregistrées dans votre dossier "
                   "personnel ; celles livrées avec le greffon ne sont "
                   "jamais écrasées.", dim=True), False, False, 0)
        return column

    # -- colonne de droite -------------------------------------------------

    def _build_right_column(self):
        column = _box(Gtk.Orientation.VERTICAL, 10)

        self.step_title = _label("", markup=True)
        self.step_help = _label("", dim=True)
        self.param_box = _box(Gtk.Orientation.VERTICAL, 4)

        param_scroller = Gtk.ScrolledWindow()
        param_scroller.set_policy(Gtk.PolicyType.NEVER,
                                  Gtk.PolicyType.AUTOMATIC)
        param_scroller.set_size_request(-1, 240)
        param_scroller.add(self.param_box)

        self.step_opacity = Gtk.SpinButton(
            adjustment=Gtk.Adjustment(value=100, lower=0, upper=100,
                                      step_increment=5, page_increment=10),
            climb_rate=5, digits=0)
        self.step_opacity.connect("value-changed", self._on_step_opacity)

        self.step_blend = Gtk.ComboBoxText()
        for value, text in BLEND_MODES:
            self.step_blend.append(value, text)
        self.step_blend.connect("changed", self._on_step_blend)

        dosing = _box(Gtk.Orientation.HORIZONTAL, 6)
        dosing.pack_start(_label("Opacité de l'étape (%) :"), False, False, 0)
        dosing.pack_start(self.step_opacity, False, False, 0)
        dosing.pack_start(_label("   Fusion :"), False, False, 0)
        dosing.pack_start(self.step_blend, False, False, 0)

        settings = _box(Gtk.Orientation.VERTICAL, 6)
        settings.pack_start(self.step_title, False, False, 0)
        settings.pack_start(self.step_help, False, False, 0)
        settings.pack_start(param_scroller, True, True, 0)
        settings.pack_start(dosing, False, False, 0)
        column.pack_start(_frame("Réglages de l'étape", settings),
                          True, True, 0)

        column.pack_start(self._build_preview(), False, False, 0)
        return column

    def _build_preview(self):
        self.before_image = Gtk.Image()
        self.after_image = Gtk.Image()

        images = _box(Gtk.Orientation.HORIZONTAL, 10)
        for caption, widget in (("Avant", self.before_image),
                                ("Après", self.after_image)):
            cell = _box(Gtk.Orientation.VERTICAL, 2)
            cell.pack_start(_label("<b>%s</b>" % caption, markup=True),
                            False, False, 0)
            cell.pack_start(widget, True, True, 0)
            images.pack_start(cell, True, True, 0)

        self.sample_chooser = Gtk.FileChooserButton(
            title="Image témoin", action=Gtk.FileChooserAction.OPEN)
        self.sample_chooser.set_size_request(240, -1)
        if self._sample_path and os.path.isfile(self._sample_path):
            self.sample_chooser.set_filename(self._sample_path)
        self.sample_chooser.connect("file-set",
                                    lambda *a: self._schedule_refresh(now=True))

        self.auto_preview = Gtk.CheckButton(label="Aperçu automatique")
        self.auto_preview.set_active(True)
        self.auto_preview.set_tooltip_text(
            "Recalcule l'aperçu après chaque réglage. Décochez-le si votre "
            "recette est lourde.")

        refresh = Gtk.Button(label="Rafraîchir")
        refresh.connect("clicked", lambda *a: self._schedule_refresh(now=True))

        controls = _box(Gtk.Orientation.HORIZONTAL, 6)
        controls.pack_start(_label("Image témoin :"), False, False, 0)
        controls.pack_start(self.sample_chooser, True, True, 0)
        controls.pack_start(self.auto_preview, False, False, 0)
        controls.pack_start(refresh, False, False, 0)

        self.preview_status = _label("", dim=True)

        panel = _box(Gtk.Orientation.VERTICAL, 6)
        panel.pack_start(images, True, True, 0)
        panel.pack_start(controls, False, False, 0)
        panel.pack_start(self.preview_status, False, False, 0)
        return _frame("Aperçu", panel)

    # -- liste des étapes --------------------------------------------------

    def _reload_steps(self, select=None):
        self._building = True
        _clear(self.step_list)
        for position, step in enumerate(self.look.steps, start=1):
            row = Gtk.ListBoxRow()
            text = "%d. %s" % (position, step.label())
            if step.opacity != 100.0:
                text += "   (%d %%)" % round(step.opacity)
            row.add(_label(text, wrap=False))
            self.step_list.add(row)
        try:
            self.step_list.show_all()
        except Exception:
            pass
        self._building = False
        if select is not None:
            self._select_step(select)

    def _select_step(self, index):
        if not self.look.steps:
            return
        index = int(clamp(index, 0, len(self.look.steps) - 1))
        try:
            row = self.step_list.get_row_at_index(index)
            if row is not None:
                self.step_list.select_row(row)
        except Exception:
            pass

    def _selected_index(self):
        try:
            row = self.step_list.get_selected_row()
            if row is None:
                return -1
            return row.get_index()
        except Exception:
            return -1

    def _selected_step(self):
        index = self._selected_index()
        if 0 <= index < len(self.look.steps):
            return self.look.steps[index]
        return None

    # -- panneau de réglages ----------------------------------------------

    def _refresh_step_panel(self):
        if self._building:
            return
        _clear(self.param_box)

        step = self._selected_step()
        if step is None:
            self.step_title.set_markup(
                "<i>Sélectionnez une étape, ou ajoutez-en une.</i>")
            self.step_help.set_text("")
            self.step_opacity.set_sensitive(False)
            self.step_blend.set_sensitive(False)
            try:
                self.param_box.show_all()
            except Exception:
                pass
            return

        self.step_opacity.set_sensitive(True)
        self.step_blend.set_sensitive(step.kind == "op")

        if step.kind == "op":
            self.step_title.set_markup(
                "<b>%s</b>  <tt>%s</tt>"
                % (GLib.markup_escape_text(opinfo.operation_title(step.op)),
                   GLib.markup_escape_text(step.op)))
            self.step_help.set_text(opinfo.operation_description(step.op))
        elif step.kind == "proc":
            self.step_title.set_markup(
                "<b>Greffon</b>  <tt>%s</tt>"
                % GLib.markup_escape_text(step.proc))
            self.step_help.set_text(
                (opinfo.procedure_blurb(step.proc) + "\n\nUn greffon peut "
                 "modifier la structure des calques ; le greffon revalide la "
                 "cible après coup, mais le résultat reste moins prévisible "
                 "qu'une opération GEGL.").strip())
        else:
            self.step_title.set_markup("<b>Virage partiel</b>")
            self.step_help.set_text(
                "Teinte séparément les ombres et les hautes lumières. "
                "Deux teintes opposées sur le cercle chromatique donnent "
                "presque toujours un meilleur résultat.")

        self._building = True
        self.step_opacity.set_value(float(step.opacity))
        self.step_blend.set_active_id(step.blend if step.blend in
                                      dict(BLEND_MODES) else "replace")
        self._building = False

        infos = opinfo.step_params(step)
        if not infos:
            self.param_box.pack_start(
                _label("Cette étape n'a aucun réglage.", dim=True),
                False, False, 0)
        for info in infos:
            self.param_box.pack_start(self._param_row(step, info),
                                      False, False, 0)
        try:
            self.param_box.show_all()
        except Exception:
            pass

    def _param_row(self, step, info):
        row = _box(Gtk.Orientation.HORIZONTAL, 8)
        caption = _label(info.label.capitalize(), wrap=False)
        caption.set_size_request(190, -1)
        if info.tooltip:
            caption.set_tooltip_text(info.tooltip)
        row.pack_start(caption, False, False, 0)

        current = step.params.get(info.name, info.default)
        widget = self._param_widget(step, info, current)
        row.pack_start(widget, True, True, 0)
        return row

    def _param_widget(self, step, info, current):
        def store(value):
            if self._building:
                return
            step.params[info.name] = value
            self._schedule_refresh()

        if info.kind == "bool":
            widget = Gtk.CheckButton()
            widget.set_active(bool(current))
            widget.connect("toggled", lambda w: store(bool(w.get_active())))
            return widget

        if info.kind in ("int", "double"):
            digits = 0 if info.kind == "int" else 3
            low = info.minimum if info.minimum is not None else -10000
            high = info.maximum if info.maximum is not None else 10000
            span = float(high) - float(low)
            step_increment = 1 if info.kind == "int" else max(span / 200.0,
                                                              0.001)
            adjustment = Gtk.Adjustment(value=float(current or 0), lower=low,
                                        upper=high,
                                        step_increment=step_increment,
                                        page_increment=step_increment * 10)
            widget = Gtk.SpinButton(adjustment=adjustment,
                                    climb_rate=step_increment, digits=digits)
            widget.set_numeric(True)
            widget.set_value(float(current or 0))
            widget.connect(
                "value-changed",
                lambda w: store(int(w.get_value()) if info.kind == "int"
                                else float(w.get_value())))
            return widget

        if info.kind == "color":
            widget = Gtk.ColorButton()
            widget.set_use_alpha(False)
            rgba = Gdk.RGBA()
            try:
                red, green, blue, _alpha = hex_to_rgba(str(current or "#000000"))
            except BatchError:
                red = green = blue = 0.0
            rgba.red, rgba.green, rgba.blue, rgba.alpha = red, green, blue, 1.0
            widget.set_rgba(rgba)
            widget.connect(
                "color-set",
                lambda w: store(rgba_to_hex((w.get_rgba().red,
                                             w.get_rgba().green,
                                             w.get_rgba().blue, 1.0))))
            return widget

        if info.kind == "enum" and info.choices:
            widget = Gtk.ComboBoxText()
            for value, text in info.choices:
                widget.append(str(value), str(text))
            widget.set_active_id(str(current if current is not None
                                     else info.choices[0][0]))
            widget.connect("changed",
                           lambda w: store(int(w.get_active_id() or 0)))
            return widget

        widget = Gtk.Entry()
        widget.set_text("" if current is None else str(current))
        widget.connect("changed", lambda w: store(w.get_text()))
        return widget

    # -- réactions ---------------------------------------------------------

    def _on_name_changed(self, entry):
        self.look.name = entry.get_text()

    def _on_step_opacity(self, spin):
        step = self._selected_step()
        if step is None or self._building:
            return
        step.opacity = float(spin.get_value())
        index = self._selected_index()
        self._reload_steps(select=index)
        self._schedule_refresh()

    def _on_step_blend(self, combo):
        step = self._selected_step()
        if step is None or self._building:
            return
        step.blend = combo.get_active_id() or "replace"
        self._schedule_refresh()

    def _on_add(self, _button):
        chooser = StepChooser(self)
        try:
            chooser.show_all()
            response = chooser.run()
            step = chooser.build_step() if response == Gtk.ResponseType.OK \
                else None
        finally:
            chooser.destroy()
        if step is None:
            return
        index = self._selected_index()
        position = index + 1 if index >= 0 else len(self.look.steps)
        self.look.steps.insert(position, step)
        self._reload_steps(select=position)
        self._schedule_refresh()

    def _on_duplicate(self, _button):
        index = self._selected_index()
        step = self._selected_step()
        if step is None:
            return
        self.look.steps.insert(index + 1, step.copy())
        self._reload_steps(select=index + 1)
        self._schedule_refresh()

    def _on_remove(self, _button):
        index = self._selected_index()
        if not (0 <= index < len(self.look.steps)):
            return
        del self.look.steps[index]
        self._reload_steps(select=min(index, len(self.look.steps) - 1))
        self._refresh_step_panel()
        self._schedule_refresh()

    def _move(self, offset):
        index = self._selected_index()
        target = index + offset
        if not (0 <= index < len(self.look.steps)) or \
                not (0 <= target < len(self.look.steps)):
            return
        steps = self.look.steps
        steps[index], steps[target] = steps[target], steps[index]
        self._reload_steps(select=target)
        self._schedule_refresh()

    def _on_move_up(self, _button):
        self._move(-1)

    def _on_move_down(self, _button):
        self._move(1)

    # -- aperçu ------------------------------------------------------------

    def _sample(self):
        try:
            path = self.sample_chooser.get_filename()
        except Exception:
            path = None
        return path or self._sample_path

    def _schedule_refresh(self, now=False):
        if not now and not self.auto_preview.get_active():
            return
        if self._refresh_handle is not None:
            try:
                GLib.source_remove(self._refresh_handle)
            except Exception:
                pass
            self._refresh_handle = None
        delay = 1 if now else _PREVIEW_DELAY_MS
        self._refresh_handle = GLib.timeout_add(delay, self._do_refresh)

    def _do_refresh(self):
        self._refresh_handle = None
        sample = self._sample()
        if not sample or not os.path.isfile(sample):
            self.preview_status.set_text(
                "Choisissez une image témoin pour voir l'aperçu.")
            return False

        directory = os.path.join(paths.data_directory(), "apercu")
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError:
            pass

        reporter = gimpops.Reporter()
        started = time.time()

        if self._preview_source != sample:
            before = os.path.join(directory, "avant.png")
            if gimpops.render_preview(sample, before, None,
                                      max_size=_PREVIEW_SIZE,
                                      reporter=reporter):
                self._set_image(self.before_image, before)
                self._preview_source = sample

        after = os.path.join(directory, "apres-%d.png" % (int(time.time()) % 9))
        if gimpops.render_preview(sample, after, self.look, 100.0,
                                  max_size=_PREVIEW_SIZE, reporter=reporter):
            self._set_image(self.after_image, after)
            message = "Aperçu recalculé en %.1f s." % (time.time() - started)
        else:
            message = "Aperçu impossible — voir les messages ci-dessous."

        warnings = [line for line in reporter.lines if line.strip()]
        if reporter.warnings or reporter.errors:
            message += "  " + warnings[-1].strip()
        self.preview_status.set_text(message)
        return False

    @staticmethod
    def _set_image(widget, path):
        try:
            widget.set_from_file(path)
        except Exception:
            pass

    # -- enregistrement ----------------------------------------------------

    def save(self):
        """Écrit la recette. Renvoie ``True`` si la fenêtre peut se fermer."""
        name = (self.name_entry.get_text() or "").strip()
        if not name:
            self._message("Donnez un nom à la recette.")
            return False
        if not self.look.steps:
            self._message("Une recette doit contenir au moins une étape.")
            return False

        self.look.name = name
        path = os.path.join(paths.user_looks_directory(),
                            slugify(name) + ".json")
        try:
            save_look(self.look, path)
        except BatchError as exc:
            self._message(str(exc))
            return False

        self.saved_name = name
        self._message("Recette enregistrée :\n%s" % path, error=False)
        return True

    def cancel_refresh(self):
        if self._refresh_handle is not None:
            try:
                GLib.source_remove(self._refresh_handle)
            except Exception:
                pass
            self._refresh_handle = None

    def _message(self, text, error=True):
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.ERROR if error
            else Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK, text="Éditeur de recettes")
        dialog.format_secondary_text(text)
        dialog.run()
        dialog.destroy()


def edit_look(parent=None, look=None, sample_path=None):
    """Ouvre l'éditeur ; renvoie le nom de la recette enregistrée, ou ``None``.

    On s'appuie sur ``Gtk.Dialog.run()`` plutôt que sur une boucle principale
    imbriquée : la fenêtre de l'éditeur s'ouvre par-dessus celle du greffon
    sans perturber la sienne.
    """
    editor = LookEditor(parent, look, sample_path)
    editor.show_all()
    editor._schedule_refresh(now=True)
    try:
        while True:
            response = editor.run()
            if response == Gtk.ResponseType.APPLY:
                if editor.save():
                    break
            else:
                break
        return editor.saved_name
    finally:
        editor.cancel_refresh()
        editor.destroy()
