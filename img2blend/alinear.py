"""
Alinea la foto de una parte (cara, mano...) con la imagen FRONTAL del modelo para saber
EXACTAMENTE donde va, en lugar de fiarse solo del rectangulo marcado a mano.

Cuatro intentos, del mas al menos preciso:
  1. 'caracteristicas': puntos SIFT comunes entre la foto del detalle y la frontal
     (tolera cambios de escala, giro en el plano y de resolucion).
  2. 'plantilla': busca la foto (a varias escalas) dentro de la zona marcada.
  3. 'clip': para cuando la foto NO es la misma (otra pose, otra luz, otro estilo): CLIP compara
     cada ventana de la zona marcada con la foto del detalle Y con el NOMBRE de la parte
     (cara -> "face"), y se queda con la ventana que mejor encaja.
  4. 'zona': si nada es fiable, se usa la zona marcada y el detalle se ajusta a ella.

Las coordenadas devueltas estan en pixeles de la imagen frontal.
"""
import math

import numpy as np
from PIL import ImageDraw

try:
    import cv2
except Exception:  # sin OpenCV solo funciona el intento 3
    cv2 = None

MAXSIDE = 1600   # las imagenes grandes se reducen para buscar puntos (mas rapido)
MARGIN = 0.6     # la zona marcada se ampla un 60% por cada lado al buscar


def _expand(box, k, W, H):
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    return (max(0.0, x0 - k * w), max(0.0, y0 - k * h), min(float(W), x1 + k * w), min(float(H), y1 + k * h))


def _gray(img):
    """Imagen PIL -> gris uint8 reducido y con contraste normalizado. Devuelve (gris, factor)."""
    g = np.asarray(img.convert("L"))
    f = min(1.0, MAXSIDE / max(g.shape))
    if f < 1.0:
        g = cv2.resize(g, (int(g.shape[1] * f), int(g.shape[0] * f)), interpolation=cv2.INTER_AREA)
    g = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(g)
    return g, f


def _by_features(front, detail, det_box, user):
    W, H = front.size
    gf, sf = _gray(front)
    gd, sd = _gray(detail)
    ex = _expand(user, MARGIN, W, H)
    mask = np.zeros_like(gf)
    mask[int(ex[1] * sf):int(ex[3] * sf) + 1, int(ex[0] * sf):int(ex[2] * sf) + 1] = 255

    sift = cv2.SIFT_create(nfeatures=5000)
    kf, df = sift.detectAndCompute(gf, mask)
    kd, dd = sift.detectAndCompute(gd, None)
    if df is None or dd is None or len(kf) < 8 or len(kd) < 8:
        return None
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(dd, df, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
    if len(good) < 8:
        return None
    src = np.float32([kd[g.queryIdx].pt for g in good]).reshape(-1, 1, 2)
    dst = np.float32([kf[g.trainIdx].pt for g in good]).reshape(-1, 1, 2)
    M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.0)
    if M is None:
        return None
    n_in = int(inl.sum())
    if n_in < 8 or n_in < 0.25 * len(good):
        return None

    s = math.hypot(M[0, 0], M[1, 0])                 # escala detalle -> frontal (px reducidos)
    rot = math.atan2(M[1, 0], M[0, 0])
    bx0, by0, bx1, by1 = det_box
    cx_d, cy_d = (bx0 + bx1) / 2 * sd, (by0 + by1) / 2 * sd
    cx = (M[0, 0] * cx_d + M[0, 1] * cy_d + M[0, 2]) / sf
    cy = (M[1, 0] * cx_d + M[1, 1] * cy_d + M[1, 2]) / sf
    w = (bx1 - bx0) * sd * s / sf
    h = (by1 - by0) * sd * s / sf

    uw = user[2] - user[0]
    inside = ex[0] <= cx <= ex[2] and ex[1] <= cy <= ex[3]
    if not inside or not (0.25 * uw <= w <= 3.0 * uw):   # coincidencia absurda -> descartada
        return None
    if abs(rot) < math.radians(2):
        rot = 0.0
    return dict(method="caracteristicas", center=(cx, cy), size=(w, h), rot=rot,
                score=n_in / len(good), matches=n_in)


