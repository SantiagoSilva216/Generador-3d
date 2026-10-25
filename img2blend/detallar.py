"""
Detalla el modelo con las PARTES de detalles/<parte>/ (imagen + zona marcada en la vista frontal):
  1. Compara la foto de cada parte con la imagen frontal para saber EXACTAMENTE donde va
     (alinear.py); si las fotos no coinciden, CLIP busca en la zona marcada lo que mejor
     encaja con la foto y con el NOMBRE de la parte; si nada es fiable, usa la zona marcada.
  2. Estima la profundidad de cada foto (profundidad.py, Depth Anything) para guiar el relieve.
  3. Genera con Hunyuan3D una malla 3D de cada parte a partir de su imagen.
  4. Blender coloca esa malla en su sitio y el modelo de output/modelo.blend adopta su relieve
     en esa zona (no se pone nada encima).
  5. Abre el resultado en Blender (modo escultura, con las imagenes como referencia) para retocar.
Las comprobaciones de la alineacion quedan en output/_detalle/<parte>_alineacion.png
(amarillo = zona que marcaste, verde = sitio calculado).

El modelo anterior se guarda en output/modelo_antes_de_detallar.blend.
Despues de detallar, repite el paso 3 (Pintar) para que la textura incluya las partes nuevas.

Uso: python detallar.py [--quality normal|alta|maxima] [--no-abrir]
"""
import argparse
import gc
import json
import shutil
import subprocess
import sys

from PIL import Image

import numpy as np

import ajustes
import alinear
import profundidad
from pipeline import BASE, IMG_EXT, QUALITY, collect_images, find_blender, load_pipe, run_generation


def collect_parts():
    """{nombre: (imagen, rect)} de las partes que tienen imagen y zona marcada."""
    parts = {}
    root = BASE / "detalles"
    if root.exists():
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            files = sorted(p for p in d.iterdir() if p.suffix.lower() in IMG_EXT)
            try:
                rect = json.loads((d / "region.json").read_text(encoding="utf-8"))["rect"]
            except Exception:
                rect = None
            if files and rect:
                parts[d.name] = (files[0], rect)
            elif files:
                print(f"  - {d.name}: sin zona marcada, se omite")
    return parts


def remove_bg(img, rembg):
    img = img.convert("RGBA")
    if img.getextrema()[3][0] == 255:  # sin transparencia -> quitar fondo
        img = rembg(img.convert("RGB")).convert("RGBA")
    return img


def to_model_uv(res, box):
    """Pasa centro y tamano (px de la frontal) a fracciones (0-1) de la silueta del objeto,
    que es lo que ocupa todo el modelo (igual que al pintar)."""
    bx0, by0, bx1, by1 = box
    bw, bh = max(bx1 - bx0, 1), max(by1 - by0, 1)
    cx, cy = res["center"]
    w, h = res["size"]
    cl = lambda v: min(max(float(v), 0.0), 1.0)
    return [cl((cx - bx0) / bw), cl((cy - by0) / bh)], [float(w) / bw, float(h) / bh]


