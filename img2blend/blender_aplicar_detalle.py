"""Se ejecuta DENTRO de Blender (lo lanza detallar.py):
  blender -b -P blender_aplicar_detalle.py -- entrada.blend salida.blend plan.json

Por cada parte del plan:
  1. importa su malla (GLB) y la escala, gira y coloca en el sitio exacto que calculo
     detallar.py comparando la foto de la parte con la imagen frontal;
  2. subdivide la zona del modelo para tener resolucion suficiente;
  3. mueve la superficie del modelo hasta el relieve de la parte. NO se pone nada encima:
     el propio modelo adopta la forma detallada, y la malla de la parte se descarta.
Si hay mapa de profundidad de la foto (profundidad.py), guia ese relieve: la malla de Hunyuan3D
fija la escala de la profundidad y el mapa aporta el detalle que se ve en la foto.
"""
import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

src, dst, plan_path = sys.argv[sys.argv.index("--") + 1:][:3]
plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))

print("[blender_aplicar_detalle v5-relieve-suave] el modelo adopta el relieve; no se anade malla encima")
bpy.ops.wm.open_mainfile(filepath=src)

MAX_FACES = 250000   # tope de caras nuevas por parte al subdividir
DEPTH_BLEND = 0.6    # peso del mapa de profundidad frente a la malla de Hunyuan3D (0-1)
DEPTH_MIN_CORR = 0.3 # correlacion minima entre ambos para fiarse del mapa
FEATHER = 0.55       # dentro de este radio (0-1) el relieve se aplica al 100%; fuera se desvanece
FACING_MIN = 0.45    # solo se mueven vertices que miran al frente (-Y) al menos asi (1 = de frente)
FACING_FULL = 0.80   # a partir de aqui el relieve se aplica entero; entre ambos se desvanece
MAX_REL = 0.12       # desplazamiento maximo = 12% del tamano de la parte (evita picos y tiras)
SMOOTH_ITERS = 2     # pasadas de suavizado del relieve (quita ruido de los rayos)


def get_model():
    obj = bpy.data.objects.get("Modelo")
    if obj is None:
        meshes = [o for o in bpy.data.objects if o.type == "MESH"]
        if not meshes:
            raise SystemExit("No hay ninguna malla en el .blend")
        obj = meshes[0]
    return obj