def _by_template(front, detail, det_box, user):
    W, H = front.size
    ex = _expand(user, MARGIN, W, H)
    sx0, sy0, sx1, sy1 = (int(v) for v in ex)
    gf = np.asarray(front.convert("L"))
    search = cv2.GaussianBlur(gf[sy0:sy1, sx0:sx1], (3, 3), 0)
    dx0, dy0, dx1, dy1 = (int(v) for v in det_box)
    obj = np.asarray(detail.convert("L"))[dy0:dy1, dx0:dx1]
    if obj.size == 0 or search.size == 0:
        return None
    uw = user[2] - user[0]
    best = None
    for k in np.arange(0.5, 1.61, 0.1):
        sc = k * uw / max(obj.shape[1], 1)
        tw, th = int(obj.shape[1] * sc), int(obj.shape[0] * sc)
        if tw < 12 or th < 12 or tw >= search.shape[1] or th >= search.shape[0]:
            continue
        t = cv2.GaussianBlur(cv2.resize(obj, (tw, th), interpolation=cv2.INTER_AREA), (3, 3), 0)
        _, val, _, loc = cv2.minMaxLoc(cv2.matchTemplate(search, t, cv2.TM_CCOEFF_NORMED))
        if best is None or val > best[0]:
            best = (val, loc, tw, th)
    if best is None or best[0] < 0.5:
        return None
    val, (lx, ly), tw, th = best
    return dict(method="plantilla", center=(sx0 + lx + tw / 2, sy0 + ly + th / 2),
                size=(float(tw), float(th)), rot=0.0, score=float(val))


# ------------------------------------------------------------------ CLIP (nombre + foto)
CLIP_REPO = "openai/clip-vit-base-patch32"   # ~600 MB, se descarga la primera vez
CLIP_MIN_SIM = 0.65      # parecido minimo (0-1) entre la foto del detalle y la ventana elegida
CLIP_MIN_MARGIN = 0.06   # cuanto debe superar a la ventana "tipica" para fiarse

# nombres frecuentes en espanol -> ingles (CLIP entiende mejor el ingles); lo demas se usa tal cual
ES_EN = {
    "cara": "face", "rostro": "face", "cabeza": "head", "cuello": "neck", "pelo": "hair",
    "cabello": "hair", "barba": "beard", "ojo": "eye", "ojos": "eyes", "nariz": "nose",
    "boca": "mouth", "oreja": "ear", "orejas": "ears", "mano": "hand", "manos": "hands",
    "dedo": "finger", "dedos": "fingers", "brazo": "arm", "brazos": "arms", "hombro": "shoulder",
    "pierna": "leg", "piernas": "legs", "pie": "foot", "pies": "feet", "torso": "torso",
    "pecho": "chest", "espalda": "back", "cuerpo": "body", "ropa": "clothes", "camisa": "shirt",
    "camiseta": "t-shirt", "chaqueta": "jacket", "abrigo": "coat", "pantalon": "pants",
    "falda": "skirt", "vestido": "dress", "capa": "cape", "zapato": "shoe", "zapatos": "shoes",
    "bota": "boot", "botas": "boots", "guante": "glove", "guantes": "gloves", "casco": "helmet",
    "sombrero": "hat", "gorra": "cap", "mascara": "mask", "armadura": "armor",
    "cinturon": "belt", "arma": "weapon", "espada": "sword", "escudo": "shield", "ala": "wing",
    "alas": "wings", "cola": "tail", "cuerno": "horn", "cuernos": "horns", "mochila": "backpack",
    "izquierda": "left", "izquierdo": "left", "derecha": "right", "derecho": "right",
}

_CLIP = None          # (modelo, procesador, dispositivo): se carga una sola vez
_CLIP_FAILED = False


def prompt_for(name):
    words = [w for w in name.replace("-", "_").split("_") if w]
    en = [ES_EN.get(w.lower(), w) for w in words]
    side = [w for w in en if w in ("left", "right")]
    rest = [w for w in en if w not in ("left", "right")]
    return "a photo of the " + " ".join(side + rest)


