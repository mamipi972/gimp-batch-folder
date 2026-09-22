# -*- coding: utf-8 -*-
"""Tests de la logique pure (aucun GIMP nécessaire)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bfp import core  # noqa: E402
from bfp.core import BatchError  # noqa: E402


class TestRatio(unittest.TestCase):
    def test_formes_acceptees(self):
        for text, expected in (("16:9", 16 / 9), ("16/9", 16 / 9),
                               ("1:1", 1.0), ("3 : 2", 1.5), ("1.5", 1.5),
                               ("1,5", 1.5), ("4×3", 4 / 3)):
            self.assertAlmostEqual(core.parse_ratio(text), expected, places=6,
                                   msg=text)

    def test_formes_refusees(self):
        for text in ("", "abc", "16:0", "0:9", "-2", None):
            with self.assertRaises(BatchError):
                core.parse_ratio(text)


class TestCouleurs(unittest.TestCase):
    def test_aller_retour(self):
        self.assertEqual(core.rgba_to_hex(core.hex_to_rgba("#3a7fd5")),
                         "#3a7fd5")

    def test_raccourci_trois_chiffres(self):
        self.assertEqual(core.rgba_to_hex(core.hex_to_rgba("#abc")), "#aabbcc")

    def test_alpha(self):
        rgba = core.hex_to_rgba("#00000080")
        self.assertAlmostEqual(rgba[3], 128 / 255.0, places=4)

    def test_invalide(self):
        for text in ("", "#12", "#zzzzzz", None):
            with self.assertRaises(BatchError):
                core.hex_to_rgba(text)


class TestRedimensionnement(unittest.TestCase):
    def test_fit_conserve_le_ratio(self):
        width, height, crop = core.compute_resize(4000, 3000, "fit", 1920, 1920)
        self.assertEqual((width, height), (1920, 1440))
        self.assertFalse(crop)

    def test_fit_ne_grandit_pas_par_defaut(self):
        width, height, _ = core.compute_resize(800, 600, "fit", 1920, 1920)
        self.assertEqual((width, height), (800, 600))

    def test_fit_grandit_si_autorise(self):
        width, height, _ = core.compute_resize(800, 600, "fit", 1600, 1600,
                                               allow_upscale=True)
        self.assertEqual((width, height), (1600, 1200))

    def test_fill_demande_un_recadrage(self):
        width, height, crop = core.compute_resize(4000, 3000, "fill", 1000, 1000)
        self.assertTrue(crop)
        # L'image doit couvrir la boîte dans les deux dimensions.
        self.assertGreaterEqual(width, 1000)
        self.assertGreaterEqual(height, 1000)

    def test_largeur_imposee(self):
        self.assertEqual(core.compute_resize(4000, 3000, "width", 1000)[:2],
                         (1000, 750))

    def test_hauteur_imposee(self):
        self.assertEqual(core.compute_resize(4000, 3000, "height", 0, 750)[:2],
                         (1000, 750))

    def test_pourcentage(self):
        self.assertEqual(core.compute_resize(1000, 800, "percent",
                                             percent=50.0)[:2], (500, 400))

    def test_pourcentage_superieur_a_100_sans_agrandissement(self):
        self.assertEqual(core.compute_resize(1000, 800, "percent",
                                             percent=200.0)[:2], (1000, 800))

    def test_exact_deforme(self):
        self.assertEqual(core.compute_resize(1000, 800, "exact", 300, 300,
                                             allow_upscale=True)[:2],
                         (300, 300))

    def test_jamais_zero(self):
        width, height, _ = core.compute_resize(10000, 10, "fit", 20, 20)
        self.assertGreaterEqual(width, 1)
        self.assertGreaterEqual(height, 1)

    def test_entrees_invalides(self):
        with self.assertRaises(BatchError):
            core.compute_resize(0, 100, "fit", 100, 100)
        with self.assertRaises(BatchError):
            core.compute_resize(100, 100, "inconnu", 10, 10)


class TestRecadrage(unittest.TestCase):
    def test_ratio_paysage_vers_carre(self):
        x, y, width, height = core.compute_crop(4000, 3000, "ratio", ratio=1.0)
        self.assertEqual((width, height), (3000, 3000))
        self.assertEqual((x, y), (500, 0))

    def test_ratio_ancre_a_gauche(self):
        x, _y, _w, _h = core.compute_crop(4000, 3000, "ratio", ratio=1.0,
                                          anchor="left")
        self.assertEqual(x, 0)

    def test_marges(self):
        self.assertEqual(
            core.compute_crop(1000, 800, "margins", margin_left=10,
                              margin_right=20, margin_top=5, margin_bottom=15),
            (10, 5, 970, 780))

    def test_marges_trop_grandes(self):
        with self.assertRaises(BatchError):
            core.compute_crop(100, 100, "margins", margin_left=60,
                              margin_right=60)

    def test_manuel_borne_dans_l_image(self):
        self.assertEqual(core.compute_crop(1000, 800, "manual", x=900, y=700,
                                           width=500, height=500),
                         (900, 700, 100, 100))

    def test_manuel_zero_va_jusqu_au_bord(self):
        self.assertEqual(core.compute_crop(1000, 800, "manual", x=100, y=50),
                         (100, 50, 900, 750))

    def test_autocrop_est_delegue(self):
        self.assertIsNone(core.compute_crop(100, 100, "autocrop"))


class TestAncrage(unittest.TestCase):
    def test_coins(self):
        self.assertEqual(core.anchor_offset(1000, 1000, 100, 100, "top-left",
                                            20, 20), (20, 20))
        self.assertEqual(core.anchor_offset(1000, 1000, 100, 100,
                                            "bottom-right", 20, 20), (880, 880))

    def test_centre_ignore_les_marges(self):
        self.assertEqual(core.anchor_offset(1000, 1000, 100, 100, "center",
                                            50, 50), (450, 450))

    def test_ancrage_inconnu(self):
        with self.assertRaises(BatchError):
            core.anchor_offset(10, 10, 1, 1, "nulle-part")


class TestNommage(unittest.TestCase):
    def test_jetons(self):
        context = core.build_name_context("/photos/vacances/IMG_0042.JPG",
                                          index=7, width=800, height=600,
                                          extension=".webp")
        self.assertEqual(core.render_template("{name}{ext}", context),
                         "IMG_0042.webp")
        self.assertEqual(core.render_template("{parent}_{index:03d}{ext}",
                                              context),
                         "vacances_007.webp")
        self.assertEqual(core.render_template("{name}_{width}x{height}{ext}",
                                              context),
                         "IMG_0042_800x600.webp")

    def test_jeton_inconnu_reste_litteral(self):
        context = core.build_name_context("/a/b.png")
        self.assertEqual(core.render_template("{name}{inconnu}", context),
                         "b{inconnu}")

    def test_modele_casse(self):
        with self.assertRaises(BatchError):
            core.render_template("{name", core.build_name_context("/a/b.png"))

    def test_caracteres_interdits(self):
        self.assertEqual(core.sanitize_filename('a/b:c*d?'), "a_b_c_d_")

    def test_extension_cible(self):
        self.assertEqual(core.target_extension("jpeg", "/a/b.png"), ".jpg")
        self.assertEqual(core.target_extension("keep", "/a/b.PNG"), ".PNG")

    def test_format_depuis_extension(self):
        self.assertEqual(core.format_from_extension("/a/b.JPEG"), "jpeg")
        self.assertEqual(core.format_from_extension("/a/b.tif"), "tiff")
        self.assertIsNone(core.format_from_extension("/a/b.heic"))


class TestCollisions(unittest.TestCase):
    def test_ecrasement(self):
        self.assertEqual(core.resolve_collision("/x/a.png", "overwrite",
                                                exists=lambda p: True),
                         "/x/a.png")

    def test_ignorer(self):
        self.assertIsNone(core.resolve_collision("/x/a.png", "skip",
                                                 exists=lambda p: True))

    def test_renommer(self):
        taken = {"/x/a.png", "/x/a_1.png"}
        self.assertEqual(core.resolve_collision("/x/a.png", "rename",
                                                exists=lambda p: p in taken),
                         "/x/a_2.png")

    def test_libre(self):
        self.assertEqual(core.resolve_collision("/x/a.png", "skip",
                                                exists=lambda p: False),
                         "/x/a.png")


class TestExtensions(unittest.TestCase):
    def test_normalisation(self):
        self.assertEqual(core.normalise_extensions("JPG, *.png;  .webp"),
                         ("jpg", "png", "webp"))

    def test_vide_donne_les_defauts(self):
        self.assertEqual(core.normalise_extensions(""), core.DEFAULT_EXTENSIONS)


class TestParcours(unittest.TestCase):
    def setUp(self):
        self.tree = {
            "/racine": (["sous", ".cache"], ["a.jpg", "b.PNG", "notes.txt",
                                             ".secret.jpg"]),
            "/racine/sous": ([], ["c.webp"]),
            "/racine/.cache": ([], ["d.jpg"]),
        }

    def _walk(self, root):
        # os.walk laisse l'appelant élaguer « dirnames » sur place ; on imite
        # ce comportement, c'est précisément ce que le code exploite.
        stack = [root]
        while stack:
            current = stack.pop(0)
            dirnames, filenames = self.tree.get(current, ([], []))
            dirnames = list(dirnames)
            yield current, dirnames, list(filenames)
            stack = [os.path.join(current, d) for d in dirnames] + stack

    def test_non_recursif(self):
        found = core.iter_images("/racine", recursive=False, walker=self._walk)
        self.assertEqual([os.path.basename(p) for p in found],
                         ["a.jpg", "b.PNG"])

    def test_recursif_sans_dossiers_caches(self):
        found = core.iter_images("/racine", recursive=True, walker=self._walk)
        self.assertEqual([os.path.basename(p) for p in found],
                         ["a.jpg", "b.PNG", "c.webp"])


class TestReglages(unittest.TestCase):
    def test_valeurs_par_defaut_valides(self):
        settings = core.default_settings()
        self.assertEqual(core.coerce_settings(settings), settings)

    def test_types_forces(self):
        coerced = core.coerce_settings({"resize_width": "800",
                                        "recursive": "oui",
                                        "jpeg_quality": "85"})
        self.assertEqual(coerced["resize_width"], 800)
        self.assertTrue(coerced["recursive"])
        self.assertAlmostEqual(coerced["jpeg_quality"], 85.0)

    def test_valeurs_aberrantes_ramenees(self):
        coerced = core.coerce_settings({"jpeg_quality": 500,
                                        "png_compression": 42,
                                        "rotate_degrees": 45,
                                        "crop_anchor": "n_importe_quoi"})
        self.assertEqual(coerced["jpeg_quality"], 100.0)
        self.assertEqual(coerced["png_compression"], 9)
        self.assertEqual(coerced["rotate_degrees"], 0)
        self.assertEqual(coerced["crop_anchor"], "center")

    def test_cles_inconnues_ignorees(self):
        self.assertNotIn("inconnu", core.coerce_settings({"inconnu": 1}))

    def test_validation_signale_les_dossiers_manquants(self):
        problems = core.validate_settings(core.default_settings())
        self.assertTrue(any("source" in p.lower() for p in problems))


class TestPlanification(unittest.TestCase):
    def test_sortie_dans_un_sous_dossier(self):
        settings = core.default_settings()
        settings.update({"source_folder": "/photos",
                         "output_in_place": True,
                         "output_subfolder": "_sortie",
                         "output_format": "jpeg",
                         "name_template": "{name}_web{ext}"})
        path, fmt = core.plan_output(settings, "/photos/a.png", 1, 800, 600,
                                     exists=lambda p: False)
        self.assertEqual(path, "/photos/_sortie/a_web.jpg")
        self.assertEqual(fmt, "jpeg")

    def test_arborescence_recreee(self):
        settings = core.default_settings()
        settings.update({"source_folder": "/photos",
                         "output_in_place": False,
                         "output_folder": "/export",
                         "keep_tree": True})
        path, _ = core.plan_output(settings, "/photos/2024/été/a.jpg", 1,
                                   800, 600, exists=lambda p: False)
        self.assertEqual(path, "/export/2024/été/a.jpg")

    def test_le_fichier_source_n_est_jamais_ecrase(self):
        settings = core.default_settings()
        settings.update({"source_folder": "/photos",
                         "output_in_place": True,
                         "output_subfolder": "",
                         "overwrite_policy": "overwrite"})
        path, _ = core.plan_output(settings, "/photos/a.jpg", 1, 800, 600,
                                   exists=lambda p: False)
        self.assertEqual(path, "/photos/a_traite.jpg")


if __name__ == "__main__":
    unittest.main(verbosity=2)
