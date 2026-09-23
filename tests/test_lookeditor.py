# -*- coding: utf-8 -*-
"""
Tests de l'introspection (`bfp.opinfo`), des étapes « proc » et de l'éditeur
de recettes, contre les faux GIMP/GEGL/GTK.

Ce qui est vérifié ici : que l'éditeur se construit bien à partir de ce que
l'installation déclare (et non d'un catalogue codé en dur), que les réglages
fabriqués correspondent aux types et aux bornes réels, que modifier un widget
modifie bien la recette, et qu'enregistrer produit un JSON relisible.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import fakegi  # noqa: E402
import fakegtk  # noqa: E402

fakegi.install(with_gtk=True)

from bfp import gimpops, lookeditor, opinfo, paths  # noqa: E402
from bfp.core import default_settings  # noqa: E402
from bfp.looks import Look, LookStep, load_look_file, parse_look  # noqa: E402
from bfp.runner import BatchRunner  # noqa: E402


def widgets_of(container, kind, found=None):
    """Tous les widgets d'un type donné dans un arbre de widgets."""
    found = [] if found is None else found
    for child in getattr(container, "children", []):
        if isinstance(child, kind):
            found.append(child)
        widgets_of(child, kind, found)
    return found


class EditorTestCase(unittest.TestCase):
    def setUp(self):
        fakegi.reset()
        fakegtk.Dialog.RESPONSES = []
        fakegtk.MessageDialog.SHOWN = []
        self.tmp = tempfile.mkdtemp(prefix="bfp-ed-")
        os.environ["FAKE_GIMP_DIR"] = os.path.join(self.tmp, "gimp")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------

class TestIntrospection(EditorTestCase):
    def test_les_operations_viennent_de_gegl(self):
        operations = opinfo.list_operations()
        self.assertIn("gegl:saturation", operations)
        self.assertIn("gegl:vignette", operations)
        # Rien n'est codé en dur : une opération que GEGL ne déclare pas ne
        # doit pas apparaître.
        self.assertNotIn("gegl:cartoon", operations)

    def test_metadonnees_d_une_operation(self):
        self.assertEqual(opinfo.operation_title("gegl:saturation"),
                         "Saturation")
        self.assertIn("saturation",
                      opinfo.operation_description("gegl:saturation").lower())
        # Sans métadonnée, on retombe sur le nom.
        self.assertEqual(opinfo.operation_title("gegl:levels"), "gegl:levels")

    def test_types_et_bornes_des_reglages(self):
        infos = {i.name: i for i in
                 opinfo.describe_params(
                     opinfo.operation_properties("gegl:noise-rgb"))}
        self.assertEqual(infos["independent"].kind, "bool")
        self.assertEqual(infos["red"].kind, "double")
        self.assertEqual(infos["red"].minimum, 0.0)
        self.assertEqual(infos["red"].maximum, 1.0)
        self.assertEqual(infos["seed"].kind, "int")

    def test_les_couleurs_sont_reconnues(self):
        infos = {i.name: i for i in
                 opinfo.describe_params(
                     opinfo.operation_properties("gegl:vignette"))}
        self.assertEqual(infos["color"].kind, "color")

    def test_bornes_aberrantes_resserrees(self):
        info = opinfo.describe_param(
            fakegi.ParamSpec("x", fakegi.GObjectModule.TYPE_DOUBLE,
                             -1e30, 1e30))
        self.assertEqual(info.minimum, -1000.0)
        self.assertEqual(info.maximum, 1000.0)

    def test_valeur_ramenee_dans_les_bornes(self):
        info = opinfo.describe_param(
            fakegi.ParamSpec("s", fakegi.GObjectModule.TYPE_DOUBLE, 0.0, 1.0))
        self.assertEqual(info.coerce(5.0), 1.0)
        self.assertEqual(info.coerce(-2.0), 0.0)

    def test_liste_des_procedures_filtree(self):
        procedures = opinfo.list_procedures()
        self.assertIn("plug-in-unsharp-mask", procedures)
        self.assertIn("script-fu-drop-shadow", procedures)
        # Ni les procédures de fichier, ni celles qui quittent GIMP.
        self.assertNotIn("file-png-export", procedures)
        self.assertNotIn("gimp-quit", procedures)
        self.assertNotIn("gimp-displays-flush", procedures)

    def test_arguments_remplis_d_office_masques(self):
        names = [i.name for i in opinfo.describe_params(
            opinfo.procedure_arguments("plug-in-unsharp-mask"))]
        self.assertEqual(names, ["radius", "amount", "threshold"])

    def test_reglages_du_virage_partiel(self):
        step = LookStep(special="split-tone")
        names = [i.name for i in opinfo.step_params(step)]
        self.assertEqual(names, ["shadows", "highlights", "amount"])


