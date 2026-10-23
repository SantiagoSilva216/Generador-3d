"""
img2blend - interfaz
6 bloques de imagen (frontal, izquierda, trasera, derecha, superior, inferior).
Pulsa "+" para elegir una imagen; se copia a la carpeta correspondiente
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

from PIL import Image, ImageTk

import ajustes
from pipeline import find_blender

BASE = Path(__file__).resolve().parent
EXT = {".png", ".jpg", ".jpeg", ".webp"}
SLOTS = [
    ("front", "FRONTAL", "obligatoria"),
    ("left", "IZQUIERDA", "opcional"),
    ("back", "TRASERA", "opcional"),
    ("right", "DERECHA", "opcional"),
    ("top", "SUPERIOR (arriba)", "opcional"),
    ("bottom", "INFERIOR (abajo)", "opcional"),
]

QUALITY_INFO = {
    "normal": "Normal: rápida, resolución 256. Usa solo la VRAM de la tarjeta gráfica.",
    "alta": "Alta: resolución 384. Pasa partes del modelo a la RAM para liberar VRAM. Algo más lenta.",
    "maxima": "Máxima: resolución 512. Usa RAM y puede tardar varios minutos; si falta memoria, baja sola de resolución.",
}
# VRAM libre (MB) recomendada antes de generar, segun la calidad
NEED_FREE_MB = {"normal": 4000, "alta": 3000, "maxima": 3000}

BG = "#1e1e1e"
CARD = "#2a2a2a"
LINE = "#555555"
TEXT = "#e6e6e6"
MUTED = "#9a9a9a"
ACCENT = "#4f8cff"
SIZE = 140


def first_image(folder: Path):
    folder.mkdir(exist_ok=True)
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in EXT)
    return files[0] if files else None


class Slot(tk.Frame):
    def __init__(self, master, key, title, note, on_change):
        super().__init__(master, bg=CARD, padx=12, pady=10)
        self.key, self.title, self.on_change = key, title, on_change
        self.folder = BASE / key
        self.photo = None  # referencia para que tkinter no libere la miniatura

        tk.Label(self, text=title, bg=CARD, fg=TEXT, font=("Segoe UI", 10, "bold")).pack()
        tk.Label(self, text=note, bg=CARD, fg=ACCENT if note == "obligatoria" else MUTED,
                 font=("Segoe UI", 8)).pack()

        self.canvas = tk.Canvas(self, width=SIZE, height=SIZE, bg=CARD,
                                highlightthickness=0, cursor="hand2")
        self.canvas.pack(pady=6)
        self.canvas.bind("<Button-1>", lambda e: self.choose())

        self.name = tk.Label(self, text="", bg=CARD, fg=MUTED, font=("Segoe UI", 8))
        self.name.pack()
        self.remove_btn = tk.Button(self, text="Quitar", command=self.remove, bg=CARD, fg=MUTED,
                                    activebackground=CARD, activeforeground=TEXT,
                                    relief="flat", cursor="hand2", font=("Segoe UI", 8))
        self.refresh(notify=False)  # sin avisar: la ventana principal aun se esta construyendo

    def draw_empty(self):
        c = self.canvas
        c.delete("all")
        c.create_rectangle(4, 4, SIZE - 4, SIZE - 4, outline=LINE, dash=(6, 4), width=2)
        c.create_text(SIZE / 2, SIZE / 2 - 8, text="+", fill=ACCENT, font=("Segoe UI", 44))
        c.create_text(SIZE / 2, SIZE - 24, text="Añadir imagen", fill=MUTED, font=("Segoe UI", 8))

    def refresh(self, notify=True):
        img_path = first_image(self.folder)
        self.canvas.delete("all")
        if img_path is None:
            self.photo = None
            self.draw_empty()
            self.name.config(text="")
            self.remove_btn.pack_forget()
        else:
            try:
                img = Image.open(img_path).convert("RGBA")
                img.thumbnail((SIZE - 8, SIZE - 8))
                self.photo = ImageTk.PhotoImage(img)
                self.canvas.create_image(SIZE / 2, SIZE / 2, image=self.photo)
                self.name.config(text=img_path.name[:22])
            except Exception:
                self.photo = None
                self.draw_empty()
                self.name.config(text="No se pudo leer la imagen")
            self.remove_btn.pack(pady=(2, 0))
        if notify:
            self.on_change()

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


def make_button(parent, text, command, bg, fg="white", active="#3a73d9", bold=False):
    return tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                     activebackground=active, activeforeground=fg, relief="flat",
                     cursor="hand2", font=("Segoe UI", 10, "bold" if bold else "normal"),
                     padx=14, pady=6)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("img2blend")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.q = queue.Queue()
        self.running = False

        tk.Label(self, text="Imágenes del modelo", bg=BG, fg=TEXT,
                 font=("Segoe UI", 16, "bold")).pack(pady=(14, 2))
        tk.Label(self, text="Pulsa + en cada bloque para elegir la imagen de esa vista. "
                            "Solo la frontal es obligatoria.\n"
                            "La IA crea la forma con frontal, izquierda, trasera y derecha. "
                            "Superior e inferior se usan al pintar y como referencia en Blender.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 9), justify="center").pack()

        row = tk.Frame(self, bg=BG)
        row.pack(padx=12, pady=12)
        self.slots = []
        for key, title, note in SLOTS:
            s = Slot(row, key, title, note, self.update_status)
            s.pack(side="left", padx=5)
            self.slots.append(s)

        self.status = tk.Label(self, text="", bg=BG, fg=MUTED, font=("Segoe UI", 10))
        self.status.pack()

        rq = tk.Frame(self, bg=BG)
        rq.pack(pady=(8, 0))
        tk.Label(rq, text="Calidad:", bg=BG, fg=TEXT, font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 6))
        self.quality = tk.StringVar(value="normal")
        for key, label in (("normal", "Normal"), ("alta", "Alta"), ("maxima", "Máxima")):
            tk.Radiobutton(rq, text=label, value=key, variable=self.quality,
                           command=self.update_quality_desc, bg=BG, fg=TEXT, selectcolor=CARD,
                           activebackground=BG, activeforeground=TEXT, font=("Segoe UI", 10),
                           cursor="hand2").pack(side="left", padx=4)
        self.quality_desc = tk.Label(self, text=QUALITY_INFO["normal"], bg=BG, fg=MUTED,
                                     font=("Segoe UI", 9))
        self.quality_desc.pack(pady=(2, 0))

        r1 = tk.Frame(self, bg=BG)
        r1.pack(pady=(8, 4))
        self.gen_btn = make_button(r1, "1. Generar modelo 3D", self.generate, ACCENT, bold=True)
        self.detail_btn = make_button(r1, "2. Detallar en Blender", self.detail, "#d9822b",
                                      active="#b56a20", bold=True)
        self.paint_btn = make_button(r1, "3. Pintar con mis imágenes", self.paint, "#2f9e5b",
                                     active="#25804a", bold=True)
        self.export_btn = make_button(r1, "4. Exportar", self.export, "#8a5cf5",
                                      active="#6f45d6", bold=True)
        for b in (self.gen_btn, self.detail_btn, self.paint_btn, self.export_btn):
            b.pack(side="left", padx=5)

        r2 = tk.Frame(self, bg=BG)
        r2.pack(pady=(0, 10))
        self.retouch_btn = make_button(r2, "Retocar pintura en Blender", self.retouch,
                                       CARD, fg=TEXT, active=LINE)
        self.open_btn = make_button(r2, "Abrir carpeta de resultado", self.open_output,
                                    CARD, fg=TEXT, active=LINE)
        for b in (self.retouch_btn, self.open_btn):
            b.pack(side="left", padx=5)

        self.log = tk.Text(self, height=9, width=120, bg="#141414", fg="#cfcfcf",
                           relief="flat", font=("Consolas", 9), state="disabled")
        self.log.pack(padx=14, pady=(0, 14))

        self.update_status()
        self.after(100, self.poll)

    # ------------------------------------------------------------ estado
    def update_status(self):
        n = sum(1 for s in self.slots if s.has_image())
        front_ok = bool(self.slots) and self.slots[0].has_image()
        self.status.config(text=f"{n}/6 imágenes cargadas." if front_ok
                           else f"{n}/6 imágenes. Falta la imagen frontal.")

        out = BASE / "output"
        has_model = (out / "modelo.blend").exists()
        idle = not self.running

        def st(ok):
            return "normal" if ok and idle else "disabled"

        self.gen_btn.config(state=st(front_ok))
        self.detail_btn.config(state=st(has_model))
        self.paint_btn.config(state=st(front_ok and has_model))
        self.export_btn.config(state=st(has_model))
        self.retouch_btn.config(state=st((out / "modelo_pintado.blend").exists()))

    def write_log(self, text):
        self.log.config(state="normal")
        for part in re.split(r"(\r|\n)", text):
            if part == "\r":  # barras de progreso: reescribe la linea actual
                self.log.delete("end-1c linestart", "end-1c")
            elif part:
                self.log.insert("end", part)
        self.log.see("end")
        self.log.config(state="disabled")

    # ------------------------------------------------------------ acciones
    def open_output(self):
        (BASE / "output").mkdir(exist_ok=True)
        os.startfile(BASE / "output")

    def update_quality_desc(self):
        self.quality_desc.config(text=QUALITY_INFO[self.quality.get()])

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
            self.run_job("pipeline.py", ["--quality", q])

    def detail(self):
        self.run_job("detallar.py")

    def paint(self):
        self.run_job("pintar.py")

    def export(self):
        path = filedialog.asksaveasfilename(
            title="Exportar modelo", defaultextension=".fbx", initialfile="modelo",
            filetypes=[("FBX (Unity)", "*.fbx"), ("glTF binario", "*.glb"), ("OBJ", "*.obj")],
        )
        if path:
            self.run_job("exportar.py", ["--out", path])

    def retouch(self):
        painted = BASE / "output" / "modelo_pintado.blend"
        try:
            blender = find_blender("blender")
        except SystemExit as e:
            messagebox.showerror("Blender", str(e))
            return
        subprocess.Popen([blender, str(painted), "--python", str(BASE / "blender_retocar.py")])

    # ------------------------------------------------------------ procesos
    def run_job(self, script, args=None):
        self.running = True
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
                    ok = item[1] == 0
                    self.write_log("\n" + ("Proceso terminado correctamente." if ok
                                           else f"El proceso terminó con error (código {item[1]})."))
                    self.update_status()
                else:
                    self.write_log(item)
        except queue.Empty:
            pass
        self.after(100, self.poll)


if __name__ == "__main__":
    App().mainloop()