def generate(parts, front_path, q, tmp, use_clip=True, use_depth=True, strength=0.7):
    import torch
    from hy3dgen.rembg import BackgroundRemover

    rembg = BackgroundRemover()
    front_raw = Image.open(front_path)
    front = remove_bg(front_raw, rembg)
    box = front.getchannel("A").point(lambda a: 255 if a > 20 else 0).getbbox()
    if not box:
        sys.exit("No encontre la silueta del objeto en la imagen frontal.")
    imgs = {n: remove_bg(Image.open(p), rembg) for n, (p, _) in parts.items()}
    del rembg

    print("Comparando cada foto con la imagen frontal para ubicarla...")
    front_rgb = front_raw.convert("RGB")
    aligned, boxes = {}, {}
    for n, (p, rect) in parts.items():
        det_box = imgs[n].getchannel("A").point(lambda a: 255 if a > 20 else 0).getbbox()
        if not det_box:
            det_box = (0, 0) + imgs[n].size
        res = alinear.align(front_rgb, Image.open(p).convert("RGB"), det_box, rect, n,
                            tmp / f"{n}_alineacion.png", use_clip=use_clip)
        aligned[n] = res
        boxes[n] = det_box
        if res["method"] == "zona":
            print(f"  - {n}: no encontre esa foto dentro de la frontal; uso la zona marcada.")
        else:
            print(f"  - {n}: ubicada por {res['method']} (confianza {res['score']:.2f})")
        print(f"    comprobacion: {tmp / (n + '_alineacion.png')}")
    alinear.release()  # libera CLIP antes de cargar Hunyuan3D
    gc.collect()
    torch.cuda.empty_cache()

    depth_files = {}
    if use_depth:
        print("Estimando la profundidad de cada foto (la primera vez descarga ~100 MB)...")
        for n, (p, _) in parts.items():
            mask = np.asarray(imgs[n].getchannel("A")) > 20
            dm = profundidad.depth_map(Image.open(p).convert("RGB"), mask)
            if dm is None:
                break
            np.save(tmp / f"{n}_profundidad.npy", dm)
            Image.fromarray((dm * 255).astype("uint8")).save(tmp / f"{n}_profundidad.png")
            depth_files[n] = tmp / f"{n}_profundidad.npy"
            print(f"  - {n}: comprobacion: {tmp / (n + '_profundidad.png')} (claro = cerca)")
        profundidad.release()
        gc.collect()
        torch.cuda.empty_cache()

    print("Cargando modelo (la primera vez descarga varios GB)...")
    pipe, _ = load_pipe(q["offload"])
    plan = []
    for n in parts:
        print(f"Generando la parte '{n}'...")
        try:
            mesh = run_generation(pipe, {"front": imgs[n]}, q)
        except Exception as e:
            print(f"  Fallo al generar '{n}': {e}")
            gc.collect()
            torch.cuda.empty_cache()
            continue
        if mesh is None:
            print(f"  Sin memoria de GPU para '{n}'; se omite.")
            continue
        glb = tmp / f"{n}.glb"
        mesh.export(glb)
        res = aligned[n]
        center, size = to_model_uv(res, box)
        plan.append({"name": n, "glb": str(glb), "method": res["method"], "center": center,
                     "size": size, "rot": float(res["rot"]), "fit": bool(res["fit"]), "strength": float(strength),
                     "depth": str(depth_files[n]) if n in depth_files else None,
                     "depth_box": [int(v) for v in boxes[n]]})
    del pipe
    gc.collect()
    torch.cuda.empty_cache()
    return plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quality", default="normal", choices=list(QUALITY))
    ap.add_argument("--blender", default="blender")
    ap.add_argument("--no-abrir", action="store_true", help="no abrir Blender al terminar")
    ap.add_argument("--sin-clip", action="store_true", help="no usar CLIP (nombre de la parte) para ubicar")
    ap.add_argument("--fuerza", type=float, default=0.7, help="intensidad del relieve (0.1 - 1.0)")
    ap.add_argument("--sin-profundidad", action="store_true", help="no estimar la profundidad de las fotos")
    a = ap.parse_args()
    q = QUALITY[a.quality]

    out = BASE / "output"
    model = out / "modelo.blend"
    if not model.exists():
        sys.exit("No hay modelo todavia. Primero pulsa 'Generar modelo 3D'.")
    found = collect_images(BASE)
    if not found["front"]:
        sys.exit("Falta la imagen frontal (se usa para ubicar cada parte).")
    parts = collect_parts()
    if not parts:
        sys.exit("No hay partes con imagen y zona marcada.")
    print("Partes a detallar:", ", ".join(parts))

    blender = find_blender(a.blender)
    tmp = out / "_detalle"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    with ajustes.keep_awake():
        plan = generate(parts, found["front"][0], q, tmp, not a.sin_clip, not a.sin_profundidad,
                        min(max(a.fuerza, 0.1), 1.0))
        if not plan:
            sys.exit("No se pudo generar ninguna parte.")
        (tmp / "plan.json").write_text(json.dumps(plan), encoding="utf-8")

        # Siempre se parte del modelo original: si modelo.blend es el resultado de un detallado
        # anterior (no lo has guardado desde entonces), se usa la copia guardada en vez de
        # apilar detalles sobre detalles.
        backup = out / "modelo_antes_de_detallar.blend"
        state = out / "_detallado_estado.json"
        base = model
        try:
            if backup.exists() and json.loads(state.read_text())["mtime"] == model.stat().st_mtime_ns:
                base = backup
        except Exception:
            pass
        if base is model:
            shutil.copy(model, backup)
            base = backup
            print("Modelo base: el modelo actual (copia en modelo_antes_de_detallar.blend)")
        else:
            print("Modelo base: modelo_antes_de_detallar.blend (el modelo original, sin detallados previos)")
        print("Adaptando el modelo al relieve de cada parte en Blender (puede tardar)...")
        subprocess.run(
            [blender, "--background", "--python", str(BASE / "blender_aplicar_detalle.py"),
             "--", str(base), str(model), str(tmp / "plan.json")],
            check=True,
        )
        state.write_text(json.dumps({"mtime": model.stat().st_mtime_ns}))

    # imagenes de referencia para el modo escultura
    refs = out / "_pintura" / "detalles"
    if refs.exists():
        shutil.rmtree(refs)
    refs.mkdir(parents=True)
    for n, (p, _) in parts.items():
        Image.open(p).convert("RGBA").save(refs / f"{n}.png")

    print(f"\nListo: {model}  (copia anterior: {backup.name})")
    print("Repite el paso 'Pintar con mis imagenes' para que la textura incluya las partes nuevas.")
    if not a.no_abrir:
        print("Abriendo el resultado en Blender para revisarlo...")
        subprocess.Popen(
            [blender, str(model), "--python", str(BASE / "blender_detallar.py"), "--", str(refs)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


if __name__ == "__main__":
    main()