# ---------------------------------------------------------------------------
# Étapes « proc »
# ---------------------------------------------------------------------------

class TestEtapesProc(EditorTestCase):
    def _run_look(self, look):
        source = os.path.join(self.tmp, "src")
        os.makedirs(source, exist_ok=True)
        with open(os.path.join(source, "a.jpg"), "wb") as handle:
            handle.write(b"x")
        settings = default_settings()
        settings.update({"source_folder": source, "output_in_place": True,
                         "output_subfolder": "_sortie", "output_format": "png",
                         "look_enabled": True, "look_name": look.name})
        reporter = gimpops.Reporter()
        BatchRunner(settings, [look], reporter=reporter).run()
        return reporter

    def test_lecture_et_ecriture_json(self):
        look = parse_look({
            "name": "Avec greffon",
            "steps": [{"proc": "plug-in-unsharp-mask",
                       "params": {"radius": 3.0, "amount": 0.4}}]})
        self.assertEqual(look.steps[0].kind, "proc")
        self.assertEqual(look.to_dict()["steps"][0]["proc"],
                         "plug-in-unsharp-mask")

    def test_arguments_remplis_automatiquement(self):
        look = parse_look({
            "name": "Netteté",
            "steps": [{"proc": "plug-in-unsharp-mask",
                       "params": {"radius": 3.0, "amount": 0.4}}]})
        self._run_look(look)

        appels = [c for c in fakegi.CALLS.of("pdb.run")
                  if c[1][0] == "plug-in-unsharp-mask"]
        self.assertTrue(appels)
        valeurs = appels[0][1][1]
        self.assertEqual(valeurs["run-mode"], "RunMode.NONINTERACTIVE")
        self.assertEqual(valeurs["radius"], 3.0)
        # « drawables » existe dans cette procédure, « drawable » non.
        self.assertIn("drawables", valeurs)
        self.assertNotIn("drawable", valeurs)

    def test_valeur_hors_bornes_ramenee(self):
        look = parse_look({
            "name": "Excès",
            "steps": [{"proc": "plug-in-unsharp-mask",
                       "params": {"amount": 99.0}}]})
        self._run_look(look)
        appels = [c for c in fakegi.CALLS.of("pdb.run")
                  if c[1][0] == "plug-in-unsharp-mask"]
        self.assertEqual(appels[0][1][1]["amount"], 10.0)

    def test_procedure_absente_signalee_sans_planter(self):
        look = parse_look({"name": "Fantôme",
                           "steps": [{"proc": "plug-in-inexistant",
                                      "params": {}},
                                     {"op": "gegl:saturation",
                                      "params": {"scale": 0.5}}]})
        reporter = self._run_look(look)
        self.assertIn("introuvable", reporter.text())
        # L'étape suivante doit tout de même être appliquée.
        self.assertTrue(fakegi.CALLS.of("layer.merge_filter"))

    def test_melange_de_genres_refuse(self):
        from bfp.core import BatchError
        with self.assertRaises(BatchError):
            parse_look({"name": "X",
                        "steps": [{"op": "gegl:saturation",
                                   "proc": "plug-in-unsharp-mask"}]})


# ---------------------------------------------------------------------------
# Choix d'une étape
# ---------------------------------------------------------------------------

