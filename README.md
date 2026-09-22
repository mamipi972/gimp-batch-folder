# Batch Folder — a BIMP replacement for GIMP 3

<p align="center">
  <a href="#english"><b>English</b></a> ·
  <a href="#français"><b>Français</b></a>
</p>

<p align="center">
  <img alt="GIMP 3.0+" src="https://img.shields.io/badge/GIMP-3.0%2B-5f3a7a">
  <img alt="Python 3" src="https://img.shields.io/badge/Python-3-3776ab">
  <img alt="License GPL-3.0-or-later" src="https://img.shields.io/badge/license-GPL--3.0--or--later-blue">
  <img alt="107 tests" src="https://img.shields.io/badge/tests-107%20passing-success">
</p>

> Resize, crop, rotate, watermark, convert, rename and colour-grade every
> image in a folder — and export each layer to its own file.
> BIMP has not been ported to GIMP 3, so this fills the gap in native Python.

---

## English

### Install

1. In GIMP: **Edit ▸ Preferences ▸ Folders ▸ Plug-ins** shows your personal
   plug-in folder. It is usually:

   | OS | Path |
   |---|---|
   | Linux | `~/.config/GIMP/3.0/plug-ins/` |
   | macOS | `~/Library/Application Support/GIMP/3.0/plug-ins/` |
   | Windows | `%APPDATA%\GIMP\3.0\plug-ins\` |

2. Copy the **whole `gimp-batch-folder` folder** into it. GIMP requires the
   main file to be named after its folder:

   ```
   plug-ins/
     gimp-batch-folder/
       gimp-batch-folder.py     ← make executable on Linux/macOS
       bfp/
       looks/
   ```

3. **Linux / macOS only** — mark it executable:

   ```bash
   chmod +x ~/.config/GIMP/3.0/plug-ins/gimp-batch-folder/gimp-batch-folder.py
   ```

   The bundled `install.sh` does steps 2 and 3 for you.

   **On Windows there is nothing else to do.** Windows has no executable bit,
   GIMP recognises Python plug-ins by their `.py` extension and runs them with
   its own bundled interpreter. Extracting into `plug-ins\` is enough —
   `chmod` and `install.sh` do not apply to you.

4. Restart GIMP. The entry appears under the **File** menu:
   **File ▸ Batch-process a folder…**

If it does not appear, start GIMP from a terminal: Python registration errors
are printed there.

<details>
<summary>Install with <code>git clone</code></summary>

The repository is laid out so that cloning it produces a correctly named
plug-in folder:

```bash
cd ~/.config/GIMP/3.0/plug-ins/
git clone https://github.com/mamipi972/gimp-batch-folder.git
chmod +x gimp-batch-folder/gimp-batch-folder.py
```

Updating later is then `git -C gimp-batch-folder pull`.
</details>

### What it does

#### Order of operations

Fixed, and chosen so the result is what you expect without thinking about it:

1. **Rotation** (90/180/270°) then **flips** — straighten first;
2. **Crop**;
3. **Resize**;
4. **Colour mode** (RGB / greyscale);
5. **Look recipe** (GEGL filters);
6. **Watermarks** — image first, text on top; they come *after* the look so a
   logo does not inherit the grain or the vignette;
7. **Export** of the composite, and/or of each layer.

#### Resize

| Mode | Effect |
|---|---|
| Fit in a box | image fits inside W×H, aspect kept |
| Fill then crop | image covers W×H, then centre crop |
| Fixed width / height | the other dimension follows |
| Percentage | proportional scaling |
| Exact size | distorts the image |

Upscaling images smaller than the target is **refused by default** — tick the
box if you really want it.

#### Crop

Four modes: aspect ratio (`16:9`, `1:1`, `3/2`…) anchored on a 3×3 grid,
explicit rectangle, per-edge margins, or automatic trimming of uniform
borders (GIMP's `plug-in-autocrop`).

#### Watermarks

* **Text** — font, size (pixels or % of the short edge), colour, opacity,
  angle, 3×3 position, margin. The text accepts the same tokens as the naming
  template: `© {parent} — {name}` becomes `© holidays — IMG_0042`.
* **Image** — ideally a PNG with transparency, scaled as a percentage of the
  image width, with opacity and position.

#### Convert and rename

Output formats: keep original, JPEG, PNG, WebP, TIFF, BMP, XCF. JPEG/WebP
quality, PNG/TIFF compression, metadata stripping.

Naming template tokens:

| Token | Meaning |
|---|---|
| `{name}` | file name without extension |
| `{ext}` | output extension (with the dot) |
| `{index}` | position in the batch — `{index:03d}` gives `007` |
| `{parent}` | name of the containing folder |
| `{width}` `{height}` | **final** dimensions |
| `{date}` | ISO date, `2026-09-22` — sorts by itself in a file browser |
| `{date_fr}` | day-first date, `22-09-2026` |
| `{time}` | `14-32-05` |
| `{year}` `{month}` `{day}` `{hour}` `{minute}` | `2026` `09` `22` `14` `32` — build any order you like |
| `{layer}` `{layer_index}` | layer export only |

If the output file already exists: rename (`_1`, `_2`…), skip, or overwrite.
The source file is **never** overwritten, even in overwrite mode — a
`_traite` suffix is added if it would collide.

#### Layer export

Each layer goes to its own file, optionally cropped to the layer bounds,
visible layers only or all of them. You can ask for the layers *and* the full
image, or the layers alone. When both are requested the composite is built on
a working copy — otherwise the composite's watermark would come out as a
separate exported layer.

### Look recipes

A recipe is a JSON file describing a chain of GEGL operations. Six ship with
the plug-in:

| Recipe | Idea |
|---|---|
| **N&B contrasté** | punchy black & white: desaturation, strong contrast, micro-sharpening, vignette |
| **Argentique doux** | film look: lifted blacks, cool/warm split tone, fine grain |
| **Cinéma teal & orange** | teal shadows, amber highlights |
| **Portrait doux** | opened shadows, soft glow |
| **Vintage délavé** | heavily lifted blacks, faded colours, warm cast |
| **Punch web** | output sharpening, more contrast and colour |

The **Dosage** slider applies the recipe to a copy of the layer whose opacity
it then sets: 40 % gives a four-times subtler effect without editing the
recipe.

#### Writing your own

Drop a `.json` into `<GIMP config dir>/batch-folder/looks/` (the exact path is
shown in the **Look** tab), then click **Recharger les recettes**.

```json
{
  "name": "My grade",
  "description": "What it does, in one sentence.",
  "steps": [
    { "op": "gegl:saturation", "params": { "scale": 0.9 } },
    { "op": "gegl:brightness-contrast",
      "params": { "contrast": 1.15, "brightness": -0.02 } },
    { "op": "gegl:vignette",
      "params": { "color": "#000000", "radius": 1.4, "softness": 0.8 },
      "opacity": 50.0 },
    { "special": "split-tone",
      "params": { "shadows": "#1d3b57", "highlights": "#e9cd93",
                  "amount": 25.0 } }
  ]
}
```

* `op` — any GEGL operation (`gegl:…`). Property names are the ones listed by
  GIMP's **Procedure Browser** and by `gegl --list-all`.
* `opacity` (0–100) and `blend` (`normal`, `overlay`, `softlight`,
  `multiply`, `screen`…) dose a single step.
* `special: "split-tone"` is the only step written in Python rather than
  GEGL: it tints shadows and highlights separately, using two layers masked
  by the image's own luminosity and merged in soft light.
* Colours are written `"#rrggbb"`.

An operation missing from your build, or a property that does not exist, is
**logged and skipped**: a recipe written for another GEGL version degrades
the result, it does not break the batch.

### Presets

The top bar saves every setting under a name, in
`<GIMP config dir>/batch-folder/presets/*.json`. They are plain files — to
share a setup with a colleague, send them the file.

Folder paths (source, destination) are deliberately **not** saved: a preset
describes a way of working, not one particular batch.

The last settings used are restored on the next launch.

### Scripting

A second procedure, `python-fu-batch-folder-run`, does the same work with no
UI — useful in a Makefile, a scheduled job or on a server.

```bash
gimp-console-3.0 -idf --batch-interpreter python-fu-eval -b '
import gi
gi.require_version("Gimp", "3.0")
from gi.repository import Gimp

proc = Gimp.get_pdb().lookup_procedure("python-fu-batch-folder-run")
config = proc.create_config()
config.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
config.set_property("preset", "Web gallery")
config.set_property("source-folder", "/photos/2026/march")
config.set_property("output-folder", "/export/web")
result = proc.run(config)
print("files written:", result.index(1))
' -b 'gimp-quit 0'
```

`settings-json` overrides any setting without going through a preset:

```python
config.set_property("settings-json",
                    '{"resize_enabled": true, "resize_mode": "fit", '
                    '"resize_width": 1600, "resize_height": 1600, '
                    '"output_format": "webp", "webp_quality": 82}')
```

The full list of keys is in `bfp/core.py`, function `default_settings()`.

### The “Simuler” (dry run) button

It walks the folder and writes to the log what *would* be produced, without
touching the disk. Use it before any large batch, especially with the
overwrite policy.

The full log of the last batch is also written to
`<GIMP config dir>/batch-folder/dernier-traitement.log`.

### Technical notes

#### Why the code introspects everything

GIMP 3's PDB API moved between 3.0.x releases, and the available GEGL
operations depend on how GIMP was built. Rather than hard-coding names that
may be missing, the plug-in:

* only writes a property if `config.find_property()` finds it;
* clamps values to the bounds declared by the `GParamSpec` — that is how
  “quality 90 %” becomes `0.9` for JPEG and stays `90` for WebP, without the
  scale being assumed anywhere;
* looks up export procedures under their GIMP 3 name (`file-jpeg-export`),
  then the old one (`file-jpeg-save`), and finally falls back to
  `Gimp.file_save()`, which picks by extension;
* logs and skips any impossible step instead of aborting the batch.

#### Layer merges and stale IDs

`gimp_image_merge_down()` **destroys both layers** it merges and creates a
third one. Continuing to work on the pre-merge Python object produces:

> Procedure “gimp-drawable-filter-new” has been called with an invalid ID for
> argument “drawable”.

That is why `apply_split_tone()` returns the resulting layer rather than a
boolean, and why `apply_gegl_step()` checks `is_valid()` before creating a
filter. The visible symptom was that in any recipe where the split tone is
not the last step, **every following step failed silently**.

During a batch, GIMP messages are also routed to the error console —
otherwise one rejected operation opens one modal dialog per image.

#### Layout

```
gimp-batch-folder.py   registers the two procedures
bfp/core.py            geometry, naming, folder walk — no GIMP import
bfp/looks.py           recipe loading and validation — no GIMP import
bfp/presets.py         JSON presets — no GIMP import
bfp/paths.py           storage locations
bfp/gimpops.py         every interaction with the GIMP 3 API
bfp/runner.py          batch orchestration
bfp/ui.py              GTK 3 dialog
looks/                 the six bundled recipes
tests/                 107 tests, runnable without GIMP
```

#### Tests

```bash
python3 -m unittest discover -s tests -p "test_*.py" -t tests
```

The tests run **without GIMP**: `tests/fakegi.py` and `tests/fakegtk.py`
simulate just enough of GIMP 3, GEGL and GTK to run the pipeline and build
the dialog. They check the order of operations, the values passed to each
procedure, the fallback paths, the settings ↔ widgets round trip, and the
enable/disable logic.

**What they cannot check**: that your actual GIMP behaves like the
simulation. Procedure, property and method names were taken from the official
libgimp 3.0 documentation and from the Python plug-ins shipped with GIMP
(`foggify.py`, `file-openraster.py`, `python-console.py`). Try a small folder
first, with the dry-run button.

#### Known limitations

* No free rotation of the whole image (only 90/180/270°); the text watermark
  does accept an arbitrary angle.
* No automatic rotation from EXIF orientation.
* Look recipes apply to each top-level layer; on a multi-layer image the
  result may differ from grading the composite.
* TIFF compression is passed as a string (`lzw`, `deflate`…); if your build
  expects something else, the log says so and GIMP's default applies.

### Related projects

**[Batcher](https://github.com/kamilburda/batcher)** by Kamil Burda is the
mature, actively maintained batch plug-in for GIMP 3 (BSD-3-Clause). It is
broader than this one: it can run *any* installed GIMP filter or plug-in as a
batch action, chain actions and conditions, and work on images already open in
GIMP — not just a folder on disk. If you want a general-purpose batch engine,
start there.

This plug-in is narrower on purpose. It does one thing — walk a folder — with
a fixed, opinionated pipeline, and adds two things Batcher does not have:
JSON **look recipes** (chains of GEGL operations with a global dosage slider,
shareable as files), and a colour-grading step built in rather than assembled
from filters. It is also GPL-3.0, like GIMP itself.

Historically, [BIMP](https://github.com/alessandrofrancesconi/gimp-plugin-bimp)
by Alessandro Francesconi was the reference for GIMP 2.10; it has not been
ported to GIMP 3.

### Contributing

Issues and pull requests welcome. Please run the test suite before opening a
PR, and add a test for any bug you fix — `tests/fakegi.py` makes that cheap.

### License

GPL-3.0-or-later, like GIMP. See [LICENSE](LICENSE).

---


## Français

### Traitement par lots d'images depuis un dossier — un remplaçant de BIMP pour GIMP 3

<p align="center">
  <a href="#english"><b>English</b></a> ·
  <a href="#français"><b>Français</b></a>
</p>

<p align="center">
  <img alt="GIMP 3.0+" src="https://img.shields.io/badge/GIMP-3.0%2B-5f3a7a">
  <img alt="Python 3" src="https://img.shields.io/badge/Python-3-3776ab">
  <img alt="License GPL-3.0-or-later" src="https://img.shields.io/badge/license-GPL--3.0--or--later-blue">
  <img alt="107 tests" src="https://img.shields.io/badge/tests-107%20passing-success">
</p>

> Redimensionnez, recadrez, faites pivoter, ajoutez un filigrane, convertissez, renommez et effectuez un étalonnage des couleurs sur chaque
> image d'un dossier — et exportez chaque calque vers un fichier distinct.
> BIMP n'ayant pas été porté vers GIMP 3, ce module comble ce manque grâce à une solution native en Python.

---

### Installation

1. Dans GIMP : **Édition ▸ Préférences ▸ Dossiers ▸ Greffons**, notez le
   dossier personnel. Il vaut généralement :

   | Système | Chemin |
   |---|---|
   | Linux | `~/.config/GIMP/3.0/plug-ins/` |
   | macOS | `~/Library/Application Support/GIMP/3.0/plug-ins/` |
   | Windows | `%APPDATA%\GIMP\3.0\plug-ins\` |

2. Copiez-y le **dossier `gimp-batch-folder` entier**. GIMP exige que le
   fichier principal porte le nom de son dossier :

   ```
   plug-ins/
     gimp-batch-folder/
       gimp-batch-folder.py     ← à rendre exécutable sous Linux/macOS
       bfp/
       looks/
   ```

3. **Linux / macOS uniquement** — rendez le fichier exécutable :

   ```bash
   chmod +x ~/.config/GIMP/3.0/plug-ins/gimp-batch-folder/gimp-batch-folder.py
   ```

   Le script `install.sh` fourni fait les étapes 2 et 3 pour vous.

   **Sur Windows, il n'y a rien à faire de plus** : le système n'a pas de bit
   d'exécution, GIMP reconnaît les greffons Python à leur extension `.py` et
   utilise l'interpréteur Python qu'il embarque. Décompresser dans
   `plug-ins\` suffit — `chmod` et `install.sh` ne vous concernent pas.

4. Relancez GIMP. L'entrée apparaît dans le menu **Fichier** :
   **Fichier ▸ Traitement par lots d'un dossier…**

Si elle n'apparaît pas, lancez GIMP depuis un terminal : les erreurs
d'enregistrement d'un greffon Python y sont affichées.

<details>
<summary>Installation par <code>git clone</code></summary>

Le dépôt est organisé pour que le cloner produise un dossier de greffon
correctement nommé :

```bash
cd ~/.config/GIMP/3.0/plug-ins/
git clone https://github.com/mamipi972/gimp-batch-folder.git
chmod +x gimp-batch-folder/gimp-batch-folder.py
```

Les mises à jour se font ensuite par `git -C gimp-batch-folder pull`.
</details>

### Ce que fait le greffon

#### Ordre des opérations

Il est fixe et pensé pour donner le résultat attendu sans réfléchir :

1. **Rotation** (90/180/270°) puis **miroirs** — on redresse d'abord ;
2. **Recadrage** ;
3. **Redimensionnement** ;
4. **Mode colorimétrique** (RVB / niveaux de gris) ;
5. **Recette de look** (filtres GEGL) ;
6. **Filigranes** — image d'abord, texte par-dessus ; ils viennent *après* le
   look pour qu'un logo n'hérite ni du grain ni du vignettage ;
7. **Export** du composite, et/ou de chaque calque.

#### Redimensionnement

| Mode | Effet |
|---|---|
| Tenir dans une boîte | l'image tient dans L×H, proportions gardées |
| Remplir puis recadrer | l'image couvre L×H, puis recadrage centré |
| Largeur / hauteur imposée | l'autre dimension suit |
| Pourcentage | mise à l'échelle proportionnelle |
| Dimensions exactes | déforme l'image |

L'agrandissement des images plus petites que la cible est **refusé par
défaut** : cochez la case si vous le voulez vraiment.

#### Recadrage

Quatre modes : rapport d'aspect (`16:9`, `1:1`, `3/2`…) avec ancrage sur une
grille 3×3, rectangle précis, rognage de marges par bord, ou détourage
automatique des bords unis (procédure `plug-in-autocrop` de GIMP).

#### Filigranes

* **Texte** — police, taille (en pixels ou en % du petit côté), couleur,
  opacité, angle, position sur la grille 3×3, marge. Le texte accepte les
  mêmes jetons que le nommage : `© {parent} — {name}` devient
  `© vacances — IMG_0042`.
* **Image** — un PNG à fond transparent de préférence, mis à l'échelle en
  pourcentage de la largeur de l'image, avec opacité et position.

#### Conversion et renommage

Formats de sortie : conserver l'original, JPEG, PNG, WebP, TIFF, BMP, XCF.
Qualité JPEG/WebP, compression PNG/TIFF, retrait des métadonnées.

Modèle de nommage, avec ces jetons :

| Jeton | Contenu |
|---|---|
| `{name}` | nom du fichier sans extension |
| `{ext}` | extension de sortie (avec le point) |
| `{index}` | numéro dans le lot — `{index:03d}` donne `007` |
| `{parent}` | nom du dossier contenant l'image |
| `{width}` `{height}` | dimensions **finales** |
| `{date}` | date ISO, `2026-09-22` — se trie toute seule dans l'explorateur |
| `{date_fr}` | date à la française, `22-09-2026` |
| `{time}` | `14-32-05` |
| `{year}` `{month}` `{day}` `{hour}` `{minute}` | `2026` `09` `22` `14` `32` — pour composer l'ordre que vous voulez |
| `{layer}` `{layer_index}` | export des calques uniquement |

Si le fichier de sortie existe déjà : renommer (`_1`, `_2`…), ignorer, ou
écraser. Le fichier source n'est **jamais** écrasé, même en mode « écraser » :
un suffixe `_traite` est ajouté le cas échéant.

#### Export des calques

Chaque calque part dans son propre fichier, éventuellement rogné à ses
limites, calques visibles seulement ou tous. On peut demander les calques
*et* l'image complète, ou les calques seuls. Quand les deux sont demandés, le
composite est fabriqué sur une copie de travail — sinon le filigrane du
composite ressortirait comme un calque exporté à part.

### Recettes de look

Une recette est un fichier JSON décrivant une suite d'opérations GEGL.
Six sont livrées :

| Recette | Idée |
|---|---|
| **N&B contrasté** | désaturation, contraste marqué, micro-netteté, vignettage |
| **Argentique doux** | noirs relevés, virage froid/chaud, grain fin |
| **Cinéma teal & orange** | ombres bleu-vert, hautes lumières ambrées |
| **Portrait doux** | ombres débouchées, voile lumineux |
| **Vintage délavé** | noirs très relevés, couleurs passées, dominante chaude |
| **Punch web** | netteté de sortie, contraste et couleurs renforcés |

Le curseur **Dosage** applique la recette sur une copie du calque dont on
règle l'opacité : 40 % donne un effet quatre fois plus discret, sans avoir à
retoucher la recette.

#### Écrire la vôtre

Déposez un `.json` dans
`<dossier de configuration GIMP>/batch-folder/looks/` (le chemin exact est
rappelé dans l'onglet **Look**), puis cliquez sur **Recharger les recettes**.

```json
{
  "name": "Mon rendu",
  "description": "Ce qu'elle fait, en une phrase.",
  "steps": [
    { "op": "gegl:saturation", "params": { "scale": 0.9 } },
    { "op": "gegl:brightness-contrast",
      "params": { "contrast": 1.15, "brightness": -0.02 } },
    { "op": "gegl:vignette",
      "params": { "color": "#000000", "radius": 1.4, "softness": 0.8 },
      "opacity": 50.0 },
    { "special": "split-tone",
      "params": { "shadows": "#1d3b57", "highlights": "#e9cd93",
                  "amount": 25.0 } }
  ]
}
```

* `op` — n'importe quelle opération GEGL (`gegl:…`). Les noms de propriétés
  sont ceux du **Navigateur de procédures** de GIMP et de `gegl --list-all`.
* `opacity` (0–100) et `blend` (`normal`, `overlay`, `softlight`, `multiply`,
  `screen`…) dosent une étape isolément.
* `special: "split-tone"` est la seule étape écrite en Python plutôt qu'en
  GEGL : elle teinte séparément ombres et hautes lumières, via deux calques
  masqués par la luminosité de l'image et fusionnés en lumière douce.
* Les couleurs s'écrivent `"#rrggbb"`.

Une opération absente de votre installation, ou une propriété qui n'existe
pas, est **signalée dans le journal et simplement sautée** : une recette
écrite pour une autre version de GEGL dégrade le rendu, elle ne casse pas le
lot.

### Préréglages

La barre du haut enregistre l'ensemble des réglages sous un nom, dans
`<configuration GIMP>/batch-folder/presets/*.json`. Ce sont de simples
fichiers : pour partager un réglage avec un collègue, envoyez-lui le fichier.

Les chemins de dossiers (source, destination) ne sont volontairement **pas**
enregistrés : un préréglage décrit une façon de travailler, pas un lot précis.

Les derniers réglages utilisés sont rouverts automatiquement au lancement
suivant.

### Utilisation en script

Une seconde procédure, `python-fu-batch-folder-run`, fait le même travail
sans interface — utile pour un Makefile, une tâche planifiée ou un serveur.

```bash
gimp-console-3.0 -idf --batch-interpreter python-fu-eval -b '
import gi
gi.require_version("Gimp", "3.0")
from gi.repository import Gimp

proc = Gimp.get_pdb().lookup_procedure("python-fu-batch-folder-run")
config = proc.create_config()
config.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
config.set_property("preset", "Galerie web")
config.set_property("source-folder", "/photos/2026/mars")
config.set_property("output-folder", "/export/web")
result = proc.run(config)
print("fichiers écrits :", result.index(1))
' -b 'gimp-quit 0'
```

`settings-json` permet de surcharger n'importe quel réglage sans passer par
un préréglage :

```python
config.set_property("settings-json",
                    '{"resize_enabled": true, "resize_mode": "fit", '
                    '"resize_width": 1600, "resize_height": 1600, '
                    '"output_format": "webp", "webp_quality": 82}')
```

La liste complète des clés se lit dans `bfp/core.py`, fonction
`default_settings()`.

### Bouton « Simuler »

Il parcourt le dossier et écrit dans le journal ce qui *serait* produit,
sans toucher au disque. À utiliser systématiquement avant un gros lot, en
particulier avec la politique « écraser ».

Le journal complet du dernier lot est aussi écrit dans
`<configuration GIMP>/batch-folder/dernier-traitement.log`.

### Notes techniques

#### Pourquoi le code introspecte tout

L'API PDB de GIMP 3 a bougé entre les versions 3.0.x, et les opérations GEGL
disponibles dépendent de la compilation. Plutôt que de coder en dur des noms
qui peuvent manquer, le greffon :

* n'écrit une propriété que si `config.find_property()` la trouve ;
* ramène les valeurs dans les bornes déclarées par le `GParamSpec` — c'est
  ainsi que « qualité 90 % » devient `0.9` pour le JPEG et reste `90` pour le
  WebP, sans que l'échelle soit supposée quelque part ;
* cherche les procédures d'export sous leur nom GIMP 3 (`file-jpeg-export`)
  puis sous l'ancien (`file-jpeg-save`), et retombe en dernier recours sur
  `Gimp.file_save()`, qui choisit d'après l'extension ;
* journalise et saute toute étape impossible plutôt que d'interrompre le lot.

#### Fusions de calques et identifiants

`gimp_image_merge_down()` **détruit les deux calques** qu'elle fusionne et en
crée un troisième. Continuer à travailler sur l'objet Python d'avant la
fusion provoque :

> La procédure « gimp-drawable-filter-new » a été appelée avec un ID erroné
> pour le paramètre « drawable ».

C'est pour cela que `apply_split_tone()` renvoie le calque résultant au lieu
d'un booléen, et que `apply_gegl_step()` vérifie `is_valid()` avant de créer
un filtre. Le symptôme visible était que, dans une recette où le virage
partiel n'est pas la dernière étape, **toutes les étapes suivantes**
échouaient silencieusement.

Pendant un lot, les messages de GIMP sont aussi redirigés vers la console
d'erreurs : sans cela, une opération refusée ouvre une fenêtre modale par
image traitée.

#### Organisation du code

```
gimp-batch-folder.py   enregistrement des deux procédures
bfp/core.py            géométrie, nommage, parcours — aucun import GIMP
bfp/looks.py           lecture et validation des recettes — aucun import GIMP
bfp/presets.py         préréglages JSON — aucun import GIMP
bfp/paths.py           emplacements de stockage
bfp/gimpops.py         toutes les interactions avec l'API GIMP 3
bfp/runner.py          orchestration du lot
bfp/ui.py              boîte de dialogue GTK 3
looks/                 les six recettes livrées
tests/                 107 tests, exécutables sans GIMP
```

#### Tests

```bash
python3 -m unittest discover -s tests -p "test_*.py" -t tests
```

Les 107 tests tournent **sans GIMP** : `tests/fakegi.py` et `tests/fakegtk.py`
simulent juste assez de GIMP 3, GEGL et GTK pour exécuter le pipeline et
construire la boîte de dialogue. Ils vérifient l'ordre des opérations, les
valeurs transmises à chaque procédure, les chemins de repli, l'aller-retour
réglages ↔ widgets, et la logique d'activation des sections.

**Ce qu'ils ne peuvent pas vérifier** : que l'API réelle de votre GIMP se
comporte comme la simulation. Les noms de procédures, de propriétés et de
méthodes ont été repris de la documentation officielle libgimp 3.0 et des
greffons Python livrés avec GIMP (`foggify.py`, `file-openraster.py`,
`python-console.py`). Faites un premier essai sur un petit dossier, avec le
bouton **Simuler**.

#### Limites connues

* Les rotations libres ne sont pas gérées pour l'image entière (seulement
  90/180/270°) ; le filigrane texte, lui, accepte un angle quelconque.
* Pas de rotation automatique d'après l'orientation EXIF.
* Les recettes de look s'appliquent à chaque calque de premier niveau ; sur
  une image à calques multiples, le résultat peut différer d'un rendu
  appliqué au composite.
* La compression TIFF est transmise en tant que chaîne (`lzw`, `deflate`…) ;
  si votre version attend autre chose, le journal le signale et la valeur par
  défaut de GIMP s'applique.

### Projets voisins

**[Batcher](https://github.com/kamilburda/batcher)**, de Kamil Burda, est le
greffon de traitement par lots mûr et activement maintenu pour GIMP 3
(licence BSD-3-Clause). Il est plus large que celui-ci : il sait exécuter
*n'importe quel* filtre ou greffon installé comme action de lot, enchaîner
actions et conditions, et travailler sur les images déjà ouvertes dans GIMP —
pas seulement sur un dossier du disque. Si vous cherchez un moteur de lot
généraliste, commencez par là.

Ce greffon-ci est volontairement plus étroit. Il fait une chose — parcourir un
dossier — avec un pipeline fixe et assumé, et apporte deux choses que Batcher
n'a pas : les **recettes de look** en JSON (enchaînements d'opérations GEGL
avec un curseur de dosage global, partageables sous forme de fichiers) et une
étape d'étalonnage intégrée plutôt qu'assemblée à partir de filtres. Il est
aussi sous GPL-3.0, comme GIMP.

Historiquement, [BIMP](https://github.com/alessandrofrancesconi/gimp-plugin-bimp)
d'Alessandro Francesconi était la référence pour GIMP 2.10 ; il n'a pas été
porté vers GIMP 3.

### Contribuer

Les issues et les pull requests sont les bienvenues. Lancez la suite de tests
avant d'ouvrir une PR, et ajoutez un test pour tout bug corrigé —
`tests/fakegi.py` rend la chose peu coûteuse.

### Licence

GPL-3.0-or-later, comme GIMP. Voir [LICENSE](LICENSE).
