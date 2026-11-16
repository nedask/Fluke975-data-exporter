#!/usr/bin/env python3
"""
Fluke 975 Data Manager — GUI for firmware 1C.

Required files in the same folder:
    run_fluke975.py
    975dlall.py
    fluke975.py

Launch:
    python3 fluke.py
    python3 fluke.py --verbose
    sudo python3 fluke.py         (if user not in `dialout` group)
"""

import csv
import glob
import os
import queue
import sys
import threading
import time
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import tkinter.font as tkfont

os.environ.setdefault('FLUKE_PREWRITE', '0.05')

try:
    _here = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _here = os.getcwd()
if _here not in sys.path:
    sys.path.insert(0, _here)

_VERBOSE = ('--verbose' in sys.argv) or ('-v' in sys.argv)
if not _VERBOSE:
    os.environ['FLUKE_DEBUG'] = '0'

try:
    import run_fluke975 as wrapper
except ImportError as e:
    print(f"ERROR: could not import run_fluke975.py — {e}")
    sys.exit(1)

if not _VERBOSE:
    wrapper.DEBUG = False

import importlib.util
_dlall_path = os.path.join(_here, '975dlall.py')
if not os.path.isfile(_dlall_path):
    print(f"ERROR: {_dlall_path} not found.")
    sys.exit(1)
_spec = importlib.util.spec_from_file_location("_fluke_dlall", _dlall_path)
dlall = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dlall)


# ===========================================================================
# Constants
# ===========================================================================

FLUKE_VID = 0x0F7E
PROBE_SEQUENCE = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 3072, 4096]

GRAPH_VARIABLES = [
    'temperature_c', 'rh_percent', 'dewpoint_c', 'wetbulb_c',
    'co_ppm', 'co2_ppm', 'extra',
]

# --- Visual theme (colors only; no behaviour depends on these) -------------
BG        = '#f3f5f8'
SURFACE   = '#ffffff'
HEADER    = '#0f172a'
TEXT      = '#1f2937'
MUTED     = '#6b7280'
BORDER    = '#d5dbe5'
ACCENT    = '#2563eb'
ACCENT_HI = '#1d4ed8'
ACCENT_LO = '#93b4f5'
OK        = '#15803d'
GRID      = '#eceff4'
AREA      = '#e4edfd'
LOG_BG    = '#0f172a'
LOG_FG    = '#d6deeb'
MONO      = ('TkFixedFont', 9)


def enumerate_serial_ports():
    entries = []
    seen_paths = set()
    try:
        from serial.tools import list_ports
        for p in list_ports.comports():
            is_fluke = (p.vid == FLUKE_VID)
            vid_pid = f"{p.vid:04x}:{p.pid:04x}" if p.vid else ""
            tags = []
            if is_fluke:
                tags.append("FLUKE")
            if p.description:
                tags.append(p.description)
            if vid_pid:
                tags.append(vid_pid)
            label = p.device + (f"  ({', '.join(tags)})" if tags else "")
            entries.append({'label': label, 'path': p.device,
                            'is_fluke': is_fluke})
            seen_paths.add(os.path.realpath(p.device))
    except Exception:
        pass

    try:
        for link in sorted(glob.glob('/dev/serial/by-id/*')):
            real = os.path.realpath(link)
            if real in seen_paths:
                for e in entries:
                    if os.path.realpath(e['path']) == real:
                        if '0f7e' in os.path.basename(link).lower():
                            e['is_fluke'] = True
                            if 'FLUKE' not in e['label']:
                                e['label'] += "  ← FLUKE"
                        break
                continue
            name = os.path.basename(link)
            is_fluke = '0f7e' in name.lower()
            tag = "  ← FLUKE" if is_fluke else ""
            entries.append({'label': link + tag, 'path': link,
                            'is_fluke': is_fluke})
            seen_paths.add(real)
    except Exception:
        pass

    for pattern in ('/dev/ttyACM*', '/dev/ttyUSB*'):
        try:
            for dev in sorted(glob.glob(pattern)):
                real = os.path.realpath(dev)
                if real in seen_paths:
                    continue
                entries.append({'label': dev, 'path': dev,
                                'is_fluke': False})
                seen_paths.add(real)
        except Exception:
            pass

    return entries


def format_eta(seconds):
    if seconds <= 0:
        return "?"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds/60:.1f}min"
    return f"{seconds/3600:.1f}h"


# ===========================================================================
# Date/time picker dialog
# ===========================================================================

class _DateTimePicker(tk.Toplevel):
    def __init__(self, parent, initial_value="", title="Pick date/time"):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.configure(bg=BG)
        self.result = None

        now = datetime.now()
        y, mo, d, h, mi, s = now.year, now.month, now.day, 0, 0, 0
        text = (initial_value or "").strip()
        for fmt, has_time in (
            ("%Y-%m-%d %H:%M:%S", True),
            ("%Y-%m-%d %H:%M", True),
            ("%Y-%m-%d %H", True),
            ("%Y-%m-%d", False),
        ):
            try:
                dt = datetime.strptime(text, fmt)
                y, mo, d = dt.year, dt.month, dt.day
                if has_time:
                    h, mi, s = dt.hour, dt.minute, dt.second
                break
            except ValueError:
                continue

        f = ttk.Frame(self, padding=16)
        f.pack(fill='both', expand=True)

        row_d = ttk.Frame(f); row_d.pack(fill='x', pady=2)
        ttk.Label(row_d, text="Date:").pack(side='left')
        self.year_var = tk.StringVar(value=str(y))
        self.month_var = tk.StringVar(value=f"{mo:02d}")
        self.day_var = tk.StringVar(value=f"{d:02d}")
        ttk.Spinbox(row_d, from_=2000, to=2100, width=6,
                    textvariable=self.year_var).pack(side='left', padx=2)
        ttk.Label(row_d, text="-").pack(side='left')
        ttk.Spinbox(row_d, from_=1, to=12, width=4,
                    textvariable=self.month_var).pack(side='left')
        ttk.Label(row_d, text="-").pack(side='left')
        ttk.Spinbox(row_d, from_=1, to=31, width=4,
                    textvariable=self.day_var).pack(side='left')

        row_t = ttk.Frame(f); row_t.pack(fill='x', pady=(6, 2))
        ttk.Label(row_t, text="Time:").pack(side='left')
        self.hour_var = tk.StringVar(value=f"{h:02d}")
        self.minute_var = tk.StringVar(value=f"{mi:02d}")
        self.second_var = tk.StringVar(value=f"{s:02d}")
        ttk.Spinbox(row_t, from_=0, to=23, width=4,
                    textvariable=self.hour_var).pack(side='left', padx=2)
        ttk.Label(row_t, text=":").pack(side='left')
        ttk.Spinbox(row_t, from_=0, to=59, width=4,
                    textvariable=self.minute_var).pack(side='left')
        ttk.Label(row_t, text=":").pack(side='left')
        ttk.Spinbox(row_t, from_=0, to=59, width=4,
                    textvariable=self.second_var).pack(side='left')

        row_b = ttk.Frame(f); row_b.pack(fill='x', pady=(12, 0))
        ttk.Button(row_b, text="Now",
                   command=self._set_now).pack(side='left')
        ttk.Button(row_b, text="Clear",
                   command=self._clear).pack(side='left', padx=6)
        ttk.Button(row_b, text="Cancel",
                   command=self._cancel).pack(side='right')
        ttk.Button(row_b, text="OK", style='Accent.TButton',
                   command=self._ok).pack(side='right', padx=6)

        self.bind('<Return>', lambda e: self._ok())
        self.bind('<Escape>', lambda e: self._cancel())
        self.grab_set()
        parent.wait_window(self)

    def _set_now(self):
        now = datetime.now()
        self.year_var.set(str(now.year))
        self.month_var.set(f"{now.month:02d}")
        self.day_var.set(f"{now.day:02d}")
        self.hour_var.set(f"{now.hour:02d}")
        self.minute_var.set(f"{now.minute:02d}")
        self.second_var.set(f"{now.second:02d}")

    def _ok(self):
        try:
            dt = datetime(
                int(self.year_var.get()),
                int(self.month_var.get()),
                int(self.day_var.get()),
                int(self.hour_var.get()),
                int(self.minute_var.get()),
                int(self.second_var.get()),
            )
        except (ValueError, TypeError) as e:
            messagebox.showerror("Invalid", f"Invalid date/time: {e}",
                                 parent=self)
            return
        self.result = dt.strftime("%Y-%m-%d %H:%M:%S")
        self.destroy()

    def _clear(self):
        self.result = ""
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


