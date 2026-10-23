"""
img2blend - interfaz (rediseño oscuro)
6 bloques de imagen (frontal, izquierda, trasera, derecha, superior, inferior).
Pulsa en un bloque para elegir una imagen; se copia a la carpeta correspondiente
(front/, left/, back/, right/, top/, bottom/), de donde leen los demas scripts.

Uso: doble clic en interfaz.bat
"""
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from tkinter import font as tkfont

from PIL import Image, ImageTk

import ajustes
from pipeline import find_blender

BASE = Path(__file__).resolve().parent
EXT = {".png", ".jpg", ".jpeg", ".webp"}
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
    folder.mkdir(exist_ok=True)
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
        self.folder.mkdir(exist_ok=True)
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
        self.bind("<Button-1>", self.click)
        self.bind("<Motion>", self.motion)
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
                  fill="#181b25" if dim else bg, outline=LINE if dim else bd, width=1)
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


# ---------------------------------------------------------------- ventana
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("img2blend")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.q = queue.Queue()
        self.running = False
        self.current_step = None
        self.console_open = True

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
        tk.Frame(shell, bg=LINE, height=1).pack(fill="x")

        # ---- cuerpo
        body = tk.Frame(shell, bg=PANEL)
        body.pack(fill="both")

        left = tk.Frame(body, bg=PANEL)
        left.pack(side="left", padx=(22, 10), pady=16)
        tk.Label(left, text="Imágenes del modelo", bg=PANEL, fg=MUTED,
                 font=(FONT, 12)).pack(anchor="w", pady=(0, 10))
        grid = tk.Frame(left, bg=PANEL)
        grid.pack()
        self.slots = []
        for i, (key, title, note) in enumerate(SLOTS):
            s = Slot(grid, key, title, note, self.update_status)
            s.config(bg=PANEL)
            s.grid(row=i // 3, column=i % 3, padx=5, pady=5)
            self.slots.append(s)

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
        self.log = tk.Text(self.console_wrap, height=6, width=120, bg=INNER, fg="#cfd3dd",
                           relief="flat", font=("Consolas", 9), state="disabled",
                           padx=22, pady=8, insertbackground=TEXT, bd=0, highlightthickness=0)
        self.log.pack(fill="x")
        self.log.tag_configure("ok", foreground=GREEN)
        self.log.tag_configure("err", foreground="#ff6b6b")

        self.update_status()
        self.after(100, self.poll)

    # ------------------------------------------------------------ estado
    def update_status(self):
        n = sum(1 for s in self.slots if s.has_image())
        front_ok = bool(self.slots) and self.slots[0].has_image()
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
            self.run_job("pipeline.py", ["--quality", q], step=0)

    def detail(self):
        self.run_job("detallar.py", step=1)

    def paint(self):
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
                    self.running = False
                    self.current_step = None
                    ok = item[1] == 0
                    if ok:
                        self.write_log("\n● Proceso terminado correctamente.", "ok")
                    else:
                        self.write_log(f"\n● El proceso terminó con error (código {item[1]}).", "err")
                    self.update_status()
                else:
                    self.write_log(item)
        except queue.Empty:
            pass
        self.after(100, self.poll)


if __name__ == "__main__":
    App().mainloop()
