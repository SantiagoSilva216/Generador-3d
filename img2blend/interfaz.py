"""
img2blend - interfaz (rediseño oscuro)
6 bloques de imagen (frontal, izquierda, trasera, derecha, superior, inferior).
Pulsa en un bloque para elegir una imagen; se copia a la carpeta correspondiente
(front/, left/, back/, right/, top/, bottom/), de donde leen los demas scripts.

Paso 2 (Detallar): el panel de vistas se sustituye por un panel de PARTES a detallar
(cara, manos, ropa...). Cada parte se guarda en detalles/<nombre>/.

Uso: doble clic en interfaz.bat
"""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
from tkinter import font as tkfont

from PIL import Image, ImageTk

import ajustes
from pipeline import find_blender

BASE = Path(__file__).resolve().parent
EXT = {".png", ".jpg", ".jpeg", ".webp"}
DETAIL_DIR = BASE / "detalles"
MAX_PARTS = 6
STRENGTH = {"suave": 0.4, "normal": 0.7, "fuerte": 1.0}   # intensidad del relieve
SLOTS = [
    ("front", "FRONTAL", "Obligatoria"),
    ("left", "IZQUIERDA", "Opcional"),
    ("back", "TRASERA", "Opcional"),
    ("right", "DERECHA", "Opcional"),
    ("top", "SUPERIOR (arriba)", "Opcional"),
    ("bottom", "INFERIOR (abajo)", "Opcional"),
]

QUALITY_INFO = {
    "normal": "Normal: rápida, resolución 256. Usa solo la VRAM de la tarjeta gráfica.",
    "alta": "Alta: resolución 384. Pasa partes del modelo a la RAM para liberar VRAM. Algo más lenta.",
    "maxima": "Máxima: resolución 512. Usa RAM y puede tardar varios minutos; si falta memoria, baja sola de resolución.",
}
# VRAM libre (MB) recomendada antes de generar, segun la calidad
NEED_FREE_MB = {"normal": 4000, "alta": 3000, "maxima": 3000}

# ---------------------------------------------------------------- estilo
BG = "#0f1117"        # fondo de la ventana
PANEL = "#151821"     # paneles
CARD = "#1b1f2b"      # tarjetas
INNER = "#12151d"     # interior de las miniaturas
LINE = "#2b3142"      # bordes
LINE_HI = "#4a5473"   # borde al pasar el raton
TEXT = "#e8eaf0"
MUTED = "#8b92a5"
ACCENT = "#4f8cff"
GREEN = "#3ccf8e"
AMBER = "#f0a93a"
FONT = "Segoe UI"

# (acento, fondo, borde) de cada paso
STEP_STYLE = [
    ("#4f8cff", "#1d2b4a", "#4f8cff"),
    ("#f0a93a", "#3a2a12", "#c98a2a"),
    ("#3ccf8e", "#123b2c", "#2ba374"),
    ("#a066f5", "#2a1f4d", "#8a52e0"),
]
STEP_ICON = ["⬢", "✎", "▣", "⇪"]
STEP_NAME = ["Generar modelo 3D", "Detallar en Blender", "Pintar con mis imágenes", "Exportar"]


def rrect(c, x1, y1, x2, y2, r=12, **kw):
    """Rectangulo redondeado dibujado como poligono suavizado."""
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


def lerp_color(a, b, t):
    a = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def first_image(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in EXT)
    return files[0] if files else None


# ---------------------------------------------------------------- widgets
class Logo(tk.Canvas):
    """Texto 'img2blend' con degradado azul -> rosa."""
    def __init__(self, master):
        f = tkfont.Font(family=FONT, size=20, weight="bold")
        text = "img2blend"
        w = f.measure(text) + 44
        super().__init__(master, width=w, height=44, bg=PANEL, highlightthickness=0)
        self.create_text(4, 22, text="⬢", fill=ACCENT, font=(FONT, 20), anchor="w")
        x = 38
        for i, ch in enumerate(text):
            col = lerp_color("#4f8cff", "#d05cf0", i / (len(text) - 1))
            self.create_text(x, 22, text=ch, fill=col, font=f, anchor="w")
            x += f.measure(ch)


class Pill(tk.Canvas):
    """Etiqueta redondeada con un punto de color (estado de las imagenes)."""
    def __init__(self, master):
        super().__init__(master, width=290, height=40, bg=PANEL, highlightthickness=0)
        self.font = tkfont.Font(family=FONT, size=10)

    def set(self, text, ok):
        self.delete("all")
        w = self.font.measure(text) + 54
        self.config(width=w)
        rrect(self, 2, 2, w - 2, 38, 18, fill=CARD, outline=LINE)
        self.create_oval(16, 15, 26, 25, fill=GREEN if ok else AMBER, outline="")
        self.create_text(36, 20, text=text, fill=TEXT, font=self.font, anchor="w")


