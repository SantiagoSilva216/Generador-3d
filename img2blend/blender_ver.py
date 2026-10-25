"""Se ejecuta al abrir Blender con ventana (lo lanza la interfaz al terminar el paso 1):
muestra el modelo recien generado, encuadrado y de frente."""
import math

import bpy
from mathutils import Euler, Vector


def preparar():
    obj = bpy.data.objects.get("Modelo")
    if obj is None:
        meshes = [o for o in bpy.data.objects if o.type == "MESH"]
        if not meshes:
            return None
        obj = meshes[0]
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    try:
        win = bpy.context.window or bpy.context.window_manager.windows[0]
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                sp = area.spaces.active
                sp.shading.type = "SOLID"
                r3d = sp.region_3d
                r3d.view_perspective = "ORTHO"
                r3d.view_rotation = Euler((math.pi / 2, 0, 0)).to_quaternion()
                r3d.view_location = (mn + mx) / 2
                r3d.view_distance = (mx - mn).z * 2.2
    except Exception as ex:
        print("No pude ajustar la vista:", ex)
    return None


bpy.app.timers.register(preparar, first_interval=0.6)