class ClipEmbedder:
    """Envoltorio de CLIP: embed(imagenes, texto) -> (vectores de imagen N x D, vector del texto)."""
    def __init__(self):
        global _CLIP
        import torch
        self.torch = torch
        if _CLIP is None:
            from transformers import CLIPModel, CLIPProcessor
            dev = "cuda" if torch.cuda.is_available() else "cpu"
            model = CLIPModel.from_pretrained(CLIP_REPO).to(dev).eval()
            _CLIP = (model, CLIPProcessor.from_pretrained(CLIP_REPO), dev)
        self.model, self.proc, self.dev = _CLIP

    def embed(self, images, prompt):
        torch = self.torch
        txt = self.proc.tokenizer([prompt], padding=True, return_tensors="pt")
        ids, att = txt["input_ids"].to(self.dev), txt["attention_mask"].to(self.dev)
        outs, tvec = [], None
        with torch.no_grad():
            for i in range(0, len(images), 32):
                pix = self.proc.image_processor(images[i:i + 32], return_tensors="pt")["pixel_values"]
                o = self.model(input_ids=ids, attention_mask=att, pixel_values=pix.to(self.dev))
                ie = o.image_embeds / o.image_embeds.norm(dim=-1, keepdim=True)
                outs.append(ie.float().cpu().numpy())
                if tvec is None:
                    te = o.text_embeds / o.text_embeds.norm(dim=-1, keepdim=True)
                    tvec = te[0].float().cpu().numpy()
        return np.concatenate(outs), tvec


def release():
    """Libera CLIP de la memoria (llamar antes de cargar Hunyuan3D)."""
    global _CLIP
    if _CLIP is not None:
        _CLIP = None
        import gc
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass


