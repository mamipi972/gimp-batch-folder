#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Traitement par lots d'un dossier — greffon GIMP 3 (Python)
#
# Redimensionner, recadrer, pivoter, filigraner, appliquer une recette de
# look GEGL, convertir et renommer toutes les images d'un dossier ; exporter
# aussi chaque calque dans son propre fichier.
#
# Installation : copier le dossier « gimp-batch-folder » complet dans
#   Édition ▸ Préférences ▸ Dossiers ▸ Greffons
# puis rendre ce fichier exécutable (chmod +x) et relancer GIMP.
#
# Licence : GPL-3.0-or-later

import os
import sys

# Le dossier du greffon doit être dans sys.path pour que le paquet « bfp »
# soit importable : GIMP exécute ce fichier directement, sans l'installer.
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

import gi

gi.require_version("Gimp", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gegl, Gimp, GLib, GObject  # noqa: E402

from bfp import paths, presets as presets_mod  # noqa: E402
from bfp.core import (  # noqa: E402
    BatchError, coerce_settings, default_settings, validate_settings)
from bfp.looks import load_looks  # noqa: E402

PROC_INTERACTIVE = "python-fu-batch-folder"
PROC_HEADLESS = "python-fu-batch-folder-run"

AUTHOR = "Greffon « Traitement par lots d'un dossier »"
COPYRIGHT = "GPL-3.0-or-later"
YEAR = "2026"


def _json_settings(raw_json, base=None):
    """Fusionne un fragment JSON avec des réglages de base."""
    import json

    settings = dict(base or default_settings())
    if raw_json:
        try:
            data = json.loads(raw_json)
        except ValueError as exc:
            raise BatchError("JSON de réglages invalide : %s" % exc)
        if not isinstance(data, dict):
            raise BatchError("Le JSON de réglages doit être un objet.")
        settings.update(data)
    return coerce_settings(settings)


# ---------------------------------------------------------------------------
# Procédure interactive
# ---------------------------------------------------------------------------

def run_interactive(procedure, config, _data):
    Gegl.init(None)

    try:
        run_mode = config.get_property("run-mode")
    except Exception:
        run_mode = Gimp.RunMode.INTERACTIVE

    if run_mode != Gimp.RunMode.INTERACTIVE:
        Gimp.message("Utilisez « %s » pour un traitement non interactif."
                     % PROC_HEADLESS)
        return procedure.new_return_values(Gimp.PDBStatusType.CALLING_ERROR,
                                           GLib.Error())

    try:
        from bfp.ui import run_dialog
    except Exception as exc:
        Gimp.message("Interface indisponible : %s" % exc)
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR,
                                           GLib.Error())

    settings = presets_mod.load_last_settings()
    try:
        run_dialog(settings)
    except Exception as exc:
        Gimp.message("Erreur du greffon : %s" % exc)
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR,
                                           GLib.Error())

    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


# ---------------------------------------------------------------------------
# Procédure non interactive (scripts, gimp-console-3.0)
# ---------------------------------------------------------------------------

def run_headless(procedure, config, _data):
    Gegl.init(None)

    def prop(name, fallback=""):
        try:
            value = config.get_property(name)
        except Exception:
            return fallback
        return fallback if value is None else value

    try:
        preset = (prop("preset") or "").strip()
        base = presets_mod.load_preset(preset) if preset \
            else presets_mod.load_last_settings()
        settings = _json_settings(prop("settings-json"), base)

        source = (prop("source-folder") or "").strip()
        if source:
            settings["source_folder"] = source
        output = (prop("output-folder") or "").strip()
        if output:
            settings["output_folder"] = output
            settings["output_in_place"] = False
        if prop("dry-run", False):
            settings["dry_run"] = True

        problems = validate_settings(settings)
        if problems:
            raise BatchError("Réglages invalides :\n" +
                             "\n".join("- " + p for p in problems))

        from bfp.gimpops import Reporter
        from bfp.runner import BatchRunner

        reporter = Reporter(echo=lambda line: print(line, flush=True))
        looks, look_errors = load_looks(paths.look_directories())
        for message in look_errors:
            reporter.warn(message)

        runner = BatchRunner(settings, looks, reporter=reporter)
        result = runner.run()
        print(result.summary(), flush=True)

        values = procedure.new_return_values(Gimp.PDBStatusType.SUCCESS,
                                             GLib.Error())
        try:
            # new_return_values() a déjà réservé la place de « files-written » ;
            # on la remplace par le compte réel.
            if values.length() > 1:
                values.remove(1)
            values.insert(1, GObject.Value(GObject.TYPE_INT,
                                           int(result.written)))
        except Exception:
            pass
        return values

    except BatchError as exc:
        Gimp.message(str(exc))
        print("ERREUR %s" % exc, file=sys.stderr, flush=True)
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR,
                                           GLib.Error())
    except Exception as exc:
        Gimp.message("Erreur inattendue : %s" % exc)
        print("ERREUR %s" % exc, file=sys.stderr, flush=True)
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR,
                                           GLib.Error())


