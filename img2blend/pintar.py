"""
Pinta output/modelo.blend proyectando tus imagenes (front/left/back/right/top/bottom) sobre la malla.

Pasos:
  1. Recorta cada imagen a la silueta del objeto (quita el fondo si hace falta).
  2. Blender despliega los UVs y vuelca la malla.
  3. Aqui se calcula la textura: cada punto de la superficie toma el color de las
     vistas que lo miran de frente (mezcla ponderada por el angulo).
  4. Blender aplica la textura como material -> output/modelo_pintado.blend

Uso: python pintar.py   (o el boton "Pintar con mis imagenes" de la interfaz)
"""
import argparse
import math
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from pipeline import BASE, collect_images, find_blender

# Si los lados te salen cambiados (izquierda <-> derecha), pon True o usa --swap-sides
SWAP_SIDES = False

# Giros de 90 grados (0-3) para las vistas superior/inferior si te salen rotadas.
# Convencion por defecto = vistas de Blender (numpad 7 / Ctrl+numpad 7):
#   superior: derecha = +X, arriba de la imagen = +Y (el frente del modelo queda ABAJO)
#   inferior: derecha = +X, arriba de la imagen = -Y (el frente del modelo queda ARRIBA)
TOP_ROT = 0
BOTTOM_ROT = 0

# vista: (eje horizontal, invertido?, eje vertical, invertido?, direccion hacia la camara)
# Ejes de Blender tras importar: el frente del modelo mira a -Y, arriba es +Z.
VIEW_CFG = {
    "front": (0, False, 2, False, (0.0, -1.0, 0.0)),
    "back": (0, True, 2, False, (0.0, 1.0, 0.0)),
    "left": (1, False, 2, False, (1.0, 0.0, 0.0)),
    "right": (1, True, 2, False, (-1.0, 0.0, 0.0)),
    "top": (0, False, 1, False, (0.0, 0.0, 1.0)),
    "bottom": (0, False, 1, True, (0.0, 0.0, -1.0)),
}


