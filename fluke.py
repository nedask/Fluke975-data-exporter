#!/usr/bin/env python3
"""
Fluke 975 Data Manager — GUI for firmware 1C. Fast edition.

Required files in the same folder:
    run_fluke975.py
    975dlall.py
    fluke975.py

Launch:
    python3 fluke.py
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

# Speed: cut pre-write delay from 300 ms to 50 ms BEFORE importing wrapper.
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
# Port enumeration
# ===========================================================================

FLUKE_VID = 0x0F7E

# Trimmed probe sequence — 14 probes cover 0..4096 (was 28).
PROBE_SEQUENCE = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 3072, 4096]


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


# ===========================================================================
# Application
# ===========================================================================

class Fluke975GUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Fluke 975 Data Manager")
        self.root.geometry("1020x780")
        self.root.minsize(840, 600)

        self.ser = None
        self.worker = None
        self.cancel = threading.Event()
        self.msg_q = queue.Queue()
        self.last_records = []
        self._port_map = {}
        self._partial_save_path = None

        self._build_ui()
        self._refresh_ports()
        self._poll_queue()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind('<Escape>', lambda e: self._cancel_op())

    # --------------------------------------------------------------- UI ----

    def _build_ui(self):
        self._build_device_bar()
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill='both', expand=True, padx=6, pady=4)
        self._build_export_tab()
        self._build_log_tab()
        self._build_status_bar()

    def _build_device_bar(self):
        frame = ttk.LabelFrame(self.root, text="Device", padding=6)
        frame.pack(fill='x', padx=6, pady=(6, 0))

        ttk.Label(frame, text="Port:").pack(side='left')
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(frame, textvariable=self.port_var,
                                        width=58)
        self.port_combo.pack(side='left', padx=6)

        self.refresh_btn = ttk.Button(frame, text="Refresh",
                                       command=self._refresh_ports)
        self.refresh_btn.pack(side='left')

        self.connect_btn = ttk.Button(frame, text="Connect",
                                       command=self._connect)
        self.connect_btn.pack(side='left', padx=4)

        self.disconnect_btn = ttk.Button(frame, text="Disconnect",
                                          command=self._disconnect,
                                          state='disabled')
        self.disconnect_btn.pack(side='left')

        self.id_var = tk.StringVar(value="Not connected")
        self.id_label = tk.Label(frame, textvariable=self.id_var,
                                  fg='#888', font=('TkDefaultFont', 10, 'bold'))
        self.id_label.pack(side='left', padx=14)

    def _build_export_tab(self):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text="  Export  ")

        # ---- Output file ----
        row = ttk.Frame(f); row.pack(fill='x', padx=8, pady=8)
        ttk.Label(row, text="Output CSV:").pack(side='left')
        self.export_path_var = tk.StringVar(value="fluke975.csv")
        ttk.Entry(row, textvariable=self.export_path_var, width=54).pack(
            side='left', padx=6)
        ttk.Button(row, text="Browse...",
                   command=self._browse_export).pack(side='left')

        # ---- Options row ----
        opt = ttk.LabelFrame(f, text="Options", padding=6)
        opt.pack(fill='x', padx=8, pady=(0, 8))

        line1 = ttk.Frame(opt); line1.pack(fill='x', pady=2)
        self.use_since_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(line1, text="Since:",
                        variable=self.use_since_var).pack(side='left')
        self.since_var = tk.StringVar()
        ttk.Entry(line1, textvariable=self.since_var, width=12).pack(
            side='left', padx=4)
        ttk.Label(line1, text="YYYY-MM-DD").pack(side='left', padx=(0, 12))

        self.overwrite_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(line1, text="Overwrite",
                        variable=self.overwrite_var).pack(side='left')
        self.verbose_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(line1, text="Verbose",
                        variable=self.verbose_var).pack(side='left', padx=10)
        self.fast_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(line1, text="Fast mode",
                        variable=self.fast_var).pack(side='left')

        # ---- Scan range ----
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
        self.read_timeout_var = tk.DoubleVar(value=1.0)
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

        # ---- Buttons ----
        btn = ttk.Frame(f); btn.pack(fill='x', padx=8, pady=6)
        self.export_btn = ttk.Button(btn, text="  Export All  ",
                                       command=self._start_export)
        self.export_btn.pack(side='left')
        self.cancel_btn = ttk.Button(btn, text="Cancel",
                                       command=self._cancel_op,
                                       state='disabled')
        self.cancel_btn.pack(side='left', padx=8)
        self.probe_btn = ttk.Button(btn, text="Probe meter speed",
                                      command=self._probe_speed)
        self.probe_btn.pack(side='left', padx=8)

        # ---- Progress ----
        self.progress = ttk.Progressbar(f, mode='determinate', maximum=1000)
        self.progress.pack(fill='x', padx=8)
        self.progress_label_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.progress_label_var,
                  font=('Courier', 9)).pack(anchor='w', padx=8, pady=2)

        # ---- Sessions ----
        ttk.Label(f, text="Sessions found:").pack(
            anchor='w', padx=8, pady=(8, 0))

        sess_frame = ttk.Frame(f); sess_frame.pack(fill='both', expand=True,
                                                    padx=8, pady=4)
        cols = ('session', 'records', 'start', 'end', 'duration')
        self.sess_tree = ttk.Treeview(sess_frame, columns=cols,
                                       show='headings', height=10)
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
                                 font=('Courier', 9))
        sb = ttk.Scrollbar(wrap, orient='vertical',
                            command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        self.log_text.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

    def _build_status_bar(self):
        bar = ttk.Frame(self.root)
        bar.pack(fill='x', padx=6, pady=(0, 6))
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(bar, textvariable=self.status_var, relief='sunken',
                  anchor='w', padding=(4, 2)).pack(fill='x')

    # ---------------------------------------------------- ports / connect ---

    def _refresh_ports(self):
        if self._busy():
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
        if self._busy():
            messagebox.showinfo("Busy", "Wait for the current operation.")
            return
        if self.ser:
            self._log("Already connected.")
            return
        port = self._selected_port()
        if not port:
            messagebox.showerror("Error", "Select a port first.")
            return

        self._set_status("Connecting...")
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
        if self._busy():
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
        self._log("Disconnected.")
        self._set_status("Disconnected")

    def _set_connection_badge(self, id_text):
        if id_text:
            self.id_var.set(f"● {id_text}")
            self.id_label.configure(fg='#106010')
        else:
            self.id_var.set("Not connected")
            self.id_label.configure(fg='#888')

    # ------------------------------------------------------ Probe meter ---

    def _probe_speed(self):
        if self._busy():
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

    # ------------------------------------------------------------ Export ---

    def _start_export(self):
        if self._busy():
            messagebox.showinfo("Busy", "An operation is already running.")
            return
        if not self.ser:
            messagebox.showwarning("Not connected", "Connect to the device first.")
            return
        path = self.export_path_var.get().strip()
        if not path:
            messagebox.showerror("Error", "Enter an output filename.")
            return

        since = None
        if self.use_since_var.get():
            since = self.since_var.get().strip()
            if since:
                try:
                    datetime.strptime(since, "%Y-%m-%d")
                except ValueError:
                    messagebox.showerror("Error", "Since must be YYYY-MM-DD.")
                    return

        opts = {
            'out': path,
            'overwrite': self.overwrite_var.get(),
            'since': since,
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
        self._partial_save_path = opts['out'] + '.partial.csv'
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
            self.msg_q.put(('log',
                f"Scan blocks 0..{opts['max_blocks']}, "
                f"silence={opts['silence']}s, read={opts['read_timeout']}s, "
                f"stop_after={opts['stop_threshold']}, "
                f"time_limit={opts['time_limit']/60:.0f}min"))
            records = self._scan_adaptive(ser, opts, start_time)

            if self._is_cancelled():
                self._assign_sessions(records)
                if records:
                    try:
                        self._write_csv(opts['out'], records, opts['overwrite'])
                        self.msg_q.put(('log',
                            f"Cancelled — partial export: {len(records)} records"))
                    except Exception as e:
                        self.msg_q.put(('log',
                            f"Cancelled — save failed: {e}"))
                else:
                    self.msg_q.put(('log', "Cancelled — no records."))
                self._finish("Cancelled.", records, opts)
                return

            if opts['since']:
                since_ts = datetime.strptime(opts['since'], "%Y-%m-%d")
                before = len(records)
                records = [r for r in records if r['timestamp'] >= since_ts]
                if before - len(records):
                    self.msg_q.put(('log',
                        f"Date filter removed {before - len(records)} records."))

            self._assign_sessions(records)

            self.msg_q.put(('log',
                f"Writing {opts['out']} ({len(records)} records)..."))
            self._write_csv(opts['out'], records, opts['overwrite'])
            elapsed = time.time() - start_time
            self._finish(
                f"Export complete: {len(records)} records in {elapsed:.0f}s.",
                records, opts)

        except Exception as e:
            import traceback
            self.msg_q.put(('log', f"ERROR: {e}\n{traceback.format_exc()}"))
            self.msg_q.put(('error', str(e)))
            self._finish("Export failed.", records, opts)
        finally:
            dlall._shutdown = False

    def _scan_adaptive(self, ser, opts, start_time):
        """
        Adaptive region scan with fast timings and QD 1 early termination.
        """
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
        last_partial_save = 0

        newest_ts_seen = None
        last_qd1_check = 0

        def read_block(b):
            nonlocal blocks_read
            if b < 0 or b >= max_blocks:
                return None
            blocks_read += 1
            t0 = time.time()
            resp, err = dlall.fetch_block(ser, b)
            dt = time.time() - t0
            if verbose and dt > 1.0:
                self.msg_q.put(('log', f"  (slow block {b}: {dt*1000:.0f} ms)"))
            if resp and not err:
                recs, _ = dlall.parse_block(resp)
                return recs
            return None

        def collect(recs):
            nonlocal newest_ts_seen
            if not recs:
                return 0
            n = 0
            for r in recs:
                key = (r['record_type'], r['record'], r['timestamp'])
                if key not in seen:
                    seen.add(key)
                    all_records.append(r)
                    n += 1
                if newest_ts_seen is None or r['timestamp'] > newest_ts_seen:
                    newest_ts_seen = r['timestamp']
            return n

        def emit_status(pos):
            elapsed = time.time() - start_time
            rate = blocks_read / elapsed if elapsed > 0 else 0
            remaining = max(0, max_blocks - pos)
            eta = remaining / rate if rate > 0.01 else 0
            self.msg_q.put(('progress_update',
                (pos, max_blocks, len(all_records), blocks_read,
                 regions_found, rate, eta)))

        def save_partial():
            nonlocal last_partial_save
            now = time.time()
            if now - last_partial_save < 30:
                return
            last_partial_save = now
            try:
                self._assign_sessions(list(all_records))
                self._write_csv(self._partial_save_path, all_records, True)
                self.msg_q.put(('log',
                    f"  (saved {len(all_records)} records → "
                    f"{self._partial_save_path})"))
            except Exception as e:
                self.msg_q.put(('log', f"  (partial save failed: {e})"))

        def check_qd1():
            """Ask meter how many sessions exist. Returns count or None."""
            try:
                cmd = dlall.add_crc(b"QD 1\r")
                ser.reset_input_buffer()
                ser.write(cmd)
                ser.flush()
                t0 = time.time()
                buf = bytearray()
                while time.time() - t0 < 1.0:
                    n = ser.in_waiting
                    if n:
                        buf.extend(ser.read(n))
                        time.sleep(0.15)
                        if not ser.in_waiting:
                            break
                    else:
                        time.sleep(0.02)
                if not buf or not dlall.verify_crc(bytes(buf)):
                    return None
                idx = buf.find(b"QD 1")
                if idx < 0 or idx + 5 > len(buf):
                    return None
                return buf[idx + 4]
            except Exception:
                return None

        pos = 0
        while pos < max_blocks:
            if self._is_cancelled():
                break
            if time.time() - start_time > time_limit:
                self.msg_q.put(('log',
                    f"Time limit ({time_limit/60:.0f} min) reached — "
                    f"stopping scan."))
                break

            # ---- linear scan through current region ----
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
                            f"  block {pos}: +{new} "
                            f"({recs[0]['timestamp']} → "
                            f"{recs[-1]['timestamp']})"))
                else:
                    consec_empty += 1
                pos += 1
                if blocks_read - last_status_emit >= 20:
                    last_status_emit = blocks_read
                    emit_status(pos)
                    save_partial()

            if pos >= max_blocks or self._is_cancelled():
                break

            if pos > region_start:
                regions_found += 1

            # ---- QD 1 session check every ~500 blocks ----
            if blocks_read - last_qd1_check >= 500:
                last_qd1_check = blocks_read
                count = check_qd1()
                if count is not None:
                    found = self._count_sessions(all_records)
                    self.msg_q.put(('log',
                        f"  QD 1: meter reports {count} sessions; "
                        f"found {found} so far"))
                    if found >= count:
                        self.msg_q.put(('log',
                            f"  All {count} sessions found — stopping."))
                        break

            # ---- gap: probe forward ----
            self.msg_q.put(('log',
                f"Gap at block {pos}. Probing up to {max_gap_probe}..."))
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
                    f"No data within {max_gap_probe} blocks of block {pos} — "
                    f"scan complete."))
                break

            # ---- binary search for region start ----
            lo, hi = pos - 1, found_pos
            self.msg_q.put(('log',
                f"Data at block {found_pos} "
                f"(+{found_pos - pos + 1}). Binary search for start..."))
            while hi - lo > 1:
                if self._is_cancelled():
                    break
                mid = (lo + hi) // 2
                if read_block(mid):
                    hi = mid
                else:
                    lo = mid
                emit_status(hi)

            self.msg_q.put(('log', f"Resuming at block {hi}."))
            pos = hi

        elapsed = time.time() - start_time
        rate = blocks_read / elapsed if elapsed > 0 else 0
        self.msg_q.put(('log',
            f"Scan complete: {blocks_read} blocks read, "
            f"{regions_found} regions, {len(all_records)} records, "
            f"{elapsed:.0f}s ({rate:.1f} blk/s)."))
        return all_records

    def _count_sessions(self, records, gap_seconds=30.0):
        if not records:
            return 0
        srt = sorted(records, key=lambda r: r['timestamp'])
        sid = 1
        prev = srt[0]['timestamp']
        for r in srt[1:]:
            if (r['timestamp'] - prev).total_seconds() > gap_seconds:
                sid += 1
            prev = r['timestamp']
        return sid

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
                w.writerow(['session', 'record_type', 'record', 'timestamp',
                            'temperature_c', 'rh_percent', 'dewpoint_c',
                            'wetbulb_c', 'co_ppm', 'co2_ppm', 'extra'])
            for r in records:
                w.writerow([
                    r.get('session', 0),
                    f"0x{r['record_type']:04X}",
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

    # ------------------------------------------- Cancel / busy / messages ---

    def _cancel_op(self):
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
        self.connect_btn.configure(
            state='normal' if not self.ser else 'disabled')
        self.disconnect_btn.configure(
            state='normal' if self.ser else 'disabled')
        self.refresh_btn.configure(state='normal')
        self.export_btn.configure(state='normal')
        self.probe_btn.configure(state='normal')
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
                    self._set_connection_badge(data)
                    self.disconnect_btn.configure(state='normal')
                    self.connect_btn.configure(state='disabled')
                    self._log(f"Connected: {data}")
                    self._set_status("Connected")
                elif kind == 'connect_fail':
                    self._set_connection_badge(None)
                    self.ser = None
                    self._log(f"Connect failed: {data}")
                    messagebox.showerror("Connect failed", data)
                elif kind == 'log':
                    self._log(data)
                elif kind == 'progress_update':
                    pos, total, nrec, nread, nreg, rate, eta = data
                    self.progress['value'] = pos
                    eta_str = (f"{eta/60:.1f}min" if eta > 0 else "?")
                    self.progress_label_var.set(
                        f"block {pos:,}/{total:,} | {nrec:,} recs | "
                        f"{rate:.1f} blk/s | ETA {eta_str}")
                elif kind == 'export_done':
                    self._on_export_done(data)
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
        if self._busy():
            if not messagebox.askyesno(
                    "Quit", "An operation is running. Cancel and exit?"):
                return
            self.cancel.set()
            dlall._shutdown = True
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
