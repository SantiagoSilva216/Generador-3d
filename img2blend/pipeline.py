"""
img2blend v2 (optimizado para GPUs de 6 GB)

Carpetas: front/ left/ back/ right/ (+ top/ bottom/ para pintar)  ->  output/modelo.blend
Uso: python pipeline.py [--quality normal|alta|maxima]   (o el boton de la interfaz)

Calidad:
  normal  resolucion 256, solo VRAM
  alta    resolucion 384, descarga partes del modelo a la RAM para liberar VRAM
  maxima  resolucion 512, igual que alta, con trozos mas pequenos (mas lento)
En todos los casos, si falta memoria baja la resolucion sola.
"""
import argparse
import gc
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Debe fijarse antes de importar torch: reduce la fragmentacion de VRAM
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "garbage_collection_threshold:0.8,max_split_size_mb:128")

import ajustes  # noqa: E402

BASE = Path(__file__).resolve().parent
VIEWS = ["front", "left", "back", "right", "top", "bottom"]
MODEL_VIEWS = ["front", "left", "back", "right"]  # las unicas que entiende el modelo de forma
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}

MODEL_REPO = "tencent/Hunyuan3D-2mv"
SUBFOLDER = "hunyuan3d-dit-v2-mv-turbo"

QUALITY = {
    "normal": dict(offload=False, res=(256, 192, 128), chunks=4000, faces=50000),
    "alta": dict(offload=True, res=(384, 320, 256, 192, 128), chunks=4000, faces=100000),
    "maxima": dict(offload=True, res=(512, 448, 384, 320, 256, 192, 128), chunks=2000, faces=150000),
}


def collect_images(base: Path) -> dict:
    found = {}
    for v in VIEWS:
        folder = base / v
        folder.mkdir(exist_ok=True)
        found[v] = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMG_EXT)
    return found


def find_blender(arg: str) -> str:
    """Usa la ruta dada, el PATH, o busca Blender en Archivos de programa."""
    if arg and arg != "blender":
        return arg
    if shutil.which("blender"):
        return "blender"
    root = Path("C:/Program Files/Blender Foundation")
    for exe in sorted(root.glob("Blender */blender.exe"), reverse=True):
        return str(exe)
    sys.exit("No encontre Blender. Instalalo o usa --blender \"ruta/a/blender.exe\"")


def choose_method(found: dict, forced: str) -> str:
    total = sum(len(x) for x in found.values())
    if total == 0:
        sys.exit("No hay imagenes. Coloca al menos una en la carpeta 'front'.")
    if forced != "auto":
        return forced
    if total >= 20:
        return "photogrammetry"
    if not found["front"]:
        sys.exit("Falta la imagen en la carpeta 'front' (es la vista principal).")
    return "multiview"


def load_pipe(offload: bool):
    """Carga el modelo. Con offload, las piezas esperan en RAM y solo la activa usa la GPU."""
    import torch
    from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline as Pipe

    kw = dict(subfolder=SUBFOLDER, variant="fp16")
    pipe, offloaded = None, False
    if offload:
        try:
            pipe = Pipe.from_pretrained(MODEL_REPO, device="cpu", **kw)
            pipe.enable_model_cpu_offload()
            offloaded = True
            print("  Descarga a RAM activada (el modelo usa tambien la RAM del equipo).")
        except Exception as e:
            print(f"  No pude activar la descarga a RAM ({e}). Sigo solo con la GPU.")
            pipe = None
            gc.collect()
            torch.cuda.empty_cache()
    if pipe is None:
        pipe = Pipe.from_pretrained(MODEL_REPO, **kw)
    try:
        pipe.enable_flashvdm()  # decodificador rapido y con menos memoria
    except Exception as e:
        print("  (flashvdm no disponible, sigo sin el):", e)
    return pipe, offloaded


def run_generation(pipe, views, q):
    """Prueba de mayor a menor resolucion hasta que quepa en memoria."""
    import torch

    for res in q["res"]:
        try:
            print(f"Generando con resolucion {res}...")
            mesh = pipe(
                image=views,
                num_inference_steps=5,
                octree_resolution=res,
                num_chunks=q["chunks"],
                output_type="trimesh",
            )[0]
            print(f"  Resolucion final: {res}")
            return mesh
        except torch.cuda.OutOfMemoryError:
            gc.collect()
            torch.cuda.empty_cache()
            print(f"  Sin memoria a {res}, bajo la resolucion.")
    return None


