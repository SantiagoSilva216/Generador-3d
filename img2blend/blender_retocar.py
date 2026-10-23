"""Se ejecuta al abrir Blender con ventana: deja el modelo listo para pintar a mano."""
import bpy


def preparar():
    obj = bpy.data.objects.get("Modelo")
    if obj is not None:
        for o in bpy.context.view_layer.objects:
            o.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

    win = bpy.context.window or bpy.context.window_manager.windows[0]
    ws = bpy.data.workspaces.get("Texture Paint")
    if ws is not None:
        win.workspace = ws
    else:
        try:
            bpy.ops.object.mode_set(mode="TEXTURE_PAINT")
        except Exception:
            pass
    return None  # no repetir


bpy.app.timers.register(preparar, first_interval=0.5)
