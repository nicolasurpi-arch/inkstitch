# This repo

A checkout of [Ink/Stitch](https://inkstitch.org) (the Inkscape embroidery
extension) with one addition: **[tools/](tools/README.md)**, a set of
personal scripts built on top of it that turn an image or a line of text
directly into a `.jef` file (Janome embroidery machine format) without
opening Inkscape by hand — a local web UI plus two CLI scripts. See
[tools/README.md](tools/README.md) for what each script does.

Everything else in the repo (`lib/`, `inkstitch.py`, `fonts/`, etc.) is
upstream Ink/Stitch, unmodified except where noted.

## Setting up on a new machine

```bash
tools/setup.sh
```

Installs Inkscape (via Homebrew) if missing, creates `venv/`, installs all
Python dependencies, pulls the `fonts/` submodule, and on macOS builds
`Ink2Jef.app` (a double-clickable launcher). Re-running it later (e.g. after
`git pull`) is safe and picks up new dependencies.

Then run the web app:

```bash
venv/bin/python tools/webapp.py
```

and open <http://127.0.0.1:5001> — or on macOS, double-click `Ink2Jef.app`.

If `tools/setup.sh` or a manual `pip install -r requirements.txt` fails on
the `inkex` line with an "invalid metadata" / "Expected matching RIGHT_
PARENTHESIS" error, see the explanation and the one-line fix
(`--use-deprecated=legacy-resolver`) in [tools/README.md](tools/README.md#setup)
— it's a real bug in `inkex`'s upstream git metadata, not a mistake in this
repo's setup, and it's already handled by `tools/setup.sh` itself.

## Working in tools/

- Every change to `tools/image_to_jef.py`'s color-clustering or stitch-type
  logic has, more than once, looked correct on the test image at hand and
  then broken a *different* real design in a way only visible by actually
  rendering it. Before changing shared logic there (`find_color_clusters`,
  `_detect_gradient`, `classify_stitch_types`, despeckling), regenerate and
  visually compare the stitch preview (`pystitch.write_png`) for every
  previously-working test design, not just the one motivating the change -
  and do this in a scratch script first, without touching the real file,
  until it's confirmed clean.
- Restart `tools/webapp.py` after any change to `tools/*.py` - it does not
  autoreload (`debug=False`).