# ===========================================================================
# Application
# ===========================================================================

class Fluke975GUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Fluke 975 Data Manager")
        self.root.geometry("1100x900")
        self.root.minsize(860, 640)

        self.ser = None
        self.worker = None
        self.cancel = threading.Event()
        self.msg_q = queue.Queue()
        self.last_records = []
        self._port_map = {}

        self._connecting = False

        self.graph_data = []
        self.graph_sessions = []
        self._last_canvas_size = (0, 0)

        self._build_ui()
        self._refresh_ports()
        self._poll_queue()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind('<Escape>', lambda e: self._cancel_op())

    # --------------------------------------------------------------- UI ----

    def _apply_theme(self):
        root = self.root
        root.configure(bg=BG)
        style = ttk.Style(root)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass

        for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont'):
            try:
                tkfont.nametofont(name).configure(size=10)
            except tk.TclError:
                pass
        try:
            tkfont.nametofont('TkFixedFont').configure(size=9)
        except tk.TclError:
            pass

        # dropdown lists of comboboxes
        root.option_add('*TCombobox*Listbox.background', SURFACE)
        root.option_add('*TCombobox*Listbox.foreground', TEXT)
        root.option_add('*TCombobox*Listbox.selectBackground', ACCENT)
        root.option_add('*TCombobox*Listbox.selectForeground', '#ffffff')
        root.option_add('*TCombobox*Listbox.font', 'TkDefaultFont')

        style.configure('.', background=BG, foreground=TEXT,
                        bordercolor=BORDER, lightcolor=BG, darkcolor=BG,
                        troughcolor='#e6eaf0', focuscolor=BG,
                        fieldbackground=SURFACE)
        style.configure('TFrame', background=BG)
        style.configure('TLabel', background=BG, foreground=TEXT)

        style.configure('TLabelframe', background=BG, bordercolor=BORDER,
                        relief='solid', borderwidth=1)
        style.configure('TLabelframe.Label', background=BG,
                        foreground=ACCENT, font=('TkDefaultFont', 10, 'bold'))

        style.configure('TButton', padding=(14, 6), background=SURFACE,
                        foreground=TEXT, bordercolor=BORDER,
                        lightcolor=SURFACE, darkcolor=SURFACE,
                        relief='solid', borderwidth=1)
        style.map('TButton',
                  background=[('disabled', '#eef0f4'),
                              ('pressed', '#dfe5ee'),
                              ('active', '#eaf0fb')],
                  foreground=[('disabled', '#a3acba')],
                  bordercolor=[('active', ACCENT), ('focus', ACCENT)])

        style.configure('Accent.TButton', padding=(16, 7),
                        background=ACCENT, foreground='#ffffff',
                        bordercolor=ACCENT, lightcolor=ACCENT,
                        darkcolor=ACCENT, font=('TkDefaultFont', 10, 'bold'))
        style.map('Accent.TButton',
                  background=[('disabled', ACCENT_LO),
                              ('pressed', ACCENT_HI),
                              ('active', ACCENT_HI)],
                  foreground=[('disabled', '#ffffff')],
                  bordercolor=[('disabled', ACCENT_LO),
                               ('active', ACCENT_HI)])

        style.configure('TEntry', padding=5, fieldbackground=SURFACE,
                        bordercolor=BORDER, lightcolor=SURFACE,
                        darkcolor=SURFACE)
        style.map('TEntry', bordercolor=[('focus', ACCENT)],
                  lightcolor=[('focus', ACCENT)],
                  darkcolor=[('focus', ACCENT)])
        style.configure('TSpinbox', padding=4, arrowsize=14,
                        fieldbackground=SURFACE, bordercolor=BORDER,
                        lightcolor=SURFACE, darkcolor=SURFACE,
                        background=BG)
        style.map('TSpinbox', bordercolor=[('focus', ACCENT)])
        style.configure('TCombobox', padding=5, arrowsize=14,
                        fieldbackground=SURFACE, background=BG,
                        bordercolor=BORDER, lightcolor=SURFACE,
                        darkcolor=SURFACE, arrowcolor=MUTED)
        style.map('TCombobox',
                  fieldbackground=[('readonly', SURFACE)],
                  bordercolor=[('focus', ACCENT)],
                  arrowcolor=[('active', ACCENT)])

        style.configure('TCheckbutton', background=BG, foreground=TEXT,
                        indicatorbackground=SURFACE,
                        indicatorforeground=ACCENT)
        style.map('TCheckbutton',
                  background=[('active', BG)],
                  indicatorbackground=[('selected', ACCENT),
                                       ('pressed', ACCENT_LO)])

        style.configure('TNotebook', background=BG, borderwidth=0,
                        tabmargins=(0, 4, 0, 0))
        style.configure('TNotebook.Tab', padding=(16, 8), background='#e3e8f0',
                        foreground=MUTED, bordercolor=BORDER,
                        font=('TkDefaultFont', 10, 'bold'))
        style.map('TNotebook.Tab',
                  background=[('selected', SURFACE), ('active', '#edf1f7')],
                  foreground=[('selected', ACCENT)],
                  expand=[('selected', (0, 0, 0, 0))])

        style.configure('Treeview', background=SURFACE, fieldbackground=SURFACE,
                        foreground=TEXT, rowheight=26, borderwidth=0)
        style.map('Treeview',
                  background=[('selected', ACCENT)],
                  foreground=[('selected', '#ffffff')])
        style.configure('Treeview.Heading', background='#e9edf4',
                        foreground=TEXT, padding=(8, 6), relief='flat',
                        font=('TkDefaultFont', 10, 'bold'))
        style.map('Treeview.Heading', background=[('active', '#dde4ef')])

        style.configure('Horizontal.TProgressbar', background=ACCENT,
                        troughcolor='#e1e6ee', bordercolor='#e1e6ee',
                        lightcolor=ACCENT, darkcolor=ACCENT, thickness=10)

        style.configure('Vertical.TScrollbar', background='#cfd6e2',
                        troughcolor=BG, bordercolor=BG, arrowcolor=MUTED,
                        lightcolor='#cfd6e2', darkcolor='#cfd6e2',
                        gripcount=0)
        style.map('Vertical.TScrollbar',
                  background=[('active', '#b4bfd1')])

        style.configure('Status.TLabel', background=HEADER,
                        foreground='#cbd5e1', padding=(12, 6))
        style.configure('Hint.TLabel', background=BG, foreground=MUTED)
        style.configure('Mono.TLabel', background=BG, foreground=MUTED,
                        font=MONO)

    def _build_header(self):
        bar = tk.Frame(self.root, bg=HEADER)
        bar.pack(fill='x')
        tk.Label(bar, text="Fluke 975", bg=HEADER, fg='#ffffff',
                 font=('TkDefaultFont', 15, 'bold')).pack(
            side='left', padx=(18, 6), pady=12)
        tk.Label(bar, text="Data Manager", bg=HEADER, fg='#7aa2f7',
                 font=('TkDefaultFont', 15)).pack(side='left', pady=12)
        tk.Label(bar, text="Download  \u2022  Export  \u2022  Graph",
                 bg=HEADER, fg='#64748b',
                 font=('TkDefaultFont', 9)).pack(side='right', padx=18)

    def _build_ui(self):
        self._apply_theme()
        self._build_header()
        self._build_device_bar()
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill='both', expand=True, padx=12, pady=(8, 8))
        self._build_export_tab()
        self._build_graph_tab()
        self._build_log_tab()
        self._build_status_bar()

    def _build_device_bar(self):
        frame = ttk.LabelFrame(self.root, text=" Device ", padding=(12, 10))
        frame.pack(fill='x', padx=12, pady=(12, 0))

        ttk.Label(frame, text="Port:").pack(side='left')
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(frame, textvariable=self.port_var,
                                        width=58)
        self.port_combo.pack(side='left', padx=6)

        self.refresh_btn = ttk.Button(frame, text="Refresh",
                                       command=self._refresh_ports)
        self.refresh_btn.pack(side='left')

        self.connect_btn = ttk.Button(frame, text="Connect",
                                       style='Accent.TButton',
                                       command=self._connect)
        self.connect_btn.pack(side='left', padx=4)

        self.disconnect_btn = ttk.Button(frame, text="Disconnect",
                                          command=self._disconnect,
                                          state='disabled')
        self.disconnect_btn.pack(side='left')

        self.conn_spinner = ttk.Progressbar(frame, mode='indeterminate',
                                            length=80)

        self.id_var = tk.StringVar(value="Not connected")
        self.id_label = tk.Label(frame, textvariable=self.id_var,
                                  bg=BG, fg=MUTED,
                                  font=('TkDefaultFont', 10, 'bold'))
        self.id_label.pack(side='left', padx=14)

    def _build_export_tab(self):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text="  Export  ")

        row = ttk.Frame(f); row.pack(fill='x', padx=8, pady=8)
        ttk.Label(row, text="Output CSV:").pack(side='left')
        self.export_path_var = tk.StringVar(value="fluke975.csv")
        ttk.Entry(row, textvariable=self.export_path_var, width=54).pack(
            side='left', padx=6)
        ttk.Button(row, text="Browse...",
                   command=self._browse_export).pack(side='left')

        dr = ttk.LabelFrame(f, text="Date range (blank = unbounded)",
                            padding=6)
        dr.pack(fill='x', padx=8, pady=(0, 8))

        dr1 = ttk.Frame(dr); dr1.pack(fill='x', pady=2)
        ttk.Button(dr1, text="Show available",
                   command=self._show_available_range).pack(side='left')
        self.avail_label_var = tk.StringVar(
            value="(auto-filled after connect; click Show available to refresh)")
        ttk.Label(dr1, textvariable=self.avail_label_var,
                  style='Hint.TLabel').pack(side='left', padx=10)

        dr2 = ttk.Frame(dr); dr2.pack(fill='x', pady=2)
        ttk.Label(dr2, text="From:").pack(side='left')
        self.from_var = tk.StringVar()
        ttk.Entry(dr2, textvariable=self.from_var, width=22).pack(
            side='left', padx=4)
        ttk.Button(dr2, text="Pick...",
                   command=lambda: self._pick_date('from')).pack(side='left')

        ttk.Label(dr2, text="To:").pack(side='left', padx=(16, 0))
        self.to_var = tk.StringVar()
        ttk.Entry(dr2, textvariable=self.to_var, width=22).pack(
            side='left', padx=4)
        ttk.Button(dr2, text="Pick...",
                   command=lambda: self._pick_date('to')).pack(side='left')

        ttk.Label(dr, text="format: YYYY-MM-DD [HH[:MM[:SS]]]",
                  style='Hint.TLabel').pack(anchor='w', padx=4, pady=(4, 0))

        opt = ttk.LabelFrame(f, text="Options", padding=6)
        opt.pack(fill='x', padx=8, pady=(0, 8))

        line1 = ttk.Frame(opt); line1.pack(fill='x', pady=2)
        self.overwrite_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(line1, text="Overwrite file",
                        variable=self.overwrite_var).pack(side='left')
        self.verbose_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(line1, text="Verbose",
                        variable=self.verbose_var).pack(side='left', padx=10)
        self.fast_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(line1, text="Fast mode",
                        variable=self.fast_var).pack(side='left')

        scan = ttk.LabelFrame(f, text="Scan range", padding=6)
        scan.pack(fill='x', padx=8, pady=(0, 8))

        sr1 = ttk.Frame(scan); sr1.pack(fill='x', pady=2)
        ttk.Label(sr1, text="Scan blocks 0 to:").pack(side='left')
        self.max_blocks_var = tk.IntVar(value=65535)
        ttk.Spinbox(sr1, from_=10, to=65535, increment=1000,
                    textvariable=self.max_blocks_var, width=8).pack(
            side='left', padx=4)
        ttk.Label(sr1, text="(65535 = full range)").pack(
            side='left', padx=(0, 20))

        ttk.Label(sr1, text="Max gap probe:").pack(side='left')
        self.max_gap_var = tk.IntVar(value=4096)
        ttk.Spinbox(sr1, from_=64, to=16384, increment=512,
                    textvariable=self.max_gap_var, width=8).pack(
            side='left', padx=4)
        ttk.Label(sr1, text="(blocks)").pack(side='left')

        sr2 = ttk.Frame(scan); sr2.pack(fill='x', pady=2)
        ttk.Label(sr2, text="Silence timeout (s):").pack(side='left')
        self.silence_var = tk.DoubleVar(value=0.20)
        ttk.Spinbox(sr2, from_=0.1, to=3.0, increment=0.05,
                    textvariable=self.silence_var, width=6).pack(
            side='left', padx=4)

        ttk.Label(sr2, text="Read timeout (s):").pack(
            side='left', padx=(16, 0))
        self.read_timeout_var = tk.DoubleVar(value=0.5)
        ttk.Spinbox(sr2, from_=0.3, to=5.0, increment=0.1,
                    textvariable=self.read_timeout_var, width=6).pack(
            side='left', padx=4)

        ttk.Label(sr2, text="Stop after (empties):").pack(
            side='left', padx=(16, 0))
        self.stop_threshold_var = tk.IntVar(value=20)
        ttk.Spinbox(sr2, from_=3, to=500, increment=5,
                    textvariable=self.stop_threshold_var, width=6).pack(
            side='left', padx=4)

        ttk.Label(sr2, text="Time limit (min):").pack(
            side='left', padx=(16, 0))
        self.time_limit_var = tk.DoubleVar(value=30.0)
        ttk.Spinbox(sr2, from_=0.5, to=600.0, increment=1.0,
                    textvariable=self.time_limit_var, width=6).pack(
            side='left', padx=4)

        btn = ttk.Frame(f); btn.pack(fill='x', padx=8, pady=6)
        self.export_btn = ttk.Button(btn, text="  Export All  ",
                                       style='Accent.TButton',
                                       command=self._start_export)
        self.export_btn.pack(side='left')
        self.cancel_btn = ttk.Button(btn, text="Cancel",
                                       command=self._cancel_op,
                                       state='disabled')
        self.cancel_btn.pack(side='left', padx=8)
        self.probe_btn = ttk.Button(btn, text="Probe meter speed",
                                      command=self._probe_speed)
        self.probe_btn.pack(side='left', padx=8)

        self.progress = ttk.Progressbar(f, mode='determinate', maximum=1000)
        self.progress.pack(fill='x', padx=8)
        self.progress_label_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.progress_label_var,
                  style='Mono.TLabel').pack(anchor='w', padx=8, pady=2)

        ttk.Label(f, text="Sessions found",
                  font=('TkDefaultFont', 10, 'bold')).pack(
            anchor='w', padx=8, pady=(10, 0))

        sess_frame = ttk.Frame(f); sess_frame.pack(fill='both', expand=True,
                                                    padx=8, pady=4)
        cols = ('session', 'records', 'start', 'end', 'duration')
        self.sess_tree = ttk.Treeview(sess_frame, columns=cols,
                                       show='headings', height=8)
        for c, label, w in [
            ('session', '#', 40), ('records', 'Records', 80),
            ('start', 'Start', 180), ('end', 'End', 180),
            ('duration', 'Duration', 90)]:
            self.sess_tree.heading(c, text=label)
            self.sess_tree.column(c, width=w, anchor='center')
        self.sess_tree.column('start', anchor='w')
        self.sess_tree.column('end', anchor='w')

        sb = ttk.Scrollbar(sess_frame, orient='vertical',
                            command=self.sess_tree.yview)
        self.sess_tree.configure(yscrollcommand=sb.set)
        self.sess_tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

    def _build_graph_tab(self):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text="  Graph  ")

        row1 = ttk.Frame(f); row1.pack(fill='x', padx=8, pady=8)
        ttk.Label(row1, text="CSV file:").pack(side='left')
        self.graph_path_var = tk.StringVar()
        ttk.Entry(row1, textvariable=self.graph_path_var,
                  width=56).pack(side='left', padx=6)
        ttk.Button(row1, text="Browse...",
                   command=self._browse_graph_csv).pack(side='left')
        ttk.Button(row1, text="Load",
                   command=self._load_graph_csv).pack(side='left', padx=4)

        row2 = ttk.Frame(f); row2.pack(fill='x', padx=8, pady=(0, 4))
        ttk.Label(row2, text="Variable:").pack(side='left')
        self.graph_var = tk.StringVar(value=GRAPH_VARIABLES[0])
        self.graph_var_combo = ttk.Combobox(
            row2, textvariable=self.graph_var,
            values=GRAPH_VARIABLES, state='readonly', width=18)
        self.graph_var_combo.pack(side='left', padx=4)
        self.graph_var_combo.bind('<<ComboboxSelected>>',
                                    lambda e: self._draw_graph())

        ttk.Label(row2, text="Session:").pack(side='left', padx=(20, 0))
        self.graph_session_var = tk.StringVar(value='All')
        self.graph_session_combo = ttk.Combobox(
            row2, textvariable=self.graph_session_var,
            values=['All'], state='readonly', width=12)
        self.graph_session_combo.pack(side='left', padx=4)
        self.graph_session_combo.bind('<<ComboboxSelected>>',
                                        lambda e: self._draw_graph())

        self.graph_info_var = tk.StringVar(value="Load a CSV file to begin.")
        ttk.Label(f, textvariable=self.graph_info_var,
                  style='Mono.TLabel').pack(anchor='w', padx=8, pady=(2, 4))

        canvas_wrap = ttk.Frame(f); canvas_wrap.pack(
            fill='both', expand=True, padx=8, pady=(0, 8))
        self.graph_canvas = tk.Canvas(canvas_wrap, bg=SURFACE,
                                        height=420, highlightthickness=1,
                                        highlightbackground=BORDER)
        self.graph_canvas.pack(fill='both', expand=True)
        self.graph_canvas.bind('<Configure>', self._on_canvas_resize)

    def _build_log_tab(self):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text="  Log  ")

        bar = ttk.Frame(f); bar.pack(fill='x', padx=8, pady=6)
        ttk.Button(bar, text="Clear", command=self._clear_log).pack(side='left')
        ttk.Button(bar, text="Save as...",
                   command=self._save_log).pack(side='left', padx=6)

        wrap = ttk.Frame(f); wrap.pack(fill='both', expand=True,
                                        padx=8, pady=(0, 8))
        self.log_text = tk.Text(wrap, wrap='word', height=20,
                                 font=('TkFixedFont', 9), bg=LOG_BG,
                                 fg=LOG_FG, insertbackground=LOG_FG,
                                 selectbackground=ACCENT, relief='flat',
                                 borderwidth=0, padx=10, pady=8,
                                 highlightthickness=1,
                                 highlightbackground=BORDER,
                                 highlightcolor=BORDER)
        sb = ttk.Scrollbar(wrap, orient='vertical',
                            command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        self.log_text.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

    def _build_status_bar(self):
        bar = ttk.Frame(self.root)
        bar.pack(fill='x', side='bottom')
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(bar, textvariable=self.status_var, style='Status.TLabel',
                  anchor='w').pack(fill='x')

    # ---------------------------------------------------- ports / connect ---

    def _refresh_ports(self):
        if self._busy() or self._connecting:
            return
        entries = enumerate_serial_ports()
        values = []
        self._port_map = {}
        fluke_label = None
        for e in entries:
            values.append(e['label'])
            self._port_map[e['label']] = e['path']
            if e['is_fluke'] and fluke_label is None:
                fluke_label = e['label']

        self.port_combo['values'] = values
        if values:
            current = self.port_var.get()
            if not current or current not in values:
                self.port_var.set(fluke_label or values[0])
            msg = f"Found {len(values)} port(s)"
            if fluke_label:
                msg += " — Fluke auto-selected"
            self._log(msg)
        else:
            self.port_var.set("")
            self._log("No serial ports found.")
            self._log("  ls -l /dev/serial/by-id/")
            self._log("  groups | grep dialout    (or sudo)")

    def _selected_port(self):
        label = self.port_var.get().strip()
        return self._port_map.get(label, label)

    def _connect(self):
        if self._busy() or self._connecting:
            return
        if self.ser:
            self._log("Already connected.")
            return
        port = self._selected_port()
        if not port:
            messagebox.showerror("Error", "Select a port first.")
            return

        self._connecting = True
        self._set_status("Connecting...")
        self.connect_btn.configure(state='disabled')
        self.refresh_btn.configure(state='disabled')
        self.disconnect_btn.configure(state='disabled')
        self.export_btn.configure(state='disabled')
        self.probe_btn.configure(state='disabled')
        self.conn_spinner.pack(side='left', padx=6)
        self.conn_spinner.start(15)
        self._log(f"Connecting to {port}...")

        def worker():
            try:
                ser = wrapper.SafeSerial(port=port, baudrate=9600, timeout=2)
                time.sleep(0.4)
                ser.reset_input_buffer()
                ser.write(b"ID\r")
                ser.flush()
                time.sleep(0.5)
                resp = ser.read(ser.in_waiting or 64)
                if b"FLUKE" not in resp:
                    ser.close()
                    self.msg_q.put(('connect_fail',
                        'No FLUKE response. Power-cycle the meter and retry.'))
                    return
                self.ser = ser
                id_text = (resp[2:-2].decode('ascii', 'replace')
                           .replace('\r', '').strip())
                self.msg_q.put(('connected', id_text))
            except Exception as e:
                self.msg_q.put(('connect_fail', str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _disconnect(self):
        if self._busy() or self._connecting:
            messagebox.showinfo("Busy", "Stop the current operation first.")
            return
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
        self._set_connection_badge(None)
        self.disconnect_btn.configure(state='disabled')
        self.connect_btn.configure(state='normal')
        self.refresh_btn.configure(state='normal')
        self.export_btn.configure(state='disabled')
        self.probe_btn.configure(state='disabled')
        self.avail_label_var.set("(connect to scan the meter)")
        self._log("Disconnected.")
        self._set_status("Disconnected")

    def _set_connection_badge(self, id_text):
        if id_text:
            self.id_var.set(f"● {id_text}")
            self.id_label.configure(fg=OK)
        else:
            self.id_var.set("Not connected")
            self.id_label.configure(fg=MUTED)

    # --------------------------------------------- Auto range after connect ---

    def _auto_range_after_connect(self):
        if not self.ser or self._busy() or self._connecting:
            return
        if self.from_var.get().strip() or self.to_var.get().strip():
            self._log("Skipping auto range scan (date fields already filled).")
            return
        self._log("Auto-scanning available date range (auto-fill enabled)...")
        self._show_available_range(autofill=True)

    # ------------------------------------------------------ Probe meter ---

    def _probe_speed(self):
        if self._busy() or self._connecting:
            messagebox.showinfo("Busy", "Wait for the current operation.")
            return
        if not self.ser:
            messagebox.showwarning("Not connected", "Connect first.")
            return

        self.cancel.clear()
        self._enter_busy("Probing meter speed")
        wrapper.DEBUG = False

        def worker():
            try:
                dlall._SILENCE_TIMEOUT = 1.0
                dlall._READ_TIMEOUT = 3.0
                times = []
                for b in (0, 1, 2, 10, 20, 50, 100, 200, 500):
                    if self._is_cancelled():
                        break
                    t0 = time.time()
                    resp, _ = dlall.fetch_block(self.ser, b)
                    dt = time.time() - t0
                    hit = False
                    if resp:
                        recs, _ = dlall.parse_block(resp)
                        hit = bool(recs)
                    times.append((b, dt, hit))
                    self.msg_q.put(('log',
                        f"  block {b:5d}: {dt*1000:6.0f} ms  "
                        f"{'data' if hit else 'empty'}"))

                if times:
                    avg = sum(t[1] for t in times) / len(times)
                    self.msg_q.put(('log',
                        f"Average: {avg*1000:.0f} ms/block. "
                        f"At this rate, 1500 blocks = {avg*1500:.0f}s"))
            except Exception as e:
                self.msg_q.put(('log', f"Probe failed: {e}"))
            finally:
                self.msg_q.put(('probe_done', None))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    # ---------------------------------------------- Date parsing helper ---

    def _parse_dt(self, text, is_end=False):
        text = (text or "").strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
            try:
                d = datetime.strptime(text, fmt)
                if is_end:
                    d = d.replace(second=59)
                return d
            except ValueError:
                continue
        try:
            d = datetime.strptime(text, "%Y-%m-%d %H")
            if is_end:
                d = d.replace(minute=59, second=59)
            return d
        except ValueError:
            pass
        try:
            d = datetime.strptime(text, "%Y-%m-%d")
            if is_end:
                d = d.replace(hour=23, minute=59, second=59)
            return d
        except ValueError:
            pass
        raise ValueError(
            "Could not parse '" + text + "'.\n"
            "Use YYYY-MM-DD [HH[:MM[:SS]]]")

    # ---------------------------------------------------- Date picker ---

    def _pick_date(self, which):
        var = self.from_var if which == 'from' else self.to_var
        dlg = _DateTimePicker(
            self.root, var.get(),
            title=("Pick From" if which == 'from' else "Pick To"))
        if dlg.result is None:
            return
        var.set(dlg.result)

    # ------------------------------------------------ Available range ---

    def _show_available_range(self, autofill=True):
        if self._busy() or self._connecting:
            messagebox.showinfo("Busy", "An operation is already running.")
            return
        if not self.ser:
            messagebox.showwarning("Not connected", "Connect first.")
            return

        self._range_autofill = autofill
        self.cancel.clear()
        self._enter_busy("Scanning for available range...")
        self.progress.configure(mode='indeterminate')
        self.progress.start(15)

        def worker():
            try:
                ser = self.ser
                oldest_dt = None
                newest_dt = None

                self.msg_q.put(('log', "Finding oldest record..."))
                for b in range(0, 200):
                    if self._is_cancelled():
                        self.msg_q.put(('range_done',
                                        (None, None, -1, -1)))
                        return
                    resp, _ = dlall.fetch_block(ser, b)
                    if not resp:
                        continue
                    recs, _ = dlall.parse_block(resp)
                    if recs:
                        oldest_dt = min(r['timestamp'] for r in recs)
                        break

                self.msg_q.put(('log', "Finding newest record..."))
                top, probes = self._probe_top(ser, 65535)
                if top >= 0:
                    resp, _ = dlall.fetch_block(ser, top)
                    if resp:
                        recs, _ = dlall.parse_block(resp)
                        if recs:
                            newest_dt = max(r['timestamp'] for r in recs)

                self.msg_q.put(('log',
                    "Range probe done (" + str(probes) + " probes)."))
                self.msg_q.put(('range_done',
                                (oldest_dt, newest_dt, 0, top)))
            except Exception as e:
                self.msg_q.put(('log', "Range detection failed: " + str(e)))
                self.msg_q.put(('range_done', (None, None, -1, -1)))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _on_range_done(self, data):
        oldest_dt, newest_dt, oldest_block, newest_block = data
        self.progress.stop()
        self.progress.configure(mode='determinate', value=0)
        self._leave_busy()

        if oldest_dt is None and newest_dt is None:
            if self._is_cancelled():
                self.avail_label_var.set("(scan cancelled)")
                self._set_status("Range scan cancelled — set From/To manually "
                                 "or click Show available")
            else:
                self.avail_label_var.set("Meter has no data.")
                self._set_status("Range: no data")
            return

        def _fmt(dt):
            return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "(unknown)"

        text = _fmt(oldest_dt) + " -> " + _fmt(newest_dt)
        self.avail_label_var.set(text)
        self._set_status("Ready")
        self._log("Available range: " + text)
        self._log("  Newest block: " + str(newest_block))

        if (getattr(self, '_range_autofill', True)
                and not self.from_var.get().strip()
                and not self.to_var.get().strip()):
            if oldest_dt:
                self.from_var.set(oldest_dt.strftime("%Y-%m-%d %H:%M:%S"))
            if newest_dt:
                self.to_var.set(newest_dt.strftime("%Y-%m-%d %H:%M:%S"))
            self._log("  (auto-filled From / To with detected range)")

    # ------------------------------------------------------ Probe top ---

    def _probe_top(self, ser, max_probe):
        step, last_hit, last_miss, probes = 1, 0, None, 0
        while step <= max_probe:
            if self._is_cancelled():
                return last_hit, probes
            resp, _ = dlall.fetch_block(ser, step)
            probes += 1
            hit = False
            if resp:
                recs, _ = dlall.parse_block(resp)
                hit = bool(recs)
            if hit:
                last_hit = step
                step *= 2
            else:
                last_miss = step
                break
        if last_miss is None:
            return last_hit, probes
        lo, hi = last_hit, last_miss
        while hi - lo > 1:
            if self._is_cancelled():
                return lo, probes
            mid = (lo + hi) // 2
            resp, _ = dlall.fetch_block(ser, mid)
            probes += 1
            hit = False
            if resp:
                recs, _ = dlall.parse_block(resp)
                hit = bool(recs)
            if hit:
                lo = mid
            else:
                hi = mid
        return lo, probes

    # ------------------------------------------------------------ Export ---

    def _start_export(self):
        if self._busy() or self._connecting:
            messagebox.showinfo("Busy", "An operation is already running.")
            return
        if not self.ser:
            messagebox.showwarning("Not connected",
                                   "Connect to the device first.")
            return
        path = self.export_path_var.get().strip()
        if not path:
            messagebox.showerror("Error", "Enter an output filename.")
            return

        try:
            from_ts = self._parse_dt(self.from_var.get(), is_end=False)
            to_ts = self._parse_dt(self.to_var.get(), is_end=True)
        except ValueError as e:
            messagebox.showerror("Invalid date", str(e))
            return

        if from_ts and to_ts and from_ts >= to_ts:
            messagebox.showerror("Invalid range",
                                 "From must be earlier than To.")
            return

        opts = {
            'out': path,
            'overwrite': self.overwrite_var.get(),
            'from_ts': from_ts,
            'to_ts': to_ts,
            'verbose': self.verbose_var.get(),
            'fast': self.fast_var.get(),
            'max_blocks': self.max_blocks_var.get(),
            'max_gap_probe': self.max_gap_var.get(),
            'silence': self.silence_var.get(),
            'read_timeout': self.read_timeout_var.get(),
            'stop_threshold': self.stop_threshold_var.get(),
            'time_limit': self.time_limit_var.get() * 60.0,
        }

        if opts['fast']:
            opts['silence'] = min(opts['silence'], 0.20)
            opts['read_timeout'] = min(opts['read_timeout'], 1.0)
            opts['stop_threshold'] = min(opts['stop_threshold'], 20)

        self.cancel.clear()
        self._enter_busy("Exporting...")
        self.progress.configure(mode='determinate')
        self.progress['value'] = 0
        self.progress['maximum'] = max(1, opts['max_blocks'])
        self.progress_label_var.set("Starting...")
        self.sess_tree.delete(*self.sess_tree.get_children())
        wrapper.DEBUG = opts['verbose']

        self.worker = threading.Thread(target=self._export_worker,
                                        args=(opts,), daemon=True)
        self.worker.start()

    def _export_worker(self, opts):
        dlall._shutdown = False
        dlall._SILENCE_TIMEOUT = opts['silence']
        dlall._READ_TIMEOUT = opts['read_timeout']
        ser = self.ser
        records = []
        start_time = time.time()
        try:
            from_ts = opts.get('from_ts')
            to_ts = opts.get('to_ts')

            if from_ts or to_ts:
                fr = from_ts.strftime("%Y-%m-%d %H:%M:%S") if from_ts else "(none)"
                to = to_ts.strftime("%Y-%m-%d %H:%M:%S") if to_ts else "(none)"
                self.msg_q.put(('log',
                    "Range export: from=" + fr + " to=" + to))
                records = self._scan_reverse(ser, opts, start_time,
                                              from_ts, to_ts)
            else:
                self.msg_q.put(('log',
                    "Full export: blocks 0.." + str(opts['max_blocks'])))
                records = self._scan_adaptive(ser, opts, start_time)

            if self._is_cancelled():
                self._assign_sessions(records)
                if records:
                    try:
                        self._write_csv(opts['out'], records,
                                         opts['overwrite'])
                        self.msg_q.put(('log',
                            "Cancelled - partial export: " +
                            str(len(records)) + " records"))
                    except Exception as e:
                        self.msg_q.put(('log',
                            "Cancelled - save failed: " + str(e)))
                else:
                    self.msg_q.put(('log', "Cancelled - no records."))
                self._finish("Cancelled.", records, opts)
                return

            self._assign_sessions(records)

            self.msg_q.put(('log',
                "Writing " + opts['out'] + " (" + str(len(records)) +
                " records)..."))
            self._write_csv(opts['out'], records, opts['overwrite'])
            elapsed = time.time() - start_time
            self._finish(
                "Export complete: " + str(len(records)) + " records in " +
                str(int(elapsed)) + "s.", records, opts)

        except Exception as e:
            import traceback
            self.msg_q.put(('log',
                "ERROR: " + str(e) + "\n" + traceback.format_exc()))
            self.msg_q.put(('error', str(e)))
            self._finish("Export failed.", records, opts)
        finally:
            dlall._shutdown = False

    def _scan_reverse(self, ser, opts, start_time, from_ts, to_ts):
        all_records = []
        seen = set()
        verbose = opts['verbose']
        time_limit = opts['time_limit']
        max_blocks = opts['max_blocks']

        blocks_read = 0
        last_status_emit = 0

        self.msg_q.put(('log', "Probing for newest block..."))
        top, probes = self._probe_top(ser, max_blocks - 1)
        if top < 0:
            self.msg_q.put(('log', "No data on meter."))
            return all_records
        blocks_read += probes
        self.msg_q.put(('log',
            "Top block = " + str(top) + " (" + str(probes) + " probes)."))

        start_block = top
        if to_ts:
            self.msg_q.put(('log',
                "Skipping blocks entirely newer than " + str(to_ts) + "..."))
            sample = top
            while sample >= 0:
                if self._is_cancelled():
                    return all_records
                resp, _ = dlall.fetch_block(ser, sample)
                blocks_read += 1
                if resp:
                    recs, _ = dlall.parse_block(resp)
                    if recs:
                        oldest = min(r['timestamp'] for r in recs)
                        if oldest <= to_ts:
                            start_block = sample
                            self.msg_q.put(('log',
                                "  start block = " + str(sample)))
                            break
                sample -= 10
            else:
                start_block = 0
            if start_block < top:
                self.msg_q.put(('log',
                    "  skipped ~" + str(top - start_block) +
                    " blocks above range."))

        self.msg_q.put(('log',
            "Walking down from block " + str(start_block) + "..."))
        scanned = 0
        error_streak = 0
        total_blocks = start_block + 1

        def emit_status():
            elapsed = time.time() - start_time
            rate = blocks_read / elapsed if elapsed > 0 else 0
            remaining = max(0, total_blocks - scanned)
            eta = remaining / rate if rate > 0.01 else 0
            self.msg_q.put(('progress',
                (scanned, total_blocks, len(all_records), blocks_read,
                 0, rate, eta)))

        for block in range(start_block, -1, -1):
            if self._is_cancelled():
                break
            if time.time() - start_time > time_limit:
                self.msg_q.put(('log',
                    "Time limit reached at block " + str(block) + "."))
                break
            scanned += 1
            blocks_read += 1
            if blocks_read - last_status_emit >= 5:
                last_status_emit = blocks_read
                emit_status()

            resp, err = dlall.fetch_block(ser, block)
            if err and err != "silence":
                error_streak += 1
                if verbose:
                    self.msg_q.put(('log',
                        "  block " + str(block) + ": " + err))
                if error_streak >= 5:
                    self.msg_q.put(('log',
                        "  Repeated errors - aborting at block " +
                        str(block) + "."))
                    break
                continue
            error_streak = 0

            if err == "silence" or not resp:
                continue

            recs, perr = dlall.parse_block(resp)
            if perr or not recs:
                continue

            if from_ts:
                newest_in_block = max(r['timestamp'] for r in recs)
                if newest_in_block < from_ts:
                    self.msg_q.put(('log',
                        "  block " + str(block) + ": newest " +
                        str(newest_in_block) + " < from " +
                        str(from_ts) + " - stopping."))
                    break

            kept = 0
            for r in recs:
                if from_ts and r['timestamp'] < from_ts:
                    continue
                if to_ts and r['timestamp'] > to_ts:
                    continue
                key = (r['record_type'], r['record'], r['timestamp'])
                if key in seen:
                    continue
                seen.add(key)
                all_records.append(r)
                kept += 1

            if verbose and kept:
                self.msg_q.put(('log',
                    "  block " + str(block) + ": +" + str(kept) +
                    " in range"))

        elapsed = time.time() - start_time
        self.msg_q.put(('log',
            "Range scan complete: " + str(scanned) + " blocks read, " +
            str(len(all_records)) + " records, " +
            str(int(elapsed)) + "s."))
        return all_records

    def _scan_adaptive(self, ser, opts, start_time):
        all_records = []
        seen = set()
        max_blocks = opts['max_blocks']
        max_gap_probe = opts['max_gap_probe']
        stop_threshold = opts['stop_threshold']
        verbose = opts['verbose']
        time_limit = opts['time_limit']

        blocks_read = 0
        regions_found = 0
        last_status_emit = 0

        def read_block(b):
            nonlocal blocks_read
            if b < 0 or b >= max_blocks:
                return None
            blocks_read += 1
            resp, err = dlall.fetch_block(ser, b)
            if resp and not err:
                recs, _ = dlall.parse_block(resp)
                return recs
            return None

        def collect(recs):
            if not recs:
                return 0
            n = 0
            for r in recs:
                key = (r['record_type'], r['record'], r['timestamp'])
                if key not in seen:
                    seen.add(key)
                    all_records.append(r)
                    n += 1
            return n

        def emit_status(pos):
            elapsed = time.time() - start_time
            rate = blocks_read / elapsed if elapsed > 0 else 0
            remaining = max(0, max_blocks - pos)
            eta = remaining / rate if rate > 0.01 else 0
            self.msg_q.put(('progress',
                (pos, max_blocks, len(all_records), blocks_read,
                 regions_found, rate, eta)))

        pos = 0
        while pos < max_blocks:
            if self._is_cancelled():
                break
            if time.time() - start_time > time_limit:
                self.msg_q.put(('log',
                    "Time limit reached at block " + str(pos) + "."))
                break

            region_start = pos
            consec_empty = 0
            while consec_empty < stop_threshold and pos < max_blocks:
                if self._is_cancelled():
                    break
                if time.time() - start_time > time_limit:
                    break
                recs = read_block(pos)
                if recs:
                    new = collect(recs)
                    consec_empty = 0
                    if verbose and new:
                        self.msg_q.put(('log',
                            "  block " + str(pos) + ": +" + str(new)))
                else:
                    consec_empty += 1
                pos += 1
                if blocks_read - last_status_emit >= 20:
                    last_status_emit = blocks_read
                    emit_status(pos)

            if pos >= max_blocks or self._is_cancelled():
                break

            if pos > region_start:
                regions_found += 1

            self.msg_q.put(('log',
                "Gap at block " + str(pos) + ". Probing up to " +
                str(max_gap_probe) + "..."))
            emit_status(pos)

            found_pos = None
            for jump in PROBE_SEQUENCE:
                if jump > max_gap_probe:
                    break
                probe_pos = pos + jump - 1
                if probe_pos >= max_blocks:
                    break
                if read_block(probe_pos):
                    found_pos = probe_pos
                    break
            if found_pos is None:
                edge = min(pos + max_gap_probe - 1, max_blocks - 1)
                if edge >= pos and read_block(edge):
                    found_pos = edge

            if found_pos is None:
                self.msg_q.put(('log',
                    "No data within " + str(max_gap_probe) +
                    " blocks of " + str(pos) + " — scan complete."))
                break

            lo, hi = pos - 1, found_pos
            self.msg_q.put(('log',
                "Data at block " + str(found_pos) +
                " (+" + str(found_pos - pos + 1) + "). Binary search..."))
            while hi - lo > 1:
                if self._is_cancelled():
                    break
                mid = (lo + hi) // 2
                if read_block(mid):
                    hi = mid
                else:
                    lo = mid
                emit_status(hi)

            self.msg_q.put(('log', "Resuming at block " + str(hi) + "."))
            pos = hi

        elapsed = time.time() - start_time
        rate = blocks_read / elapsed if elapsed > 0 else 0
        self.msg_q.put(('log',
            "Scan complete: " + str(blocks_read) + " blocks read, " +
            str(regions_found) + " regions, " + str(len(all_records)) +
            " records, " + str(int(elapsed)) + "s (" +
            str(round(rate, 1)) + " blk/s)."))
        return all_records

    def _finish(self, status, records, opts):
        self.msg_q.put(('export_done', (status, records, opts)))

    def _assign_sessions(self, records, gap_seconds=30.0):
        records.sort(key=lambda r: r['timestamp'])
        sid = 0
        prev = None
        for r in records:
            if prev is None:
                sid += 1
            elif (r['timestamp'] - prev['timestamp']).total_seconds() > gap_seconds:
                sid += 1
            r['session'] = sid
            prev = r
        return sid

    def _write_csv(self, path, records, overwrite):
        mode = 'w' if overwrite else 'a'
        is_new = (overwrite or not os.path.exists(path)
                  or os.path.getsize(path) == 0)
        with open(path, mode, newline='') as f:
            w = csv.writer(f)
            if is_new:
                w.writerow(['session', 'record', 'timestamp',
                            'temperature_c', 'rh_percent', 'dewpoint_c',
                            'wetbulb_c', 'co_ppm', 'co2_ppm', 'extra'])
            for r in records:
                w.writerow([
                    r.get('session', 0),
                    r['record'],
                    r['timestamp'].strftime("%Y-%m-%d %H:%M:%S"),
                    f"{r['temperature_c']:.1f}",
                    f"{r['rh_percent']:.2f}",
                    f"{r['dewpoint_c']:.1f}",
                    f"{r['wetbulb_c']:.1f}",
                    r['co_ppm'], r['co2_ppm'], r['extra'],
                ])

    def _on_export_done(self, payload):
        status, records, opts = payload
        self.progress['value'] = self.progress['maximum']
        self._leave_busy()
        self._set_status(status)
        self._log(status)
        self.last_records = records
        self.sess_tree.delete(*self.sess_tree.get_children())
        if records:
            by_sess = {}
            for r in records:
                by_sess.setdefault(r.get('session', 0), []).append(r)
            for sid in sorted(by_sess):
                recs = by_sess[sid]
                dur = (recs[-1]['timestamp']
                       - recs[0]['timestamp']).total_seconds()
                dur_str = (f"{int(dur // 60)}m{int(dur % 60):02d}s"
                           if dur >= 60 else f"{int(dur)}s")
                self.sess_tree.insert('', 'end', values=(
                    sid, len(recs),
                    recs[0]['timestamp'].strftime("%Y-%m-%d %H:%M:%S"),
                    recs[-1]['timestamp'].strftime("%Y-%m-%d %H:%M:%S"),
                    dur_str,
                ))
            self._log(f"Sessions: {len(by_sess)}; records: {len(records)}")
        else:
            self._log("No records found.")

        if opts.get('out'):
            try:
                sz = os.path.getsize(opts['out'])
                self._log(f"File: {opts['out']} ({sz:,} bytes)")
            except OSError:
                pass

            try:
                if os.path.isfile(opts['out']) and not self._is_cancelled():
                    self.graph_path_var.set(opts['out'])
                    self._load_graph_csv()
                    self._log("Graph tab: loaded the exported CSV.")
            except Exception as e:
                self._log(f"(graph auto-load skipped: {e})")

    # ------------------------------------------------ Graph tab ---

    def _browse_graph_csv(self):
        path = filedialog.askopenfilename(
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if path:
            self.graph_path_var.set(path)
            self._load_graph_csv()

    def _load_graph_csv(self):
        path = self.graph_path_var.get().strip()
        if not path or not os.path.isfile(path):
            messagebox.showerror("Error", "Select a valid CSV file first.")
            return
        try:
            rows = []
            sessions = set()
            with open(path, newline='', encoding='utf-8') as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    parsed = {}
                    parsed['timestamp'] = row.get('timestamp', '')
                    try:
                        sess_int = int(row.get('session', '0'))
                    except (ValueError, TypeError):
                        sess_int = 0
                    parsed['session'] = sess_int
                    sessions.add(sess_int)
                    try:
                        parsed['record'] = int(row.get('record', 0))
                    except (ValueError, TypeError):
                        parsed['record'] = 0
                    for v in GRAPH_VARIABLES:
                        raw = row.get(v, '')
                        try:
                            parsed[v] = float(raw)
                        except (ValueError, TypeError):
                            parsed[v] = 0.0
                    rows.append(parsed)

            self.graph_data = rows
            self.graph_sessions = sorted(sessions)

            session_values = ['All'] + [str(s) for s in self.graph_sessions]
            self.graph_session_combo['values'] = session_values
            if self.graph_session_var.get() not in session_values:
                self.graph_session_var.set('All')

            self.graph_info_var.set(
                f"Loaded {len(rows):,} records from "
                f"{len(self.graph_sessions)} session(s).")
            self._draw_graph()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load CSV:\n{e}")

    def _on_canvas_resize(self, event):
        size = (event.width, event.height)
        if size != self._last_canvas_size:
            self._last_canvas_size = size
            self.root.after(100, self._draw_graph)

    def _draw_graph(self):
        canvas = self.graph_canvas
        canvas.delete('all')

        W = canvas.winfo_width()
        H = canvas.winfo_height()
        if W < 100 or H < 100:
            self.root.after(200, self._draw_graph)
            return

        if not self.graph_data:
            canvas.create_text(W // 2, H // 2,
                               text="No data loaded.\n"
                                    "Click 'Browse...' to pick a CSV, "
                                    "then 'Load'.",
                               fill=MUTED,
                               font=('TkDefaultFont', 12),
                               justify='center')
            return

        choice = self.graph_session_var.get()
        if choice == 'All':
            rows = self.graph_data
        else:
            try:
                want = int(choice)
                rows = [r for r in self.graph_data
                        if r.get('session') == want]
            except ValueError:
                rows = self.graph_data

        if not rows:
            canvas.create_text(W // 2, H // 2,
                               text="No data in the selected session.",
                               fill=MUTED,
                               font=('TkDefaultFont', 12))
            return

        variable = self.graph_var.get()
        timestamps = []
        values = []
        for r in rows:
            ts = r.get('timestamp', '')
            val = r.get(variable)
            if val is None:
                continue
            timestamps.append(ts)
            values.append(val)

        if not values:
            canvas.create_text(W // 2, H // 2,
                               text=f"No '{variable}' data to plot.",
                               fill=MUTED)
            return

        vmin = min(values)
        vmax = max(values)
        vavg = sum(values) / len(values)
        n = len(values)
        self.graph_info_var.set(
            f"{variable}  |  {n:,} points  |  "
            f"min {vmin:.2f}   max {vmax:.2f}   avg {vavg:.2f}   "
            f"session: {choice}")

        pad_l, pad_r, pad_t, pad_b = 78, 28, 28, 52
        plot_w = max(10, W - pad_l - pad_r)
        plot_h = max(10, H - pad_t - pad_b)

        span = vmax - vmin
        if span < 1e-9:
            span = 1.0
        y_lo = vmin - span * 0.08
        y_hi = vmax + span * 0.08
        y_range = y_hi - y_lo

        def x_of(i):
            if n == 1:
                return pad_l + plot_w / 2
            return pad_l + (i / (n - 1)) * plot_w

        def y_of(v):
            return pad_t + plot_h - ((v - y_lo) / y_range) * plot_h

        for i in range(6):
            frac = i / 5.0
            y = pad_t + frac * plot_h
            y_val = y_hi - frac * y_range
            canvas.create_line(pad_l, y, W - pad_r, y, fill=GRID)
            canvas.create_text(pad_l - 8, y, text=f"{y_val:.1f}",
                               anchor='e', fill=MUTED,
                               font=MONO)

        n_ticks = min(6, n)
        if n_ticks >= 2:
            step = (n - 1) / (n_ticks - 1)
            for i in range(n_ticks):
                idx = int(round(i * step))
                if idx >= n:
                    idx = n - 1
                x = x_of(idx)
                ts = timestamps[idx]
                label = ts[11:19] if len(ts) >= 19 else ts
                canvas.create_line(x, pad_t, x, H - pad_b, fill='#f5f7fa')
                canvas.create_text(x, H - pad_b + 16, text=label,
                                   fill=MUTED, font=MONO)

        canvas.create_line(pad_l, pad_t, pad_l, H - pad_b,
                           fill='#9aa5b8', width=1)
        canvas.create_line(pad_l, H - pad_b, W - pad_r, H - pad_b,
                           fill='#9aa5b8', width=1)

        if n == 1:
            x = x_of(0)
            y = y_of(values[0])
            canvas.create_oval(x - 5, y - 5, x + 5, y + 5,
                               fill=ACCENT, outline=SURFACE, width=2)
        else:
            coords = []
            for i, v in enumerate(values):
                coords.append(x_of(i))
                coords.append(y_of(v))
            base_y = pad_t + plot_h
            area = canvas.create_polygon(
                x_of(0), base_y, *coords, x_of(n - 1), base_y,
                fill=AREA, outline='')
            canvas.tag_lower(area)
            canvas.create_line(*coords, fill=ACCENT, width=2,
                               joinstyle='round')
            if n <= 400:
                for i, v in enumerate(values):
                    x = x_of(i)
                    y = y_of(v)
                    canvas.create_oval(x - 2.5, y - 2.5, x + 2.5, y + 2.5,
                                       fill=SURFACE, outline=ACCENT, width=1.5)

        canvas.create_text(22, pad_t + plot_h / 2,
                           text=variable, angle=90, fill=TEXT,
                           font=('TkDefaultFont', 10, 'bold'))

    # ------------------------------------------- Cancel / busy / messages ---

    def _cancel_op(self):
        if self._connecting:
            return
        if not self._busy():
            return
        self.cancel.set()
        dlall._shutdown = True
        self._set_status("Cancelling...")
        self._log("Cancel requested.")

    def _is_cancelled(self):
        return self.cancel.is_set()

    def _busy(self):
        return self.worker is not None and self.worker.is_alive()

    def _enter_busy(self, status):
        self._set_status(status)
        self.connect_btn.configure(state='disabled')
        self.disconnect_btn.configure(state='disabled')
        self.refresh_btn.configure(state='disabled')
        self.export_btn.configure(state='disabled')
        self.probe_btn.configure(state='disabled')
        self.cancel_btn.configure(state='normal')

    def _leave_busy(self):
        connected = bool(self.ser)
        self.connect_btn.configure(state='disabled' if connected else 'normal')
        self.disconnect_btn.configure(state='normal' if connected else 'disabled')
        self.refresh_btn.configure(state='normal')
        self.export_btn.configure(state='normal' if connected else 'disabled')
        self.probe_btn.configure(state='normal' if connected else 'disabled')
        self.cancel_btn.configure(state='disabled')

    def _set_status(self, msg):
        self.status_var.set(msg)

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert('end', f"[{ts}] {msg}\n")
        self.log_text.see('end')

    def _clear_log(self):
        self.log_text.delete('1.0', 'end')

    def _save_log(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if path:
            with open(path, 'w') as f:
                f.write(self.log_text.get('1.0', 'end'))
            self._log(f"Log saved to {path}")

    def _browse_export(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
            initialfile=self.export_path_var.get() or "fluke975.csv")
        if path:
            self.export_path_var.set(path)

    # -------------------------------------------------------- Message pump ---

    def _poll_queue(self):
        try:
            while True:
                kind, data = self.msg_q.get_nowait()
                if kind == 'connected':
                    self._connecting = False
                    self.conn_spinner.stop()
                    self.conn_spinner.pack_forget()
                    self._set_connection_badge(data)
                    self.disconnect_btn.configure(state='normal')
                    self.connect_btn.configure(state='disabled')
                    self.refresh_btn.configure(state='normal')
                    self.export_btn.configure(state='normal')
                    self.probe_btn.configure(state='normal')
                    self._log(f"Connected: {data}")
                    self._set_status("Connected — scanning date range...")
                    self.root.after(300, self._auto_range_after_connect)
                elif kind == 'connect_fail':
                    self._connecting = False
                    self.conn_spinner.stop()
                    self.conn_spinner.pack_forget()
                    self._set_connection_badge(None)
                    self.ser = None
                    self.connect_btn.configure(state='normal')
                    self.refresh_btn.configure(state='normal')
                    self._log(f"Connect failed: {data}")
                    self._set_status("Connect failed")
                    messagebox.showerror("Connect failed", data)
                elif kind == 'log':
                    self._log(data)
                elif kind == 'progress':
                    pos, total, nrec, nread, nreg, rate, eta = data
                    self.progress['maximum'] = max(total, 1)
                    self.progress['value'] = pos
                    eta_str = format_eta(eta) if rate > 0.01 else "?"
                    self.progress_label_var.set(
                        f"block {pos:,}/{total:,} | {nrec:,} recs | "
                        f"{rate:.1f} blk/s | ETA {eta_str}")
                elif kind == 'export_done':
                    self._on_export_done(data)
                elif kind == 'range_done':
                    self._on_range_done(data)
                elif kind == 'probe_done':
                    self._leave_busy()
                    self._set_status("Probe complete.")
                elif kind == 'error':
                    messagebox.showerror("Error", data)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    # ------------------------------------------------------------ Shutdown ---

    def _on_close(self):
        if self._busy() or self._connecting:
            if not messagebox.askyesno(
                    "Quit", "An operation is running. Cancel and exit?"):
                return
            self.cancel.set()
            dlall._shutdown = True
            if self.worker:
                self.worker.join(timeout=3.0)
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.root.destroy()


# ===========================================================================

def main():
    root = tk.Tk()
    try:
        root.tk.call('tk', 'scaling', 1.2)
    except Exception:
        pass
    Fluke975GUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