def run_multiview(found: dict, out: Path, q: dict) -> Path:
    import torch
    from PIL import Image
    from hy3dgen.rembg import BackgroundRemover

    rembg = BackgroundRemover()
    views = {}
    for v, files in ((k, found.get(k, [])) for k in MODEL_VIEWS):
        if not files:
            print(f"  - {v}: vacia (se omite)")
            continue
        print(f"  - {v}: {files[0].name}" + (f" (+{len(files)-1} ignoradas)" if len(files) > 1 else ""))
        img = Image.open(files[0]).convert("RGBA")
        if img.getextrema()[3][0] == 255:  # sin transparencia -> quitar fondo
            img = rembg(img.convert("RGB"))
        views[v] = img
    if found.get("top") or found.get("bottom"):
        print("  (top/bottom no las usa el modelo de forma; se usaran al pintar)")
    del rembg
    gc.collect()
    torch.cuda.empty_cache()

    print("Cargando modelo (la primera vez descarga varios GB)...")
    pipe, offloaded = load_pipe(q["offload"])
    try:
        mesh = run_generation(pipe, views, q)
    except Exception as e:
        if not offloaded:
            raise
        print(f"La descarga a RAM fallo ({e}). Reintento sin ella...")
        del pipe
        gc.collect()
        torch.cuda.empty_cache()
        pipe, _ = load_pipe(False)
        mesh = run_generation(pipe, views, q)
    if mesh is None:
        sys.exit("Sin memoria de GPU ni a resolucion minima. Cierra Unity/navegador y reintenta.")

    path = out / "raw_mesh.glb"
    mesh.export(path)
    return path


def run_photogrammetry(found: dict, out: Path) -> Path:
    photos = out / "_photos"
    if photos.exists():
        shutil.rmtree(photos)
    photos.mkdir(parents=True)
    for v, files in found.items():
        for p in files:
            shutil.copy(p, photos / f"{v}_{p.name}")
    ws = out / "colmap"
    ws.mkdir(exist_ok=True)
    subprocess.run(
        ["colmap", "automatic_reconstructor",
         "--workspace_path", str(ws), "--image_path", str(photos), "--dense", "1"],
        check=True,
    )
    mesh = ws / "dense" / "0" / "meshed-poisson.ply"
    if not mesh.exists():
        sys.exit("COLMAP no genero malla. Revisa que las fotos se solapen bien.")
    return mesh


def blender_cleanup(blender: str, mesh: Path, out: Path, faces: int):
    subprocess.run(
        [blender, "--background", "--python", str(BASE / "blender_cleanup.py"),
         "--", str(mesh), str(out / "modelo.blend"), str(faces)],
        check=True,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", default="auto", choices=["auto", "multiview", "photogrammetry"])
    ap.add_argument("--quality", default="normal", choices=list(QUALITY))
    ap.add_argument("--blender", default="blender")
    ap.add_argument("--faces", type=int, default=None, help="caras objetivo (por defecto segun la calidad)")
    a = ap.parse_args()
    q = QUALITY[a.quality]
    faces = a.faces or q["faces"]

    out = BASE / "output"
    out.mkdir(exist_ok=True)

    found = collect_images(BASE)
    print("Imagenes detectadas:", {v: len(f) for v, f in found.items()})
    method = choose_method(found, a.method)
    blender = find_blender(a.blender)
    print(f"Metodo: {method} | Calidad: {a.quality} | Blender: {blender}")

    info = ajustes.vram_info()
    if info:
        print(f"GPU: {info[1] / 1024:.1f} GB de VRAM, {(info[1] - info[0]) / 1024:.1f} GB libres")
    if ajustes.on_battery():
        print("AVISO: el portatil esta con bateria; conecta el cargador para que la GPU rinda.")

    with ajustes.keep_awake():  # el equipo no se suspende mientras genera
        mesh = run_multiview(found, out, q) if method == "multiview" else run_photogrammetry(found, out)
        blender_cleanup(blender, mesh, out, faces)
    print(f"\nListo: {out / 'modelo.blend'}")


if __name__ == "__main__":
    main()