def flat_button(parent, text, command):
    b = tk.Button(parent, text=text, command=command, bg=CARD, fg=TEXT,
                  activebackground=LINE, activeforeground=TEXT, relief="flat", bd=0,
                  highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE_HI,
                  cursor="hand2", font=(FONT, 10), padx=16, pady=8)
    b.bind("<Enter>", lambda e: b.config(bg="#242a3a"))
    b.bind("<Leave>", lambda e: b.config(bg=CARD))
    return b


class Slot(tk.Canvas):
    W, H = 240, 252

    def __init__(self, master, key, title, note, on_change):
        super().__init__(master, width=self.W, height=self.H, bg=BG,
                         highlightthickness=0, cursor="hand2")
        self.key, self.title, self.note, self.on_change = key, title, note, on_change
        self.folder = BASE / key
        self.photo = None
        self.hover = False
        self.f_title = tkfont.Font(family=FONT, size=10, weight="bold")
        self.f_note = tkfont.Font(family=FONT, size=8)
        self.bind("<Button-1>", self.click)
        self.bind("<Enter>", lambda e: self.set_hover(True))
        self.bind("<Leave>", lambda e: self.set_hover(False))
        self.draw()

    def set_hover(self, v):
        self.hover = v
        self.draw()

    def draw(self):
        W, H = self.W, self.H
        self.delete("all")
        rrect(self, 2, 2, W - 2, H - 2, 14, fill=CARD, outline=LINE_HI if self.hover else LINE, width=1)

        self.create_text(18, 24, text=self.title, fill=TEXT, font=self.f_title, anchor="w")
        tw = self.f_title.measure(self.title)
        required = self.note == "Obligatoria"
        nw = self.f_note.measure(self.note) + 18
        if 18 + tw + 8 + nw < W - 46:   # cabe al lado del titulo -> pastilla
            x = 18 + tw + 8
            rrect(self, x, 14, x + nw, 34, 9,
                  fill="#24407a" if required else "#262c3b", outline="")
            self.create_text(x + nw / 2, 24, text=self.note, font=self.f_note,
                             fill="#9fc0ff" if required else MUTED)
        else:                            # no cabe -> texto debajo
            self.create_text(18, 44, text=self.note, fill=MUTED, font=self.f_note, anchor="w")

        img_path = first_image(self.folder)
        # zona de la miniatura
        x1, y1, x2, y2 = 16, 58, W - 16, H - 16
        rrect(self, x1, y1, x2, y2, 10, fill=INNER, outline="")
        self.photo = None
        if img_path is None:
            self.create_rectangle(x1 + 10, y1 + 10, x2 - 10, y2 - 10, outline=LINE_HI, dash=(5, 4))
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            self.create_text(cx, cy - 10, text="+", fill=ACCENT, font=(FONT, 38))
            self.create_text(cx, cy + 32, text="Añadir imagen", fill=MUTED, font=(FONT, 9))
        else:
            try:
                img = Image.open(img_path).convert("RGBA")
                img.thumbnail((x2 - x1 - 8, y2 - y1 - 40))
                self.photo = ImageTk.PhotoImage(img)
                self.create_image((x1 + x2) / 2, y1 + 6 + (y2 - y1 - 40) / 2, image=self.photo)
                label = img_path.name
            except Exception:
                label = "No se pudo leer"
            rrect(self, x1 + 8, y2 - 32, x2 - 8, y2 - 8, 8, fill="#0b0d12", outline="")
            self.create_text(x1 + 18, y2 - 20, text="▣", fill=MUTED, font=(FONT, 10), anchor="w")
            self.create_text(x1 + 38, y2 - 20, text=label[:22] + ("…" if len(label) > 22 else ""),
                             fill=TEXT, font=(FONT, 9), anchor="w")
            # boton de quitar
            self.create_text(W - 24, 24, text="✕", fill=MUTED, font=(FONT, 11, "bold"), tags="trash")

    def refresh(self, notify=True):
        self.draw()
        if notify:
            self.on_change()

    def click(self, e):
        if self.has_image() and e.x > self.W - 44 and e.y < 46:
            self.remove()
        else:
            self.choose()

    def choose(self):
        path = filedialog.askopenfilename(
            title=f"Elige la imagen {self.title.lower()}",
            filetypes=[("Imágenes", "*.png *.jpg *.jpeg *.webp"), ("Todos", "*.*")],
        )
        if not path:
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        for f in self.folder.iterdir():  # una sola imagen por bloque
            if f.suffix.lower() in EXT:
                f.unlink()
        shutil.copy(path, self.folder / Path(path).name)
        self.refresh()

    def remove(self):
        for f in self.folder.iterdir():
            if f.suffix.lower() in EXT:
                f.unlink()
        self.refresh()

    def has_image(self):
        return first_image(self.folder) is not None


def read_region(part):
    """Rectangulo [x0,y0,x1,y1] (0-1, sobre la imagen frontal) guardado para una parte, o None."""
    try:
        return json.loads((DETAIL_DIR / part / "region.json").read_text(encoding="utf-8"))["rect"]
    except Exception:
        return None