# ---------------------------------------------------------------- imagenes
def prepare_views(found, out_dir: Path):
    """Quita el fondo (si no hay transparencia) y recorta a la silueta."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    rembg = None
    for v, files in found.items():
        if not files:
            continue
        img = Image.open(files[0]).convert("RGBA")
        if img.getextrema()[3][0] == 255:  # sin transparencia -> quitar fondo
            if rembg is None:
                from hy3dgen.rembg import BackgroundRemover
                rembg = BackgroundRemover()
            img = rembg(img.convert("RGB")).convert("RGBA")
        box = img.getchannel("A").point(lambda a: 255 if a > 20 else 0).getbbox()
        if box:
            img = img.crop(box)
        img.save(out_dir / f"{v}.png")
        print(f"  - {v}: {files[0].name} -> {img.size[0]}x{img.size[1]}")


def load_view(path: Path):
    a = np.asarray(Image.open(path).convert("RGBA"), dtype=np.float32) / 255.0
    a = a.copy()
    a[..., :3] *= a[..., 3:4]  # alfa premultiplicado
    return a


def sample(img, u, v):
    """Muestreo bilineal. u,v en [0,1]; v=1 es arriba."""
    H, W, _ = img.shape
    x = np.clip(u, 0, 1) * (W - 1)
    y = (1 - np.clip(v, 0, 1)) * (H - 1)
    x0 = np.floor(x).astype(np.int32)
    y0 = np.floor(y).astype(np.int32)
    x1 = np.minimum(x0 + 1, W - 1)
    y1 = np.minimum(y0 + 1, H - 1)
    fx = (x - x0)[:, None].astype(np.float32)
    fy = (y - y0)[:, None].astype(np.float32)
    top = img[y0, x0] * (1 - fx) + img[y0, x1] * fx
    bot = img[y1, x0] * (1 - fx) + img[y1, x1] * fx
    return top * (1 - fy) + bot * fy


# ---------------------------------------------------------------- geometria
def vertex_normals(pos, tris):
    fn = np.cross(pos[tris[:, 1]] - pos[tris[:, 0]], pos[tris[:, 2]] - pos[tris[:, 0]])
    vn = np.zeros_like(pos)
    for k in range(3):
        np.add.at(vn, tris[:, k], fn)
    return vn / np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-12)


def rasterize(uvs, size):
    """Para cada texel guarda en que triangulo UV cae y sus coordenadas baricentricas."""
    tri_id = np.full((size, size), -1, dtype=np.int32)
    bary = np.zeros((size, size, 3), dtype=np.float32)
    uvp = uvs.astype(np.float64) * size
    T = len(uvp)
    for t in range(T):
        (ax, ay), (bx, by), (cx, cy) = uvp[t]
        d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(d) < 1e-9:
            continue
        x0 = max(int(math.floor(min(ax, bx, cx))) - 1, 0)
        x1 = min(int(math.ceil(max(ax, bx, cx))) + 1, size)
        y0 = max(int(math.floor(min(ay, by, cy))) - 1, 0)
        y1 = min(int(math.ceil(max(ay, by, cy))) + 1, size)
        if x1 <= x0 or y1 <= y0:
            continue
        px = np.arange(x0, x1) + 0.5
        py = (np.arange(y0, y1) + 0.5)[:, None]
        l0 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
        l1 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
        l2 = 1 - l0 - l1
        m = (l0 >= -1e-3) & (l1 >= -1e-3) & (l2 >= -1e-3)
        if not m.any():
            continue
        tri_id[y0:y1, x0:x1][m] = t
        sb = bary[y0:y1, x0:x1]
        sb[m, 0] = l0[m]
        sb[m, 1] = l1[m]
        sb[m, 2] = l2[m]
        if t % 10000 == 0:
            print(f"    triangulos {t}/{T}", flush=True)
    return tri_id, bary


def dilate(img, mask, iters=10):
    """Extiende los colores unos pixeles fuera de cada isla para evitar costuras."""
    img = img.copy()
    mask = mask.copy()
    for _ in range(iters):
        new = mask.copy()
        out = img.copy()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sm = np.roll(mask, (dy, dx), (0, 1))
            si = np.roll(img, (dy, dx), (0, 1))
            take = sm & ~new
            out[take] = si[take]
            new |= take
        img, mask = out, new
    return img, mask


# ---------------------------------------------------------------- textura
def build_texture(npz_path, views_dir, size, out_png, swap_sides=False):
    data = np.load(npz_path)
    pos = data["pos"].astype(np.float32)
    tris = data["tris"].astype(np.int64)
    uvs = data["uvs"]

    files = {v: views_dir / f"{v}.png" for v in VIEW_CFG}
    if swap_sides:
        files["left"], files["right"] = files["right"], files["left"]
    imgs = {v: load_view(p) for v, p in files.items() if p.exists()}
    for v, k in (("top", TOP_ROT), ("bottom", BOTTOM_ROT)):
        if v in imgs and k % 4:
            imgs[v] = np.ascontiguousarray(np.rot90(imgs[v], k))
    if not imgs:
        sys.exit("No hay imagenes de vistas para proyectar.")

    # color medio (relleno de zonas sin informacion)
    rgb_sum = sum(i[..., :3].sum((0, 1)) for i in imgs.values())
    a_sum = sum(i[..., 3].sum() for i in imgs.values())
    mean = (rgb_sum / a_sum).astype(np.float32) if a_sum > 0 else np.full(3, 0.5, np.float32)

    print("  rasterizando UVs...")
    tri_id, bary = rasterize(uvs, size)
    mask = tri_id >= 0
    ys, xs = np.nonzero(mask)
    tid = tri_id[ys, xs]
    bc = bary[ys, xs]
    tv = tris[tid]
    P = (pos[tv] * bc[:, :, None]).sum(1)
    vn = vertex_normals(pos, tris)
    N = (vn[tv] * bc[:, :, None]).sum(1)
    N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)

    mn, mx = pos.min(0), pos.max(0)
    nrm = (P - mn) / np.maximum(mx - mn, 1e-9)

    print("  proyectando vistas...")
    acc_rgb = np.zeros((len(P), 3), np.float32)
    acc_a = np.zeros(len(P), np.float32)
    for v, img in imgs.items():
        axis, flip, vaxis, vflip, d = VIEW_CFG[v]
        u = nrm[:, axis]
        if flip:
            u = 1 - u
        vv = nrm[:, vaxis]
        if vflip:
            vv = 1 - vv
        s = sample(img, u, vv)
        w = np.clip(N @ np.array(d, np.float32), 0, 1) ** 4 + 1e-3
        acc_rgb += w[:, None] * s[:, :3]
        acc_a += w * s[:, 3]

    rgb = acc_rgb / np.maximum(acc_a, 1e-6)[:, None]
    rgb[acc_a < 1e-4] = mean

    tex = np.zeros((size, size, 3), np.float32)
    tex[ys, xs] = rgb
    tex, filled = dilate(tex, mask)
    tex[~filled] = mean

    arr = (np.clip(tex[::-1], 0, 1) * 255 + 0.5).astype(np.uint8)  # fila 0 = arriba
    Image.fromarray(arr).save(out_png)
    print(f"  textura guardada: {out_png}")


# ---------------------------------------------------------------- principal
def run_blender(blender, *args):
    subprocess.run([blender, "--background", "--python", str(BASE / "blender_paint.py"),
                    "--", *map(str, args)], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=2048, help="resolucion de la textura")
    ap.add_argument("--swap-sides", action="store_true", help="intercambia izquierda y derecha")
    ap.add_argument("--blender", default="blender")
    a = ap.parse_args()

    out = BASE / "output"
    model = out / "modelo.blend"
    if not model.exists():
        sys.exit("Primero genera el modelo (boton 'Generar modelo 3D'): falta output/modelo.blend")

    found = collect_images(BASE)
    if not any(found.values()):
        sys.exit("No hay imagenes en front/left/back/right.")

    blender = find_blender(a.blender)
    work = out / "_pintura"
    work.mkdir(parents=True, exist_ok=True)
    views_dir = work / "vistas"
    texture = out / "textura.png"

    print("Preparando imagenes...")
    prepare_views(found, views_dir)

    print("1/3 Desplegando UVs en Blender...")
    run_blender(blender, "uv", model, work / "modelo_uv.blend", work / "malla.npz")

    print("2/3 Calculando la textura...")
    build_texture(work / "malla.npz", views_dir, a.size, texture, a.swap_sides or SWAP_SIDES)

    painted = out / "modelo_pintado.blend"
    if painted.exists():  # por si lo habias retocado a mano
        shutil.copy(painted, out / "modelo_pintado_anterior.blend")

    print("3/3 Aplicando la textura en Blender...")
    run_blender(blender, "material", work / "modelo_uv.blend", texture, out / "modelo_pintado.blend")

    print(f"\nListo: {out / 'modelo_pintado.blend'}")


if __name__ == "__main__":
    main()
