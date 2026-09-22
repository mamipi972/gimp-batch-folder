# -*- coding: utf-8 -*-
"""
Traitement par lots d'un dossier — greffon GIMP 3 (Python).

Le paquet est découpé de façon à ce que la logique métier reste testable
en dehors de GIMP :

``bfp.core``      géométrie, nommage, parcours de dossiers — aucun import GIMP
``bfp.looks``     lecture et validation des recettes GEGL — aucun import GIMP
``bfp.presets``   préréglages JSON — aucun import GIMP
``bfp.paths``     emplacements de stockage (GIMP optionnel)
``bfp.gimpops``   toutes les vraies interactions avec l'API GIMP 3
``bfp.runner``    orchestration du lot
``bfp.ui``        boîte de dialogue GTK 3
"""

__all__ = ["core", "looks", "presets", "paths", "gimpops", "runner", "ui"]
__version__ = "1.0.0"
