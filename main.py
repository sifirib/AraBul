"""
AraBul - PDF Search Application
Author: Original author unknown
Contact: ahusrevceker+arabul@gmail.com
Version: 1.17
Date: October 23, 2025
Description: A desktop application to search text within PDF files, with highlighting capability.
License: All rights reserved by Prof. Dr. Ebubekir Sifil. This software may not be copied, distributed, or modified without explicit permission.

This application allows users to search for text within PDF files in a specified directory,
with features such as exact matching, text highlighting and other cool features with a themed interface.
"""

from pathlib import Path
import platform
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
from collections import Counter

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
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (FileNotFoundError, PermissionError) as e:
            logger.error(f"File access error: {e}")
        except json.JSONDecodeError as e:
            logger.error(f"JSON parsing error in config file: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error while loading config: {e}")
    
    config.setdefault("default_folder", os.path.join(os.getcwd(), "pdfs"))
    config.setdefault("font_size", PDFSearchApp.DEFAULT_FONT_SIZE)
    config.setdefault("theme", PDFSearchApp.LIGHT_THEME)
    config.setdefault("search_history", [])
    
    if not os.path.isdir(config["default_folder"]):
        logger.warning(f"Invalid default_folder path: {config['default_folder']}. Resetting to current working directory.")
        config["default_folder"] = os.path.join(os.getcwd(), "pdfs")
    
    return config

def save_config(config: dict) -> None:
    """Save configuration to a file using atomic saves."""
    temp_file = CONFIG_FILE + ".tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
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
        self.waittime = 500
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
        tw.configure(bg="#2b2b2b")
        label = tk.Label(
            tw,
            text=self.text,
            justify=tk.LEFT,
            background="#2b2b2b",
            foreground="#ffffff",
            relief=tk.SOLID,
            borderwidth=1,
            font=("Segoe UI", 9),
            padx=8,
            pady=6
        )
        label.pack()
        self.tipwindow = tw

    def _hide_tip(self):
        if self.tipwindow:
            self.tipwindow.destroy()
            self.tipwindow = None

def get_pdf_files(folder: str) -> list[Path]:
    """Get all PDF files in the specified folder and subfolders."""
    folder_path = Path(folder)
    return list(folder_path.glob('**/*.pdf'))

HYPHENS = (
    '\u00AD',
    '\u002D',
    '\u2010',
    '\u2011',
    '\u2012',
    '\u2013',
    '\u2014',
    '\u2015',
)

def normalize(text: str,
              lowercase: bool = True,
              remove_accents: bool = True,
              remove_whitespaces: bool = True,
              remove_punctuation: bool = True) -> str:
    if not text:
        return ''
    result = ''.join(
        c for c in text
        if not (unicodedata.category(c) in ('Cf', 'Cc', 'Zs', 'Mn') and c != ' ')
    )
    for hyphen in HYPHENS:
        result = result.replace(hyphen, '')
    result = result.replace('\xad', '').replace('\ufeff', '').replace('\u200f', '')
    result = re.sub(r'­\n|-\n|\n', '', result)

    result = re.sub(r'([.,!?])([^\s])', r'\1 \2', result)

    if remove_whitespaces:
        result = re.sub(r'\s+', ' ', result).strip()
    if remove_accents:
        nfkd = unicodedata.normalize('NFKD', result)
        result = ''.join(c for c in nfkd if not unicodedata.combining(c))
    if remove_punctuation:
        result = re.sub(r'[\'",.:;!?()\[\]{}<>\\\/|`~@#$%^&*_+=]', '', result)
    if lowercase:
        result = result.lower()
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
            messagebox.showerror("Hata", f"Dosya erişim hatası: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error in {func.__name__}: {e}")
            messagebox.showerror("Hata", f"Beklenmeyen bir hata oluştu: {e}")
    return wrapper

