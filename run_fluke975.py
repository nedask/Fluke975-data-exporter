#!/usr/bin/env python3
"""
Drop-in wrapper for fluke975.py that fixes:
  1. Fluke 975's reset-on-open behavior (warm-up + wait + reopen).
  2. The original script's timeout=0.1, far too short for QD responses.
  3. Missing inter-command pacing between ID and QD commands.

Env vars:
  FLUKE_DEBUG=0       silence the wire log
  FLUKE_TIMEOUT=5.0   force at least this read timeout (seconds)
  FLUKE_PREWRITE=0.3  delay before each write after a read (seconds)
"""

import sys
import os
import time
import glob

DEBUG = os.environ.get('FLUKE_DEBUG', '1') == '1'
FORCED_TIMEOUT = float(os.environ.get('FLUKE_TIMEOUT', '5.0'))
PRE_WRITE_DELAY = float(os.environ.get('FLUKE_PREWRITE', '0.3'))

import serial
import serial.serialutil


def _find_fluke_by_id():
    for link in glob.glob('/dev/serial/by-id/*'):
        if '0f7e' in os.path.basename(link).lower():
            return link
    return None


def _resolve_to_by_id(port):
    if port and 'by-id' in port:
        return port
    if port and os.path.exists(port):
        try:
            real = os.path.realpath(port)
            for link in glob.glob('/dev/serial/by-id/*'):
                try:
                    if os.path.realpath(link) == real:
                        return link
                except OSError:
                    continue
        except OSError:
            pass
    fluke = _find_fluke_by_id()
    return fluke or port


def _wait_for_port(port, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(port):
            return True
        time.sleep(0.2)
    return False


_OriginalSerial = serial.Serial


class SafeSerial(_OriginalSerial):
    def __init__(self, *args, **kwargs):
        if 'port' in kwargs:
            port = kwargs['port']
        elif args:
            port = args[0]
            args = args[1:]
        else:
            raise TypeError("SafeSerial requires a port")

        # --- THE KEY FIX ---
        # Original script passes timeout=0.1; that's far too short for
        # the QD commands. Force a generous read timeout.
        orig_to = kwargs.get('timeout')
        kwargs['timeout'] = max(orig_to or 0.0, FORCED_TIMEOUT)
        kwargs.setdefault('inter_byte_timeout', 0.5)
        if orig_to is not None and orig_to < FORCED_TIMEOUT:
            print(f"[safe_open] overriding timeout {orig_to}s -> "
                  f"{kwargs['timeout']}s", file=sys.stderr)

        port = _resolve_to_by_id(port)
        kwargs['port'] = port
        print(f"[safe_open] target port: {port}", file=sys.stderr)

        # ---- Warm-up open: triggers the meter's reset, then close ----
        warmup_ok = False
        try:
            super().__init__(*args, **kwargs)
            try:
                self.dtr = False
                self.rts = False
            except Exception:
                pass
            time.sleep(0.5)
            self.close()
            warmup_ok = True
        except Exception as e:
            print(f"[safe_open] warm-up open failed: {e}", file=sys.stderr)

        # Re-resolve in case the tty number changed
        port = _resolve_to_by_id(kwargs['port'])
        kwargs['port'] = port

        if warmup_ok:
            if not _wait_for_port(port, timeout=8.0):
                print(f"[safe_open] WARNING: {port} did not reappear",
                      file=sys.stderr)
            else:
                time.sleep(1.5)

        # ---- Real open, with retry ----
        last_err = None
        for attempt in range(5):
            try:
                super().__init__(*args, **kwargs)
                try:
                    self.dtr = False
                    self.rts = False
                except Exception:
                    pass
                time.sleep(0.3)
                try:
                    self.reset_input_buffer()
                    self.reset_output_buffer()
                except Exception:
                    pass
                print(f"[safe_open] ready on {port} "
                      f"(timeout={self.timeout}s, ibt={self.inter_byte_timeout}s)",
                      file=sys.stderr)
                self._last_read_time = 0.0
                return
            except Exception as e:
                last_err = e
                print(f"[safe_open] open attempt {attempt+1} failed: {e}",
                      file=sys.stderr)
                time.sleep(1.0)
                port = _resolve_to_by_id(kwargs['port'])
                kwargs['port'] = port

        raise last_err if last_err else RuntimeError("Could not open port")

    # ---- wire log + pacing ----
    def write(self, data):
        # Pace: pause briefly before writing if we just read
        now = time.time()
        last_read = getattr(self, '_last_read_time', 0.0)
        gap = now - last_read
        if last_read and gap < PRE_WRITE_DELAY:
            time.sleep(PRE_WRITE_DELAY - gap)
        if DEBUG:
            print(f"[TX {len(data):3d}] {data.hex(' ')}", file=sys.stderr)
        return super().write(data)

    def read(self, size=1):
        data = super().read(size)
        if data:
            self._last_read_time = time.time()
        if DEBUG and data:
            print(f"[RX {len(data):3d}] {data.hex(' ')}", file=sys.stderr)
        return data

    def readline(self):
        data = super().readline()
        if data:
            self._last_read_time = time.time()
        if DEBUG and data:
            print(f"[RXl{len(data):3d}] {data.hex(' ')}", file=sys.stderr)
        return data

    def read_until(self, expected=b'\n', size=None):
        data = super().read_until(expected, size)
        if data:
            self._last_read_time = time.time()
        if DEBUG and data:
            print(f"[RXu{len(data):3d}] {data.hex(' ')}", file=sys.stderr)
        return data


serial.Serial = SafeSerial
serial.serialutil.Serial = SafeSerial


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fluke975  # noqa: E402

if __name__ == "__main__":
    fluke975.main()