class RegionDialog(tk.Toplevel):
    """Muestra la imagen frontal; se arrastra un rectangulo sobre la zona donde va la parte."""
    MAXW, MAXH = 520, 560

    def __init__(self, master, front_path, part, initial=None):
        super().__init__(master)
        self.title(f"Zona de «{part}»")
        self.configure(bg=PANEL)
        self.resizable(False, False)
        self.result = None
        self.rect = None
        self.rect_id = None
        img = Image.open(front_path).convert("RGBA")
        sc = min(self.MAXW / img.width, self.MAXH / img.height)
        self.cw, self.ch = max(int(img.width * sc), 1), max(int(img.height * sc), 1)
        bg = Image.new("RGBA", (self.cw, self.ch), INNER)
        bg.alpha_composite(img.resize((self.cw, self.ch)))
        self.photo = ImageTk.PhotoImage(bg.convert("RGB"))

        tk.Label(self, bg=PANEL, fg=TEXT, font=(FONT, 10), wraplength=self.cw,
                 text=f"Marca de forma aproximada dónde está «{part}» en la imagen frontal. Se afina comparando las fotos."
                 ).pack(padx=16, pady=(14, 8))
        self.canvas = tk.Canvas(self, width=self.cw, height=self.ch, bg=INNER,
                                highlightthickness=1, highlightbackground=LINE, cursor="crosshair")
        self.canvas.pack(padx=16)
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        if initial:
            x0, y0, x1, y1 = initial
            self.rect = (x0 * self.cw, y0 * self.ch, x1 * self.cw, y1 * self.ch)
            self.redraw()
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.drag)

        row = tk.Frame(self, bg=PANEL)
        row.pack(pady=12)
        flat_button(row, "Cancelar", self.destroy).pack(side="left", padx=6)
        self.ok_btn = flat_button(row, "Aceptar", self.accept)
        self.ok_btn.pack(side="left", padx=6)
        self.update_ok()
        self.transient(master)
        self.grab_set()
        self.wait_window()

    def _clamp(self, e):
        return min(max(e.x, 0), self.cw), min(max(e.y, 0), self.ch)

    def press(self, e):
        x, y = self._clamp(e)
        self.rect = (x, y, x, y)
        self.redraw()

    def drag(self, e):
        x, y = self._clamp(e)
        self.rect = (self.rect[0], self.rect[1], x, y)
        self.redraw()

    def redraw(self):
        if self.rect_id is not None:
            self.canvas.delete(self.rect_id)
        x0, y0, x1, y1 = self.rect
        self.rect_id = self.canvas.create_rectangle(x0, y0, x1, y1, outline=AMBER, width=2, dash=(6, 3))
        self.update_ok()

    def update_ok(self):
        ok = (self.rect is not None and abs(self.rect[2] - self.rect[0]) >= 8
              and abs(self.rect[3] - self.rect[1]) >= 8)
        self.ok_btn.config(state="normal" if ok else "disabled")

    def accept(self):
        x0, y0, x1, y1 = self.rect
        self.result = [min(x0, x1) / self.cw, min(y0, y1) / self.ch,
                       max(x0, x1) / self.cw, max(y0, y1) / self.ch]
        self.destroy()


class PartSlot(Slot):
    """Bloque de una parte a detallar: carpeta detalles/<nombre>/ con una imagen y su zona."""
    def __init__(self, master, part, on_change, on_remove, on_region):
        self.part, self.on_remove, self.on_region = part, on_remove, on_region
        super().__init__(master, f"detalles/{part}", part.replace("_", " ").upper(),
                         "Parte", on_change)

    def draw(self):
        super().draw()
        if self.has_image():  # ⌖ verde = zona marcada, ambar = falta marcarla
            ok = read_region(self.part) is not None
            self.create_text(self.W - 52, 24, text="⌖", fill=GREEN if ok else AMBER,
                             font=(FONT, 13, "bold"))

    def click(self, e):
        if e.y < 46 and self.has_image():
            if e.x > self.W - 44:
                return self.remove()
            if e.x > self.W - 70:
                return self.on_region(self.part)
        self.choose()

    def remove(self):  # la ✕ borra la parte entera, no solo la imagen
        shutil.rmtree(self.folder, ignore_errors=True)
        self.on_remove()


