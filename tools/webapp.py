#!/usr/bin/env python
"""
Local web UI for image_to_jef: upload a PNG/JPG, pick an approximate size,
and get back a .jef embroidery file plus a preview of how the stitches will
look.

This is a personal dev utility, not part of the Ink/Stitch extension itself.

Run with the project's venv so inkex/lib import correctly:
    venv/bin/python tools/webapp.py
Then open http://127.0.0.1:5001 in a browser.
"""
import shutil
import sys
import uuid
from pathlib import Path

from flask import Flask, abort, render_template_string, request, send_file, url_for

sys.path.insert(0, str(Path(__file__).parent))
import image_to_jef as pipeline  # noqa: E402
import text_to_jef  # noqa: E402

APP_ROOT = Path(__file__).parent.resolve()
JOBS_DIR = Path.home() / ".cache" / "inkstitch-webapp"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
FONTS_DIR = APP_ROOT.parent / "fonts" / "src"

INKSCAPE_BIN = shutil.which("inkscape") or "/opt/homebrew/bin/inkscape"

app = Flask(__name__)

PAGE = """
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Ink/Stitch a JEF</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, sans-serif; max-width: 680px; margin: 40px auto; padding: 0 16px; color: #222; }
  h1 { font-size: 1.4rem; }
  form { display: flex; flex-direction: column; gap: 14px; background: #f7f5f2; padding: 20px; border-radius: 10px; }
  label { font-weight: 600; font-size: 0.9rem; }
  input[type=file], input[type=number], input[type=text], select {
    width: 100%; padding: 8px; font-size: 1rem; border-radius: 6px; border: 1px solid #ccc;
  }
  .row { display: flex; flex-wrap: wrap; gap: 12px; }
  .row > div { flex: 1 1 140px; min-width: 0; }
  button { padding: 10px 18px; font-size: 1rem; border-radius: 6px; border: none; background: #970533; color: white; cursor: pointer; }
  button:hover { background: #7c0429; }
  .hint { color: #666; font-size: 0.85rem; margin-top: -8px; }
  .error { background: #fdecea; color: #a4262c; padding: 12px; border-radius: 8px; margin-bottom: 16px; }
  .warning { background: #fff4e5; color: #8a5300; padding: 12px; border-radius: 8px; margin-bottom: 16px; }
  .result img { max-width: 100%; border: 1px solid #ddd; border-radius: 8px; background: white; }
  .stats { background: #f7f5f2; padding: 14px 20px; border-radius: 10px; margin: 16px 0; }
  a.button { display: inline-block; margin-top: 10px; padding: 10px 18px; background: #970533; color: white;
             border-radius: 6px; text-decoration: none; }
  a.back { display: inline-block; margin-top: 16px; color: #970533; }
  .tabs { display: flex; gap: 8px; margin-bottom: 16px; }
  .tab-btn { flex: 1; padding: 10px; border: 1px solid #ccc; background: white; border-radius: 8px;
             cursor: pointer; font-weight: 600; color: #666; }
  .tab-btn.active { background: #970533; color: white; border-color: #970533; }
  .tab-panel { display: none; }
  .tab-panel.active { display: block; }
  #font-search { margin-bottom: 10px; }
  .font-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; max-height: 320px; overflow-y: auto;
               border: 1px solid #ddd; border-radius: 8px; padding: 8px; background: white; }
  .font-card { display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 8px;
               border: 1px solid #eee; border-radius: 6px; cursor: pointer; text-align: center; }
  .font-card:hover { border-color: #970533; }
  .font-card:has(input:checked) { border-color: #970533; background: #fdf0f3; }
  .font-card input { margin: 0; }
  .font-card img { max-width: 100%; max-height: 40px; object-fit: contain; }
  .font-card span { font-size: 0.75rem; color: #444; }
  .font-card .font-min { font-size: 0.7rem; color: #970533; font-weight: 600; }
  .font-card.hidden { display: none; }
  .logo { display: block; width: 100%; max-width: 100%; height: auto; border-radius: 10px; margin-bottom: 20px; }
</style>
</head>
<body>
<img class="logo" src="{{ url_for('static', filename='logo.jpg') }}" alt="Meg&amp;Co. Design">
<h1>Ink/Stitch &rarr; .jef para bordadora</h1>
{% if error %}<div class="error">{{ error }}</div>{% endif %}

{% if not result %}
<div class="tabs">
  <button type="button" class="tab-btn {{ 'active' if tab == 'image' else '' }}" onclick="showTab('image')">Imagen</button>
  <button type="button" class="tab-btn {{ 'active' if tab == 'text' else '' }}" onclick="showTab('text')">Texto</button>
</div>

<div id="panel-image" class="tab-panel {{ 'active' if tab == 'image' else '' }}">
<form method="post" action="{{ url_for('convert_image_view') }}" enctype="multipart/form-data">
  <div>
    <label>Imagen (PNG o JPG)</label><br>
    <input type="file" name="image" accept=".png,.jpg,.jpeg" required>
  </div>
  <div class="row">
    <div>
      <label>Ancho deseado (mm)</label>
      <input type="number" name="width_mm" step="0.1" min="1" placeholder="ej. 21">
    </div>
    <div>
      <label>Alto deseado (mm)</label>
      <input type="number" name="height_mm" step="0.1" min="1" placeholder="ej. 30">
    </div>
  </div>
  <div class="hint">Si dejás ambos vacíos, se usa el tamaño natural del trazado (puede ser grande).</div>
  <button type="submit">Generar .jef</button>
</form>
</div>

<div id="panel-text" class="tab-panel {{ 'active' if tab == 'text' else '' }}">
<form method="post" action="{{ url_for('convert_text_view') }}">
  <div>
    <label>Texto</label>
    <input type="text" name="text" placeholder="ej. Meg&amp;Co" required>
  </div>
  <div>
    <label>Tipografía de bordado</label>
    <input type="text" id="font-search" placeholder="Buscar por nombre..." oninput="filterFonts(this.value)">
    <div class="font-grid">
      {% for font_id, name, min_height_mm in fonts %}
      <label class="font-card" data-name="{{ name.lower() }}">
        <input type="radio" name="font" value="{{ font_id }}" {{ 'checked' if loop.first else '' }} required>
        <img src="{{ url_for('font_preview', font_id=font_id) }}" alt="{{ name }}" loading="lazy">
        <span>{{ name }}</span>
        <span class="font-min">m&iacute;n {{ '%.1f'|format(min_height_mm) }}mm</span>
      </label>
      {% endfor %}
    </div>
  </div>
  <div class="row">
    <div>
      <label>Alto de una letra may&uacute;scula (mm)</label>
      <input type="number" name="height_mm" step="0.1" min="1" value="15">
    </div>
    <div>
      <label>Color de hilo (opcional)</label>
      <input type="text" name="color" placeholder="ej. #9da274">
    </div>
  </div>
  <div>
    <label>Tipo de puntada</label>
    <select name="stitch_style">
      <option value="satin">Sat&iacute;n (relleno, m&aacute;s bordado tradicional)</option>
      <option value="running_stitch">L&iacute;nea corrida (fina y s&oacute;lida, mejor para tama&ntilde;os muy chicos)</option>
    </select>
  </div>
  <div class="hint">El tama&ntilde;o es por letra, no por la frase completa - la palabra se arma con letras a esa escala. Cada tipograf&iacute;a tiene un rango recomendado; si el resultado sale fuera de rango, te avisamos.</div>
  <div class="hint">Las fuentes con sat&iacute;n pueden tardar varios minutos en generarse (Ink/Stitch calcula el ruteo del hilo letra por letra) - la p&aacute;gina va a quedar cargando, es normal. L&iacute;nea corrida es mucho m&aacute;s r&aacute;pida.</div>
  <button type="submit">Generar .jef</button>
</form>
</div>

<script>
function showTab(name) {
  document.querySelectorAll('.tab-panel').forEach(function(el) { el.classList.remove('active'); });
  document.querySelectorAll('.tab-btn').forEach(function(el) { el.classList.remove('active'); });
  document.getElementById('panel-' + name).classList.add('active');
  document.querySelectorAll('.tab-btn')[name === 'image' ? 0 : 1].classList.add('active');
}
function filterFonts(query) {
  var q = query.trim().toLowerCase();
  document.querySelectorAll('.font-card').forEach(function(card) {
    card.classList.toggle('hidden', q.length > 0 && card.dataset.name.indexOf(q) === -1);
  });
}
</script>

{% else %}
<div class="result">
  <h2>Resultado</h2>
  {% if result.warning %}<div class="warning">{{ result.warning }}</div>{% endif %}
  <img src="{{ url_for('preview', job_id=result.job_id) }}" alt="Preview de puntadas">
  <div class="stats">
    <div><b>Tamaño final:</b> {{ '%.2f'|format(result.width_mm) }}mm &times; {{ '%.2f'|format(result.height_mm) }}mm</div>
    <div><b>Puntadas:</b> {{ result.stitch_count }}</div>
    <div><b>Colores de hilo:</b> {{ result.colors|join(', ') }}</div>
  </div>
  <a class="button" href="{{ url_for('download', job_id=result.job_id) }}">Descargar .jef</a>
</div>
<a class="back" href="{{ url_for('index', tab=result.mode) }}">&larr; Convertir otra {{ 'imagen' if result.mode == 'image' else 'frase' }}</a>
{% endif %}
</body>
</html>
"""


