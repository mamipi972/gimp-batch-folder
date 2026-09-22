# -*- coding: utf-8 -*-
"""
bfp.runner — orchestration du traitement par lots.

Ordre des opérations appliqué à chaque image (celui annoncé dans l'interface) :

1. rotation puis miroirs ;
2. recadrage ;
3. redimensionnement ;
4. mode colorimétrique ;
5. recette de look (filtres GEGL) ;
6. filigranes (image puis texte, pour que le texte reste au-dessus) ;
7. export du composite, et/ou export de chaque calque séparément.

Les filigranes viennent après le look : un logo ne doit pas hériter du grain
ni du vignettage. Quand l'export des calques est demandé, les filigranes sont
appliqués à chaque calque extrait, pas au composite — sinon ils ressortiraient
comme des fichiers à part.
"""

from __future__ import annotations

import os
import time

from . import gimpops, paths
from .core import (
    BatchError,
    build_name_context,
    iter_images,
    output_directory,
    plan_output,
    render_template,
    resolve_collision,
    sanitize_filename,
    target_extension,
    format_from_extension,
)
from .looks import find_look


class Cancelled(Exception):
    """Levée quand l'utilisateur interrompt le lot."""


class BatchResult(object):
    """Bilan d'un traitement."""

    __slots__ = ("processed", "written", "skipped", "failed", "seconds",
                 "log_path", "cancelled")

    def __init__(self):
        self.processed = 0
        self.written = 0
        self.skipped = 0
        self.failed = 0
        self.seconds = 0.0
        self.log_path = None
        self.cancelled = False

    def summary(self):
        parts = ["%d image(s) traitée(s)" % self.processed,
                 "%d fichier(s) écrit(s)" % self.written]
        if self.skipped:
            parts.append("%d ignorée(s)" % self.skipped)
        if self.failed:
            parts.append("%d en échec" % self.failed)
        parts.append("en %.1f s" % self.seconds)
        text = ", ".join(parts)
        if self.cancelled:
            text = "Interrompu — " + text
        return text


