# -*- coding: utf-8 -*-
"""
Tests de la boîte de dialogue contre un faux GTK (voir ``fakegtk``).

Ce que ces tests valident vraiment : que chaque réglage est relié à un
widget, que l'aller-retour réglages → widgets → réglages est fidèle (un
préréglage rechargé doit redonner exactement les mêmes réglages), et que la
logique de sensibilité correspond aux cases cochées.

Ce qu'ils ne valident pas : l'API GTK elle-même, qui n'est pas installable
ici. Le rendu doit être vérifié dans GIMP.
"""

import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import fakegi  # noqa: E402

fakegi.install(with_gtk=True)

from bfp import ui  # noqa: E402
from bfp.core import default_settings  # noqa: E402


class UITestCase(unittest.TestCase):
    def setUp(self):
        fakegi.reset()
        self.tmp = tempfile.mkdtemp(prefix="bfp-ui-")
        os.environ["FAKE_GIMP_DIR"] = os.path.join(self.tmp, "gimp")
        self.dialog = ui.BatchDialog()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestConstruction(UITestCase):
    def test_six_onglets(self):
        self.assertEqual(self.dialog.notebook.get_n_pages(), 6)

    def test_toutes_les_cles_sont_reliees(self):
        """Aucun réglage ne doit être orphelin dans l'interface."""
        non_exposees = {
            "version",            # interne
            "dry_run",            # décidé par le bouton « Simuler »
            "resize_interpolation",
        }
        manquantes = set()
        for key in default_settings():
            if key in non_exposees:
                continue
            if self.dialog.binder.widget(key) is None:
                manquantes.add(key)
        self.assertEqual(manquantes, set(),
                         "réglages sans widget : %s" % sorted(manquantes))

    def test_recettes_proposees(self):
        identifiants = [i for i, _t in self.dialog.look_combo.items]
        self.assertIn("Argentique doux", identifiants)


class TestAllerRetour(UITestCase):
    def test_valeurs_par_defaut(self):
        settings = default_settings()
        # Le dialogue présélectionne la première recette quand aucune n'est
        # choisie : on part donc de cet état-là.
        settings["look_name"] = self.dialog._looks[0].name
        self.dialog.binder.apply(settings)
        collected = self.dialog.binder.collect()
        differences = {k: (settings[k], collected[k])
                       for k in settings
                       if k in collected and settings[k] != collected[k]}
        # dry_run est volontairement remis à False par le dialogue.
        differences.pop("dry_run", None)
        self.assertEqual(differences, {})

    def test_valeurs_personnalisees(self):
        settings = default_settings()
        settings.update({
            "recursive": True,
            "extensions": "jpg,png",
            "output_in_place": False,
            "output_subfolder": "export",
            "keep_tree": False,
            "name_template": "{parent}_{index:03d}{ext}",
            "overwrite_policy": "skip",
            "rotate_enabled": True,
            "rotate_degrees": 270,
            "flip_horizontal": True,
            "crop_enabled": True,
            "crop_mode": "margins",
            "crop_margin_left": 12,
            "crop_margin_bottom": 34,
            "crop_anchor": "top-left",
            "resize_enabled": True,
            "resize_mode": "percent",
            "resize_percent": 42.0,
            "resize_allow_upscale": True,
            "color_enabled": True,
            "color_mode": "grayscale",
            "background_color": "#123456",
            "look_enabled": True,
            "look_name": "Punch web",
            "look_opacity": 65.0,
            "wm_text_enabled": True,
            "wm_text": "© test",
            "wm_text_font": "Serif Italic",
            "wm_text_size": 12.5,
            "wm_text_size_is_percent": False,
            "wm_text_color": "#abcdef",
            "wm_text_opacity": 33.0,
            "wm_text_anchor": "top-right",
            "wm_text_angle": -45.0,
            "output_format": "webp",
            "webp_quality": 77.0,
            "webp_lossless": True,
            "png_compression": 3,
            "tiff_compression": "deflate",
            "strip_metadata": True,
            "export_layers": True,
            "export_layers_only": True,
            "export_layers_crop": True,
            "stop_on_error": True,
        })
        self.dialog.binder.apply(settings)
        collected = self.dialog.binder.collect()
        for key, expected in settings.items():
            if key in ("dry_run", "version", "source_folder", "output_folder",
                       "wm_image_path", "resize_interpolation"):
                continue
            self.assertEqual(collected[key], expected,
                             "réglage « %s » mal restitué" % key)

    def test_le_choix_du_dossier_de_destination_bascule_le_radio(self):
        self.dialog.binder.apply({"output_in_place": False})
        self.assertFalse(self.dialog.dest_inplace.get_active())
        self.assertTrue(self.dialog.dest_elsewhere.get_active())
        self.assertFalse(self.dialog.binder.collect()["output_in_place"])

        self.dialog.binder.apply({"output_in_place": True})
        self.assertTrue(self.dialog.binder.collect()["output_in_place"])


