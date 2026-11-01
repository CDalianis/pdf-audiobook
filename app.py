"""Desktop app: narrate a PDF in English or Greek."""

from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from engine import (
    APP_DIR,
    LANGUAGES,
    Cancelled,
    build_audiobook,
    default_voice,
    output_dir_for,
    voice_label,
)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

INK = "#f3efe6"
MUTED = "#b7ab9a"
PAPER = "#1c1814"
PANEL = "#262019"
ACCENT = "#c45c26"
ACCENT_HOVER = "#a84b1c"
LINE = "#3a322a"
CHOICES = {"English": "en", "Ελληνικά": "el"}


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PDF Audiobook")
        self._apply_icon()
        self.geometry("880x860")
        self.minsize(780, 760)
        self.configure(fg_color=PAPER)

        self.events: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.pdf_path = tk.StringVar(value="")
        self.output_root = tk.StringVar(value=str(APP_DIR / "audiobooks"))
        self.source_language = tk.StringVar(value="en")
        self.output_language = tk.StringVar(value="en")
        self.voice = tk.StringVar(value=default_voice("en"))
        self.voice_name = tk.StringVar(value=voice_label("en", default_voice("en")))
        self.scope = tk.StringVar(value="full")
        self.export = tk.StringVar(value="parts")

        self._build()
        self._refresh_note()
        self._refresh_chapters()
        self.after(120, self._drain)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _apply_icon(self) -> None:
        if getattr(sys, "frozen", False):
            base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        else:
            base = Path(__file__).resolve().parent
        icon = base / "pdf-audiobook.ico"
        if icon.exists():
            self.iconbitmap(str(icon))

    def _build(self) -> None:
        root = ctk.CTkFrame(self, fg_color=PAPER)
        root.pack(fill="both", expand=True, padx=28, pady=22)

        ctk.CTkLabel(
            root,
            text="PDF AUDIOBOOK",
            font=ctk.CTkFont(family="Georgia", size=30, weight="bold"),
            text_color=INK,
            anchor="w",
        ).pack(fill="x")
        ctk.CTkLabel(
            root,
            text="Read any text PDF aloud in English or Greek",
            font=ctk.CTkFont(family="Georgia", size=16, slant="italic"),
            text_color=ACCENT,
            anchor="w",
        ).pack(fill="x", pady=(0, 16))

        panel = ctk.CTkFrame(root, fg_color=PANEL, corner_radius=14)
        panel.pack(fill="x")
        panel.grid_columnconfigure(1, weight=1)

        self._field_label(panel, "PDF", 0)
        pdf_row = ctk.CTkFrame(panel, fg_color="transparent")
        pdf_row.grid(row=0, column=1, sticky="ew", padx=12, pady=(16, 6))
        pdf_row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(
            pdf_row,
            textvariable=self.pdf_path,
            fg_color=PAPER,
            border_color=LINE,
            text_color=INK,
            font=ctk.CTkFont(size=13),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            pdf_row,
            text="Browse",
            width=90,
            command=self._browse_pdf,
            fg_color=PAPER,
            hover_color="#322a23",
            border_width=1,
            border_color=LINE,
            text_color=INK,
        ).grid(row=0, column=1)

        self._field_label(panel, "Text in", 1)
        self.source_switch = ctk.CTkSegmentedButton(
            panel,
            values=["English", "Ελληνικά"],
            command=self._on_source,
            font=ctk.CTkFont(size=14),
            selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            unselected_color=PAPER,
            unselected_hover_color="#322a23",
            text_color=INK,
        )
        self.source_switch.set("English")
        self.source_switch.grid(row=1, column=1, sticky="ew", padx=12, pady=6)

        self._field_label(panel, "Read in", 2)
        self.output_switch = ctk.CTkSegmentedButton(
            panel,
            values=["English", "Ελληνικά"],
            command=self._on_output,
            font=ctk.CTkFont(size=14),
            selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            unselected_color=PAPER,
            unselected_hover_color="#322a23",
            text_color=INK,
        )
        self.output_switch.set("English")
        self.output_switch.grid(row=2, column=1, sticky="ew", padx=12, pady=6)

        self.note = ctk.CTkLabel(
            panel,
            text="",
            font=ctk.CTkFont(size=12),
            text_color=MUTED,
            anchor="w",
            wraplength=560,
            justify="left",
        )
        self.note.grid(row=3, column=1, sticky="ew", padx=12, pady=(0, 4))

        self._field_label(panel, "Voice", 4)
        self.voice_menu = ctk.CTkOptionMenu(
            panel,
            variable=self.voice_name,
            values=self._voice_labels("en"),
            command=self._on_voice_pick,
            fg_color=PAPER,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            dropdown_fg_color=PANEL,
            text_color=INK,
            font=ctk.CTkFont(size=14),
        )
        self.voice_menu.grid(row=4, column=1, sticky="ew", padx=12, pady=6)

        self._field_label(panel, "Length", 5)
        self.scope_switch = ctk.CTkSegmentedButton(
            panel,
            values=["Full document", "First part"],
            command=self._on_scope,
            font=ctk.CTkFont(size=14),
            selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            unselected_color=PAPER,
            unselected_hover_color="#322a23",
            text_color=INK,
        )
        self.scope_switch.set("Full document")
        self.scope_switch.grid(row=5, column=1, sticky="ew", padx=12, pady=6)

        self._field_label(panel, "Export", 6)
        self.export_switch = ctk.CTkSegmentedButton(
            panel,
            values=["Chapters", "Single file"],
            command=self._on_export,
            font=ctk.CTkFont(size=14),
            selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            unselected_color=PAPER,
            unselected_hover_color="#322a23",
            text_color=INK,
        )
        self.export_switch.set("Chapters")
        self.export_switch.grid(row=6, column=1, sticky="ew", padx=12, pady=6)

        self._field_label(panel, "Save to", 7)
        path_row = ctk.CTkFrame(panel, fg_color="transparent")
        path_row.grid(row=7, column=1, sticky="ew", padx=12, pady=(6, 16))
        path_row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(
            path_row,
            textvariable=self.output_root,
            fg_color=PAPER,
            border_color=LINE,
            text_color=INK,
            font=ctk.CTkFont(size=13),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            path_row,
            text="Browse",
            width=90,
            command=self._browse_folder,
            fg_color=PAPER,
            hover_color="#322a23",
            border_width=1,
            border_color=LINE,
            text_color=INK,
        ).grid(row=0, column=1)

        actions = ctk.CTkFrame(root, fg_color="transparent")
        actions.pack(fill="x", pady=(16, 8))
        self.start_button = ctk.CTkButton(
            actions,
            text="Create audiobook",
            command=self._start,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="#fff8f3",
            font=ctk.CTkFont(size=15, weight="bold"),
            height=40,
            width=180,
        )
        self.start_button.pack(side="left")
        self.stop_button = ctk.CTkButton(
            actions,
            text="Stop",
            command=self._stop,
            state="disabled",
            fg_color=PAPER,
            hover_color="#322a23",
            border_width=1,
            border_color=LINE,
            text_color=INK,
            height=40,
            width=90,
        )
        self.stop_button.pack(side="left", padx=8)
        ctk.CTkButton(
            actions,
            text="Open folder",
            command=self._open_folder,
            fg_color=PAPER,
            hover_color="#322a23",
            border_width=1,
            border_color=LINE,
            text_color=INK,
            height=40,
            width=120,
        ).pack(side="left")

        self.progress = ctk.CTkProgressBar(root, progress_color=ACCENT, fg_color=LINE)
        self.progress.pack(fill="x", pady=(8, 4))
        self.progress.set(0)
        self.status = ctk.CTkLabel(
            root,
            text="Choose a PDF. The text language and the narration language are separate.",
            font=ctk.CTkFont(size=13),
            text_color=MUTED,
            anchor="w",
        )
        self.status.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(
            root,
            text="Recordings",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=INK,
            anchor="w",
        ).pack(fill="x")
        self.chapter_list = ctk.CTkScrollableFrame(root, fg_color=PANEL, corner_radius=12, height=200)
        self.chapter_list.pack(fill="both", expand=True, pady=(6, 0))

    def _field_label(self, parent, text: str, row: int) -> None:
        ctk.CTkLabel(
            parent,
            text=text,
            font=ctk.CTkFont(size=13),
            text_color=MUTED,
            anchor="w",
            width=90,
        ).grid(row=row, column=0, sticky="nw", padx=(16, 0), pady=10)

    def _voice_labels(self, language: str) -> list[str]:
        return [label for _, label in LANGUAGES[language]["voices"]]

    def _show_voices(self) -> None:
        language = self.output_language.get()
        labels = self._voice_labels(language)
        self.voice_menu.configure(values=labels)
        current = voice_label(language, self.voice.get())
        if current not in labels:
            self.voice.set(default_voice(language))
            current = voice_label(language, self.voice.get())
        self.voice_name.set(current)
        self.voice_menu.set(current)

    def _voice_id_from_label(self, label: str) -> str:
        language = self.output_language.get()
        for voice_id, voice_name in LANGUAGES[language]["voices"]:
            if voice_name == label:
                return voice_id
        return default_voice(language)

    def _refresh_note(self) -> None:
        source = LANGUAGES[self.source_language.get()]["label"]
        target = LANGUAGES[self.output_language.get()]["label"]
        if self.source_language.get() == self.output_language.get():
            text = f"The PDF is read aloud in {target}."
        else:
            text = f"The PDF is translated from {source} into {target}, then read aloud."
        self.note.configure(text=text)

    def _on_source(self, value: str) -> None:
        self.source_language.set(CHOICES[value])
        self._refresh_note()
        self._refresh_chapters()

    def _on_output(self, value: str) -> None:
        self.output_language.set(CHOICES[value])
        self.voice.set(default_voice(self.output_language.get()))
        self._show_voices()
        self._refresh_note()
        self._refresh_chapters()

    def _on_voice_pick(self, label: str) -> None:
        self.voice.set(self._voice_id_from_label(label))
        self._refresh_chapters()

    def _on_scope(self, value: str) -> None:
        self.scope.set("first" if value == "First part" else "full")

    def _on_export(self, value: str) -> None:
        self.export.set("single" if value == "Single file" else "parts")
        self._refresh_chapters()

    def _browse_pdf(self) -> None:
        chosen = filedialog.askopenfilename(
            title="Choose a PDF",
            filetypes=[("PDF files", "*.pdf")],
            initialdir=str(Path(self.pdf_path.get()).parent) if self.pdf_path.get() else str(APP_DIR),
        )
        if chosen:
            self.pdf_path.set(chosen)
            self._refresh_chapters()

    def _browse_folder(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.output_root.get() or str(APP_DIR))
        if chosen:
            self.output_root.set(chosen)
            self._refresh_chapters()

    def _current_folder(self) -> Path | None:
        raw = self.pdf_path.get().strip()
        if not raw:
            return None
        pdf = Path(raw)
        root = Path(self.output_root.get().strip() or APP_DIR / "audiobooks")
        return output_dir_for(
            root,
            pdf,
            self.source_language.get(),
            self.output_language.get(),
            self.voice.get(),
            self.export.get(),
        )

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        pdf = Path(self.pdf_path.get().strip())
        if pdf.suffix.lower() != ".pdf" or not pdf.is_file():
            messagebox.showerror("PDF Audiobook", "Choose a PDF file first.")
            return
        folder = Path(self.output_root.get().strip())
        if not str(folder):
            messagebox.showerror("PDF Audiobook", "Choose a folder to save the audiobook.")
            return
        self.cancel_event.clear()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.progress.set(0)
        self.status.configure(text="Starting...")
        source = self.source_language.get()
        target = self.output_language.get()
        voice = self.voice.get()
        scope = self.scope.get()
        export = self.export.get()

        def run() -> None:
            try:
                result = build_audiobook(
                    pdf,
                    source,
                    target,
                    voice,
                    folder,
                    scope,
                    lambda fraction, message: self.events.put(("progress", fraction, message)),
                    lambda message: self.events.put(("status", message)),
                    self.cancel_event.is_set,
                    export,
                )
                self.events.put(("done", str(result)))
            except Cancelled:
                self.events.put(("cancelled",))
            except Exception as error:
                self.events.put(("error", str(error)))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _stop(self) -> None:
        self.cancel_event.set()
        self.status.configure(text="Stopping after the current passage...")

    def _open_folder(self) -> None:
        folder = self._current_folder()
        if folder is None:
            messagebox.showerror("PDF Audiobook", "Choose a PDF file first.")
            return
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)  # type: ignore[attr-defined]

    def _refresh_chapters(self) -> None:
        for child in self.chapter_list.winfo_children():
            child.destroy()
        folder = self._current_folder()
        files = []
        if folder and folder.exists():
            files = sorted(path for path in folder.glob("*.mp3") if not path.name.startswith("."))
        if not files:
            ctk.CTkLabel(
                self.chapter_list,
                text="No audio yet for this PDF and voice.",
                text_color=MUTED,
                anchor="w",
            ).pack(fill="x", padx=12, pady=10)
            return
        for file in files:
            ctk.CTkButton(
                self.chapter_list,
                text=file.stem,
                anchor="w",
                fg_color="transparent",
                hover_color="#322a23",
                text_color=INK,
                command=lambda path=file: os.startfile(path),  # type: ignore[attr-defined]
            ).pack(fill="x", padx=6, pady=1)

    def _drain(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "progress":
                    _, fraction, message = event
                    self.progress.set(max(0.0, min(1.0, fraction)))
                    self.status.configure(text=message)
                elif kind == "status":
                    self.status.configure(text=event[1])
                elif kind == "done":
                    self.progress.set(1)
                    self.status.configure(text=f"Audiobook ready · {event[1]}")
                    self._idle()
                    self._refresh_chapters()
                elif kind == "cancelled":
                    self.status.configure(text="Stopped. Saved parts are kept.")
                    self._idle()
                    self._refresh_chapters()
                elif kind == "error":
                    self.status.configure(text="Something went wrong.")
                    self._idle()
                    messagebox.showerror("PDF Audiobook", event[1])
        except queue.Empty:
            pass
        self.after(120, self._drain)

    def _idle(self) -> None:
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def _on_close(self) -> None:
        self.cancel_event.set()
        self.destroy()


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log = APP_DIR / "error.log"
        import traceback

        log.write_text(traceback.format_exc(), encoding="utf-8")
        raise
