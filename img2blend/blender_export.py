"""Se ejecuta DENTRO de Blender (lo lanza exportar.py).
  blender -b -P blender_export.py -- entrada.blend salida.(fbx|glb|obj)
"""
import sys
from pathlib import Path

import bpy

src, dst = sys.argv[sys.argv.index("--") + 1:][:2]
ext = Path(dst).suffix.lower()

bpy.ops.wm.open_mainfile(filepath=src)
obj = bpy.data.objects.get("Modelo")
if obj is None:
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if not meshes:
        raise SystemExit("No hay ninguna malla en el .blend")
    obj = meshes[0]

bpy.ops.object.select_all(action="DESELECT")
obj.select_set(True)
bpy.context.view_layer.objects.active = obj


def save_texture():
    """Guarda la textura actual del material (incluye retoques a mano) junto al archivo."""
    for mat in obj.data.materials:
        if not mat or not mat.node_tree:
            continue
        for n in mat.node_tree.nodes:
            if n.type == "TEX_IMAGE" and n.image:
                png = Path(dst).with_name(Path(dst).stem + "_textura.png")
                try:
                    n.image.filepath_raw = str(png)
                    n.image.file_format = "PNG"
                    n.image.save()
                    print(f"Textura guardada: {png}")
                except Exception as e:
                    print("No pude guardar la textura desde Blender:", e)
                return


if ext == ".fbx":
    try:
        bpy.ops.export_scene.fbx(
            filepath=dst, use_selection=True, object_types={"MESH"},
            apply_scale_options="FBX_SCALE_ALL", bake_space_transform=True,
            axis_forward="-Z", axis_up="Y",
            path_mode="COPY", embed_textures=True, mesh_smooth_type="FACE",
        )
    except Exception as e:
        print("Ajustes avanzados no aceptados, exporto con los basicos:", e)
        bpy.ops.export_scene.fbx(filepath=dst, use_selection=True)
elif ext == ".glb":
    bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True)
elif ext == ".obj":
    try:
        bpy.ops.wm.obj_export(filepath=dst, export_selected_objects=True, path_mode="COPY")
    except Exception as e:
        print("Ajustes avanzados no aceptados, exporto con los basicos:", e)
        bpy.ops.wm.obj_export(filepath=dst)
else:
    raise SystemExit(f"Formato no soportado: {ext}")

save_texture()
print(f"Exportado: {dst}")