class BatchRunner(object):
    """Exécute le lot décrit par ``settings``."""

    def __init__(self, settings, looks=(), reporter=None, progress=None,
                 is_cancelled=None):
        self.settings = settings
        self.looks = list(looks)
        self.reporter = reporter or gimpops.Reporter()
        self._progress = progress
        self._is_cancelled = is_cancelled or (lambda: False)
        self.result = BatchResult()

    # -- utilitaires -------------------------------------------------------

    def _tick(self, fraction, message):
        if self._is_cancelled():
            raise Cancelled()
        if self._progress:
            try:
                self._progress(fraction, message)
            except Exception:
                pass

    def files(self):
        """Liste des images à traiter."""
        source = self.settings.get("source_folder") or ""
        if not os.path.isdir(source):
            raise BatchError("Dossier source introuvable : %s" % source)
        skip = [".git", "__pycache__"]
        subfolder = (self.settings.get("output_subfolder") or "").strip()
        if self.settings.get("output_in_place") and subfolder:
            skip.append(subfolder)
        return iter_images(source,
                           recursive=self.settings.get("recursive", False),
                           extensions=self.settings.get("extensions"),
                           skip_dirs=tuple(skip))

    def _look(self):
        if not self.settings.get("look_enabled"):
            return None
        look = find_look(self.looks, self.settings.get("look_name"))
        if look is None and self.settings.get("look_name"):
            self.reporter.warn("Recette de look introuvable : %s"
                               % self.settings.get("look_name"))
        return look

    # -- pipeline ----------------------------------------------------------

    def _apply_transforms(self, image, look):
        settings = self.settings

        if settings.get("rotate_enabled"):
            gimpops.apply_rotation(image,
                                   settings.get("rotate_degrees", 0),
                                   settings.get("flip_horizontal", False),
                                   settings.get("flip_vertical", False),
                                   self.reporter)

        if settings.get("crop_enabled"):
            gimpops.apply_crop(image, settings, self.reporter)

        if settings.get("resize_enabled"):
            gimpops.apply_resize(image, settings, self.reporter)

        if settings.get("color_enabled"):
            gimpops.apply_color_mode(image, settings.get("color_mode", "rgb"),
                                     self.reporter)

        if look is not None:
            gimpops.apply_look(image, look,
                               settings.get("look_opacity", 100.0),
                               self.reporter)

    def _apply_watermarks(self, image, source_path, index):
        settings = self.settings
        if settings.get("wm_image_enabled"):
            gimpops.add_image_watermark(image, settings, self.reporter)
        if settings.get("wm_text_enabled"):
            context = build_name_context(source_path, index=index,
                                         width=image.get_width(),
                                         height=image.get_height())
            text = render_template(settings.get("wm_text", ""), context)
            gimpops.add_text_watermark(image, settings, text, self.reporter)

    def _export_composite(self, image, source_path, index):
        settings = self.settings
        path, output_format = plan_output(settings, source_path, index,
                                          image.get_width(),
                                          image.get_height())
        if path is None:
            self.reporter.info("Ignoré (le fichier de sortie existe déjà) : %s"
                               % os.path.basename(source_path))
            self.result.skipped += 1
            return False

        if settings.get("dry_run"):
            self.reporter.info("[simulation] %s → %s"
                               % (os.path.basename(source_path), path))
            self.result.written += 1
            return True

        if gimpops.export_image(image, path, output_format, settings,
                                self.reporter):
            self.reporter.info("%s → %s" % (os.path.basename(source_path), path))
            self.result.written += 1
            return True

        self.result.failed += 1
        return False

    def _export_layers(self, image, source_path, index):
        settings = self.settings
        layers = gimpops.top_drawables(image)
        if not layers:
            self.reporter.warn("Aucun calque à exporter dans %s" % source_path)
            return

        output_format = settings.get("output_format", "keep")
        resolved_format = (format_from_extension(source_path) or "png") \
            if output_format == "keep" else output_format
        extension = target_extension(output_format, source_path)

        # Les calques sont listés du haut vers le bas ; on numérote du bas
        # vers le haut, comme dans la boîte de dialogue des calques.
        ordered = list(reversed(layers))
        for layer_index, layer in enumerate(ordered, start=1):
            self._tick(-1, "Calque %d/%d" % (layer_index, len(ordered)))

            if settings.get("export_layers_visible_only", True):
                try:
                    if not layer.get_visible():
                        continue
                except Exception:
                    pass

            single = gimpops.image_from_layer(
                image, layer,
                crop_to_layer=settings.get("export_layers_crop", False),
                reporter=self.reporter)
            if single is None:
                self.result.failed += 1
                continue

            try:
                self._apply_watermarks(single, source_path, index)

                try:
                    layer_name = layer.get_name()
                except Exception:
                    layer_name = "calque"

                context = build_name_context(
                    source_path, index=index,
                    width=single.get_width(), height=single.get_height(),
                    extension=extension, layer_name=layer_name,
                    layer_index=layer_index)
                filename = sanitize_filename(render_template(
                    settings.get("export_layers_template"), context))

                directory = output_directory(settings, source_path)
                path = resolve_collision(
                    os.path.join(directory, filename),
                    settings.get("overwrite_policy", "rename"))
                if path is None:
                    self.result.skipped += 1
                    continue

                if settings.get("dry_run"):
                    self.reporter.info("[simulation] calque « %s » → %s"
                                       % (layer_name, path))
                    self.result.written += 1
                    continue

                if gimpops.export_image(single, path, resolved_format,
                                        settings, self.reporter):
                    self.reporter.info("calque « %s » → %s"
                                       % (layer_name, path))
                    self.result.written += 1
                else:
                    self.result.failed += 1
            finally:
                gimpops.discard_image(single)

    def process_file(self, source_path, index, look):
        """Traite une image.  Renvoie True si au moins un fichier est écrit."""
        settings = self.settings

        # Économie : si le modèle de nom ne dépend pas des dimensions finales,
        # on peut décider d'ignorer le fichier avant même de l'ouvrir.
        template = settings.get("name_template") or ""
        if ("{width" not in template and "{height" not in template
                and not settings.get("export_layers")):
            planned, _ = plan_output(settings, source_path, index, 0, 0)
            if planned is None:
                self.reporter.info("Ignoré (déjà présent) : %s"
                                   % os.path.basename(source_path))
                self.result.skipped += 1
                return False

        image = gimpops.load_image(source_path, self.reporter)
        if image is None:
            self.result.failed += 1
            return False

        wrote = False
        try:
            self._apply_transforms(image, look)

            export_layers = settings.get("export_layers", False)
            want_composite = not (export_layers and
                                  settings.get("export_layers_only"))

            if want_composite:
                # Quand les calques sont aussi exportés, le composite est
                # fabriqué sur une copie : l'export aplatit l'image (JPEG) et
                # le filigrane deviendrait sinon un calque exporté à part.
                composite = image
                temporary = False
                if export_layers:
                    duplicated = gimpops.duplicate_image(image, self.reporter)
                    if duplicated is not None:
                        composite, temporary = duplicated, True
                try:
                    self._apply_watermarks(composite, source_path, index)
                    wrote = self._export_composite(composite, source_path,
                                                   index)
                finally:
                    if temporary:
                        gimpops.discard_image(composite)

            if export_layers:
                self._export_layers(image, source_path, index)
                wrote = True
        except Cancelled:
            raise
        except BatchError as exc:
            self.reporter.error("%s : %s" % (os.path.basename(source_path), exc))
            self.result.failed += 1
        except Exception as exc:  # garde-fou : un fichier cassé n'arrête pas tout
            self.reporter.error("%s : erreur inattendue (%s)"
                                % (os.path.basename(source_path), exc))
            self.result.failed += 1
        finally:
            gimpops.discard_image(image)

        self.result.processed += 1
        return wrote

    # -- boucle principale -------------------------------------------------

    def run(self):
        started = time.time()
        look = self._look()
        files = self.files()
        total = len(files)

        if not total:
            self.reporter.warn("Aucune image correspondante dans le dossier "
                               "source.")
            self.result.seconds = time.time() - started
            return self.result

        self.reporter.info("%d image(s) à traiter." % total)
        if self.settings.get("dry_run"):
            self.reporter.info("Mode simulation : aucun fichier ne sera écrit.")

        # Un lot de 300 images ne doit pas pouvoir ouvrir 300 fenêtres modales.
        quieted = gimpops.quiet_messages()
        try:
            for position, source_path in enumerate(files, start=1):
                self._tick(float(position - 1) / total,
                           "%d/%d — %s" % (position, total,
                                           os.path.basename(source_path)))
                self.process_file(source_path, position, look)
                if self.settings.get("stop_on_error") and self.result.failed:
                    self.reporter.warn("Arrêt demandé après la première erreur.")
                    break
            self._tick(1.0, "Terminé")
        except Cancelled:
            self.result.cancelled = True
            self.reporter.warn("Traitement interrompu par l'utilisateur.")
        finally:
            if quieted:
                gimpops.restore_messages()

        self.result.seconds = time.time() - started
        self.result.log_path = self._write_log()
        return self.result

    def _write_log(self):
        path = paths.log_path()
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("Traitement par lots — %s\n"
                             % time.strftime("%Y-%m-%d %H:%M:%S"))
                handle.write("Dossier source : %s\n\n"
                             % self.settings.get("source_folder", ""))
                handle.write(self.reporter.text())
                handle.write("\n\n%s\n" % self.result.summary())
        except OSError:
            return None
        return path