@handle_exception
def search_text_in_pdf(pdf_path: str, search_term: str, exact_match: bool, unordered_match: bool = False, flexible_match: bool = False) -> list[tuple[str, int, list[pymupdf.Rect], str]]:
    matches: list[tuple[str, int, list[pymupdf.Rect], str]] = []
    pdf = pymupdf.open(pdf_path)
    with pdf:
        term = normalize(search_term)
        term_words = term.split()
        
        for page in pdf:
            try:
                raw = get_pdf_text(pdf_path, page)
                page_text = normalize(raw)
            except Exception:
                logger.exception(f"{pdf_path} dosyasının {page.number+1}. sayfasından metin çıkarılamadı.")
                continue
            
            # ESNEK EŞLEŞME MOD
            if flexible_match:
                num_terms = len(term_words)
                if num_terms < 2:
                    continue
                
                required_count = num_terms if num_terms <= 3 else 3
                
                term_counter = Counter(term_words)
                
                found_counter = Counter()
                for search_word in term_words:
                    if search_word in page_text:
                        found_counter[search_word] += 1
                
                total_found = 0
                for word in term_counter:
                    found = 1 if found_counter[word] > 0 else 0
                    total_found += found
                
                if total_found < required_count:
                    continue
                
                try:
                    words = [(w[4], pymupdf.Rect(*w[:4])) for w in page.get_text("words")]
                except Exception:
                    logger.exception(f"{pdf_path} dosyasının {page.number+1}. sayfasından kelimeler alınamadı.")
                    continue
                
                word_blocks = bond_hyphenated_words(words)
                
                all_rects = []
                rect_counter = Counter()
                
                for word, rect in word_blocks:
                    normalized_word = normalize(word)
                    for search_word in term_counter:
                        if search_word in normalized_word:
                            if rect_counter[search_word] < term_counter[search_word]:
                                all_rects.append(rect)
                                rect_counter[search_word] += 1
                                break
                
                first_found_idx = None
                for i, (word, rect) in enumerate(word_blocks):
                    normalized_word = normalize(word)
                    if any(sw in normalized_word for sw in term_words):
                        first_found_idx = i
                        break
                
                if first_found_idx is None:
                    continue
                
                start_idx = max(0, first_found_idx - 50)
                end_idx = min(len(word_blocks), first_found_idx + 50)
                snippet_words = [normalize(w[0]) for w in word_blocks[start_idx:end_idx]]
                snippet = ' '.join(snippet_words)
                
                if all_rects:
                    matches.append((os.path.basename(pdf_path), page.number+1, all_rects, snippet))
                continue
            
            # Orijinal arama modları
            if not unordered_match:
                index = page_text.find(term)
                if index == -1:
                    continue
                start = max(0, index - 50)
                end = index + len(term) + 50
                snippet = page_text[start:end]
            else:
                first_word = term_words[0]
                index = page_text.find(first_word)
                if index == -1:
                    continue
                start = max(0, index - 50)
                end = index + len(first_word) + 50
                snippet = page_text[start:end]

            try:
                words = [(w[4], pymupdf.Rect(*w[:4])) for w in page.get_text("words")]
            except Exception:
                logger.exception(f"{pdf_path} dosyasının {page.number+1}. sayfasından kelimeler alınamadı.")
                continue
            word_blocks = bond_hyphenated_words(words)
            n = len(term_words)
            for i in range(len(word_blocks) - n + 1):
                seq = ' '.join(normalize(w[0]) for w in word_blocks[i:i+n])

                if unordered_match:
                    if all(w in seq for w in term_words):
                        rects = [w[1] for w in word_blocks[i:i+n]]
                        matches.append((os.path.basename(pdf_path), page.number+1, rects, " ".join(snippet.splitlines())))
                else:
                    if (term == seq if exact_match else term in seq):
                        rects = [w[1] for w in word_blocks[i:i+n]]
                        matches.append((os.path.basename(pdf_path), page.number+1, rects, " ".join(snippet.splitlines())))
    return matches

