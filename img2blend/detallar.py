"""
Abre el modelo en Blender (modo escultura) con tus imagenes como referencia en cada vista,
para detallarlo y corregir imperfecciones a mano. Abre el .blend mas reciente.

Uso: python detallar.py   (o el boton "Detallar en Blender" de la interfaz)
"""
import subprocess
import sys
from pathlib import Path

from pintar import prepare_views
from pipeline import BASE, collect_images, find_blender


def main():
    out = BASE / "output"
    cands = [p for p in (out / "modelo_pintado.blend", out / "modelo.blend") if p.exists()]
    if not cands:
        sys.exit("No hay modelo todavia. Primero pulsa 'Generar modelo 3d'.")
    model = max(cands, key=lambda p: p.stat().st_mtime)

    views = out / "_pintura" / "vistas"
    found = collect_images(BASE)
    if any(found.values()):
        print("Preparando las imagenes de referencia...")
        prepare_views(found, views)

    blender = find_blender("blender")
    print(f"Abriendo {model.name} en Blender (modo escultura)...")
    # DEVNULL: Blender queda independiente y no bloquea la interfaz
    subprocess.Popen(
        [blender, str(model), "--python", str(BASE / "blender_detallar.py"), "--", str(views)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print("Blender abierto. Cuando termines, guarda con Ctrl+S.")


if __name__ == "__main__":
    main()