class TestChoixDEtape(EditorTestCase):
    def test_catalogue_complet(self):
        chooser = lookeditor.StepChooser(None)
        libelles = " ".join(e[1] for e in chooser._entries)
        self.assertIn("Virage partiel", libelles)
        self.assertIn("gegl:saturation", libelles)
        self.assertIn("plug-in-unsharp-mask", libelles)

    def test_les_suggerees_sont_en_tete(self):
        chooser = lookeditor.StepChooser(None)
        premiers = [e[1] for e in chooser._entries[:6]]
        self.assertTrue(any("★" in libelle for libelle in premiers))

    def test_recherche(self):
        chooser = lookeditor.StepChooser(None)
        chooser.search.set_text("vignette")
        textes = " ".join(chooser.listbox.rows_text())
        self.assertIn("gegl:vignette", textes)
        self.assertNotIn("gegl:saturation", textes)

    def test_etape_construite_avec_ses_defauts(self):
        chooser = lookeditor.StepChooser(None)
        chooser.search.set_text("gegl:noise-rgb")
        chooser.listbox.select_row(chooser.listbox.get_row_at_index(0))
        step = chooser.build_step()
        self.assertEqual(step.op, "gegl:noise-rgb")
        # Les valeurs par défaut déclarées par l'opération sont reprises.
        self.assertIn("seed", step.params)
        self.assertIn("red", step.params)

    def test_virage_partiel_choisissable(self):
        chooser = lookeditor.StepChooser(None)
        chooser.search.set_text("virage")
        chooser.listbox.select_row(chooser.listbox.get_row_at_index(0))
        step = chooser.build_step()
        self.assertEqual(step.special, "split-tone")
        self.assertEqual(step.params["amount"], 25.0)


# ---------------------------------------------------------------------------
# Éditeur
# ---------------------------------------------------------------------------

class TestEditeur(EditorTestCase):
    def _editor(self, look=None):
        editor = lookeditor.LookEditor(None, look, None)
        editor.auto_preview.set_active(False)
        return editor

    def test_recette_vide_au_depart(self):
        editor = self._editor()
        self.assertEqual(editor.look.steps, [])
        self.assertIn("Sélectionnez une étape",
                      editor.step_title.get_text())

    def test_l_editeur_travaille_sur_une_copie(self):
        original = parse_look({"name": "Source",
                               "steps": [{"op": "gegl:saturation",
                                          "params": {"scale": 0.5}}]})
        editor = self._editor(original)
        editor.look.steps[0].params["scale"] = 0.1
        self.assertEqual(original.steps[0].params["scale"], 0.5)

    def test_liste_des_etapes_numerotee(self):
        look = parse_look({"name": "L", "steps": [
            {"op": "gegl:saturation", "params": {}},
            {"special": "split-tone", "params": {}},
            {"proc": "plug-in-unsharp-mask", "params": {}}]})
        editor = self._editor(look)
        self.assertEqual(editor.step_list.rows_text(),
                         ["1. gegl:saturation",
                          "2. Virage partiel",
                          "3. plug-in-unsharp-mask (greffon)"])

    def test_reglages_generes_pour_l_etape_choisie(self):
        look = parse_look({"name": "L", "steps": [
            {"op": "gegl:vignette", "params": {"radius": 1.2}}]})
        editor = self._editor(look)
        editor._select_step(0)

        spins = widgets_of(editor.param_box, fakegtk.SpinButton)
        colors = widgets_of(editor.param_box, fakegtk.ColorButton)
        self.assertTrue(spins, "aucun curseur généré")
        self.assertTrue(colors, "la couleur du vignettage devrait être un "
                                "sélecteur de couleur")

    def test_modifier_un_widget_modifie_la_recette(self):
        look = parse_look({"name": "L", "steps": [
            {"op": "gegl:saturation", "params": {"scale": 1.0}}]})
        editor = self._editor(look)
        editor._select_step(0)

        spins = widgets_of(editor.param_box, fakegtk.SpinButton)
        spins[0].set_value(0.25)
        self.assertAlmostEqual(editor.look.steps[0].params["scale"], 0.25)

    def test_ajout_suppression_et_ordre(self):
        look = parse_look({"name": "L", "steps": [
            {"op": "gegl:saturation", "params": {}},
            {"op": "gegl:vignette", "params": {}}]})
        editor = self._editor(look)

        editor._select_step(0)
        editor._on_duplicate(None)
        self.assertEqual(len(editor.look.steps), 3)

        editor._select_step(2)
        editor._on_move_up(None)
        self.assertEqual(editor.look.steps[1].op, "gegl:vignette")

        editor._on_remove(None)
        self.assertEqual([s.op for s in editor.look.steps],
                         ["gegl:saturation", "gegl:saturation"])

    def test_opacite_d_etape_visible_dans_la_liste(self):
        look = parse_look({"name": "L", "steps": [
            {"op": "gegl:vignette", "params": {}}]})
        editor = self._editor(look)
        editor._select_step(0)
        editor.step_opacity.set_value(40)
        self.assertEqual(editor.look.steps[0].opacity, 40.0)
        self.assertIn("40 %", editor.step_list.rows_text()[0])

    def test_enregistrement_et_relecture(self):
        look = parse_look({"name": "L", "steps": [
            {"op": "gegl:saturation", "params": {"scale": 0.8}},
            {"special": "split-tone",
             "params": {"shadows": "#112233", "highlights": "#ffeecc",
                        "amount": 30.0}}]})
        editor = self._editor(look)
        editor.name_entry.set_text("Mon Rendu Été")
        editor.description_entry.set_text("Essai")

        self.assertTrue(editor.save())
        self.assertEqual(editor.saved_name, "Mon Rendu Été")

        path = os.path.join(paths.user_looks_directory(), "mon-rendu-été.json")
        self.assertTrue(os.path.isfile(path), os.listdir(
            paths.user_looks_directory()))

        relu = load_look_file(path)
        self.assertEqual(relu.name, "Mon Rendu Été")
        self.assertEqual(relu.description, "Essai")
        self.assertEqual(len(relu.steps), 2)
        self.assertAlmostEqual(relu.steps[0].params["scale"], 0.8)
        self.assertEqual(relu.steps[1].special, "split-tone")

    def test_refus_d_une_recette_sans_nom_ou_sans_etape(self):
        editor = self._editor()
        editor.name_entry.set_text("Vide")
        self.assertFalse(editor.save())
        self.assertIn("au moins une étape",
                      " ".join(fakegtk.MessageDialog.SHOWN))

        look = parse_look({"name": "L",
                           "steps": [{"op": "gegl:saturation", "params": {}}]})
        editor = self._editor(look)
        editor.name_entry.set_text("   ")
        self.assertFalse(editor.save())
        self.assertIn("Donnez un nom", " ".join(fakegtk.MessageDialog.SHOWN))

    def test_le_nom_de_fichier_est_assaini(self):
        self.assertEqual(lookeditor.slugify("Cinéma teal & orange"),
                         "cinéma-teal-_-orange")
        self.assertEqual(lookeditor.slugify("  "), "recette")


