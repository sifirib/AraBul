"""
AraBul - PDF Search Application
Author: Original author unknown
Contact: ahusrevceker+arabul@gmail.com
Version: 1.11
Date: May 31, 2025
Description: A desktop application to search text within PDF files, with highlighting capability.
License: All rights reserved by Prof. Dr. Ebubekir Sifil. This software may not be copied, distributed, or modified without explicit permission.

This application allows users to search for text within PDF files in a specified directory,
with features such as exact matching, text highlighting, and a themed interface.
"""

from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import os
import re
import time
import unicodedata
import threading
import logging
from logging.handlers import RotatingFileHandler
import json
import tempfile
import arabic_reshaper

import pymupdf
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import sv_ttk

CONFIG_FILE = os.path.join(os.getcwd(), "appdata", "config.json")
LOG_FILE = os.path.join(os.getcwd(), "appdata", "app.log")
SUMATRAPDF_FILE = os.path.join(os.getcwd(), "appdata", "SumatraPDF-3.5.2-64.exe")

OS = platform.system()

# ---- Logging Setup ----
logger = logging.getLogger('arabul_app')
logger.setLevel(logging.DEBUG)
handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

def load_config() -> dict:
    """Load configuration from a file and validate required keys."""
    config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:  # Specify utf-8 encoding
                config = json.load(f)
        except (FileNotFoundError, PermissionError) as e:
            logger.error(f"File access error: {e}")
        except json.JSONDecodeError as e:
            logger.error(f"JSON parsing error in config file: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error while loading config: {e}")
    
    # Ensure required keys exist with default values
    config.setdefault("default_folder", os.path.join(os.getcwd(), "pdfs"))
    config.setdefault("font_size", PDFSearchApp.DEFAULT_FONT_SIZE)
    config.setdefault("theme", PDFSearchApp.LIGHT_THEME)
    config.setdefault("search_history", [])
    
    # Validate default_folder
    if not os.path.isdir(config["default_folder"]):
        logger.warning(f"Invalid default_folder path: {config['default_folder']}. Resetting to current working directory.")
        config["default_folder"] = os.path.join(os.getcwd(), "pdfs")
    
    return config

def save_config(config: dict) -> None:
    """Save configuration to a file using atomic saves."""
    temp_file = CONFIG_FILE + ".tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:  # Specify utf-8 encoding
            json.dump(config, f, indent=4, ensure_ascii=False)
        os.replace(temp_file, CONFIG_FILE)
    except (FileNotFoundError, PermissionError) as e:
        logger.error(f"File access error during config save: {e}")
    except TypeError as e:
        logger.error(f"Data serialization error during config save: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error saving config: {e}")

class ToolTip:
    """Creates a tooltip for a given widget"""
    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self.tipwindow = None
        self.id = None
        self.waittime = 500  # ms
        widget.bind("<Enter>", self._enter)
        widget.bind("<Leave>", self._leave)

    def _enter(self, event=None):
        self._schedule()

    def _leave(self, event=None):
        self._unschedule()
        self._hide_tip()

    def _schedule(self):
        self._unschedule()
        self.id = self.widget.after(self.waittime, self._show_tip)

    def _unschedule(self):
        if self.id:
            self.widget.after_cancel(self.id)
            self.id = None

    def _show_tip(self):
        if self.tipwindow or not self.text:
            return
        x = self.widget.winfo_pointerx() + 10
        y = self.widget.winfo_pointery() + 10
        tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.configure(bg="#ffffe0")
        label = tk.Label(
            tw,
            text=self.text,
            justify=tk.LEFT,
            background="#ffffe0",
            foreground="#000000",
            relief=tk.SOLID,
            borderwidth=1,
            font=("TkDefaultFont", 8)
        )
        label.pack(ipadx=1, ipady=1)
        self.tipwindow = tw

    def _hide_tip(self):
        if self.tipwindow:
            self.tipwindow.destroy()
            self.tipwindow = None