class Segmented(tk.Canvas):
    """Selector Normal | Alta | Máxima."""
    def __init__(self, master, var, options, command, width=290):
        super().__init__(master, width=width, height=46, bg=PANEL, highlightthickness=0, cursor="hand2")
        self.var, self.options, self.command, self.w = var, options, command, width
        self.bind("<Button-1>", self.click)
        self.draw()

    def draw(self):
        self.delete("all")
        rrect(self, 2, 2, self.w - 2, 44, 20, fill=CARD, outline=LINE)
        n = len(self.options)
        seg = (self.w - 12) / n
        for i, (key, label) in enumerate(self.options):
            x1 = 6 + i * seg
            sel = self.var.get() == key
            if sel:
                rrect(self, x1, 6, x1 + seg, 40, 16, fill="#34508a", outline="#4f78c8")
            self.create_text(x1 + seg / 2, 23, text=label, font=(FONT, 11),
                             fill="white" if sel else MUTED)

    def click(self, e):
        n = len(self.options)
        i = min(max(int((e.x - 6) // ((self.w - 12) / n)), 0), n - 1)
        self.var.set(self.options[i][0])
        self.draw()
        self.command()


class Steps(tk.Canvas):
    """Pasos del proceso con linea de tiempo. Estados: disabled / enabled / running / done."""
    ROW, CH = 80, 66

    def __init__(self, master, actions, width=330):
        super().__init__(master, width=width, height=self.ROW * 4, bg=PANEL,
                         highlightthickness=0)
        self.w, self.actions = width, actions
        self.states = ["disabled"] * 4
        self.next = None
        self.bind("<Button-1>", self.click)
        self.bind("<Motion>", self.motion)
        self.draw()

    def set_next(self, i):
        self.next = i
        self.draw()

    def set_states(self, states):
        self.states = states
        self.draw()

    def draw(self):
        self.delete("all")
        for i in range(4):
            acc, bg, bd = STEP_STYLE[i]
            st = self.states[i]
            top = i * self.ROW + 6
            mid = top + self.CH / 2
            dim = st == "disabled"
            # linea de tiempo
            if i < 3:
                self.create_line(14, mid + 8, 14, mid + self.ROW - 8, fill=LINE, width=2)
            self.create_oval(8, mid - 6, 20, mid + 6, fill=MUTED if dim else acc, outline="")
            # tarjeta
            rrect(self, 40, top, self.w - 2, top + self.CH, 14,
                  fill="#181b25" if dim else bg, outline=LINE if dim else bd,
                  width=2 if (i == self.next and not dim) else 1)
            rrect(self, 52, top + 15, 88, top + 51, 9,
                  fill="#222736" if dim else lerp_color(bg, acc, 0.22), outline="")
            self.create_text(70, mid, text=STEP_ICON[i], font=(FONT, 15),
                             fill=MUTED if dim else acc)
            self.create_text(100, top + 22, text=f"Step {i + 1}", anchor="w", font=(FONT, 8),
                             fill=MUTED if dim else lerp_color(acc, "#ffffff", 0.35))
            self.create_text(100, top + 43, text=STEP_NAME[i], anchor="w", font=(FONT, 10, "bold"),
                             fill="#6d7385" if dim else TEXT)
            if st == "done":
                self.create_oval(self.w - 38, mid - 11, self.w - 16, mid + 11, fill=acc, outline="")
                self.create_text(self.w - 27, mid, text="✓", fill="white", font=(FONT, 11, "bold"))
            elif st == "running":
                self.create_text(self.w - 14, top + 22, text="en curso…", anchor="e",
                                 font=(FONT, 8), fill=acc)
            elif i == self.next and st == "enabled":
                self.create_text(self.w - 14, top + 22, text="siguiente ▸", anchor="e",
                                 font=(FONT, 8, "bold"), fill=acc)

    def _index(self, y):
        i = int(y // self.ROW)
        return i if 0 <= i < 4 else None

    def click(self, e):
        i = self._index(e.y)
        if i is not None and self.states[i] in ("enabled", "done"):
            self.actions[i]()

    def motion(self, e):
        i = self._index(e.y)
        ok = i is not None and self.states[i] in ("enabled", "done")
        self.config(cursor="hand2" if ok else "")


class DetailPanel(tk.Frame):
    """Panel del paso 2: partes del modelo a detallar (cara, manos, ropa...)."""
    def __init__(self, master, on_change, on_apply, on_back):
        super().__init__(master, bg=PANEL)
        self.on_change = on_change
        top = tk.Frame(self, bg=PANEL)
        top.pack(fill="x", pady=(0, 6))
        tk.Label(top, text="Partes a detallar", bg=PANEL, fg=MUTED,
                 font=(FONT, 12)).pack(side="left")
        flat_button(top, "← Volver a las vistas", on_back).pack(side="right")
        self.add_btn = flat_button(top, "+ Añadir parte", self.add)
        self.add_btn.pack(side="right", padx=8)
        flat_button(top, "Detallar ▶", on_apply).pack(side="right")
        tk.Label(self, bg=PANEL, fg=MUTED, font=(FONT, 9), justify="left", wraplength=740,
                 text="Añade una imagen por cada parte (cara, manos, ropa…) y marca de forma aproximada "
                      "dónde va en la vista frontal (icono ⌖ de cada bloque). El programa compara las fotos "
                      "para ubicarla con exactitud, genera su malla 3D y el modelo del paso 1 adopta su relieve."
                 ).pack(anchor="w", pady=(0, 6))
        row = tk.Frame(self, bg=PANEL)
        row.pack(fill="x", pady=(0, 6))
        tk.Label(row, text="Intensidad del relieve", bg=PANEL, fg=MUTED,
                 font=(FONT, 10)).pack(side="left", padx=(0, 12))
        self.strength = tk.StringVar(value="normal")
        Segmented(row, self.strength, [("suave", "Suave"), ("normal", "Normal"), ("fuerte", "Fuerte")],
                  lambda: None, width=290).pack(side="left")
        self.grid_frame = tk.Frame(self, bg=PANEL, width=750, height=2 * (Slot.H + 10))
        self.grid_frame.pack()
        self.grid_frame.grid_propagate(False)
        self.reload()

    def names(self):
        DETAIL_DIR.mkdir(exist_ok=True)
        return [d.name for d in sorted(DETAIL_DIR.iterdir()) if d.is_dir() and first_image(d)]

    def count(self):
        return len(self.names())

    def reload(self):
        for w in self.grid_frame.winfo_children():
            w.destroy()
        names = self.names()
        if not names:
            tk.Label(self.grid_frame, bg=PANEL, fg=MUTED, font=(FONT, 11),
                     text="Aún no hay partes.\nPulsa «+ Añadir parte» y elige su imagen."
                     ).grid(row=0, column=0, padx=40, pady=60)
        for i, n in enumerate(names):
            s = PartSlot(self.grid_frame, n, self.on_change, self.removed, self.edit_region)
            s.config(bg=PANEL)
            s.grid(row=i // 3, column=i % 3, padx=5, pady=5)
        self.add_btn.config(state="normal" if len(names) < MAX_PARTS else "disabled")

    def removed(self):
        self.after(10, lambda: (self.reload(), self.on_change()))

    def missing_regions(self):
        return [n for n in self.names() if read_region(n) is None]

    def ask_region(self, part, initial=None):
        front = first_image(BASE / "front")
        if front is None:
            messagebox.showinfo("Falta la imagen frontal",
                                "Carga primero la imagen FRONTAL en el panel de vistas: "
                                "se usa para marcar dónde va cada parte.")
            return None
        return RegionDialog(self.winfo_toplevel(), front, part, initial).result

    def save_region(self, part, rect):
        (DETAIL_DIR / part).mkdir(parents=True, exist_ok=True)
        (DETAIL_DIR / part / "region.json").write_text(json.dumps({"rect": rect}), encoding="utf-8")

    def edit_region(self, part):
        rect = self.ask_region(part, read_region(part))
        if rect:
            self.save_region(part, rect)
            self.after(10, lambda: (self.reload(), self.on_change()))

    def add(self):
        if first_image(BASE / "front") is None:
            messagebox.showinfo("Falta la imagen frontal",
                                "Carga primero la imagen FRONTAL en el panel de vistas.")
            return
        name = simpledialog.askstring("Nueva parte", "¿Qué parte es? (cara, mano izquierda, ropa…)\nEl nombre ayuda a localizarla en el modelo.", parent=self)
        name = re.sub(r"\W+", "_", (name or "").strip().lower()).strip("_")
        if not name:
            return
        folder = DETAIL_DIR / name
        if folder.exists() and first_image(folder):
            messagebox.showinfo("Parte existente", f"Ya hay una parte llamada «{name}».")
            return
        path = filedialog.askopenfilename(
            title=f"Imagen de: {name}",
            filetypes=[("Imágenes", "*.png *.jpg *.jpeg *.webp"), ("Todos", "*.*")])
        if not path:
            return
        rect = self.ask_region(name)
        if rect is None:  # cancelado: no se crea nada
            return
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, folder / Path(path).name)
        self.save_region(name, rect)
        self.reload()
        self.on_change()


# ---------------------------------------------------------------- ventana
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("img2blend")
        self.configure(bg=BG)
        self.resizable(False, False)
        # pantallas bajas: bloques mas pequenos para que la consola quede visible
        Slot.H = 180 if self.winfo_screenheight() < 900 else 215
        self.q = queue.Queue()
        self.running = False
        self.current_step = None
        self.console_open = True
        self.mode = "views"

        shell = tk.Frame(self, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        shell.pack(padx=14, pady=14)

        # ---- cabecera
        head = tk.Frame(shell, bg=PANEL)
        head.pack(fill="x", padx=22, pady=(14, 12))
        Logo(head).pack(side="left")
        tk.Frame(head, bg=LINE, width=1, height=34).pack(side="left", padx=16)
        self.pill = Pill(head)
        self.pill.pack(side="left")
        self.gear_btn = flat_button(head, "⚙", self.settings)
        self.gear_btn.config(padx=12, font=(FONT, 12))
        self.gear_btn.pack(side="right")
        self.open_btn = flat_button(head, "Abrir carpeta de resultado", self.open_output)
        self.open_btn.pack(side="right", padx=8)
        self.retouch_btn = flat_button(head, "Retocar pintura en Blender", self.retouch)
        self.retouch_btn.pack(side="right")
        self.delete_btn = flat_button(head, "Borrar modelo 3D", self.delete_model)
        self.delete_btn.config(fg="#ff8a8a")
        self.delete_btn.pack(side="right", padx=(0, 8))
        tk.Frame(shell, bg=LINE, height=1).pack(fill="x")

        # ---- cuerpo
        body = tk.Frame(shell, bg=PANEL)
        body.pack(fill="both")

        left = tk.Frame(body, bg=PANEL)
        left.pack(side="left", padx=(22, 10), pady=16)
        self.stage = tk.Frame(left, bg=PANEL, width=760, height=2 * (Slot.H + 10) + 150)
        self.stage.pack()
        self.stage.pack_propagate(False)

        # panel 1: las 6 vistas del modelo
        self.views_panel = tk.Frame(self.stage, bg=PANEL)
        tk.Label(self.views_panel, text="Imágenes del modelo", bg=PANEL, fg=MUTED,
                 font=(FONT, 12)).pack(anchor="w", pady=(0, 10))
        grid = tk.Frame(self.views_panel, bg=PANEL)
        grid.pack()
        self.slots = []
        for i, (key, title, note) in enumerate(SLOTS):
            s = Slot(grid, key, title, note, self.update_status)
            s.config(bg=PANEL)
            s.grid(row=i // 3, column=i % 3, padx=5, pady=5)
            self.slots.append(s)

        # panel 2: partes a detallar
        self.detail_panel = DetailPanel(self.stage, self.update_status,
                                        self.apply_detail, lambda: self.show_mode("views"))

        tk.Frame(body, bg=LINE, width=1).pack(side="left", fill="y", pady=0)

        right = tk.Frame(body, bg=PANEL)
        right.pack(side="left", padx=(20, 22), pady=16, anchor="n")
        tk.Label(right, text="Pipeline de Generación 3D", bg=PANEL, fg=TEXT,
                 font=(FONT, 15, "bold")).pack(anchor="w", pady=(0, 18))
        tk.Label(right, text="Configuración de Calidad", bg=PANEL, fg=TEXT,
                 font=(FONT, 11)).pack(anchor="w", pady=(0, 8))
        self.quality = tk.StringVar(value="normal")
        Segmented(right, self.quality,
                  [("normal", "Normal"), ("alta", "Alta"), ("maxima", "Máxima")],
                  self.update_quality_desc, width=330).pack(anchor="w")
        self.quality_desc = tk.Label(right, text="ⓘ  " + QUALITY_INFO["normal"], bg=PANEL, fg=MUTED,
                                     font=(FONT, 9), justify="left", wraplength=330)
        self.quality_desc.pack(anchor="w", pady=(8, 0))
        tk.Label(right, text="Pasos del Proceso", bg=PANEL, fg=TEXT,
                 font=(FONT, 11)).pack(anchor="w", pady=(18, 8))
        self.steps = Steps(right, [self.generate, self.detail, self.paint, self.export], width=330)
        self.steps.pack(anchor="w")

        # ---- consola
        tk.Frame(shell, bg=LINE, height=1).pack(fill="x")
        bar = tk.Frame(shell, bg=PANEL, cursor="hand2")
        bar.pack(fill="x")
        self.console_title = tk.Label(bar, text="Terminal / Console", bg=PANEL, fg=MUTED,
                                      font=(FONT, 10), cursor="hand2")
        self.console_title.pack(side="left", padx=22, pady=8)
        self.chevron = tk.Label(bar, text="⌄", bg=PANEL, fg=MUTED, font=(FONT, 12), cursor="hand2")
        self.chevron.pack(side="right", padx=22)
        for w in (bar, self.console_title, self.chevron):
            w.bind("<Button-1>", lambda e: self.toggle_console())

        self.console_wrap = tk.Frame(shell, bg=INNER)
        self.console_wrap.pack(fill="x")
        self.log = tk.Text(self.console_wrap, height=7, width=120, bg=INNER, fg="#cfd3dd",
                           relief="flat", font=("Consolas", 9), state="disabled",
                           padx=22, pady=8, insertbackground=TEXT, bd=0, highlightthickness=0)
        self.log.pack(fill="x")
        self.log.tag_configure("ok", foreground=GREEN)
        self.log.tag_configure("err", foreground="#ff6b6b")

        self.show_mode("views")
        self.steps.set_next(None if (BASE / "output" / "modelo.blend").exists() else 0)
        self.after(50, self.place_window)
        self.after(100, self.poll)

    # ------------------------------------------------------------ estado
    def show_mode(self, mode):
        """Alterna entre el panel de vistas y el de partes a detallar."""
        self.mode = mode
        self.views_panel.pack_forget()
        self.detail_panel.pack_forget()
        (self.views_panel if mode == "views" else self.detail_panel).pack(anchor="nw")
        self.update_status()

    def update_status(self):
        n = sum(1 for s in self.slots if s.has_image())
        front_ok = bool(self.slots) and self.slots[0].has_image()
        if self.mode == "detail":
            c = self.detail_panel.count()
            self.pill.set(f"{c}/{MAX_PARTS} partes para detallar", c > 0)
        else:
            self.pill.set(f"{n}/6 imágenes cargadas" if front_ok
                          else f"{n}/6 imágenes · falta la frontal", front_ok)

        out = BASE / "output"
        has_model = (out / "modelo.blend").exists()
        has_paint = (out / "modelo_pintado.blend").exists()
        idle = not self.running

        def st(i, ok, done=False):
            if self.running and self.current_step == i:
                return "running"
            if not ok or not idle:
                return "disabled"
            return "done" if done else "enabled"

        self.steps.set_states([
            st(0, front_ok, has_model),
            st(1, has_model),
            st(2, front_ok and has_model, has_paint),
            st(3, has_model),
        ])
        self.retouch_btn.config(state="normal" if has_paint and idle else "disabled")
        has_any = any(t.exists() for t in self.model_files())
        self.delete_btn.config(state="normal" if has_any and idle else "disabled")

    def write_log(self, text, tag=None):
        self.log.config(state="normal")
        for part in re.split(r"(\r|\n)", text):
            if part == "\r":  # barras de progreso: reescribe la linea actual
                self.log.delete("end-1c linestart", "end-1c")
            elif part:
                self.log.insert("end", part, tag or ())
        self.log.see("end")
        self.log.config(state="disabled")

    def toggle_console(self):
        self.console_open = not self.console_open
        if self.console_open:
            self.console_wrap.pack(fill="x")
            self.chevron.config(text="⌄")
        else:
            self.console_wrap.pack_forget()
            self.chevron.config(text="⌃")

    # ------------------------------------------------------------ acciones
    def open_output(self):
        (BASE / "output").mkdir(exist_ok=True)
        os.startfile(BASE / "output")

    def update_quality_desc(self):
        self.quality_desc.config(text="ⓘ  " + QUALITY_INFO[self.quality.get()])

    def settings(self):
        """Resumen del equipo: GPU, VRAM, bateria, programas que usan la GPU."""
        lines = []
        info = ajustes.vram_info()
        if info:
            lines.append(f"GPU NVIDIA: {info[1] / 1024:.1f} GB de VRAM, "
                         f"{(info[1] - info[0]) / 1024:.1f} GB libres.")
            procs = ajustes.gpu_processes()
            if procs:
                lines.append("Programas usando la GPU: " + ", ".join(procs) + ".")
        else:
            lines.append("No se detectó una GPU NVIDIA (nvidia-smi no disponible).")
        bat = ajustes.on_battery()
        lines.append({True: "Equipo con batería: conecta el cargador para generar.",
                      False: "Equipo conectado a la corriente.",
                      None: "Estado de energía desconocido."}[bat])
        try:
            lines.append(f"Blender: {find_blender('blender')}")
        except SystemExit as e:
            lines.append(str(e))
        messagebox.showinfo("Estado del equipo", "\n\n".join(lines))

    def precheck(self, q):
        """Avisa de lo que pueda estropear la generacion (bateria, VRAM ocupada)."""
        msgs = []
        if ajustes.on_battery():
            msgs.append("El portátil está funcionando con batería: la GPU rinde mucho menos "
                        "y el proceso puede fallar. Conecta el cargador.")
        info = ajustes.vram_info()
        if info:
            free, total = info[1] - info[0], info[1]
            need = NEED_FREE_MB[q]
            if free < need:
                m = (f"Solo hay {free / 1024:.1f} GB de VRAM libres de {total / 1024:.1f} GB "
                     f"(se recomiendan {need / 1024:.1f} GB para calidad {q}).")
                procs = ajustes.gpu_processes()
                if procs:
                    m += " Programas usando la GPU: " + ", ".join(procs) + "."
                msgs.append(m + " Cierra los que no necesites.")
        if not msgs:
            return True
        return messagebox.askyesno("Antes de generar", "\n\n".join(msgs) + "\n\n¿Generar de todos modos?")

    def generate(self):
        q = self.quality.get()
        if self.precheck(q):
            self.show_mode("views")
            self.run_job("pipeline.py", ["--quality", q], step=0)

    def detail(self):
        """Clic en el paso 2: primero muestra el panel de partes; despues lanza Blender."""
        if self.mode != "detail":
            self.show_mode("detail")
        else:
            self.apply_detail()

    def apply_detail(self):
        if self.detail_panel.count() == 0:
            messagebox.showinfo("Detallar", "Añade al menos una parte con su imagen.")
            return
        missing = self.detail_panel.missing_regions()
        if missing:
            messagebox.showinfo("Detallar", "Marca la zona (icono ⌖) de: " + ", ".join(missing))
            return
        q = self.quality.get()
        if self.precheck(q):
            self.run_job("detallar.py", ["--quality", q, "--fuerza",
                                         str(STRENGTH[self.detail_panel.strength.get()])], step=1)

    def paint(self):
        self.show_mode("views")
        self.run_job("pintar.py", step=2)

    def export(self):
        path = filedialog.asksaveasfilename(
            title="Exportar modelo", defaultextension=".fbx", initialfile="modelo",
            filetypes=[("FBX (Unity)", "*.fbx"), ("glTF binario", "*.glb"), ("OBJ", "*.obj")],
        )
        if path:
            self.run_job("exportar.py", ["--out", path], step=3)

    def retouch(self):
        painted = BASE / "output" / "modelo_pintado.blend"
        try:
            blender = find_blender("blender")
        except SystemExit as e:
            messagebox.showerror("Blender", str(e))
            return
        subprocess.Popen([blender, str(painted), "--python", str(BASE / "blender_retocar.py")])

    def model_files(self):
        """Todo lo que genera el modelo 3D (NO incluye tus imagenes ni las partes a detallar)."""
        out = BASE / "output"
        names = ["modelo.blend", "modelo_pintado.blend", "modelo_pintado_anterior.blend",
                 "modelo_antes_de_detallar.blend", "textura.png", "raw_mesh.glb",
                 "_detallado_estado.json", "_pintura", "_detalle", "_photos", "colmap"]
        files = [out / n for n in names]
        if out.exists():
            files += list(out.glob("*.blend1"))   # copias automaticas de Blender
        return files

    def delete_model(self):
        """Borra el modelo 3D generado para empezar de nuevo (el usuario no quedo conforme)."""
        existing = [t for t in self.model_files() if t.exists()]
        if not existing:
            messagebox.showinfo("Borrar modelo 3D", "No hay ningún modelo generado.")
            return
        ok = messagebox.askyesno(
            "Borrar modelo 3D",
            "Se borrará el modelo 3D generado, con su versión pintada, la textura, las copias de "
            "seguridad y los archivos temporales.\n\n"
            "NO se borran tus imágenes de las vistas, las partes a detallar ni lo que hayas "
            "exportado.\n\nEsta acción no se puede deshacer. ¿Borrar el modelo?",
            icon="warning", default="no")
        if not ok:
            return
        errors = []
        for t in existing:
            try:
                if t.is_dir():
                    shutil.rmtree(t)
                else:
                    t.unlink()
            except Exception as e:
                errors.append(f"{t.name}: {e}")
        self.show_mode("views")
        self.steps.set_next(0)
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")
        if errors:
            self.write_log("\n● No se pudo borrar todo:\n" + "\n".join(errors) +
                           "\nCierra Blender si tiene el modelo abierto y vuelve a intentarlo.\n", "err")
        else:
            self.write_log("\n● Modelo 3D borrado. Pulsa «Generar modelo 3D» para crear uno nuevo.\n", "ok")

    def place_window(self):
        """Centra la ventana sin dejar que se salga por abajo (la consola debe verse)."""
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = max((self.winfo_screenwidth() - w) // 2, 0)
        y = max((self.winfo_screenheight() - h) // 2 - 30, 0)
        self.geometry(f"+{x}+{y}")

    def view_in_blender(self, path):
        """Abre Blender (ventana normal) para mostrar el resultado de un paso."""
        try:
            blender = find_blender("blender")
            subprocess.Popen([blender, str(path), "--python", str(BASE / "blender_ver.py")],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except SystemExit as e:        # find_blender no encontro Blender
            self.write_log(f"\n{e}\n", "err")
        except Exception as e:
            self.write_log(f"\nNo pude abrir Blender: {e}\n", "err")

    def after_step(self, step):
        """Al terminar bien un paso: pasa al siguiente y, en los pasos 1 y 2, muestra el resultado."""
        if step == 0:
            self.show_mode("detail")
            self.steps.set_next(1)
            self.write_log("\n● Paso 1 listo: se abre Blender para que veas el modelo. "
                           "Ahora añade las partes a detallar (paso 2).\n", "ok")
            self.view_in_blender(BASE / "output" / "modelo.blend")
        elif step == 1:                # detallar.py ya abre Blender con el resultado
            self.show_mode("views")
            self.steps.set_next(2)
            self.write_log("\n● Paso 2 listo: Blender se abre con el resultado. "
                           "Siguiente: pintar con tus imágenes (paso 3).\n", "ok")
        elif step == 2:
            self.steps.set_next(3)
        elif step == 3:
            self.steps.set_next(None)

    # ------------------------------------------------------------ procesos
    def run_job(self, script, args=None, step=None):
        self.running = True
        self.current_step = step
        self.update_status()
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")
        threading.Thread(target=self.worker, args=(script, args or []), daemon=True).start()

    def worker(self, script, args):
        py = sys.executable.replace("pythonw", "python")
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            p = subprocess.Popen([py, str(BASE / script), *args], cwd=BASE,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 env=env, creationflags=flags)
            while True:
                chunk = p.stdout.read1(1024)
                if not chunk:
                    break
                self.q.put(chunk.decode("utf-8", "replace"))
            code = p.wait()
        except Exception as e:
            self.q.put(f"\nError al lanzar el proceso: {e}\n")
            code = -1
        self.q.put(("done", code))

    def poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if isinstance(item, tuple):
                    step = self.current_step
                    self.running = False
                    self.current_step = None
                    ok = item[1] == 0
                    if ok:
                        self.write_log("\n● Proceso terminado correctamente.", "ok")
                    else:
                        self.write_log(f"\n● El proceso terminó con error (código {item[1]}).", "err")
                    self.update_status()
                    if ok:
                        self.after_step(step)
                else:
                    self.write_log(item)
        except queue.Empty:
            pass
        self.after(100, self.poll)


if __name__ == "__main__":
    App().mainloop()
