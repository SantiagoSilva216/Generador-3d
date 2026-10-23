"""Se ejecuta al abrir Blender con ventana (lo lanza detallar.py):
modelo en modo escultura + tus imagenes colocadas como referencia en cada vista."""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Euler, Vector

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
REF_DIR = Path(args[0]) if args else None

# Rotacion de cada imagen de referencia para que se vea al mirar desde esa vista
ROT = {
    "front": (math.pi / 2, 0, 0),
    "back": (math.pi / 2, 0, math.pi),
    "left": (math.pi / 2, 0, math.pi / 2),
    "right": (math.pi / 2, 0, -math.pi / 2),
    "top": (0, 0, 0),
    "bottom": (math.pi, 0, 0),
}


def model_bounds(obj):
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return mn, mx


def add_refs(obj):
    if REF_DIR is None or not REF_DIR.exists():
        return
    mn, mx = model_bounds(obj)
    d = mx - mn
    center = (mn + mx) / 2
    size = {  # (ancho, alto) del modelo visto desde cada vista
        "front": (d.x, d.z), "back": (d.x, d.z),
        "left": (d.y, d.z), "right": (d.y, d.z),
        "top": (d.x, d.y), "bottom": (d.x, d.y),
    }
    coll = bpy.data.collections.get("Referencias")
    if coll is None:
        coll = bpy.data.collections.new("Referencias")
        bpy.context.scene.collection.children.link(coll)
    for name, rot in ROT.items():
        path = REF_DIR / f"{name}.png"
        if not path.exists() or bpy.data.objects.get(f"Ref_{name}"):
            continue
        try:
            img = bpy.data.images.load(str(path), check_existing=True)
            aspect = img.size[0] / max(img.size[1], 1)
            e = bpy.data.objects.new(f"Ref_{name}", None)
            e.empty_display_type = "IMAGE"
            e.data = img
            e.empty_image_depth = "BACK"
            e.show_empty_image_perspective = False
            e.show_empty_image_only_axis_aligned = True
            e.show_empty_image_backside = False
            e.use_empty_image_alpha = True
            e.color = (1, 1, 1, 0.45)
            e.empty_display_size = size[name][1] * max(1.0, aspect)
            e.rotation_euler = rot
            e.location = center
            e.hide_select = True
            coll.objects.link(e)
        except Exception as ex:
            print(f"No pude colocar la referencia {name}: {ex}")


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
        win = bpy.context.window or bpy.context.window_manager.windows[0]
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                r3d = area.spaces.active.region_3d
                r3d.view_perspective = "ORTHO"
                r3d.view_rotation = Euler((math.pi / 2, 0, 0)).to_quaternion()
                r3d.view_location = (mn + mx) / 2
                r3d.view_distance = (mx - mn).z * 2.4
    except Exception as ex:
        print("No pude ajustar la vista:", ex)
    return None


bpy.app.timers.register(preparar, first_interval=0.5)