# ---------------------------------------------------------------------------
# Aperçu
# ---------------------------------------------------------------------------

class TestApercu(EditorTestCase):
    def _sample(self):
        path = os.path.join(self.tmp, "temoin.jpg")
        with open(path, "wb") as handle:
            handle.write(b"pas vraiment une image")
        return path

    def test_rendu_avant_et_apres(self):
        look = parse_look({"name": "L", "steps": [
            {"op": "gegl:saturation", "params": {"scale": 0.0}}]})
        editor = lookeditor.LookEditor(None, look, self._sample())
        editor._do_refresh()

        self.assertTrue(editor.before_image.file, "aperçu « avant » manquant")
        self.assertTrue(editor.after_image.file, "aperçu « après » manquant")
        self.assertTrue(os.path.isfile(editor.after_image.file))
        self.assertIn("recalculé", editor.preview_status.get_text())

    def test_la_reduction_precede_les_filtres(self):
        look = parse_look({"name": "L", "steps": [
            {"op": "gegl:saturation", "params": {"scale": 0.5}}]})
        editor = lookeditor.LookEditor(None, look, self._sample())
        fakegi.reset()
        editor._do_refresh()

        noms = [n for n in fakegi.CALLS.names()
                if n in ("image.scale", "layer.merge_filter")]
        self.assertEqual(noms[0], "image.scale",
                         "l'aperçu doit réduire l'image avant de filtrer")

    def test_sans_image_temoin(self):
        editor = lookeditor.LookEditor(None, None, None)
        editor._do_refresh()
        self.assertIn("image témoin", editor.preview_status.get_text())

    def test_l_apercu_respecte_la_taille_maximale(self):
        look = Look(name="L", steps=[LookStep(op="gegl:saturation")])
        editor = lookeditor.LookEditor(None, look, self._sample())
        editor._do_refresh()
        # La fausse image fait 1600×1200 ; réduite pour tenir dans 420.
        echelles = fakegi.CALLS.of("image.scale")
        self.assertTrue(echelles)
        largeur, hauteur = echelles[0][1]
        self.assertLessEqual(max(largeur, hauteur), lookeditor._PREVIEW_SIZE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