def _square(img, size=224):
    """Pasa la imagen a cuadrada rellenando con gris (CLIP recorta si no es cuadrada)."""
    from PIL import Image
    w, h = img.size
    s = max(w, h)
    bg = Image.new("RGB", (s, s), (127, 127, 127))
    bg.paste(img.convert("RGB"), ((s - w) // 2, (s - h) // 2))
    return bg.resize((size, size))


def _windows(area, w, aspect, step_frac, bounds):
    """Ventanas (x0,y0,x1,y1) de ancho w y la proporcion dada que caben dentro de area."""
    h = w / aspect
    ax0, ay0, ax1, ay1 = area
    out = []
    step = max(w * step_frac, 6.0)
    y = ay0
    while y + h <= min(ay1, bounds[1]) + 1e-6:
        x = ax0
        while x + w <= min(ax1, bounds[0]) + 1e-6:
            out.append((x, y, x + w, y + h))
            x += step
        y += step
    return out


def _z(a):
    return (a - a.mean()) / (a.std() + 1e-6)


def _by_clip(front, detail, det_box, user, name, emb=None):
    """Busca en la zona marcada la ventana que mas se parece a la foto del detalle (imagen) y
    al nombre de la parte (texto). Dos pasadas: una gruesa y otra fina alrededor del mejor sitio."""
    global _CLIP_FAILED
    if emb is None:
        if _CLIP_FAILED:
            return None
        try:
            emb = ClipEmbedder()
        except Exception as e:
            _CLIP_FAILED = True
            print(f"  (CLIP no disponible: {e}); sigo con la zona marcada)")
            return None
    W, H = front.size
    ex = _expand(user, MARGIN, W, H)
    ref_img = detail.crop(tuple(int(v) for v in det_box))
    aspect = max(ref_img.size[0], 1) / max(ref_img.size[1], 1)
    uw = user[2] - user[0]
    front = front.convert("RGB")
    prompt = prompt_for(name)

    def score(wins):
        crops = [_square(front.crop(tuple(int(round(v)) for v in b))) for b in wins]
        vecs, tvec = emb.embed([_square(ref_img)] + crops, prompt)
        sims, tsim = vecs[1:] @ vecs[0], vecs[1:] @ tvec
        return sims, 0.7 * _z(sims) + 0.3 * _z(tsim)

    # pasada gruesa: varias escalas sobre toda la zona ampliada
    wins = []
    for k in (0.5, 0.65, 0.8, 0.95, 1.1, 1.25, 1.4):
        wins += _windows(ex, k * uw, aspect, 0.25, (W, H))
    if len(wins) < 4:
        return None
    sims, comb = score(wins)
    i = int(np.argmax(comb))
    if sims[i] < CLIP_MIN_SIM or sims[i] - float(np.median(sims)) < CLIP_MIN_MARGIN:
        print(f"  (CLIP sin confianza: parecido {sims[i]:.2f})")
        return None

    # pasada fina alrededor del mejor sitio
    bx0, by0, bx1, by1 = wins[i]
    bw = bx1 - bx0
    fine = []
    for sc in (0.85, 0.92, 1.0, 1.08, 1.15):
        w2 = bw * sc
        h2 = w2 / aspect
        for dx in (-0.2, -0.1, 0.0, 0.1, 0.2):
            for dy in (-0.2, -0.1, 0.0, 0.1, 0.2):
                cx = (bx0 + bx1) / 2 + dx * bw
                cy = (by0 + by1) / 2 + dy * bw
                x0, y0 = min(max(cx - w2 / 2, 0.0), W - w2), min(max(cy - h2 / 2, 0.0), H - h2)
                if w2 <= W and h2 <= H:
                    fine.append((x0, y0, x0 + w2, y0 + h2))
    if fine:
        sims2, comb2 = score(fine)
        j = int(np.argmax(comb2))
        best, best_sim = fine[j], float(sims2[j])
    else:
        best, best_sim = wins[i], float(sims[i])
    x0, y0, x1, y1 = best
    return dict(method="clip", center=((x0 + x1) / 2, (y0 + y1) / 2), size=(x1 - x0, y1 - y0),
                rot=0.0, score=best_sim)


def _debug(front, user, res, path, name):
    img = front.convert("RGB").copy()
    d = ImageDraw.Draw(img)
    lw = max(3, max(img.size) // 300)
    d.rectangle(user, outline=(240, 169, 58), width=lw)            # zona marcada (ambar)
    cx, cy = res["center"]
    w, h = res["size"]
    c, s = math.cos(res["rot"]), math.sin(res["rot"])
    pts = [(cx + x * c - y * s, cy + x * s + y * c)
           for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]
    d.line(pts + [pts[0]], fill=(60, 207, 142), width=lw + 1)     # sitio calculado (verde)
    d.text((10, 10), f"{name}: {res['method']} ({res['score']:.2f})", fill=(255, 255, 255))
    img.save(path)


def align(front, detail, det_box, rect, name="parte", debug_path=None, use_clip=True):
    """front, detail: imagenes PIL originales. det_box: (x0,y0,x1,y1) de la silueta del objeto
    dentro de la foto del detalle (px). rect: [x0,y0,x1,y1] (0-1) marcado sobre la frontal.
    name: nombre de la parte (lo usa CLIP). Devuelve dict(method, center, size, rot, score, fit)
    en px de la frontal. fit=True significa que no se pudo comparar y el detalle debe encajarse
    en la zona marcada."""
    W, H = front.size
    user = (rect[0] * W, rect[1] * H, rect[2] * W, rect[3] * H)
    steps = []
    if cv2 is not None:
        steps += [("caracteristicas", lambda: _by_features(front, detail, det_box, user)),
                  ("plantilla", lambda: _by_template(front, detail, det_box, user))]
    if use_clip:
        steps.append(("clip", lambda: _by_clip(front, detail, det_box, user, name)))
    res = None
    for label, fn in steps:
        try:
            res = fn()
        except Exception as e:
            print(f"  ({label} fallo: {e})")
            res = None
        if res:
            break
    if res:
        res["fit"] = False
    else:
        res = dict(method="zona", center=((user[0] + user[2]) / 2, (user[1] + user[3]) / 2),
                   size=(user[2] - user[0], user[3] - user[1]), rot=0.0, score=0.0, fit=True)
    if debug_path:
        try:
            _debug(front, user, res, debug_path, name)
        except Exception as e:
            print("  (no pude guardar la imagen de comprobacion:", e, ")")
    return res