# ---------------------------------------------------------------------------
# Enregistrement
# ---------------------------------------------------------------------------

class BatchFolderPlugIn(Gimp.PlugIn):
    """Point d'entrée vu par GIMP."""

    def do_set_i18n(self, _procname):
        # Le greffon est livré en français, sans catalogue gettext.
        return False

    def do_query_procedures(self):
        return [PROC_INTERACTIVE, PROC_HEADLESS]

    def do_create_procedure(self, name):
        Gegl.init(None)

        if name == PROC_INTERACTIVE:
            procedure = Gimp.Procedure.new(self, name,
                                           Gimp.PDBProcType.PLUGIN,
                                           run_interactive, None)
            procedure.set_menu_label("_Traitement par lots d'un dossier…")
            procedure.add_menu_path("<Image>/File/")
            procedure.set_documentation(
                "Traiter par lots toutes les images d'un dossier",
                "Redimensionner, recadrer, pivoter, filigraner, appliquer "
                "une recette de look GEGL, convertir et renommer toutes les "
                "images d'un dossier, et exporter les calques séparément.",
                name)
            procedure.set_attribution(AUTHOR, AUTHOR, YEAR)
            procedure.add_enum_argument(
                "run-mode", "Mode d'exécution", "Mode d'exécution",
                Gimp.RunMode, Gimp.RunMode.INTERACTIVE,
                GObject.ParamFlags.READWRITE)
            return procedure

        if name == PROC_HEADLESS:
            procedure = Gimp.Procedure.new(self, name,
                                           Gimp.PDBProcType.PLUGIN,
                                           run_headless, None)
            procedure.set_documentation(
                "Traitement par lots d'un dossier, sans interface",
                "Version scriptable : indiquez un préréglage enregistré "
                "et/ou un fragment JSON de réglages. Renvoie le nombre de "
                "fichiers écrits.",
                name)
            procedure.set_attribution(AUTHOR, AUTHOR, YEAR)

            procedure.add_enum_argument(
                "run-mode", "Mode d'exécution", "Mode d'exécution",
                Gimp.RunMode, Gimp.RunMode.NONINTERACTIVE,
                GObject.ParamFlags.READWRITE)
            procedure.add_string_argument(
                "preset", "Préréglage",
                "Nom d'un préréglage enregistré (vide = derniers réglages)",
                "", GObject.ParamFlags.READWRITE)
            procedure.add_string_argument(
                "settings-json", "Réglages JSON",
                "Objet JSON fusionné par-dessus le préréglage",
                "", GObject.ParamFlags.READWRITE)
            procedure.add_string_argument(
                "source-folder", "Dossier source",
                "Dossier à traiter (prioritaire sur le préréglage)",
                "", GObject.ParamFlags.READWRITE)
            procedure.add_string_argument(
                "output-folder", "Dossier de sortie",
                "Dossier de destination (vide = comportement du préréglage)",
                "", GObject.ParamFlags.READWRITE)
            procedure.add_boolean_argument(
                "dry-run", "Simulation",
                "N'écrire aucun fichier, seulement journaliser",
                False, GObject.ParamFlags.READWRITE)

            try:
                procedure.add_int_return_value(
                    "files-written", "Fichiers écrits",
                    "Nombre de fichiers effectivement écrits",
                    0, GLib.MAXINT, 0, GObject.ParamFlags.READWRITE)
            except Exception:
                # Si cette version de libgimp ne connaît pas cette méthode,
                # mieux vaut une procédure sans valeur de retour qu'un greffon
                # qui refuse de s'enregistrer.
                pass
            return procedure

        return None


Gimp.main(BatchFolderPlugIn.__gtype__, sys.argv)
