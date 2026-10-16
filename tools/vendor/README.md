# tools/vendor/

`inkex-1.4.1-py3-none-any.whl` is a plain repackaging of `inkex` 1.4.1 as
built from `https://gitlab.com/inkscape/extensions.git` at commit
`2044255c7a79a0d0b5fb20cb8b0f573ed1ae7892` (tag `EXTENSIONS_AT_INKSCAPE_1.4.1`)
- pure Python, no compiled parts, so it installs identically on any OS/arch.
It's here because building that package straight from git intermittently
produces an invalid metadata string for its `lxml` dependency (see the
comment in `requirements.txt`), which makes a fresh `pip install` fail
unpredictably. This wheel is a known-good build with clean metadata, built
once and then installed directly - no fresh build from git, no flaky step.

## If you need a newer inkex later

1. Try installing straight from git first - this may simply be fixed
   upstream by then:
   ```
   pip install "inkex @ git+https://gitlab.com/inkscape/extensions.git@EXTENSIONS_AT_INKSCAPE_<version>"
   ```
2. If that still fails with the same "invalid metadata"/"RIGHT_PARENTHESIS"
   error, install it with `pip install --no-deps --target /tmp/inkex-build
   "inkex @ git+...@<ref>"` a few times until one attempt succeeds (it's
   non-deterministic, so retrying can work) or inspect
   `/tmp/.../Requires-Dist` and hand-fix the offending line, then rebuild a
   wheel from the resulting `site-packages/inkex` + `inkex-*.dist-info`
   directories as this one was made - zip them together with a `RECORD`
   file listing each entry's path, sha256 (`sha256=<url-safe-b64, no
   padding>`), and size, named `inkex-<version>-py3-none-any.whl`.
3. Update the filename in `requirements.txt` to match.