@app.route("/", methods=["GET"])
def index():
    tab = request.args.get("tab", "image")
    return render_template_string(PAGE, error=None, result=None, tab=tab, fonts=list(text_to_jef.iter_fonts()))


@app.route("/convert/image", methods=["POST"])
def convert_image_view():
    upload = request.files.get("image")
    if not upload or not upload.filename:
        return render_template_string(PAGE, error="Subí una imagen PNG o JPG.", result=None, tab="image",
                                       fonts=list(text_to_jef.iter_fonts()))

    suffix = Path(upload.filename).suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg"):
        return render_template_string(PAGE, error="Formato no soportado, usá PNG o JPG.", result=None, tab="image",
                                       fonts=list(text_to_jef.iter_fonts()))

    width_mm = request.form.get("width_mm", type=float)
    height_mm = request.form.get("height_mm", type=float)
    if bool(width_mm) != bool(height_mm):
        return render_template_string(PAGE, error="Completá ancho Y alto, o dejá ambos vacíos.", result=None,
                                       tab="image", fonts=list(text_to_jef.iter_fonts()))

    job_id = uuid.uuid4().hex[:12]
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True)

    input_path = job_dir / f"input{suffix}"
    upload.save(input_path)
    jef_path = job_dir / "output.jef"

    try:
        actual_w, actual_h = pipeline.convert(
            input_path, jef_path,
            inkscape_bin=INKSCAPE_BIN, python_bin=sys.executable,
            width_mm=width_mm, height_mm=height_mm,
        )
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return render_template_string(PAGE, error=f"No se pudo convertir la imagen: {e}", result=None, tab="image",
                                       fonts=list(text_to_jef.iter_fonts()))

    result = _build_result(job_id, jef_path, job_dir, actual_w, actual_h, mode="image")
    return render_template_string(PAGE, error=None, result=result, tab="image", fonts=[])