def activate(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def world_bounds(obj):
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return mn, mx


def mesh_bounds(me):
    n = len(me.vertices)
    a = np.empty(n * 3, dtype=np.float32)
    me.vertices.foreach_get("co", a)
    a = a.reshape(n, 3)
    return Vector(a.min(0).tolist()), Vector(a.max(0).tolist())


def import_detail(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == "MESH"]
    extras = [o for o in new if o.type != "MESH"]
    if not meshes:
        for o in new:
            bpy.data.objects.remove(o, do_unlink=True)
        return None
    for o in meshes:  # quita el padre conservando la posicion
        if o.parent is not None:
            m = o.matrix_world.copy()
            o.parent = None
            o.matrix_world = m
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    for o in extras:
        bpy.data.objects.remove(o, do_unlink=True)
    return obj


def drop(obj):
    me = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if me.users == 0:
        bpy.data.meshes.remove(me)


def place_detail(detail, p, mn, mx):
    """Escala, gira (en el plano de la vista frontal) y centra la parte sobre su sitio.
    Devuelve (cx, cz, mitad_ancho, mitad_alto, giro) de la parte ya colocada."""
    u, v = p["center"]
    wu, hv = p["size"]
    DX, DZ = mx.x - mn.x, mx.z - mn.z
    cx, cz = mn.x + u * DX, mx.z - v * DZ
    tw, th = wu * DX, hv * DZ
    dmn, dmx = mesh_bounds(detail.data)
    dd = dmx - dmn
    sx, sz = tw / max(dd.x, 1e-6), th / max(dd.z, 1e-6)
    s = min(sx, sz) if p.get("fit") else (sx + sz) / 2
    rot = float(p.get("rot", 0.0))
    c = (dmn + dmx) / 2
    m = (Matrix.Translation(Vector((cx, 0.0, cz))) @ Matrix.Rotation(rot, 4, "Y")
         @ Matrix.Scale(s, 4) @ Matrix.Translation(Vector((-c.x, -c.y, -c.z))))
    detail.data.transform(m)
    detail.data.update()
    return cx, cz, dd.x * s / 2, dd.z * s / 2, rot


def to_local(co, cx, cz, rot):
    """Coordenadas (x, z) de un punto en el sistema de la parte (deshace su giro)."""
    dx, dz = co.x - cx, co.z - cz
    c, s = math.cos(rot), math.sin(rot)
    return dx * c - dz * s, dx * s + dz * c


def region_r(co, cx, cz, hw, hh, rot):
    """0 en el centro de la zona, 1 en su borde (forma entre elipse y rectangulo)."""
    lx, lz = to_local(co, cx, cz, rot)
    return ((abs(lx) / hw) ** 3 + (abs(lz) / hh) ** 3) ** (1.0 / 3.0)


def sample_depth(dm, px, py):
    """Muestreo bilineal del mapa de profundidad (px, py en pixeles de la foto)."""
    H, W = dm.shape
    x = min(max(px, 0.0), W - 1.0)
    y = min(max(py, 0.0), H - 1.0)
    x0, y0 = int(x), int(y)
    x1, y1 = min(x0 + 1, W - 1), min(y0 + 1, H - 1)
    fx, fy = x - x0, y - y0
    return float((dm[y0, x0] * (1 - fx) + dm[y0, x1] * fx) * (1 - fy)
                 + (dm[y1, x0] * (1 - fx) + dm[y1, x1] * fx) * fy)


def blend_with_depth(zs, hys, beta=DEPTH_BLEND, min_corr=DEPTH_MIN_CORR):
    """zs: profundidad de la foto (1 = mas cerca) en cada vertice. hys: Y de la malla de Hunyuan3D.
    Ajusta hy ~ b - a*z (la malla fija la escala) y mezcla ambos. Devuelve (nuevos_hy, corr, usado)."""
    zs, hys = np.asarray(zs, dtype=np.float64), np.asarray(hys, dtype=np.float64)
    if len(zs) < 20 or zs.std() < 1e-6 or hys.std() < 1e-9:
        return hys, 0.0, False
    corr = float(np.corrcoef(zs, hys)[0, 1])          # lo cercano (z alto) tiene Y menor: corr < 0
    A = np.stack([-zs, np.ones_like(zs)], axis=1)
    (a, b), *_ = np.linalg.lstsq(A, hys, rcond=None)
    if a <= 0 or -corr < min_corr:
        return hys, corr, False
    return (1.0 - beta) * hys + beta * (b - a * zs), corr, True


def weight(r):
    if r >= 1.0:
        return 0.0
    if r <= FEATHER:
        return 1.0
    t = (1.0 - r) / (1.0 - FEATHER)
    return t * t * (3.0 - 2.0 * t)


def smoothstep(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def robust_clip(dys, maxd):
    """Recorta los desplazamientos atipicos (picos): como mucho maxd y, ademas, unas 4 desviaciones
    robustas respecto a la mediana."""
    a = np.asarray(dys, dtype=np.float64)
    med = float(np.median(a))
    mad = 1.4826 * float(np.median(np.abs(a - med)))
    lim = maxd if mad < 1e-9 else min(maxd, max(4.0 * mad, 0.25 * maxd))
    return np.clip(a, max(med - lim, -maxd), min(med + lim, maxd))


def smooth_field(raw, nbrs, iters=SMOOTH_ITERS, k=0.5):
    """Suaviza el desplazamiento promediando cada vertice con sus vecinos."""
    cur = dict(raw)
    for _ in range(iters):
        new = {}
        for i, t in cur.items():
            ns = nbrs.get(i)
            new[i] = t if not ns else (1 - k) * t + k * sum(cur.get(j, t) for j in ns) / len(ns)
        cur = new
    return cur


def apply_relief(model, detail, cx, cz, hw, hh, rot, depth=None, strength=0.7):
    hw, hh = max(hw, 1e-6), max(hh, 1e-6)

    def rr(co):
        return region_r(co, cx, cz, hw, hh, rot)

    bpy.context.view_layer.update()
    tree = BVHTree.FromObject(detail, bpy.context.evaluated_depsgraph_get())
    y_far = mesh_bounds(detail.data)[0].y - 1.0     # rayos desde delante de la parte

    bm = bmesh.new()
    bm.from_mesh(model.data)
    bm.normal_update()

    # 1) subdividir solo la parte delantera de la zona
    n0 = sum(1 for f in bm.faces if f.normal.y < 0.3 and rr(f.calc_center_median()) <= 1.15)
    if n0 == 0:
        bm.free()
        raise RuntimeError("la zona queda fuera de la superficie del modelo")
    levels = 0
    while levels < 3 and n0 * 4 ** (levels + 1) <= MAX_FACES:
        levels += 1
    for _ in range(levels):
        bm.normal_update()
        edges = [e for e in bm.edges
                 if rr(e.verts[0].co) <= 1.15 and rr(e.verts[1].co) <= 1.15
                 and any(f.normal.y < 0.3 for f in e.link_faces)]
        if not edges:
            break
        bmesh.ops.subdivide_edges(bm, edges=edges, cuts=1, use_grid_fill=True)
    bigger = [f for f in bm.faces if len(f.verts) > 3]
    if bigger:
        bmesh.ops.triangulate(bm, faces=bigger)
    bm.normal_update()
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()

    # 2) donde esta la superficie de la parte, delante de cada vertice que mira al frente.
    #    Los vertices de los laterales (casi paralelos al eje Y) NO se tocan: moverlos en Y los
    #    estiraria en tiras en vez de dar relieve.
    cand = []   # (indice, normal, peso, y de la parte)
    for v in bm.verts:
        r = rr(v.co)
        facing = -v.normal.y
        if r >= 1.0 or facing < FACING_MIN:
            continue
        hit, hn, _i, _d = tree.ray_cast(Vector((v.co.x, y_far, v.co.z)), Vector((0.0, 1.0, 0.0)))
        if hit is None or hn.y > -0.3:            # sin superficie frontal de la parte ahi
            continue
        w = weight(r) * smoothstep((facing - FACING_MIN) / (FACING_FULL - FACING_MIN))
        if w > 1e-4:
            cand.append((v.index, v.normal.copy(), w, hit.y))
    if len(cand) < 20:
        bm.free()
        raise RuntimeError("la parte no coincide con la superficie del modelo en esa zona")

    # 2b) profundidad estimada de la FOTO: guia el relieve (la malla de Hunyuan3D da la escala)
    depth_txt = "sin mapa de profundidad"
    if depth is not None:
        dm, (bx0, by0, bx1, by1) = depth
        zs = []
        for i, _n, _w, _hy in cand:
            lx, lz = to_local(bm.verts[i].co, cx, cz, rot)
            u, vv = 0.5 + lx / (2 * hw), 0.5 - lz / (2 * hh)
            zs.append(sample_depth(dm, bx0 + u * (bx1 - bx0), by0 + vv * (by1 - by0)))
        new_hy, corr, used = blend_with_depth(zs, [c[3] for c in cand])
        if used:
            cand = [(i, n, w, float(h)) for (i, n, w, _hy), h in zip(cand, new_hy)]
            depth_txt = f"profundidad usada (correlacion {-corr:.2f}, mezcla {DEPTH_BLEND:.0%})"
        else:
            depth_txt = f"profundidad descartada (correlacion {-corr:.2f} < {DEPTH_MIN_CORR})"

    # 3) igualar la profundidad media, recortar picos y suavizar
    dys = np.array([hy - bm.verts[i].co.y for i, _n, _w, hy in cand])
    core = [d for d, c in zip(dys, cand) if c[2] >= 0.9] or list(dys)
    off = float(np.median(core))
    size = 2 * max(hw, hh)
    dys = robust_clip(dys - off, MAX_REL * size)

    # el desplazamiento va a lo largo de la NORMAL (t = dy * n.y), no en Y: asi no hay cizalla
    raw = {i: float(dy) * n.y * w * strength for (i, n, w, _hy), dy in zip(cand, dys)}
    nbrs = {i: [e.other_vert(bm.verts[i]).index for e in bm.verts[i].link_edges] for i in raw}
    field = smooth_field(raw, nbrs)

    biggest = 0.0
    normals = {i: n for i, n, _w, _hy in cand}
    for i, t in field.items():
        bm.verts[i].co += normals[i] * t
        biggest = max(biggest, abs(t))

    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(model.data)
    bm.free()
    model.data.update()
    print(f"  Subdivision x{levels}, {len(field)} vertices adaptados, intensidad {strength:.0%}, "
          f"desplazamiento maximo {biggest * 100:.1f} cm; {depth_txt}")


model = get_model()
activate(model)
# la malla pasa a coordenadas del mundo (el origen queda en el suelo)
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

applied = 0
for p in plan:
    name = p["name"]
    print(f"Parte '{name}' (ubicada por: {p.get('method', '?')})...")
    detail = None
    try:
        mn, mx = world_bounds(model)
        detail = import_detail(p["glb"])
        if detail is None:
            print("  El archivo no tiene malla, se omite.")
            continue
        geo = place_detail(detail, p, mn, mx)
        depth = None
        if p.get("depth") and p.get("depth_box"):
            try:
                depth = (np.load(p["depth"]), tuple(p["depth_box"]))
            except Exception as ex:
                print("  No pude leer el mapa de profundidad:", ex)
        apply_relief(model, detail, *geo, depth=depth, strength=float(p.get("strength", 0.7)))
        applied += 1
    except Exception as ex:
        print(f"  Error con '{name}': {ex}")
    finally:
        if detail is not None and detail.name in bpy.data.objects:
            drop(detail)

if applied == 0:
    raise SystemExit("No se pudo aplicar ninguna parte.")

activate(model)
bpy.ops.object.shade_smooth()
bpy.ops.wm.save_as_mainfile(filepath=dst)
print(f"Guardado {dst} con {len(model.data.polygons)} caras ({applied} partes aplicadas)")