class PlaceholderEntry(ttk.Entry):
    """An Entry widget with placeholder text."""
    def __init__(self, master=None, placeholder="", placeholder_color="gray", *args, **kwargs):
        super().__init__(master, *args, **kwargs)
        self.placeholder = placeholder
        self.placeholder_color = placeholder_color
        self.default_fg_color = self["foreground"]
        self._has_placeholder = True
        self.bind("<Key>", self._clear_placeholder)  # Remove placeholder on typing
        self.bind("<FocusOut>", self._add_placeholder)
        self.bind("<FocusIn>", self._focus_in)  # Handle focus in
        self._add_placeholder()

    def _clear_placeholder(self, event=None):
        if self._has_placeholder:
            self.delete(0, tk.END)
            self["foreground"] = self.default_fg_color
            self._has_placeholder = False

    def _add_placeholder(self, event=None):
        if not self.get():  # Check if the entry is empty
            self.delete(0, tk.END)  # Clear any existing text
            self.insert(0, self.placeholder)
            self["foreground"] = self.placeholder_color
            self._has_placeholder = True

    def _focus_in(self, event=None):
        if self._has_placeholder:
            self.icursor(0)  # Place the cursor at the beginning

    def get(self):
        """Override get method to return an empty string if the placeholder is active."""
        if self._has_placeholder:
            return ""
        return super().get()

def get_pdf_files(folder: str) -> list[Path]:
    """Get all PDF files in the specified folder and subfolders."""
    folder_path = Path(folder)
    return list(folder_path.glob('**/*.pdf'))

HYPHENS = (
    '\u00AD',  # SOFT HYPHEN (U+00AD)
    '\u002D',  # HYPHEN-MINUS (U+002D)
    '\u2010',  # HYPHEN (U+2010)
    '\u2011',  # NON-BREAKING HYPHEN (U+2011)
    '\u2012',  # FIGURE DASH (U+2012)
    '\u2013',  # EN DASH (U+2013)
    '\u2014',  # EM DASH (U+2014)
    '\u2015',  # HORIZONTAL BAR (U+2015)
)

def normalize(text: str,
              lowercase: bool = True,
              remove_accents: bool = True,
              remove_whitespaces: bool = True) -> str:
    if not text: return ''
    result = ''.join(
        c for c in text
        if not (unicodedata.category(c) in ('Cf', 'Cc', 'Zs', 'Mn') and c != ' ')
    )
    for hyphen in HYPHENS:
        result = result.replace(hyphen, '')
    result = result.replace('\xad', '').replace('\ufeff', '').replace('\u200f', '')
    result = re.sub(r'­\n|-\n|\n', '', result)
    if remove_whitespaces:
        result = re.sub(r'\s+', ' ', result).strip()
    if remove_accents:
        nfkd = unicodedata.normalize('NFKD', result)
        result = ''.join(c for c in nfkd if not unicodedata.combining(c))
    if lowercase:
        result = result.lower()

    result = arabic_reshaper.reshape(result)  # Reshape Arabic text

    return result

def bond_hyphenated_words(words: list[tuple[str, pymupdf.Rect]]) -> list[tuple[str, pymupdf.Rect]]:
    i = 0
    while i < len(words) - 1:
        word, rect = words[i]
        if word.endswith(HYPHENS):
            next_word, _ = words.pop(i + 1)
            words[i] = (word[:-1] + next_word, rect)
        else:
            i += 1
    return words

def highlight(page: pymupdf.Page, rect: pymupdf.Rect) -> None:
    """Highlight a rectangle on a PDF page."""
    annot = page.add_highlight_annot(rect)
    annot.update()

def get_pdf_text(pdf_path, page):
    """Get PDF text without caching."""
    try:
        return page.get_text("text")
    except pymupdf.fitz.FileDataError as e:
        logger.error(f"PDF parsing error in {pdf_path} page {page.number+1}: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error extracting text from {pdf_path} page {page.number+1}: {e}")
    return ""

def handle_exception(func):
    """Decorator to handle exceptions consistently."""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (FileNotFoundError, PermissionError) as e:
            logger.error(f"File access error in {func.__name__}: {e}")
            messagebox.showerror("Error", f"File access issue: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error in {func.__name__}: {e}")
            messagebox.showerror("Error", f"An unexpected error occurred: {e}")
    return wrapper

