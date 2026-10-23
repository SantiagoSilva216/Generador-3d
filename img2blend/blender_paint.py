"""Se ejecuta DENTRO de Blender (lo lanza pintar.py).

  blender -b -P blender_paint.py -- uv entrada.blend salida_uv.blend malla.npz
  blender -b -P blender_paint.py -- material entrada.blend textura.png salida.blend
"""
import math
import sys

import bpy
import numpy as np

args = sys.argv[sys.argv.index("--") + 1:]
mode = args[0]


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


if mode == "uv":
    src, dst, npz = args[1:4]
    bpy.ops.wm.open_mainfile(filepath=src)
    obj = get_model()
    activate(obj)

    # Asegura que todo son triangulos
    mod = obj.modifiers.new("Tri", "TRIANGULATE")
    bpy.ops.object.modifier_apply(modifier=mod.name)

    # Despliegue automatico de UVs
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.003)
    bpy.ops.object.mode_set(mode="OBJECT")

    # Volcado de la malla para calcular la textura fuera de Blender
    me = obj.data
    V = len(me.vertices)
    pos = np.empty(V * 3, dtype=np.float32)
    me.vertices.foreach_get("co", pos)
    pos = pos.reshape(V, 3)

    P = len(me.polygons)
    L = len(me.loops)
    if L != 3 * P:
        raise SystemExit("La malla no esta triangulada")
    vidx = np.empty(L, dtype=np.int32)
    try:
        me.loops.foreach_get("vertex_index", vidx)
    except Exception:
        me.attributes[".corner_vert"].data.foreach_get("value", vidx)

    uv = np.empty(L * 2, dtype=np.float32)
    me.uv_layers.active.data.foreach_get("uv", uv)

    np.savez(npz, pos=pos, tris=vidx.reshape(P, 3), uvs=uv.reshape(P, 3, 2))
    bpy.ops.wm.save_as_mainfile(filepath=dst)
    print(f"UV listos: {P} triangulos")

elif mode == "material":
    src, tex_path, dst = args[1:4]
    bpy.ops.wm.open_mainfile(filepath=src)
    obj = get_model()
    activate(obj)

    img = bpy.data.images.load(tex_path)
    img.pack()  # la textura queda guardada dentro del .blend

    mat = bpy.data.materials.new("Pintura")
    try:
        mat.use_nodes = True
    except Exception:
        pass
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if out is None:
        out = nt.nodes.new("ShaderNodeOutputMaterial")
    if not out.inputs["Surface"].is_linked:
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.8
    nt.nodes.active = tex  # para que Texture Paint pinte sobre esta imagen

    obj.data.materials.clear()
    obj.data.materials.append(mat)
    bpy.ops.wm.save_as_mainfile(filepath=dst)
    print(f"Guardado {dst}")

else:
    raise SystemExit(f"Modo desconocido: {mode}")
