"""
Exporta el modelo (el .blend mas reciente: pintado si existe) a FBX / GLB / OBJ.
Uso: python exportar.py --out C:/ruta/modelo.fbx   (o el boton Exportar de la interfaz)
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from pipeline import BASE, find_blender


def newest_model(out: Path) -> Path:
    cands = [p for p in (out / "modelo_pintado.blend", out / "modelo.blend") if p.exists()]
    if not cands:
        sys.exit("No hay modelo todavia. Primero pulsa 'Generar modelo 3D'.")
    return max(cands, key=lambda p: p.stat().st_mtime)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="archivo de salida (.fbx, .glb o .obj)")
    ap.add_argument("--blender", default="blender")
    a = ap.parse_args()

    dst = Path(a.out)
    if dst.suffix.lower() not in (".fbx", ".glb", ".obj"):
        sys.exit("Formato no soportado. Usa .fbx, .glb o .obj")
    dst.parent.mkdir(parents=True, exist_ok=True)

    out = BASE / "output"
    src = newest_model(out)
    print(f"Exportando {src.name} -> {dst}")
    subprocess.run(
        [find_blender(a.blender), "--background", "--python", str(BASE / "blender_export.py"),
         "--", str(src), str(dst)],
        check=True,
    )

    tex_out = dst.with_name(dst.stem + "_textura.png")
    tex_src = out / "textura.png"
    if src.name == "modelo_pintado.blend" and not tex_out.exists() and tex_src.exists():
        shutil.copy(tex_src, tex_out)  # plan B si Blender no pudo guardarla
        print(f"Textura copiada: {tex_out}")
    print(f"\nListo: {dst}")


if __name__ == "__main__":
    main()
