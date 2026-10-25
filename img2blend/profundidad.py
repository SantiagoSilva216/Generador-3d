"""
Detector de profundidad para guiar el relieve de las partes a detallar.

Usa Depth Anything V2 (small, ~100 MB, se descarga la primera vez) para estimar, a partir de la
FOTO de la parte, un mapa de profundidad relativo: que zonas estan mas cerca de la camara
(nariz, frente, pliegues...) y cuales mas lejos. Hunyuan3D solo imagina esa profundidad; este
mapa sale de la foto real y se usa en Blender para que el relieve siga lo que se ve en ella.

depth_map(img, mask) -> array float32 (alto x ancho) entre 0 y 1, donde 1 = lo mas cercano.
Devuelve None si el modelo no esta disponible (el detallado sigue sin profundidad).
"""
import numpy as np

try:
    import cv2
except Exception:
    cv2 = None

REPO = "depth-anything/Depth-Anything-V2-Small-hf"
SMOOTH = 0.008        # suavizado (fraccion del lado menor) para quitar ruido fino del mapa

_PIPE = None
_FAILED = False


def _load():
    global _PIPE
    if _PIPE is None:
        import torch
        from transformers import pipeline
        _PIPE = pipeline("depth-estimation", model=REPO, device=0 if torch.cuda.is_available() else -1)
    return _PIPE


def release():
    """Libera el modelo de la memoria (llamar antes de cargar Hunyuan3D)."""
    global _PIPE
    if _PIPE is not None:
        _PIPE = None
        import gc
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass


def _to_numpy(t):
    if hasattr(t, "detach"):
        t = t.detach().float().cpu().numpy()
    return np.asarray(t, dtype=np.float32).squeeze()


def depth_map(img, mask=None):
    """img: imagen PIL. mask: array bool (alto x ancho) con el objeto, para normalizar solo con el."""
    global _FAILED
    if _FAILED or cv2 is None:
        return None
    try:
        out = _load()(img.convert("RGB"))
        d = _to_numpy(out["predicted_depth"])
    except Exception as e:
        _FAILED = True
        print(f"  (profundidad no disponible: {e}); sigo sin ella)")
        return None
    W, H = img.size
    if d.shape != (H, W):
        d = cv2.resize(d, (W, H), interpolation=cv2.INTER_CUBIC)

    ref = d[mask] if (mask is not None and mask.shape == d.shape and mask.sum() > 50) else d.ravel()
    lo, hi = np.percentile(ref, [2, 98])
    d = np.clip((d - lo) / max(float(hi - lo), 1e-6), 0.0, 1.0)
    sigma = max(min(H, W) * SMOOTH, 1.0)
    return cv2.GaussianBlur(d, (0, 0), sigma).astype(np.float32)