@handle_exception
def search_text_in_pdf(pdf_path: str, search_term: str, exact_match: bool) -> list[tuple[str, int, list[pymupdf.Rect], str]]:
    matches: list[tuple[str, int, list[pymupdf.Rect], str]] = []
    pdf = pymupdf.open(pdf_path)
    with pdf:
        term = normalize(search_term)
        for page in pdf:
            try:
                raw = get_pdf_text(pdf_path, page)  # Use cached text retrieval
                page_text = normalize(raw)
            except Exception:
                logger.exception(f"{pdf_path} dosyasının {page.number+1}. sayfasından metin çıkarılamadı.")
                continue
            index = page_text.find(term)
            if index == -1:
                continue
            # build snippet around match
            start = max(0, index - 30)
            end = index + len(term) + 30
            snippet = f"...{raw[start:end]}..."
            try:
                words = [(w[4], pymupdf.Rect(*w[:4])) for w in page.get_text("words")]
            except Exception:
                logger.exception(f"{pdf_path} dosyasının {page.number+1}. sayfasından kelimeler alınamadı.")
                continue
            word_blocks = bond_hyphenated_words(words)
            n = len(term.split())
            for i in range(len(word_blocks) - n + 1):
                seq = ' '.join(normalize(w[0]) for w in word_blocks[i:i+n])
                if (term == seq if exact_match else term in seq):
                    rects = [w[1] for w in word_blocks[i:i+n]]
                    matches.append((os.path.basename(pdf_path), page.number+1, rects, " ".join(snippet.splitlines())))
    return matches

