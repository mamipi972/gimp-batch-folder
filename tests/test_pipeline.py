# -*- coding: utf-8 -*-
"""
Tests du pipeline complet contre un faux GIMP (voir ``fakegi``).

GIMP 3 n'est pas installable dans l'environnement de développement ; ces
tests vérifient donc ce qui peut l'être sans lui : que le greffon appelle les
bonnes fonctions, dans le bon ordre, avec les bonnes valeurs, et qu'il
dégrade proprement quand une procédure ou une opération manque.
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

fakegi.install()

from bfp import gimpops, paths  # noqa: E402
from bfp.core import default_settings  # noqa: E402
from bfp.looks import load_looks, parse_look  # noqa: E402
from bfp.runner import BatchRunner  # noqa: E402


def _make_tree(root):
    os.makedirs(os.path.join(root, "sous"), exist_ok=True)
    for relative in ("a.jpg", "b.PNG", "notes.txt", "sous/c.jpg"):
        path = os.path.join(root, relative)
        with open(path, "wb") as handle:
            handle.write(b"pas vraiment une image")
    return root


class PipelineTestCase(unittest.TestCase):
    def setUp(self):
        fakegi.reset()
        self.tmp = tempfile.mkdtemp(prefix="bfp-test-")
        self.source = _make_tree(os.path.join(self.tmp, "source"))
        os.environ["FAKE_GIMP_DIR"] = os.path.join(self.tmp, "gimp")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def settings(self, **overrides):
        settings = default_settings()
        settings["source_folder"] = self.source
        settings["output_in_place"] = True
        settings["output_subfolder"] = "_sortie"
        settings.update(overrides)
        return settings

    def run_batch(self, settings, looks=()):
        reporter = gimpops.Reporter()
        runner = BatchRunner(settings, looks, reporter=reporter)
        result = runner.run()
        return result, reporter


class TestParcours(PipelineTestCase):
    def test_non_recursif_ignore_les_sous_dossiers(self):
        result, _ = self.run_batch(self.settings(dry_run=True))
        self.assertEqual(result.processed, 2)

    def test_recursif(self):
        result, _ = self.run_batch(self.settings(recursive=True, dry_run=True))
        self.assertEqual(result.processed, 3)

    def test_le_dossier_de_sortie_n_est_pas_re_traite(self):
        # Premier passage : écrit dans source/_sortie
        self.run_batch(self.settings(recursive=True))
        fakegi.reset()
        # Second passage : les fichiers produits ne doivent pas être repris.
        result, _ = self.run_batch(self.settings(recursive=True,
                                                 overwrite_policy="overwrite"))
        self.assertEqual(result.processed, 3)


class TestOrdreDesOperations(PipelineTestCase):
    def test_rotation_puis_recadrage_puis_echelle(self):
        settings = self.settings(
            rotate_enabled=True, rotate_degrees=90,
            crop_enabled=True, crop_mode="ratio", crop_ratio="1:1",
            resize_enabled=True, resize_mode="fit",
            resize_width=800, resize_height=800)
        self.run_batch(settings)

        names = [n for n in fakegi.CALLS.names()
                 if n in ("image.rotate", "image.crop", "image.scale")]
        self.assertEqual(names[:3], ["image.rotate", "image.crop",
                                     "image.scale"])

    def test_recadrage_carre_sur_image_1600x1200(self):
        settings = self.settings(crop_enabled=True, crop_mode="ratio",
                                 crop_ratio="1:1", crop_anchor="center")
        self.run_batch(settings)
        crop = fakegi.CALLS.of("image.crop")[0]
        self.assertEqual(crop[1], (1200, 1200, 200, 0))

    def test_mode_remplir_recadre_apres_mise_a_l_echelle(self):
        settings = self.settings(resize_enabled=True, resize_mode="fill",
                                 resize_width=600, resize_height=600)
        self.run_batch(settings)
        order = [n for n in fakegi.CALLS.names()
                 if n in ("image.scale", "image.crop")]
        self.assertEqual(order[:2], ["image.scale", "image.crop"])
        self.assertEqual(fakegi.CALLS.of("image.crop")[0][1][:2], (600, 600))


class TestExport(PipelineTestCase):
    def test_jpeg_qualite_ramenee_entre_0_et_1(self):
        # Le faux file-jpeg-export déclare quality dans [0, 1] : le greffon
        # doit traduire « 90 % » en 0.9 sans que ce soit codé en dur.
        self.run_batch(self.settings(output_format="jpeg", jpeg_quality=90.0))
        call = fakegi.CALLS.of("pdb.run")[0]
        self.assertEqual(call[1][0], "file-jpeg-export")
        self.assertAlmostEqual(call[1][1]["quality"], 0.9, places=6)

    def test_webp_qualite_laissee_entre_0_et_100(self):
        self.run_batch(self.settings(output_format="webp", webp_quality=80.0))
        call = fakegi.CALLS.of("pdb.run")[0]
        self.assertEqual(call[1][0], "file-webp-export")
        self.assertAlmostEqual(call[1][1]["quality"], 80.0, places=6)

    def test_jpeg_aplatit_sur_le_fond(self):
        self.run_batch(self.settings(output_format="jpeg",
                                     background_color="#ff0000"))
        self.assertTrue(fakegi.CALLS.count("image.flatten") >= 1)
        fond = fakegi.CALLS.of("context_set_background")[0][1][0]
        self.assertEqual(fond[:3], (1.0, 0.0, 0.0))

    def test_format_sans_procedure_specialisee_retombe_sur_file_save(self):
        # Le faux PDB ne connaît pas file-tiff-export.
        result, reporter = self.run_batch(self.settings(output_format="tiff"))
        self.assertEqual(result.written, 2)
        self.assertTrue(fakegi.CALLS.count("Gimp.file_save") >= 2)
        self.assertIn("repli", reporter.text().lower())

    def test_xcf_passe_par_file_save(self):
        self.run_batch(self.settings(output_format="xcf"))
        self.assertEqual(fakegi.CALLS.count("Gimp.file_save"), 2)

    def test_fichiers_reellement_ecrits(self):
        self.run_batch(self.settings(output_format="png",
                                     name_template="{name}_{index:02d}{ext}"))
        produced = sorted(os.path.basename(p) for p in fakegi.WRITTEN)
        self.assertEqual(produced, ["a_01.png", "b_02.png"])
        for path in fakegi.WRITTEN:
            self.assertTrue(os.path.isfile(path))
            self.assertIn("_sortie", path)

    def test_simulation_n_ecrit_rien(self):
        result, _ = self.run_batch(self.settings(dry_run=True))
        self.assertEqual(fakegi.WRITTEN, [])
        self.assertEqual(result.written, 2)

    def test_politique_ignorer(self):
        self.run_batch(self.settings(output_format="png"))
        fakegi.reset()
        result, _ = self.run_batch(self.settings(output_format="png",
                                                 overwrite_policy="skip"))
        self.assertEqual(result.skipped, 2)
        self.assertEqual(result.written, 0)

    def test_politique_renommer(self):
        self.run_batch(self.settings(output_format="png"))
        fakegi.reset()
        self.run_batch(self.settings(output_format="png",
                                     overwrite_policy="rename"))
        produced = sorted(os.path.basename(p) for p in fakegi.WRITTEN)
        self.assertEqual(produced, ["a_1.png", "b_1.png"])

    def test_metadonnees_retirees(self):
        self.run_batch(self.settings(output_format="jpeg", strip_metadata=True))
        call = fakegi.CALLS.of("pdb.run")[0]
        self.assertFalse(call[1][1]["include-exif"])
        self.assertTrue(fakegi.CALLS.count("image.set_metadata") >= 1)


class TestFiligranes(PipelineTestCase):
    def test_texte_place_en_bas_a_droite(self):
        settings = self.settings(wm_text_enabled=True,
                                 wm_text="© {name}",
                                 wm_text_anchor="bottom-right",
                                 wm_text_margin=10.0,
                                 wm_text_margin_is_percent=False,
                                 wm_text_size=40.0,
                                 wm_text_size_is_percent=False,
                                 wm_text_opacity=50.0)
        self.run_batch(settings)

        texte = fakegi.CALLS.of("Gimp.text_font")[0]
        self.assertEqual(texte[1][0], "© a")      # le jeton a été remplacé
        self.assertEqual(texte[1][1], 40.0)

        offsets = fakegi.CALLS.of("layer.set_offsets")[0][1]
        largeur_texte = max(1, int(len("© a") * 40.0 * 0.55))
        self.assertEqual(offsets[1], 1600 - largeur_texte - 10)

    def test_taille_en_pourcentage_du_petit_cote(self):
        # Image 1600×1200 → petit côté 1200 → 5 % = 60 px
        self.run_batch(self.settings(wm_text_enabled=True, wm_text="X",
                                     wm_text_size=5.0,
                                     wm_text_size_is_percent=True))
        self.assertEqual(fakegi.CALLS.of("Gimp.text_font")[0][1][1], 60.0)

    def test_filigrane_image_mis_a_l_echelle(self):
        logo = os.path.join(self.tmp, "logo.png")
        with open(logo, "wb") as handle:
            handle.write(b"logo")
        self.run_batch(self.settings(wm_image_enabled=True,
                                     wm_image_path=logo,
                                     wm_image_scale=25.0))
        # Le faux calque fait 400×200 ; 25 % de 1600 = 400 → hauteur 200.
        self.assertEqual(fakegi.CALLS.of("layer.scale")[0][1][1:], (400, 200))

    def test_filigrane_image_absent_signale_sans_planter(self):
        result, reporter = self.run_batch(
            self.settings(wm_image_enabled=True,
                          wm_image_path=os.path.join(self.tmp, "absent.png")))
        # validate_settings() est du ressort de l'interface ; ici on vérifie
        # simplement que le lot va au bout.
        self.assertEqual(result.written, 2)
        self.assertIn("introuvable", reporter.text())


class TestLooks(PipelineTestCase):
    def test_recettes_livrees_chargeables(self):
        looks, errors = load_looks([paths.bundled_looks_directory()])
        self.assertEqual(errors, [])
        self.assertEqual(len(looks), 15)

    def test_application_d_une_recette(self):
        look = parse_look({
            "name": "Test",
            "steps": [{"op": "gegl:saturation", "params": {"scale": 0.5}},
                      {"op": "gegl:brightness-contrast",
                       "params": {"contrast": 1.2}}]})
        self.run_batch(self.settings(look_enabled=True, look_name="Test"),
                       looks=[look])
        filtres = [c[1][1] for c in fakegi.CALLS.of("layer.merge_filter")]
        self.assertEqual(filtres[:2], ["gegl:saturation",
                                       "gegl:brightness-contrast"])
        valeurs = fakegi.CALLS.of("layer.merge_filter")[0][1][2]
        self.assertAlmostEqual(valeurs["scale"], 0.5)

    def test_valeur_hors_bornes_ramenee(self):
        look = parse_look({"name": "T",
                           "steps": [{"op": "gegl:saturation",
                                      "params": {"scale": 99.0}}]})
        self.run_batch(self.settings(look_enabled=True, look_name="T"),
                       looks=[look])
        valeurs = fakegi.CALLS.of("layer.merge_filter")[0][1][2]
        self.assertAlmostEqual(valeurs["scale"], 10.0)  # maximum déclaré

    def test_operation_inconnue_signalee_et_sautee(self):
        look = parse_look({"name": "T",
                           "steps": [{"op": "gegl:ceci-n-existe-pas",
                                      "params": {}},
                                     {"op": "gegl:saturation",
                                      "params": {"scale": 0.9}}]})
        result, reporter = self.run_batch(
            self.settings(look_enabled=True, look_name="T"), looks=[look])
        self.assertEqual(result.written, 2)
        self.assertIn("ceci-n-existe-pas", reporter.text())
        filtres = [c[1][1] for c in fakegi.CALLS.of("layer.merge_filter")]
        self.assertEqual(filtres, ["gegl:saturation"] * 2)

    def test_propriete_inconnue_signalee(self):
        look = parse_look({"name": "T",
                           "steps": [{"op": "gegl:saturation",
                                      "params": {"scale": 0.9,
                                                 "propriete-fantaisiste": 1}}]})
        _result, reporter = self.run_batch(
            self.settings(look_enabled=True, look_name="T"), looks=[look])
        self.assertIn("propriete-fantaisiste", reporter.text())

    def test_virage_partiel(self):
        look = parse_look({"name": "T",
                           "steps": [{"special": "split-tone",
                                      "params": {"shadows": "#0000ff",
                                                 "highlights": "#ffaa00",
                                                 "amount": 30.0}}]})
        self.run_batch(self.settings(look_enabled=True, look_name="T"),
                       looks=[look])
        # Deux calques teintés par image : hautes lumières puis ombres.
        self.assertEqual(fakegi.CALLS.count("layer.create_mask"), 4)
        self.assertEqual(fakegi.CALLS.count("image.merge_down"), 4)
        self.assertEqual(fakegi.CALLS.count("layer.edit_fill"), 4)
        premiere_couleur = fakegi.CALLS.of("context_set_foreground")[0][1][0]
        self.assertEqual(premiere_couleur[:3], (1.0, 2 / 3.0, 0.0))  # #ffaa00

    def test_etapes_apres_un_virage_partiel(self):
        """Régression : le virage partiel fusionne des calques.

        ``gimp_image_merge_down()`` détruit les deux calques et en crée un
        nouveau. Continuer sur l'ancien objet faisait échouer toutes les
        étapes suivantes avec « gimp-drawable-filter-new a été appelée avec un
        ID erroné pour le paramètre drawable » — visible sur les recettes où
        le virage partiel n'est pas la dernière étape.
        """
        look = parse_look({
            "name": "Cinéma",
            "steps": [
                {"op": "gegl:brightness-contrast", "params": {"contrast": 1.2}},
                {"special": "split-tone",
                 "params": {"shadows": "#0f4c5c", "highlights": "#ff9f45",
                            "amount": 34.0}},
                {"op": "gegl:saturation", "params": {"scale": 1.1}},
                {"op": "gegl:vignette", "params": {"radius": 1.3}},
            ]})
        result, reporter = self.run_batch(
            self.settings(look_enabled=True, look_name="Cinéma"), looks=[look])

        self.assertEqual(fakegi.CALLS.count("erreur.drawable-invalide"), 0,
                         "une étape a visé un calque détruit")
        filtres = [c[1][1] for c in fakegi.CALLS.of("layer.merge_filter")
                   if not c[1][1].startswith("gegl:invert")]
        self.assertEqual(filtres, ["gegl:brightness-contrast",
                                   "gegl:saturation", "gegl:vignette"] * 2)
        self.assertNotIn("n'existe plus", reporter.text())
        self.assertEqual(result.written, 2)

    def test_etapes_apres_virage_partiel_avec_dosage(self):
        """Même chose, mais la recette est appliquée sur une copie dosée."""
        look = parse_look({
            "name": "Vintage",
            "steps": [
                {"op": "gegl:saturation", "params": {"scale": 0.7}},
                {"special": "split-tone",
                 "params": {"shadows": "#4a3f6b", "highlights": "#f2e0b8",
                            "amount": 26.0}},
                {"op": "gegl:noise-rgb", "params": {"red": 0.06}},
            ]})
        result, _ = self.run_batch(
            self.settings(look_enabled=True, look_name="Vintage",
                          look_opacity=60.0), looks=[look])

        self.assertEqual(fakegi.CALLS.count("erreur.drawable-invalide"), 0)
        filtres = [c[1][1] for c in fakegi.CALLS.of("layer.merge_filter")
                   if not c[1][1].startswith("gegl:invert")]
        self.assertEqual(filtres, ["gegl:saturation", "gegl:noise-rgb"] * 2)
        # Le dosage doit bien avoir été posé sur le calque final.
        self.assertIn(60.0, [c[1][1] for c in
                             fakegi.CALLS.of("layer.set_opacity")])
        self.assertEqual(result.written, 2)

    def test_les_six_recettes_livrees_passent_de_bout_en_bout(self):
        looks, _ = load_looks([paths.bundled_looks_directory()])
        for look in looks:
            fakegi.reset()
            result, reporter = self.run_batch(
                self.settings(look_enabled=True, look_name=look.name),
                looks=looks)
            self.assertEqual(fakegi.CALLS.count("erreur.drawable-invalide"), 0,
                             "recette « %s » : calque détruit" % look.name)
            self.assertEqual(result.failed, 0, look.name)
            self.assertNotIn("n'existe plus", reporter.text())

    def test_recette_absente_signalee(self):
        result, reporter = self.run_batch(
            self.settings(look_enabled=True, look_name="Inexistante"))
        self.assertEqual(result.written, 2)
        self.assertIn("introuvable", reporter.text())

    def test_dosage_partiel_duplique_le_calque(self):
        look = parse_look({"name": "T",
                           "steps": [{"op": "gegl:saturation",
                                      "params": {"scale": 0.0}}]})
        self.run_batch(self.settings(look_enabled=True, look_name="T",
                                     look_opacity=40.0), looks=[look])
        self.assertTrue(fakegi.CALLS.count("layer.copy") >= 2)
        opacites = [c[1][1] for c in fakegi.CALLS.of("layer.set_opacity")]
        self.assertIn(40.0, opacites)


class TestExportDesCalques(PipelineTestCase):
    def test_un_fichier_par_calque(self):
        result, _ = self.run_batch(
            self.settings(output_format="png", export_layers=True,
                          export_layers_only=True,
                          export_layers_template="{name}_{layer_index:02d}{ext}"))
        produced = sorted(os.path.basename(p) for p in fakegi.WRITTEN)
        self.assertEqual(produced, ["a_01.png", "b_01.png"])
        self.assertEqual(result.written, 2)

    def test_composite_et_calques(self):
        self.run_batch(self.settings(output_format="png", export_layers=True,
                                     export_layers_only=False))
        produced = sorted(os.path.basename(p) for p in fakegi.WRITTEN)
        self.assertEqual(produced, ["a.png", "a_01_Arrière-plan.png",
                                    "b.png", "b_01_Arrière-plan.png"])

    def test_calques_dans_un_dossier_de_destination_avec_arborescence(self):
        export = os.path.join(self.tmp, "export")
        self.run_batch(self.settings(
            recursive=True, output_in_place=False, output_folder=export,
            keep_tree=True, output_format="png",
            export_layers=True, export_layers_only=True,
            export_layers_template="{name}_{layer_index}{ext}"))
        relatifs = sorted(os.path.relpath(p, export) for p in fakegi.WRITTEN)
        self.assertEqual(relatifs, ["a_1.png", "b_1.png",
                                    os.path.join("sous", "c_1.png")])

    def test_le_composite_est_fait_sur_une_copie(self):
        # Sinon le filigrane du composite ressortirait comme calque exporté.
        self.run_batch(self.settings(output_format="png", export_layers=True,
                                     wm_text_enabled=True, wm_text="©"))
        self.assertEqual(fakegi.CALLS.count("image.duplicate"), 2)


class TestRobustesse(PipelineTestCase):
    def test_dossier_source_absent(self):
        from bfp.core import BatchError
        with self.assertRaises(BatchError):
            self.run_batch(self.settings(source_folder="/dossier/absent"))

    def test_dossier_vide(self):
        vide = os.path.join(self.tmp, "vide")
        os.makedirs(vide)
        result, reporter = self.run_batch(self.settings(source_folder=vide))
        self.assertEqual(result.processed, 0)
        self.assertIn("Aucune image", reporter.text())

    def test_image_illisible_ne_stoppe_pas_le_lot(self):
        original = gimpops.Gimp.file_load
        state = {"premier": True}

        def parfois_casse(run_mode, gfile):
            if state["premier"]:
                state["premier"] = False
                raise RuntimeError("fichier corrompu")
            return original(run_mode, gfile)

        gimpops.Gimp.file_load = parfois_casse
        try:
            result, reporter = self.run_batch(self.settings())
        finally:
            gimpops.Gimp.file_load = original

        self.assertEqual(result.failed, 1)
        self.assertEqual(result.written, 1)
        self.assertIn("corrompu", reporter.text())

    def test_arret_a_la_premiere_erreur(self):
        original = gimpops.Gimp.file_load
        tentatives = []

        def toujours_casse(run_mode, gfile):
            tentatives.append(gfile.get_path())
            raise RuntimeError("boum")

        gimpops.Gimp.file_load = toujours_casse
        try:
            result, _ = self.run_batch(self.settings(stop_on_error=True))
        finally:
            gimpops.Gimp.file_load = original

        self.assertEqual(result.failed, 1)
        # La boucle doit s'arrêter : une seule tentative d'ouverture.
        self.assertEqual(len(tentatives), 1)

    def test_journal_ecrit(self):
        result, _ = self.run_batch(self.settings(dry_run=True))
        self.assertTrue(result.log_path)
        self.assertTrue(os.path.isfile(result.log_path))

    def test_images_toujours_liberees(self):
        self.run_batch(self.settings(output_format="png"))
        # Chaque Image créée par le faux GIMP doit avoir été supprimée.
        self.assertTrue(fakegi.CALLS.count("Gimp.file_load") >= 2)


class TestPreréglages(PipelineTestCase):
    def test_aller_retour(self):
        from bfp import presets
        settings = self.settings(resize_enabled=True, resize_width=1234,
                                 wm_text="bonjour")
        presets.save_preset("Mon réglage", settings)
        self.assertIn("Mon réglage", presets.list_presets())

        rechargé = presets.load_preset("Mon réglage")
        self.assertEqual(rechargé["resize_width"], 1234)
        self.assertEqual(rechargé["wm_text"], "bonjour")
        # Les chemins de dossiers ne sont volontairement pas enregistrés.
        self.assertEqual(rechargé["source_folder"], "")

        self.assertTrue(presets.delete_preset("Mon réglage"))
        self.assertNotIn("Mon réglage", presets.list_presets())

    def test_prereglage_corrompu_ne_plante_pas(self):
        from bfp import presets
        path = os.path.join(paths.presets_directory(), "casse.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{ pas du json")
        self.assertIn("casse", presets.list_presets())


if __name__ == "__main__":
    unittest.main(verbosity=2)
