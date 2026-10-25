"""Se ejecuta al abrir Blender con ventana (lo lanza detallar.py):
modelo en modo escultura + imagenes de las partes a detallar colocadas junto a el."""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Euler, Vector

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
REF_DIR = Path(args[0]) if args else None
GAP = 0.1


def model_bounds(obj):
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return mn, mx


def part_files():
    return sorted(REF_DIR.glob("*.png")) if REF_DIR and REF_DIR.exists() else []


def add_refs(obj):
    files = part_files()
    if not files:
        return
    mn, mx = model_bounds(obj)
    d = mx - mn
    cy = (mn.y + mx.y) / 2
    side = d.z * 0.45
    coll = bpy.data.collections.get("Detalles")
    if coll is None:
        coll = bpy.data.collections.new("Detalles")
        bpy.context.scene.collection.children.link(coll)
    for i, path in enumerate(files):
        name = f"Detalle_{path.stem}"
        if bpy.data.objects.get(name):
            continue
        col, row = i // 2, i % 2  # columnas de 2 imagenes a la derecha del modelo
        try:
            img = bpy.data.images.load(str(path), check_existing=True)
            e = bpy.data.objects.new(name, None)
            e.empty_display_type = "IMAGE"
            e.data = img
            e.empty_image_depth = "BACK"
            e.show_empty_image_perspective = False
            e.show_empty_image_only_axis_aligned = True
            e.use_empty_image_alpha = True
            e.show_name = True
            e.empty_display_size = side
            e.rotation_euler = (math.pi / 2, 0, 0)
            e.location = (mx.x + 0.2 + side / 2 + col * (side + GAP), cy,
                          mx.z - side / 2 - row * (side + GAP))
            e.hide_select = True
            coll.objects.link(e)
        except Exception as ex:
            print(f"No pude colocar la parte {path.stem}: {ex}")


def preparar():
    obj = bpy.data.objects.get("Modelo")
    if obj is None:
        return None
    add_refs(obj)
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    win = bpy.context.window or bpy.context.window_manager.windows[0]
    ws = bpy.data.workspaces.get("Sculpting")
    if ws is not None:
        win.workspace = ws  # cambia tambien al modo escultura
    else:
        try:
            bpy.ops.object.mode_set(mode="SCULPT")
        except Exception:
            pass
    bpy.app.timers.register(vista_frontal, first_interval=0.8)
    return None


def vista_frontal():
    try:
        obj = bpy.data.objects.get("Modelo")
        mn, mx = model_bounds(obj)
        side = (mx - mn).z * 0.45
        ncols = (len(part_files()) + 1) // 2
        right = mx.x + 0.2 + ncols * (side + GAP)
        win = bpy.context.window or bpy.context.window_manager.windows[0]
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                r3d = area.spaces.active.region_3d
                r3d.view_perspective = "ORTHO"
                r3d.view_rotation = Euler((math.pi / 2, 0, 0)).to_quaternion()
                r3d.view_location = ((mn.x + right) / 2, (mn.y + mx.y) / 2, (mn.z + mx.z) / 2)
                r3d.view_distance = max((mx - mn).z * 2.4, (right - mn.x) * 1.6)
    except Exception as ex:
        print("No pude ajustar la vista:", ex)
    return None


bpy.app.timers.register(preparar, first_interval=0.5)