class TestSensibilite(UITestCase):
    def _sensitive(self, key):
        return self.dialog.binder.widget(key).get_sensitive()

    def test_sections_desactivees_par_defaut(self):
        self.dialog.binder.apply(default_settings())
        self.dialog._update_sensitivity()
        for key in ("resize_mode", "look_name", "wm_text", "wm_image_scale",
                    "color_mode", "export_layers_only"):
            self.assertFalse(self._sensitive(key),
                             "« %s » devrait être désactivé" % key)

    def test_activation_d_une_section(self):
        self.dialog.binder.apply({"resize_enabled": True,
                                  "wm_text_enabled": True})
        self.dialog._update_sensitivity()
        self.assertTrue(self._sensitive("resize_mode"))
        self.assertTrue(self._sensitive("wm_text"))
        self.assertFalse(self._sensitive("wm_image_scale"))

    def test_le_mode_de_recadrage_pilote_les_champs(self):
        self.dialog.binder.apply({"crop_enabled": True, "crop_mode": "margins"})
        self.dialog._update_sensitivity()
        self.assertTrue(self._sensitive("crop_margin_left"))
        self.assertFalse(self._sensitive("crop_x"))
        self.assertFalse(self._sensitive("crop_ratio"))

        self.dialog.binder.apply({"crop_mode": "manual"})
        self.dialog._update_sensitivity()
        self.assertTrue(self._sensitive("crop_x"))
        self.assertFalse(self._sensitive("crop_margin_left"))

    def test_destination(self):
        self.dialog.binder.apply({"output_in_place": True})
        self.dialog._update_sensitivity()
        self.assertTrue(self._sensitive("output_subfolder"))
        self.assertFalse(self.dialog.output_chooser.get_sensitive())

        self.dialog.binder.apply({"output_in_place": False})
        self.dialog._update_sensitivity()
        self.assertFalse(self._sensitive("output_subfolder"))
        self.assertTrue(self.dialog.output_chooser.get_sensitive())


class TestPrereglagesDepuisLInterface(UITestCase):
    def test_enregistrer_puis_recharger(self):
        self.dialog.binder.apply({"resize_enabled": True,
                                  "resize_width": 1234,
                                  "wm_text": "signature"})
        self.dialog.preset_combo.get_child().set_text("Mon réglage")
        self.dialog._on_save_preset(None)

        self.dialog.binder.apply(default_settings())
        self.assertEqual(self.dialog.binder.collect()["resize_width"], 1920)

        self.dialog.preset_combo.get_child().set_text("Mon réglage")
        self.dialog._on_load_preset(None)
        collected = self.dialog.binder.collect()
        self.assertEqual(collected["resize_width"], 1234)
        self.assertEqual(collected["wm_text"], "signature")

    def test_reinitialiser(self):
        self.dialog.binder.apply({"resize_width": 999})
        self.dialog._on_reset(None)
        self.assertEqual(self.dialog.binder.collect()["resize_width"], 1920)


class TestValidationAvantLancement(UITestCase):
    def test_refus_sans_dossier_source(self):
        messages = []
        self.dialog._message = lambda text, error=False: messages.append(text)
        self.dialog._start(dry_run=True)
        self.assertTrue(messages)
        self.assertIn("source", messages[0].lower())

    def test_lancement_avec_un_dossier_valide(self):
        source = os.path.join(self.tmp, "images")
        os.makedirs(source)
        with open(os.path.join(source, "a.jpg"), "wb") as handle:
            handle.write(b"x")

        messages = []
        self.dialog._message = lambda text, error=False: messages.append(text)
        self.dialog.source_chooser.set_filename(source)
        self.dialog._start(dry_run=True)

        self.assertEqual(messages, [])
        self.assertIn("1 image(s) à traiter", self.dialog.log_buffer.text)
        self.assertIn("simulation", self.dialog.log_buffer.text.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