class PDFSearchApp:
    # Class constants
    DEFAULT_WINDOW_WIDTH = 700
    DEFAULT_WINDOW_HEIGHT = 768
    DEFAULT_WINDOW_X = 100
    DEFAULT_WINDOW_Y = 100
    DEFAULT_FONT_SIZE = 11
    HIGHLIGHT_COLOR = "#FF6347"
    DARK_THEME = "dark"
    LIGHT_THEME = "light"
    ICON_THEME_BUTTON = "🔆"
    GEMI_IMAGE_PATH = os.path.join(os.getcwd(), "appdata", "assets", "minigemi.png")
    ICON_PATH = os.path.join(os.getcwd(), "appdata", "assets", "icon.ico")
    HIGHLIGHTED_PDFS_DIR = tempfile.mkdtemp()
    MAX_HISTORY_SIZE = 100

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.config = load_config()
        self.root.title("AraBul")
        self.style = ttk.Style()
        
        # Set application icon
        if os.path.exists(self.ICON_PATH) and OS == "Windows":
            self.root.iconbitmap(self.ICON_PATH)
        
        self._apply_window_settings()
        self._apply_theme()
        self.results: list[tuple[str, int, list[pymupdf.Rect], str]] = []
        self._cancel_event = threading.Event()
        self.opened_viewers = []  # Track opened PDF viewers
        default_folder = self.config.get("default_folder", os.path.join(os.getcwd(), "pdfs"))
        self.font_size = self.config.get("font_size", self.DEFAULT_FONT_SIZE)
        self.exact_match = tk.BooleanVar(value=False)
        # Initialize search history
        self.search_history = self.config.get("search_history", [])
        # Track column sorting
        self.sort_column = None
        self.sort_reverse = False
        self._build_ui(default_folder)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)  # Handle app close
    
    def _on_close(self) -> None:
        """Close all opened PDF viewers, clean up temporary files, and exit the application."""
        for process in self.opened_viewers:
            if process.poll() is None:  # Check if the process is still running
                process.terminate()
        shutil.rmtree(self.HIGHLIGHTED_PDFS_DIR, ignore_errors=True)
        self.root.destroy()

    def _apply_window_settings(self) -> None:
        """Apply saved window size and position."""
        width = self.config.get("window_width", self.DEFAULT_WINDOW_WIDTH)
        height = self.config.get("window_height", self.DEFAULT_WINDOW_HEIGHT)
        x = self.config.get("window_x", self.DEFAULT_WINDOW_X)
        y = self.config.get("window_y", self.DEFAULT_WINDOW_Y)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.bind("<Configure>", self._save_window_settings)

    def _save_window_settings(self, event) -> None:
        """Save window size and position to the config."""
        if event.widget == self.root:
            self.config["window_width"] = self.root.winfo_width()
            self.config["window_height"] = self.root.winfo_height()
            self.config["window_x"] = self.root.winfo_x()
            self.config["window_y"] = self.root.winfo_y()
            save_config(self.config)

    def _apply_theme(self) -> None:
        """Apply saved theme preference."""
        theme = self.config.get("theme", self.DARK_THEME)
        if theme == self.DARK_THEME:
            sv_ttk.use_dark_theme()
        else:
            sv_ttk.use_light_theme()

    def toggle_theme(self) -> None:
        """Toggle between dark and light themes."""
        current_theme = self.config.get("theme", self.LIGHT_THEME)
        new_theme = self.LIGHT_THEME if current_theme == self.DARK_THEME else self.DARK_THEME
        self.config["theme"] = new_theme
        save_config(self.config)
        self._apply_theme()

        self.search_entry.config(foreground=(
            "gray" if self.search_var.get() == self.search_entry_placeholder
            else "white" if new_theme == self.DARK_THEME 
            else "black" 
        ))
        
        self.style.map('Treeview', background=[('selected', self.HIGHLIGHT_COLOR)])

    def _build_ui(self, default_folder: str) -> None:
        # Create a frame for the UI elements
        frame = ttk.Frame(self.root)
        frame.pack(pady=10, fill=tk.BOTH, expand=True)

        if os.path.exists(self.GEMI_IMAGE_PATH):
            self.gemi_image = tk.PhotoImage(file=self.GEMI_IMAGE_PATH)
            # Add gemi.png image before the search entry
            gemi_label = tk.Label(frame, image=self.gemi_image)
            gemi_label.pack(pady=5)
            ToolTip(gemi_label, (
                "1930-1940'lardan tekke işi Ashab-ı Kehf yazılı cam altı Amentü gemisi.\n"
                "Bayraklarda; \"La ilahe illallah Muhammeden Resulallah (s.a.v.)\" (Kelime-i Tevhid) ve \"Maşallah\" yazısı,\n"
                "Yelkenlerde; \"Ya Malik-ül Mülk\" ve \"İnna fetahna leke fethan mubiyna\" yazısı,\n"
                "Gemi gövdesinde ise: \"Yemliha, Mislina, Mekselina, Mernuş, Debernuş, Şazenuş, Kefeştetayyuş ve Kıtmir\" "
                "(Ashab-ı Kehf'in isimleri) yazılıdır."
            ))
            
        # Combine search entry and search history into a single combobox with placeholder logic
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Combobox(
            frame,
            textvariable=self.search_var,
            width=45,
            postcommand=self._update_search_history_dropdown
        )
        self.search_entry.pack(pady=5)
        self.search_entry.bind("<Return>", lambda e: self.start_search())
        self.search_entry.bind("<<ComboboxSelected>>", self._on_history_selected)
        self.search_entry.bind("<Control-a>", self._select_all)
        ToolTip(self.search_entry, "Aramak istediğiniz metni giriniz veya geçmişten seçiniz.")
        self._update_search_history_dropdown()  # Initially populate the dropdown

        # Placeholder logic for the search entry
        self.search_entry_placeholder = "Aramak istediğiniz metni giriniz..."
        self.search_entry.bind("<FocusIn>", self._clear_placeholder)
        self.search_entry.bind("<FocusOut>", self._add_placeholder)
        self._add_placeholder()

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=5)

        self.browse_button = ttk.Button(btn_frame, text="Dizin Seç", command=self.browse_folder)
        self.browse_button.pack(side=tk.LEFT, padx=5)
        ToolTip(self.browse_button, f"PDF'lerinizin olduğu klasörü seçiniz.")

        self.search_button = ttk.Button(btn_frame, text="Bul", command=self.start_search)
        self.search_button.pack(side=tk.LEFT, padx=5)
        self.cancel_button = ttk.Button(btn_frame, text="Durdur", command=self.cancel_search, state=tk.DISABLED)
        self.cancel_button.pack(side=tk.LEFT, padx=5)
        self.exact_match_checkbox = ttk.Checkbutton(
            btn_frame, text="Tam Eşleşme", variable=self.exact_match
        )
        self.exact_match_checkbox.pack(side=tk.LEFT, padx=5)
        self.theme_button = ttk.Button(btn_frame, text=self.ICON_THEME_BUTTON, width=2, command=self.toggle_theme)
        self.theme_button.pack(side=tk.RIGHT, padx=5)
        ToolTip(self.theme_button, "Tema arasında geçiş yapar (Koyu/Açık).")
        ToolTip(self.search_button, "Aramayı başlatır.")
        ToolTip(self.cancel_button, "Devam eden aramayı durdurur.")
        ToolTip(self.exact_match_checkbox, "Tam eşleşme araması yapar. Örneğin, 'kitap' arandığında 'kitaplık' eşleşmez.")

        self.count_label = ttk.Label(
            frame, font=("TkDefaultFont", self.font_size, "bold"), foreground=self.HIGHLIGHT_COLOR
        )
        self.count_label.pack(pady=5)
        self.time_label = ttk.Label(
            frame, text="", font=("TkDefaultFont", self.font_size, "bold"), foreground=self.HIGHLIGHT_COLOR
        )
        self.time_label.pack(pady=5)

        self.progress = ttk.Progressbar(frame, orient=tk.HORIZONTAL, length=400, mode='determinate')
        self.progress.pack(pady=5)
        
        ### Treeview
        # self.style.configure("Treeview", background="#E1E1E1", foreground="#000000", rowheight=25, fieldbackground="#E1E1E1")
        self.style.map('Treeview', background=[('selected', self.HIGHLIGHT_COLOR)])
        rf = ttk.Frame(frame)
        rf.pack(pady=10, fill=tk.BOTH, expand=True)
        columns = ("No", "Kaynak", "Eşleşme")
        self.tree = ttk.Treeview(rf, columns=columns, show="headings", height=14)
        self.tree.heading("No", text="No", command=lambda: self._sort_tree("No", int))
        self.tree.heading("Kaynak", text="Kaynak", command=lambda: self._sort_tree("Kaynak", str))
        self.tree.heading("Eşleşme", text="Eşleşme", command=lambda: self._sort_tree("Eşleşme", str))
        self.tree.column("No", width=50, anchor=tk.CENTER)
        self.tree.column("Kaynak", width=300, anchor=tk.W)
        self.tree.column("Eşleşme", width=300, anchor=tk.W)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        sb = ttk.Scrollbar(rf, orient=tk.VERTICAL, command=self.tree.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=sb.set)

        self.tree.bind("<Double-1>", self.open_selected)
        self.tree.bind("<Return>", self.open_selected)
        self.tree.bind("<Button-3>", self._show_menu)  # Bind right-click to show the context menu

        self._create_context_menu()

        self.debug = tk.Text(frame, height=2, state=tk.DISABLED)
        self.debug.pack(fill=tk.X, padx=5, pady=(0, 5))
        self.debug_log("Program başlatıldı.")

    def _add_placeholder(self, event=None):
        """Add placeholder text to the search entry if it's empty."""
        if not self.search_var.get():
            self.search_entry.set(self.search_entry_placeholder)
            self.search_entry.config(foreground="gray")

    def _clear_placeholder(self, event=None):
        """Clear placeholder text when the search entry gains focus."""
        if self.search_var.get() == self.search_entry_placeholder:
            self.search_entry.set("")
            current_theme = self.config.get("theme", self.LIGHT_THEME)
            self.search_entry.config(foreground="white" if current_theme == self.DARK_THEME else "black")


    def _update_search_history_dropdown(self):
        """Update the search history dropdown."""
        self.search_entry['values'] = self.search_history

    def _on_history_selected(self, event):
        """Handle search history selection."""
        selected = self.search_var.get()
        if selected:
            self.search_entry.icursor(tk.END)  # Place cursor at the end of the selected text
            self.search_entry.focus_set()

    def _add_to_search_history(self, term: str):
        """Add a term to the search history."""
        if not term or term == self.search_entry_placeholder:
            return
            
        # Remove the term if it already exists to avoid duplicates
        if term in self.search_history:
            self.search_history.remove(term)
            
        # Add the term to the beginning of the list
        self.search_history.insert(0, term)
        
        # Keep the history at a reasonable size
        if len(self.search_history) > self.MAX_HISTORY_SIZE:
            self.search_history = self.search_history[:self.MAX_HISTORY_SIZE]
            
        # Save to config
        self.config["search_history"] = self.search_history
        save_config(self.config)
        
        # Update the dropdown
        self._update_search_history_dropdown()

    def debug_log(self, msg: str):
        timestamp = time.strftime('%H:%M:%S')
        self.debug.config(state=tk.NORMAL)
        self.debug.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.debug.see(tk.END)
        self.debug.config(state=tk.DISABLED)
        logger.info(msg)

    def browse_folder(self) -> None:
        folder = filedialog.askdirectory()
        if folder:
            self.config["default_folder"] = folder
            save_config(self.config)
            self.debug_log(f"Seçilen klasör: {folder}")

    def start_search(self) -> None:
        folder = self.config.get("default_folder", "")
        term = self.search_var.get()
        if not os.path.isdir(folder):
            self.count_label.config(text="Lütfen geçerli bir klasör seçin.")
            return
        if not term or term == self.search_entry_placeholder:
            self.count_label.config(text="Lütfen bir arama terimi girin.")
            return

        # Add the term to search history
        self._add_to_search_history(term)

        self._cancel_event.clear()
        self.search_button.config(state=tk.DISABLED)
        self.cancel_button.config(state=tk.NORMAL)
        self._set_busy(True)
        self.count_label.config(text="Aranıyor...")
        self.debug_log(f"'{folder}' dizininde '{term}' araması başlatıldı.")
        exact_match = self.exact_match.get()
        threading.Thread(target=self._run_search, args=(folder, term, exact_match), daemon=True).start()

    def cancel_search(self) -> None:
        self._cancel_event.set()
        self.cancel_button.config(state=tk.DISABLED)
        self.count_label.config(text="İptal edildi.")
        self.debug_log("Arama kullanıcı tarafından durduruldu.")
        self._set_busy(False)

    def _run_search(self, folder: str, term: str, exact_match: bool) -> None:
        total = 0
        start = time.time()
        pdf_list = get_pdf_files(folder)
        if not pdf_list:
            self.root.after(0, lambda: self.count_label.config(
                text="Seçili dizinde herhangi bir PDF dosyası bulunamadı."))
            self.root.after(0, lambda: self._finish_search(0))
            return

        self.results.clear()
        self.root.after(0, lambda: [
            self.progress.config(maximum=len(pdf_list), value=0),
            self.tree.delete(*self.tree.get_children())
        ])

        for index, path in enumerate(pdf_list, 1):
            if self._cancel_event.is_set():
                break
            try:
                matches = search_text_in_pdf(path, term, exact_match)
            except Exception:
                logger.exception(f"{path} işlenemedi.")
                self.root.after(0, lambda p=path: self.debug_log(f"{p} işlenemedi."))
                continue

            for src, page_num, rects, snippet in matches:
                if self._cancel_event.is_set():
                    break
                total += 1
                self.results.append((path, page_num, rects, snippet))
                title = os.path.splitext(os.path.basename(src))[0]
                self.root.after(0, lambda t=total, ti=title, pn=page_num, sn=snippet:
                                self.tree.insert("", "end", values=(t, f"{ti}, {pn}", sn)))

            elapsed = time.time() - start
            self.root.after(0, lambda v=index, e=elapsed: [
                self.progress.config(value=v),
                self.time_label.config(text=f"Geçen Süre: {e:.1f} saniye")
            ])

        self.root.after(0, lambda: self._finish_search(total))

    def _finish_search(self, total: int = 0) -> None:
        msg = f"{total} eşleşme bulundu." if total > 0 else "Eşleşme bulunamadı."
        self.count_label.config(text=msg)
        self.search_button.config(state=tk.NORMAL)
        self.cancel_button.config(state=tk.DISABLED)
        self.debug_log(f"Arama tamamlandı: {msg}")
        self._set_busy(False)

    def open_selected(self, event) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        index = self.tree.index(sel[0])
        path, page_num, rects, _snippet = self.results[index]

        def process_pdf():
            try:
                with pymupdf.open(path) as pdf:
                    page = pdf[page_num - 1]
                    for r in rects:
                        try:
                            highlight(page, r)
                        except Exception as h_err:
                            logger.warning(f"Metin vurgulanamadı: {h_err} — rect: {r}")
                            continue  # Diğer rect'leri denemeye devam et
                    out_path = os.path.join(self.HIGHLIGHTED_PDFS_DIR, os.path.basename(path))
                    pdf.save(out_path)
                self.open_pdf_viewer(out_path, page_num)  # PDF her durumda açılır
            except Exception as e:
                logger.exception(f"{path} işlenirken hata oluştu.")
                self.root.after(0, lambda e=e: messagebox.showerror("Hata", f"PDF işlenirken hata oluştu: {e}"))

        threading.Thread(target=process_pdf, daemon=True).start()

    @handle_exception
    def open_pdf_viewer(self, pdf_path: str, page_number: int) -> None:
        """Open a PDF viewer and track the process."""
        pdf_path = os.path.normpath(pdf_path)
        if OS == "Linux":
            process = subprocess.Popen(["mupdf", pdf_path, str(page_number)])
        elif OS == "Darwin":
            process = subprocess.Popen(["open", pdf_path])
        elif OS == "Windows":
            # !!! RememberOpenedFiles = false, RememberStatePerDocument = false, RestoreSession = false in SumatraPDF-settings.txt !!!
            process = subprocess.Popen([
                SUMATRAPDF_FILE, pdf_path, "-page", str(page_number), "-lang", "tr" 
            ])
        else:
            messagebox.showerror("Error", "Unsupported operating system.")
            return
        self.opened_viewers.append(process)  # Track the process

    def copy_reference(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0], "values")
        self.root.clipboard_clear()
        self.root.clipboard_append(values[1])
        self.count_label.config(text="Eşleşen metnin kaynağı panoya kopyalandı.")
        self.debug_log("Eşleşen metnin kaynağı panoya kopyalandı.")

    def _select_all(self, event) -> str:
        event.widget.select_range(0, tk.END)
        event.widget.icursor(tk.END)
        return "break"

    def _create_context_menu(self):
        """Create the right-click context menu for search results."""
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Aç", command=self._open_selected_item)
        self.menu.add_command(label="Kaynağı Kopyala", command=self.copy_reference)
        self.menu.add_command(label="Metni Kopyala", command=self._copy_snippet)
        self.menu.add_separator()
        self.menu.add_command(label="Kaynağı Dosya Gezgininde Göster", command=self._show_in_explorer)

    def _show_menu(self, event) -> None:
        """Display the context menu on right-click."""
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
            self.menu.post(event.x_root, event.y_root)
            self.root.bind("<Button-1>", lambda e: self.menu.unpost(), add="+")

    def _open_selected_item(self):
        """Open the selected item."""
        self.open_selected(None)

    def _copy_snippet(self):
        """Copy the snippet of the selected item to the clipboard."""
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0], "values")
        snippet = values[2]  # Snippet is the third column
        self.root.clipboard_clear()
        self.root.clipboard_append(snippet)
        self.count_label.config(text="Eşleşen metin panoya kopyalandı.")
        self.debug_log("Eşleşen metin panoya kopyalandı.")

    def _show_in_explorer(self):
        """Show the source file in the file explorer."""
        sel = self.tree.selection()
        if not sel:
            return
        index = self.tree.index(sel[0])
        path, _, _, _ = self.results[index]
        try:
            if OS == "Windows":
                subprocess.Popen(f'explorer /select,"{path}"')
            elif OS == "Darwin":
                subprocess.Popen(["open", "-R", path])
            elif OS == "Linux":
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
            else:
                messagebox.showerror("Hata", "Bu özellik desteklenmiyor.")
        except Exception as e:
            logger.exception(f"Dosya gezgininde gösterilemedi: {path}")
            messagebox.showerror("Hata", f"Dosya gezgininde gösterilemedi: {e}")

    def _set_busy(self, busy: bool) -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        cursor = "watch" if busy else ""
        self.search_entry.config(state=state)
        self.browse_button.config(state=state)
        self.search_button.config(state=state)
        self.exact_match_checkbox.config(state=state)
        self.theme_button.config(state=state)
        
        if busy:self.search_entry.unbind("<Return>")
        else:self.search_entry.bind("<Return>", lambda e: self.start_search())
        
        self.root.config(cursor=cursor)
        self.root.update()

    def _sort_tree(self, column: str, data_type: type) -> None:
        """Sort the Treeview by a given column with toggling sort direction."""
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = False
        
        # Display sort indicator in column heading
        for col in self.tree["columns"]:
            # Reset all headers
            self.tree.heading(col, text=col)
        
        # Update current column heading with indicator
        direction_indicator = " ↓" if self.sort_reverse else " ↑"
        self.tree.heading(column, text=f"{column}{direction_indicator}")
        
        # Get all items with their values for the selected column
        data = [(self.tree.set(child, column), child) for child in self.tree.get_children("")]
        
        # Sort data based on column type and direction
        try:
            if data_type == int:
                data.sort(key=lambda item: int(item[0]), reverse=self.sort_reverse)
            else:
                data.sort(key=lambda item: item[0], reverse=self.sort_reverse)
        except (ValueError, TypeError):
            # Fallback to string comparison if conversion fails
            data.sort(key=lambda item: str(item[0]), reverse=self.sort_reverse)
        
        # Rearrange items in sorted positions
        for index, (_, child) in enumerate(data):
            self.tree.move(child, "", index)
        
        self.debug_log(f"Sonuçlar '{column}' sütununa göre {'azalan' if self.sort_reverse else 'artan'} sırada sıralandı.")

if __name__ == "__main__":
    root = tk.Tk()
    PDFSearchApp(root)
    root.mainloop()