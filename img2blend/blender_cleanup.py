"""Se ejecuta DENTRO de Blender: blender -b -P blender_cleanup.py -- malla salida.blend caras"""
import sys
from pathlib import Path

import bpy

mesh_path, out_path, target_faces = sys.argv[sys.argv.index("--") + 1:][:3]
target_faces = int(target_faces)

# Escena vacía
bpy.ops.wm.read_factory_settings(use_empty=True)

# Importar
ext = Path(mesh_path).suffix.lower()
if ext in (".glb", ".gltf"):
    bpy.ops.import_scene.gltf(filepath=mesh_path)
elif ext == ".ply":
    bpy.ops.wm.ply_import(filepath=mesh_path)
elif ext == ".obj":
    bpy.ops.wm.obj_import(filepath=mesh_path)
else:
    raise SystemExit(f"Formato no soportado: {ext}")

objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not objs:
    raise SystemExit("No se importó ninguna malla.")

# Unir en un solo objeto
bpy.ops.object.select_all(action="DESELECT")
for o in objs:
    o.select_set(True)
bpy.context.view_layer.objects.active = objs[0]
if len(objs) > 1:
    bpy.ops.object.join()
obj = bpy.context.view_layer.objects.active
obj.name = "Modelo"

# Centrar en el origen, apoyar sobre el suelo y escalar a ~2 m de alto
bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
obj.location = (0, 0, 0)
height = max(obj.dimensions) or 1.0
s = 2.0 / height
obj.scale = (s, s, s)
bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
obj.location.z = obj.dimensions.z / 2

# Limpieza de geometría
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.remove_doubles(threshold=0.0001)
bpy.ops.mesh.normals_make_consistent(inside=False)
bpy.ops.object.mode_set(mode="OBJECT")

# Reducir polígonos al objetivo
faces = len(obj.data.polygons)
if faces > target_faces:
    mod = obj.modifiers.new("Decimate", "DECIMATE")
    mod.ratio = target_faces / faces
    bpy.ops.object.modifier_apply(modifier=mod.name)

bpy.ops.object.shade_smooth()

bpy.ops.wm.save_as_mainfile(filepath=out_path)
print(f"Guardado {out_path} con {len(obj.data.polygons)} caras")