@app.route("/convert/text", methods=["POST"])
def convert_text_view():
    text = request.form.get("text", "").strip()
    font_id = request.form.get("font", "")
    height_mm = request.form.get("height_mm", type=float) or 15.0
    color = request.form.get("color", "").strip() or None
    stitch_style = request.form.get("stitch_style", "satin")
    fonts = list(text_to_jef.iter_fonts())

    if not text:
        return render_template_string(PAGE, error="Escribí el texto a bordar.", result=None, tab="text", fonts=fonts)

    job_id = uuid.uuid4().hex[:12]
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True)
    jef_path = job_dir / "output.jef"
    svg_path = job_dir / "text.svg"

    try:
        _, _, within_range, narrowest_pinch_mm = text_to_jef.render_text_to_svg(
            font_id, text, height_mm, svg_path, color=color, stitch_style=stitch_style)
        pipeline.svg_to_jef(sys.executable, svg_path, jef_path)
        actual_w, actual_h = pipeline.jef_bounds_mm(jef_path)
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return render_template_string(PAGE, error=f"No se pudo generar el texto: {e}", result=None, tab="text",
                                       fonts=fonts)

    warnings = []
    if not within_range:
        warnings.append(f"{height_mm}mm por letra está fuera del rango recomendado para esta tipografía - "
                         "puede verse deforme o difícil de coser bien. Probá otro tamaño u otra fuente.")
    if narrowest_pinch_mm is not None and narrowest_pinch_mm < text_to_jef.PINCH_WARNING_MM:
        warnings.append(f"Este texto tiene una esquina que queda en {narrowest_pinch_mm:.3f}mm de ancho a este "
                         "tamaño - Ink/Stitch va a amontonar muchas puntadas ahí tratando de coserla, lo que puede "
                         "trabar una máquina real. Probá un tamaño más grande, o el estilo \"Línea corrida\" "
                         "(sin rieles de satín que puedan pellizcarse).")
    warning = " ".join(warnings) or None

    result = _build_result(job_id, jef_path, job_dir, actual_w, actual_h, mode="text", warning=warning)
    return render_template_string(PAGE, error=None, result=result, tab="text", fonts=[])


def _build_result(job_id, jef_path, job_dir, width_mm, height_mm, mode, warning=None):
    import pystitch
    pattern = pystitch.read(str(jef_path))
    pystitch.write_png(pattern, str(job_dir / "preview.png"))
    return {
        "job_id": job_id,
        "width_mm": width_mm,
        "height_mm": height_mm,
        "stitch_count": len(pattern.stitches),
        "colors": [t.description for t in pattern.threadlist],
        "mode": mode,
        "warning": warning,
    }


@app.route("/download/<job_id>.jef")
def download(job_id):
    jef_path = JOBS_DIR / job_id / "output.jef"
    if not jef_path.exists():
        abort(404)
    return send_file(jef_path, as_attachment=True, download_name=f"{job_id}.jef")


@app.route("/preview/<job_id>.png")
def preview(job_id):
    png_path = JOBS_DIR / job_id / "preview.png"
    if not png_path.exists():
        abort(404)
    return send_file(png_path, mimetype="image/png")


@app.route("/font-preview/<font_id>.png")
def font_preview(font_id):
    # font_id comes from our own generated <option>/<label> values (font folder
    # names we listed ourselves), but guard against path traversal regardless.
    png_path = (FONTS_DIR / font_id / "preview.png").resolve()
    if FONTS_DIR.resolve() not in png_path.parents or not png_path.exists():
        abort(404)
    return send_file(png_path, mimetype="image/png")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=False, threaded=True)