class PDFSearchApp:
    DEFAULT_WINDOW_WIDTH = 900
    DEFAULT_WINDOW_HEIGHT = 850
    DEFAULT_WINDOW_X = 100
    DEFAULT_WINDOW_Y = 50
    DEFAULT_FONT_SIZE = 12
    HIGHLIGHT_COLOR = "#FF6347"
    DARK_THEME = "dark"
    LIGHT_THEME = "light"
    ICON_THEME_BUTTON = "🌙"
    ICON_THEME_BUTTON_LIGHT = "🔆"
    GEMI_IMAGE_PATH = os.path.join(os.getcwd(), "appdata", "assets", "minigemi.png")
    ICON_PATH = os.path.join(os.getcwd(), "appdata", "assets", "icon.ico")
    HIGHLIGHTED_PDFS_DIR = tempfile.mkdtemp()
    MAX_HISTORY_SIZE = 100

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.config = load_config()
        self.root.title("AraBul")
        self.style = ttk.Style()
        
        if os.path.exists(self.ICON_PATH) and OS == "Windows":
            self.root.iconbitmap(self.ICON_PATH)
        
        self._apply_window_settings()
        self._apply_theme()
        self._configure_styles()
        
        self.results: list[tuple[str, int, list[pymupdf.Rect], str]] = []
        self._cancel_event = threading.Event()
        self.opened_viewers = []
        default_folder = self.config.get("default_folder", os.path.join(os.getcwd(), "pdfs"))
        self.font_size = self.config.get("font_size", self.DEFAULT_FONT_SIZE)
        self.exact_match = tk.BooleanVar(value=False)
        self.unordered_match = tk.BooleanVar(value=False)
        self.flexible_match = tk.BooleanVar(value=False)
        self.search_history = self.config.get("search_history", [])
        self.sort_column = None
        self.sort_reverse = False
        self._build_ui(default_folder)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
    
    def _configure_styles(self):
        """Configure custom styles for better appearance."""
        # Custom button style
        self.style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        
        # Treeview style
        self.style.configure("Treeview", 
                           rowheight=28,
                           font=("Segoe UI", 10))
        self.style.configure("Treeview.Heading", 
                           font=("Segoe UI", 11, "bold"))
        
        # Progress bar style
        self.style.configure("TProgressbar", thickness=20)
    
    def _on_close(self) -> None:
        """Close all opened PDF viewers, clean up temporary files, and exit the application."""
        for process in self.opened_viewers:
            if process.poll() is None:
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
        self.root.minsize(800, 600)

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

        # Update theme button icon
        self.theme_button.config(text=self.ICON_THEME_BUTTON_LIGHT if new_theme == self.LIGHT_THEME else self.ICON_THEME_BUTTON)

        self.search_entry.config(foreground=(
            "gray" if self.search_var.get() == self.search_entry_placeholder
            else "white" if new_theme == self.DARK_THEME 
            else "black" 
        ))
        
        self.style.map('Treeview', background=[('selected', self.HIGHLIGHT_COLOR)])

    def _build_ui(self, default_folder: str) -> None:
        # Main container with padding
        main_container = ttk.Frame(self.root, padding="15 15 15 15")
        main_container.pack(fill=tk.BOTH, expand=True)

        # Header frame with image
        if os.path.exists(self.GEMI_IMAGE_PATH):
            header_frame = ttk.Frame(main_container)
            header_frame.pack(fill=tk.X, pady=(0, 15))
            
            self.gemi_image = tk.PhotoImage(file=self.GEMI_IMAGE_PATH)
            gemi_label = tk.Label(header_frame, image=self.gemi_image, cursor="hand2")
            gemi_label.pack()
            ToolTip(gemi_label, (
                "1930-1940'lardan tekke işi Ashab-ı Kehf yazılı cam altı Amentü gemisi.\n"
                "Bayraklarda; \"La ilahe illallah Muhammeden Resulallah (s.a.v.)\" (Kelime-i Tevhid) ve \"Maşallah\" yazısı,\n"
                "Yelkenlerde; \"Ya Malik-ül Mülk\" ve \"İnna fetahna leke fethan mubiyna\" yazısı,\n"
                "Gemi gövdesinde ise: \"Yemliha, Mislina, Mekselina, Mernuş, Debernuş, Şazenuş, Kefeştetayyuş ve Kıtmir\" "
                "(Ashab-ı Kehf'in isimleri) yazılıdır."
            ))
        
        # Search input frame
        search_frame = ttk.LabelFrame(main_container, text=" 🔍 Arama ", padding="10 10 10 10")
        search_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Combobox(
            search_frame,
            textvariable=self.search_var,
            font=("Segoe UI", 11),
            postcommand=self._update_search_history_dropdown
        )
        self.search_entry.pack(fill=tk.X, pady=(0, 10), ipady=5)
        self.search_entry.bind("<Return>", lambda e: self.start_search())
        self.search_entry.bind("<<ComboboxSelected>>", self._on_history_selected)
        self.search_entry.bind("<Control-a>", self._select_all)
        ToolTip(self.search_entry, "Aramak istediğiniz metni giriniz veya geçmişten seçiniz (Enter ile ara)")
        self._update_search_history_dropdown()

        self.search_entry_placeholder = "Aramak istediğiniz metni giriniz..."
        self.search_entry.bind("<FocusIn>", self._clear_placeholder)
        self.search_entry.bind("<FocusOut>", self._add_placeholder)
        self._add_placeholder()

        # Buttons and options frame
        controls_frame = ttk.Frame(search_frame)
        controls_frame.pack(fill=tk.X)
        
        # Left side buttons
        left_buttons = ttk.Frame(controls_frame)
        left_buttons.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.browse_button = ttk.Button(left_buttons, text="Dizin Seç", command=self.browse_folder, width=12)
        self.browse_button.pack(side=tk.LEFT, padx=(0, 5))
        ToolTip(self.browse_button, f"PDF'lerinizin olduğu klasörü seçiniz\nMevcut: {default_folder}")

        self.search_button = ttk.Button(left_buttons, text="Bul", command=self.start_search, style="Accent.TButton", width=10)
        self.search_button.pack(side=tk.LEFT, padx=(0, 5))
        ToolTip(self.search_button, "Aramayı başlatır (Enter)")
        
        self.cancel_button = ttk.Button(left_buttons, text="Durdur", command=self.cancel_search, state=tk.DISABLED, width=10)
        self.cancel_button.pack(side=tk.LEFT, padx=(0, 5))
        ToolTip(self.cancel_button, "Devam eden aramayı durdurur")

        # Right side - theme button
        self.theme_button = ttk.Button(
            controls_frame, 
            text=self.ICON_THEME_BUTTON if self.config.get("theme", self.DARK_THEME) == self.DARK_THEME else self.ICON_THEME_BUTTON_LIGHT,
            width=3, 
            command=self.toggle_theme
        )
        self.theme_button.pack(side=tk.RIGHT)
        ToolTip(self.theme_button, "Tema değiştir (Koyu ⇄ Açık)")

        # Search options frame
        options_frame = ttk.LabelFrame(main_container, text=" ⚙️ Arama Seçenekleri ", padding="10 10 10 10")
        options_frame.pack(fill=tk.X, pady=(0, 10))
        
        def on_exact_match_toggle():
            if self.exact_match.get():
                self.unordered_match_checkbox.config(state=tk.DISABLED)
                self.flexible_match_checkbox.config(state=tk.DISABLED)
            else:
                self.unordered_match_checkbox.config(state=tk.NORMAL)
                self.flexible_match_checkbox.config(state=tk.NORMAL)
                
        def on_unordered_match_toggle():
            if self.unordered_match.get():
                self.exact_match_checkbox.config(state=tk.DISABLED)
                self.flexible_match_checkbox.config(state=tk.DISABLED)
            else:
                self.exact_match_checkbox.config(state=tk.NORMAL)
                self.flexible_match_checkbox.config(state=tk.NORMAL)
                
        def on_flexible_match_toggle():
            if self.flexible_match.get():
                self.exact_match_checkbox.config(state=tk.DISABLED)
                self.unordered_match_checkbox.config(state=tk.DISABLED)
            else:
                self.exact_match_checkbox.config(state=tk.NORMAL)
                self.unordered_match_checkbox.config(state=tk.NORMAL)
        
        self.exact_match_checkbox = ttk.Checkbutton(
            options_frame, text="✓ Tam Eşleşme", variable=self.exact_match, command=on_exact_match_toggle
        )
        self.exact_match_checkbox.pack(side=tk.LEFT, padx=(0, 15))
        ToolTip(self.exact_match_checkbox, "Tam eşleşme araması yapar\nÖrnek: 'kitap' arandığında 'kitaplık' eşleşmez")
        
        self.unordered_match_checkbox = ttk.Checkbutton(
            options_frame, text="↔️ Takdim Tehir", variable=self.unordered_match, command=on_unordered_match_toggle
        )
        self.unordered_match_checkbox.pack(side=tk.LEFT, padx=(0, 15))
        ToolTip(self.unordered_match_checkbox, "Aranan kelimelerin cümledeki sırasını önemsiz kılar")
        
        self.flexible_match_checkbox = ttk.Checkbutton(
            options_frame, text="🔀 Esnek Eşleşme", variable=self.flexible_match, command=on_flexible_match_toggle
        )
        self.flexible_match_checkbox.pack(side=tk.LEFT)
        ToolTip(self.flexible_match_checkbox, "Arama terimindeki kelimelerin beraber bulunduğu sayfaları arar. \n(Kelimeler sayfanın farklı yerlerinde bulunabilir.) \n• 2-3 kelime: Hepsini arar\n• 4+ kelime: En az 3 kelime arar")

        # Status frame
        status_frame = ttk.Frame(main_container)
        status_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.count_label = ttk.Label(
            status_frame, 
            text="Aramaya hazır", 
            font=("Segoe UI", 11, "bold"), 
            foreground=self.HIGHLIGHT_COLOR
        )
        self.count_label.pack(side=tk.LEFT, padx=(0, 20))
        
        self.time_label = ttk.Label(
            status_frame, 
            text="", 
            font=("Segoe UI", 10), 
            foreground="#888"
        )
        self.time_label.pack(side=tk.LEFT)

        # Progress bar
        self.progress = ttk.Progressbar(main_container, orient=tk.HORIZONTAL, mode='determinate')
        self.progress.pack(fill=tk.X, pady=(0, 10))

        # Filter frame
        filter_frame = ttk.LabelFrame(main_container, text=" 🔎 Sonuç Filtresi ", padding="10 10 10 10")
        filter_frame.pack(fill=tk.X, pady=(0, 10))
        
        filter_inner = ttk.Frame(filter_frame)
        filter_inner.pack(fill=tk.X)
        
        ttk.Label(filter_inner, text="Filtre:").pack(side=tk.LEFT, padx=(0, 5))
        self.snippet_filter_var = tk.StringVar()
        self.snippet_filter_entry = ttk.Entry(filter_inner, textvariable=self.snippet_filter_var, font=("Segoe UI", 10))
        self.snippet_filter_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5), ipady=3)
        self.snippet_filter_entry.bind("<Return>", lambda e: self.apply_snippet_filter())
        
        self.snippet_filter_button = ttk.Button(filter_inner, text="Filtrele", command=self.apply_snippet_filter, width=10)
        self.snippet_filter_button.pack(side=tk.LEFT, padx=(0, 5))
        
        clear_filter_button = ttk.Button(filter_inner, text="✕ Temizle", command=self.clear_filter, width=10)
        clear_filter_button.pack(side=tk.LEFT)
        
        ToolTip(self.snippet_filter_entry, "Sonuçları kelime ile filtreler (Enter ile filtrele)")
        ToolTip(self.snippet_filter_button, "Girilen kelimeyi içeren sonuçları gösterir")
        ToolTip(clear_filter_button, "Filtreyi kaldırır ve tüm sonuçları gösterir")

        # Results frame
        results_frame = ttk.LabelFrame(main_container, text=" 📄 Sonuçlar ", padding="10 10 10 10")
        results_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # Treeview with scrollbar
        tree_container = ttk.Frame(results_frame)
        tree_container.pack(fill=tk.BOTH, expand=True)
        self.style.map('Treeview', background=[('selected', self.HIGHLIGHT_COLOR)])

        
        columns = ("No", "Kaynak", "Eşleşme")
        self.tree = ttk.Treeview(tree_container, columns=columns, show="headings", height=16)
        self.tree.heading("No", text="No", command=lambda: self._sort_tree("No", int))
        self.tree.heading("Kaynak", text="Kaynak", command=lambda: self._sort_tree("Kaynak", str))
        self.tree.heading("Eşleşme", text="Eşleşme", command=lambda: self._sort_tree("Eşleşme", str))
        self.tree.column("No", width=60, anchor=tk.CENTER)
        self.tree.column("Kaynak", width=350, anchor=tk.W)
        self.tree.column("Eşleşme", width=450, anchor=tk.W)
        
        # Scrollbars
        vsb = ttk.Scrollbar(tree_container, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_container, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        
        tree_container.grid_rowconfigure(0, weight=1)
        tree_container.grid_columnconfigure(0, weight=1)

        self.tree.bind("<Double-1>", self.open_selected)
        self.tree.bind("<Return>", self.open_selected)
        self.tree.bind("<Button-3>", self._show_menu)
        
        # Add alternating row colors
        self.tree.tag_configure('oddrow', background='')
        self.tree.tag_configure('evenrow', background='')

        self._create_context_menu()
        
        # Debug/Log frame
        log_frame = ttk.LabelFrame(main_container, text=" 📋 Günlük ", padding="5 5 5 5")
        log_frame.pack(fill=tk.X)
        
        self.debug = tk.Text(log_frame, height=3, state=tk.DISABLED, font=("Consolas", 9), wrap=tk.WORD)
        self.debug.pack(fill=tk.X)
        self.debug_log("Program başlatıldı. Aramaya hazır.")

    def clear_filter(self):
        """Clear the snippet filter and show all results."""
        self.snippet_filter_var.set("")
        self.apply_snippet_filter()

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
            self.search_entry.icursor(tk.END)
            self.search_entry.focus_set()

    def _add_to_search_history(self, term: str):
        """Add a term to the search history."""
        if not term or term == self.search_entry_placeholder:
            return
            
        if term in self.search_history:
            self.search_history.remove(term)
            
        self.search_history.insert(0, term)
        
        if len(self.search_history) > self.MAX_HISTORY_SIZE:
            self.search_history = self.search_history[:self.MAX_HISTORY_SIZE]
            
        self.config["search_history"] = self.search_history
        save_config(self.config)
        
        self._update_search_history_dropdown()

    def debug_log(self, msg: str):
        timestamp = time.strftime('%H:%M:%S')
        self.debug.config(state=tk.NORMAL)
        self.debug.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.debug.see(tk.END)
        self.debug.config(state=tk.DISABLED)
        logger.info(msg)

    def browse_folder(self) -> None:
        folder = filedialog.askdirectory(title="PDF Klasörünü Seçin")
        if folder:
            self.config["default_folder"] = folder
            save_config(self.config)
            self.debug_log(f"Seçilen klasör: {folder}")
            # Update tooltip
            ToolTip(self.browse_button, f"PDF'lerinizin olduğu klasörü seçiniz\nMevcut: {folder}")
            self.count_label.config(text=f"Klasör güncellendi: {os.path.basename(folder)}")

    def start_search(self) -> None:
        folder = self.config.get("default_folder", "")
        term = self.search_var.get()
        if not os.path.isdir(folder):
            self.count_label.config(text="⚠️ Lütfen geçerli bir klasör seçin")
            messagebox.showwarning("Uyarı", "Lütfen geçerli bir PDF klasörü seçin.")
            return
        if not term or term == self.search_entry_placeholder:
            self.count_label.config(text="⚠️ Lütfen bir arama terimi girin")
            messagebox.showwarning("Uyarı", "Lütfen aramak istediğiniz metni girin.")
            return

        self._add_to_search_history(term)

        self._cancel_event.clear()
        self.search_button.config(state=tk.DISABLED)
        self.cancel_button.config(state=tk.NORMAL)
        self._set_busy(True)
        self.count_label.config(text="🔍 Aranıyor...")
        self.time_label.config(text="")
        self.debug_log(f"'{folder}' dizininde '{term}' araması başlatıldı.")
        exact_match = self.exact_match.get()
        unordered_match = self.unordered_match.get()
        flexible_match = self.flexible_match.get()
        threading.Thread(target=self._run_search, args=(folder, term, exact_match, unordered_match, flexible_match), daemon=True).start()

    def cancel_search(self) -> None:
        self._cancel_event.set()
        self.cancel_button.config(state=tk.DISABLED)
        self.count_label.config(text="⏹ Arama iptal edildi")
        self.debug_log("Arama kullanıcı tarafından durduruldu.")
        self._set_busy(False)

    def _run_search(self, folder: str, term: str, exact_match: bool, unordered_match: bool, flexible_match: bool) -> None:
        total = 0
        start = time.time()
        pdf_list = get_pdf_files(folder)
        if not pdf_list:
            self.root.after(0, lambda: self.count_label.config(
                text="⚠️ Seçili dizinde PDF dosyası bulunamadı"))
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
                matches = search_text_in_pdf(path, term, exact_match, unordered_match, flexible_match)
            except Exception:
                logger.exception(f"{path} işlenemedi.")
                self.root.after(0, lambda p=path: self.debug_log(f"❌ {os.path.basename(p)} işlenemedi."))
                continue

            for src, page_num, rects, snippet in matches:
                if self._cancel_event.is_set():
                    break
                total += 1
                self.results.append((path, page_num, rects, snippet))
                title = os.path.splitext(os.path.basename(src))[0]
                # Add alternating row colors
                tag = 'evenrow' if total % 2 == 0 else 'oddrow'
                self.root.after(0, lambda t=total, ti=title, pn=page_num, sn=f"...{snippet}...", tg=tag:
                                self.tree.insert("", "end", values=(t, f"{ti}, sayfa {pn}", sn), tags=(tg,)))

            elapsed = time.time() - start
            avg_time = elapsed / index if index > 0 else 0
            remaining = (len(pdf_list) - index) * avg_time
            self.root.after(0, lambda v=index, e=elapsed, r=remaining: [
                self.progress.config(value=v),
                self.time_label.config(text=f"⏱️ Geçen: {e:.1f}s | Tahmini kalan: {r:.1f}s | {v}/{len(pdf_list)} PDF")
            ])

        self.root.after(0, lambda: self._finish_search(total))

    def _finish_search(self, total: int = 0) -> None:
        if total > 0:
            msg = f"✅ {total} eşleşme bulundu"
        else:
            msg = "❌ Eşleşme bulunamadı"
        self.count_label.config(text=msg)
        self.search_button.config(state=tk.NORMAL)
        self.cancel_button.config(state=tk.DISABLED)
        self.debug_log(f"Arama tamamlandı: {total} eşleşme bulundu.")
        self._set_busy(False)

    def open_selected(self, event) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        
        index = int(self.tree.item(sel[0], "values")[0]) - 1

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
                            continue
                    out_path = os.path.join(self.HIGHLIGHTED_PDFS_DIR, os.path.basename(path))
                    pdf.save(out_path)
                self.open_pdf_viewer(out_path, page_num)
                self.root.after(0, lambda: self.debug_log(f"✓ PDF açıldı: {os.path.basename(path)} (Sayfa {page_num})"))
            except Exception as e:
                logger.exception(f"{path} işlenirken hata oluştu.")
                self.root.after(0, lambda e=e: messagebox.showerror("Hata", f"PDF işlenirken hata oluştu:\n{e}"))

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
            process = subprocess.Popen([
                SUMATRAPDF_FILE, pdf_path, "-page", str(page_number), "-lang", "tr" 
            ])
        else:
            messagebox.showerror("Hata", "Desteklenmeyen işletim sistemi.")
            return
        self.opened_viewers.append(process)

    def copy_reference(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0], "values")
        self.root.clipboard_clear()
        self.root.clipboard_append(values[1])
        self.count_label.config(text="📋 Kaynak panoya kopyalandı")
        self.debug_log("📋 Kaynak panoya kopyalandı.")

    def _select_all(self, event) -> str:
        event.widget.select_range(0, tk.END)
        event.widget.icursor(tk.END)
        return "break"

    def _create_context_menu(self):
        """Create the right-click context menu for search results."""
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="📂 Aç", command=self._open_selected_item)
        self.menu.add_command(label="📋 Kaynağı Kopyala", command=self.copy_reference)
        self.menu.add_command(label="📝 Metni Kopyala", command=self._copy_snippet)
        self.menu.add_separator()
        self.menu.add_command(label="📁 Dosya Gezgininde Göster", command=self._show_in_explorer)

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
        snippet = values[2]
        self.root.clipboard_clear()
        self.root.clipboard_append(snippet)
        self.count_label.config(text="📋 Metin panoya kopyalandı")
        self.debug_log("📋 Metin panoya kopyalandı.")

    def _show_in_explorer(self):
        """Show the source file in the file explorer."""
        sel = self.tree.selection()
        if not sel:
            return
        index = int(self.tree.item(sel[0], "values")[0]) - 1
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
            self.debug_log(f"📁 Dosya konumu açıldı: {os.path.basename(path)}")
        except Exception as e:
            logger.exception(f"Dosya gezgininde gösterilemedi: {path}")
            messagebox.showerror("Hata", f"Dosya gezgininde gösterilemedi:\n{e}")

    def _set_busy(self, busy: bool) -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        cursor = "watch" if busy else ""
        self.search_entry.config(state=state)
        self.browse_button.config(state=state)
        self.search_button.config(state=state)
        self.exact_match_checkbox.config(state=state)
        self.unordered_match_checkbox.config(state=state)
        self.flexible_match_checkbox.config(state=state)
        self.theme_button.config(state=state)
        self.snippet_filter_entry.config(state=state)
        self.snippet_filter_button.config(state=state)
        
        if busy:
            self.search_entry.unbind("<Return>")
        else:
            self.search_entry.bind("<Return>", lambda e: self.start_search())
        
        self.root.config(cursor=cursor)
        self.root.update()

    def _sort_tree(self, column: str, data_type: type) -> None:
        """Sort the Treeview by a given column with toggling sort direction."""
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = False
        
        for col in self.tree["columns"]:
            if col == "No":
                self.tree.heading(col, text="No")
            elif col == "Kaynak":
                self.tree.heading(col, text="📄 Kaynak")
            else:
                self.tree.heading(col, text="📝 Eşleşme")
        
        direction_indicator = " ↓" if self.sort_reverse else " ↑"
        if column == "Kaynak":
            self.tree.heading(column, text=f"📄 {column}{direction_indicator}")
        elif column == "Eşleşme":
            self.tree.heading(column, text=f"📝 {column}{direction_indicator}")
        else:
            self.tree.heading(column, text=f"{column}{direction_indicator}")
        
        data = [(self.tree.set(child, column), child) for child in self.tree.get_children("")]
        
        try:
            if data_type == int:
                data.sort(key=lambda item: int(item[0]), reverse=self.sort_reverse)
            else:
                data.sort(key=lambda item: item[0], reverse=self.sort_reverse)
        except (ValueError, TypeError):
            data.sort(key=lambda item: str(item[0]), reverse=self.sort_reverse)
        
        for index, (_, child) in enumerate(data):
            self.tree.move(child, "", index)
        
        self.debug_log(f"↕️ Sonuçlar '{column}' sütununa göre sıralandı ({'azalan' if self.sort_reverse else 'artan'})")

    def apply_snippet_filter(self):
        """Filter results by keyword in snippet and update Treeview, keeping original index."""
        keyword = self.snippet_filter_var.get()
        if not keyword:
            # Show all results if filter is empty
            self.tree.delete(*self.tree.get_children())
            for idx, (path, page_num, rects, snippet) in enumerate(self.results, 1):
                title = os.path.splitext(os.path.basename(path))[0]
                tag = 'evenrow' if idx % 2 == 0 else 'oddrow'
                self.tree.insert("", "end", values=(idx, f"{title}, sayfa {page_num}", f"...{snippet}..."), tags=(tag,))
            self.count_label.config(text=f"✅ Tüm sonuçlar gösteriliyor ({len(self.results)} eşleşme)")
            self.debug_log(f"🔎 Filtre kaldırıldı, {len(self.results)} sonuç gösteriliyor.")
            return
            
        keyword = normalize(keyword)

        self.tree.delete(*self.tree.get_children())
        count = 0
        for idx, (path, page_num, rects, snippet) in enumerate(self.results, 1):
            if keyword in snippet:
                title = os.path.splitext(os.path.basename(path))[0]
                tag = 'evenrow' if count % 2 == 0 else 'oddrow'
                self.tree.insert("", "end", values=(idx, f"{title}, sayfa {page_num}", f"...{snippet}..."), tags=(tag,))
                count += 1
        self.count_label.config(text=f"🔎 {count} sonuç gösteriliyor (filtreli)")
        self.debug_log(f"🔎 Filtre uygulandı: '{self.snippet_filter_var.get()}' ile {count}/{len(self.results)} sonuç.")

if __name__ == "__main__":
    root = tk.Tk()
    app = PDFSearchApp(root)
    root.mainloop()