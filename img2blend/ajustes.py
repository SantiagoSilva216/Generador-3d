"""
Ajustes automaticos del equipo para la generacion:
  - keep_awake(): impide que Windows suspenda el equipo mientras se genera.
  - vram_info() / gpu_processes(): lee la VRAM libre y que programas usan la GPU (nvidia-smi).
  - on_battery(): avisa si el portatil esta funcionando con bateria.
Todo es seguro si falta nvidia-smi o no es Windows (devuelve None).
"""
import contextlib
import ctypes
import os
import re
import subprocess

_NOWIN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# Procesos del sistema que siempre aparecen en la lista de la GPU
IGNORAR = {
    "dwm.exe", "explorer.exe", "csrss.exe", "searchhost.exe", "shellexperiencehost.exe",
    "startmenuexperiencehost.exe", "textinputhost.exe", "lockapp.exe", "applicationframehost.exe",
    "systemsettings.exe", "widgets.exe", "nvidia-smi.exe",
}


def _run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15, creationflags=_NOWIN)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def parse_vram(text):
    m = re.search(r"(\d+)\s*,\s*(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def vram_info():
    """(usada_MB, total_MB) de la primera GPU NVIDIA, o None."""
    return parse_vram(_run(["nvidia-smi", "--query-gpu=memory.used,memory.total",
                            "--format=csv,noheader,nounits"]))


def parse_gpu_processes(text):
    tail = (text or "").split("Processes:")
    if len(tail) < 2:
        return []
    names = set()
    for m in re.finditer(r"([^\\/\s|]+\.exe)", tail[-1], re.I):
        if m.group(1).lower() not in IGNORAR:
            names.add(m.group(1))
    return sorted(names)


def gpu_processes():
    return parse_gpu_processes(_run(["nvidia-smi"]))


def on_battery():
    """True si funciona con bateria, False si esta enchufado, None si no se sabe."""
    if os.name != "nt":
        return None

    class SPS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

    s = SPS()
    try:
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
            return None
    except Exception:
        return None
    if s.ACLineStatus == 0:
        return True
    if s.ACLineStatus == 1:
        return False
    return None


@contextlib.contextmanager
def keep_awake():
    """Mientras dura el bloque, Windows no suspende el equipo (se restaura solo al salir)."""
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    active = False
    if os.name == "nt":
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
            active = True
        except Exception:
            pass
    try:
        yield
    finally:
        if active:
            try:
                ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(ES_CONTINUOUS))
            except Exception:
                pass
