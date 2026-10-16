# tools/ — image & text → .jef

Custom scripts on top of this Ink/Stitch checkout that turn a PNG/JPG image
or a line of text directly into a `.jef` file (Janome embroidery machine
format), without opening Inkscape by hand. Not part of the upstream Ink/Stitch
extension — personal tooling built on top of it.

- **`image_to_jef.py`** — traces a raster image color-by-color (via Inkscape's
  bitmap tracing) and runs it through Ink/Stitch's stitch generation. Picks
  tatami fill, satin columns, or contour fill per region automatically.
- **`text_to_jef.py`** — renders text in any of the ~140 bundled embroidery
  fonts (`fonts/src/`) straight to `.jef`, with a minimum-readable-size check
  per font.
- **`webapp.py`** — a local Flask UI for both of the above: upload an image or
  type text, preview the stitched result, download the `.jef`.
- **`font_catalog_pdf.py`** — generates a browsable PDF of every bundled font
  at its own minimum readable size, for picking a font by eye.

Each script's own module docstring (top of the file) has the full usage and
design notes — this file is just the map and the setup steps.

## Setup

From the repo root:

```bash
tools/setup.sh
```

This checks for Python 3.11+ and Inkscape (installing Inkscape via Homebrew
if it's missing and Homebrew is available), creates `venv/` if needed,
installs all Python dependencies, pulls the `fonts/` submodule, and — on
macOS — builds `Ink2Jef.app` (see below). Safe to re-run any time, e.g.
after `git pull`.

Do this by hand instead if you'd rather not run a script, or need Linux/
Windows (the dependency install step works there too, just not the `.app`
build, which is macOS-only):

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt -r tools/requirements.txt
git submodule update --init --recursive
```

`requirements.txt` points `inkex` at a wheel vendored in `tools/vendor/`
instead of building it from git directly - see the comment above that line
in `requirements.txt` and `tools/vendor/README.md` for why (building it
fresh from git intermittently produces invalid metadata and the install
fails outright - not a version pin we chose, a real upstream bug).

## Running it

```bash
venv/bin/python tools/webapp.py
```

then open <http://127.0.0.1:5001>. Job inputs/outputs are cached under
`~/.cache/inkstitch-webapp/<job_id>/`.

Or use either script directly from the command line - `--help` on each for
the full flag list:

```bash
venv/bin/python tools/image_to_jef.py input.png output.jef --width-mm 25 --height-mm 40
venv/bin/python tools/text_to_jef.py "Meg&Co." output.jef --font <font_id>
```

### Ink2Jef.app (macOS)

A double-clickable launcher that starts the web server (if it isn't already
running) and opens it in the browser. Built by `tools/setup.sh`, or directly
via `tools/build_app.sh`. It's generated, not committed - re-run
`tools/build_app.sh` any time after moving the repo, and move/copy the
`.app` itself wherever you like afterwards (Desktop, Dock, `/Applications`)
since the path to this repo is baked in at build time.

Server logs land in `~/Library/Logs/Ink2Jef/webapp.log`.
