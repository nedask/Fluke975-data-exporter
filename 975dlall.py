#!/usr/bin/env python3
"""
Full CSV downloader for Fluke 975 firmware 1C — fast edition.

Speed-ups vs previous version:
  * fetch_block stops the instant it has 268 bytes (was: wait 0.5s quiet).
  * Silence timeout is 0.6s (was: 3s per empty block).
  * --since uses a REVERSE scan: probes for the newest block with an
    exponential + binary search, then walks backwards, stopping as soon
    as a block's newest record is older than --since. Typically 10-20x
    faster than forward-scan-then-filter on a meter that has been logging
    for months or years.
  * New --reverse to force reverse scan without --since.
  * New --silence-timeout and --read-timeout to tune.
"""

import argparse
import atexit
import csv
import os
import signal
import struct
import sys
import time
from datetime import datetime

if '--verbose' not in sys.argv and '-v' not in sys.argv:
    os.environ.setdefault('FLUKE_DEBUG', '0')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_fluke975 as wrapper

if '--verbose' not in sys.argv and '-v' not in sys.argv:
    wrapper.DEBUG = False


RECORD_TYPE_OLD = 0x01D8
RECORD_TYPE_NEW = 0x03F8

BLOCK_RESPONSE_LEN = 268          # 10 header + 256 payload + 2 CRC
PAYLOAD_RECORD_SIZE = 32
PAYLOAD_RECORDS = 8


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

def crc16_modbus(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def add_crc(data):
    return data + struct.pack("<H", crc16_modbus(data))


def verify_crc(data):
    return len(data) >= 3 and struct.unpack("<H", data[-2:])[0] == crc16_modbus(data[:-2])


def make_qd2(block):
    return add_crc(b"QD 2\x00" + struct.pack("<H", block) + b"\x00\r")


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

def bcd(v):
    return ((v >> 4) * 10) + (v & 0x0F)


def decode_timestamp(raw):
    return datetime(2000 + bcd(raw[5]), bcd(raw[4]), bcd(raw[3]),
                    bcd(raw[0]), bcd(raw[1]), bcd(raw[2]))


def decode_record(data):
    if len(data) < PAYLOAD_RECORD_SIZE:
        return None
    record_type = struct.unpack_from("<H", data, 0)[0]
    record_num = struct.unpack_from("<H", data, 2)[0]
    if record_num == 0:
        return None
    try:
        ts = decode_timestamp(data[4:10])
    except ValueError:
        return None

    temp = struct.unpack_from("<h", data, 10)[0] / 10.0
    rh   = struct.unpack_from("<H", data, 12)[0] / 100.0
    dew  = struct.unpack_from("<h", data, 14)[0] / 10.0
    wet  = struct.unpack_from("<h", data, 16)[0] / 10.0
    co   = struct.unpack_from("<H", data, 18)[0]

    if record_type == RECORD_TYPE_NEW:
        co2   = struct.unpack_from("<H", data, 20)[0]
        extra = struct.unpack_from("<H", data, 22)[0]
    else:
        co2   = struct.unpack_from("<I", data, 20)[0]
        extra = 0

    return {
        "record_type": record_type,
        "record": record_num,
        "timestamp": ts,
        "temperature_c": temp,
        "rh_percent": rh,
        "dewpoint_c": dew,
        "wetbulb_c": wet,
        "co_ppm": co,
        "co2_ppm": co2,
        "extra": extra,
    }


def parse_block(resp):
    if not resp or not resp.startswith(b"0\rQD 2"):
        return None, "not a QD 2 response"
    if len(resp) < 266:
        return None, f"short ({len(resp)} bytes)"
    if not verify_crc(resp):
        return None, "bad CRC"
    payload = resp[10:-2]
    out = []
    for i in range(0, 256, PAYLOAD_RECORD_SIZE):
        r = decode_record(payload[i:i + PAYLOAD_RECORD_SIZE])
        if r is not None:
            out.append(r)
    return out, None


# ---------------------------------------------------------------------------
# Serial I/O — the speed fix lives here
# ---------------------------------------------------------------------------

_shutdown = False

# Tunables set from CLI
_SILENCE_TIMEOUT = 0.6      # s to wait before declaring "no response"
_READ_TIMEOUT = 2.5         # s total per block


def fetch_block(ser, block):
    """
    Fast QD 2 fetch.

      * Stops as soon as BLOCK_RESPONSE_LEN bytes have arrived.
      * Declares silence after _SILENCE_TIMEOUT with zero bytes received.
      * Caps total per-block time at _READ_TIMEOUT.
    """
    try:
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        ser.write(make_qd2(block))
        ser.flush()
    except Exception as e:
        return None, f"write: {e}"

    start = time.time()
    buf = bytearray()
    while True:
        if _shutdown:
            return None, "interrupted"

        elapsed = time.time() - start
        if elapsed >= _READ_TIMEOUT:
            break

        try:
            n = ser.in_waiting
        except Exception as e:
            return None, f"read: {e}"

        if n:
            try:
                buf.extend(ser.read(n))
            except Exception as e:
                return None, f"read: {e}"
            # Full response received — done, no more waiting
            if len(buf) >= BLOCK_RESPONSE_LEN:
                break
            # NAK (1\r + CRC) is a fast reject — no need to wait
            if len(buf) >= 4 and buf[:2] == b"1\r":
                break
        else:
            # No bytes yet — is it silence?
            if not buf and elapsed >= _SILENCE_TIMEOUT:
                return None, "silence"
            time.sleep(0.01)

    return (bytes(buf) if buf else None), (None if buf else "silence")


# ---------------------------------------------------------------------------
# Scan strategies
# ---------------------------------------------------------------------------

def scan_forward(ser, start_block, max_blocks, stop_after_empty, verbose):
    """Walk from start_block upward until N consecutive blocks yield no new records."""
    all_records = []
    seen = set()
    last_data_block = start_block - 1
    consecutive_no_new = 0
    error_streak = 0
    scanned = 0

    for block in range(start_block, start_block + max_blocks):
        if _shutdown:
            break
        resp, err = fetch_block(ser, block)
        scanned += 1

        if err and err != "silence":
            error_streak += 1
            if verbose:
                print(f"  block {block:4d}: {err}", file=sys.stderr)
            if error_streak >= 3:
                print(f"  3 consecutive errors — aborting at block {block}.",
                      file=sys.stderr)
                break
            continue
        error_streak = 0

        if err == "silence":
            if verbose:
                print(f"  block {block:4d}: silence", file=sys.stderr)
            consecutive_no_new += 1
        else:
            records, perr = parse_block(resp)
            if perr:
                if verbose:
                    print(f"  block {block:4d}: {perr}", file=sys.stderr)
                consecutive_no_new += 1
            else:
                new_here = []
                for r in records:
                    key = (r["record_type"], r["record"], r["timestamp"])
                    if key not in seen:
                        seen.add(key)
                        new_here.append(r)
                if new_here:
                    consecutive_no_new = 0
                    last_data_block = block
                    all_records.extend(new_here)
                    if verbose:
                        print(f"  block {block:4d}: +{len(new_here):2d} new  "
                              f"({new_here[0]['timestamp']} → "
                              f"{new_here[-1]['timestamp']})", file=sys.stderr)
                else:
                    consecutive_no_new += 1
                    if verbose:
                        print(f"  block {block:4d}: {len(records)} records "
                              f"(all stale)", file=sys.stderr)
        if consecutive_no_new >= stop_after_empty:
            if verbose:
                print(f"  {consecutive_no_new} no-new blocks — stopping.",
                      file=sys.stderr)
            break

    return all_records, last_data_block, scanned


def find_top_block(ser, max_probe, verbose):
    """
    Exponential probe + binary search for the highest block with data.

    Probe order: 1, 2, 4, 8, 16, 32, 64, ... until silence.
    Then binary search between last hit and first miss.
    """
    step = 1
    last_hit = 0
    last_miss = None
    probes = 0

    while step <= max_probe:
        resp, err = fetch_block(ser, step)
        probes += 1
        if resp:
            records, perr = parse_block(resp)
            hit = records is not None and len(records) > 0
        else:
            hit = False
        if verbose:
            print(f"  probe block {step:4d}: {'hit' if hit else 'miss'}",
                  file=sys.stderr)
        if hit:
            last_hit = step
            step *= 2
        else:
            last_miss = step
            break

    if last_miss is None:
        # Data extends all the way to max_probe
        return last_hit, probes

    # Binary search between last_hit and last_miss
    lo, hi = last_hit, last_miss
    while hi - lo > 1:
        mid = (lo + hi) // 2
        resp, err = fetch_block(ser, mid)
        probes += 1
        if resp:
            records, perr = parse_block(resp)
            hit = records is not None and len(records) > 0
        else:
            hit = False
        if verbose:
            print(f"  binary block {mid:4d}: {'hit' if hit else 'miss'}",
                  file=sys.stderr)
        if hit:
            lo = mid
        else:
            hi = mid
    return lo, probes


def scan_reverse_since(ser, since_ts, top, verbose):
    """
    Walk backwards from `top`, collecting records until we hit a block
    whose newest record is older than `since_ts`.
    Assumes memory is chronological (verified on this meter).
    """
    all_records = []
    seen = set()
    scanned = 0

    for block in range(top, -1, -1):
        if _shutdown:
            break
        resp, err = fetch_block(ser, block)
        scanned += 1

        if err:
            if verbose:
                print(f"  block {block:4d}: {err}", file=sys.stderr)
            continue

        records, perr = parse_block(resp)
        if perr or not records:
            if verbose:
                print(f"  block {block:4d}: {perr or 'empty'}", file=sys.stderr)
            continue

        newest_in_block = max(r["timestamp"] for r in records)
        new_here = []
        for r in records:
            key = (r["record_type"], r["record"], r["timestamp"])
            if key not in seen:
                seen.add(key)
                new_here.append(r)

        if verbose:
            print(f"  block {block:4d}: {len(records)} records, "
                  f"newest={newest_in_block}", file=sys.stderr)

        if newest_in_block < since_ts:
            # This entire block (and everything below) predates --since
            if verbose:
                print(f"  newest in block < since — stopping at block {block}",
                      file=sys.stderr)
            break

        all_records.extend(new_here)

    return all_records, scanned


# ---------------------------------------------------------------------------
# Session split + helpers
# ---------------------------------------------------------------------------

def split_sessions(records, gap_seconds):
    records.sort(key=lambda r: r["timestamp"])
    session_id = 0
    prev = None
    for r in records:
        if prev is None:
            session_id += 1
        elif (r["timestamp"] - prev["timestamp"]).total_seconds() > gap_seconds:
            session_id += 1
        r["session"] = session_id
        prev = r
    return session_id


def count_by_type(records):
    counts = {}
    for r in records:
        counts[r["record_type"]] = counts.get(r["record_type"], 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

_cleanup_hooks = []
_cleanup_done = False


def _register(fn):
    _cleanup_hooks.append(fn)


def _run_cleanup(reason=""):
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True
    if reason:
        print(f"\n{reason}", file=sys.stderr)
    for fn in _cleanup_hooks:
        try:
            fn()
        except Exception as e:
            print(f"cleanup: {e}", file=sys.stderr)


def _on_signal(signum, frame):
    global _shutdown
    _shutdown = True


def _install_signal_handlers():
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass


atexit.register(lambda: _run_cleanup("atexit"))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global _SILENCE_TIMEOUT, _READ_TIMEOUT

    ap = argparse.ArgumentParser(description="Fluke 975 full data export.")
    ap.add_argument("--out", default="fluke975.csv")
    ap.add_argument("--max-blocks", type=int, default=10000,
                    help="Max blocks to scan from --start-block (default: 500)")
    ap.add_argument("--start-block", type=int, default=0,
                    help="First block to scan (forward) or highest block (reverse)")
    ap.add_argument("--stop-after-empty", type=int, default=3,
                    help="Forward scan: stop after N consecutive no-new blocks")
    ap.add_argument("--gap-seconds", type=float, default=30.0)
    ap.add_argument("--since", metavar="YYYY-MM-DD", default=None,
                    help="Only export records on/after this date (auto-uses reverse scan)")
    ap.add_argument("--reverse", action="store_true",
                    help="Force reverse scan (use with --since or standalone)")
    ap.add_argument("--silence-timeout", type=float, default=0.6,
                    help="s before treating no-response as silence (default: 0.6)")
    ap.add_argument("--read-timeout", type=float, default=2.5,
                    help="s max per block (default: 2.5)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    _SILENCE_TIMEOUT = args.silence_timeout
    _READ_TIMEOUT = args.read_timeout

    since_ts = None
    if args.since:
        try:
            since_ts = datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError:
            ap.error("--since must be YYYY-MM-DD")

    use_reverse = args.reverse or (since_ts is not None and not args.start_block)

    _install_signal_handlers()

    port = wrapper.select_device()
    if not port:
        return
    print(f"Port: {port}", file=sys.stderr)

    ser = wrapper.SafeSerial(port=port, baudrate=9600, timeout=2)
    time.sleep(0.5)
    _register(lambda: ser.close() if ser else None)

    try:
        ser.reset_input_buffer()
        ser.write(b"ID\r")
        ser.flush()
        time.sleep(0.5)
        id_resp = ser.read(ser.in_waiting or 64)
    except Exception as e:
        print(f"ID failed: {e}", file=sys.stderr)
        _run_cleanup()
        return
    if b"FLUKE" not in id_resp:
        print("ID failed — power-cycle the meter and retry.", file=sys.stderr)
        _run_cleanup()
        return
    print(f"ID: {id_resp[2:-2].decode('ascii', 'replace').replace(chr(13), '').strip()}",
          file=sys.stderr)

    t0 = time.time()
    all_records = []
    last_data_block = -1
    scanned = 0

    if use_reverse:
        top_start = args.start_block if args.start_block else args.max_blocks - 1
        print(f"Reverse scan — probing for top block (<= {top_start})...",
              file=sys.stderr, flush=True)
        top, probes = find_top_block(ser, top_start, args.verbose)
        print(f"Top block = {top} (probed {probes} blocks in "
              f"{time.time() - t0:.1f}s)", file=sys.stderr, flush=True)

        print(f"Walking down from block {top}...", file=sys.stderr, flush=True)
        if since_ts:
            all_records, scanned = scan_reverse_since(ser, since_ts, top, args.verbose)
            last_data_block = top
        else:
            # Reverse without --since: scan down to block 0, stop after
            # N consecutive no-new blocks
            seen = set()
            consec = 0
            for block in range(top, -1, -1):
                if _shutdown:
                    break
                resp, err = fetch_block(ser, block)
                scanned += 1
                if err:
                    if args.verbose:
                        print(f"  block {block:4d}: {err}", file=sys.stderr)
                    consec += 1
                    if consec >= args.stop_after_empty:
                        break
                    continue
                recs, perr = parse_block(resp)
                if perr or not recs:
                    if args.verbose:
                        print(f"  block {block:4d}: {perr or 'empty'}",
                              file=sys.stderr)
                    consec += 1
                    if consec >= args.stop_after_empty:
                        break
                    continue
                new_here = []
                for r in recs:
                    key = (r["record_type"], r["record"], r["timestamp"])
                    if key not in seen:
                        seen.add(key)
                        new_here.append(r)
                if new_here:
                    consec = 0
                    last_data_block = max(last_data_block, block)
                    all_records.extend(new_here)
                    if args.verbose:
                        print(f"  block {block:4d}: +{len(new_here):2d} new",
                              file=sys.stderr)
                else:
                    consec += 1
                    if consec >= args.stop_after_empty:
                        break

    else:
        print(f"Forward scan from block {args.start_block}...",
              file=sys.stderr, flush=True)
        all_records, last_data_block, scanned = scan_forward(
            ser, args.start_block, args.max_blocks,
            args.stop_after_empty, args.verbose,
        )

    elapsed = time.time() - t0
    print(file=sys.stderr)

    if _shutdown and not all_records:
        print("Interrupted before any records were found.", file=sys.stderr)
        _run_cleanup()
        return

    # ---- Apply --since filter (reverse already filters, but keep for safety) ----
    if since_ts:
        before = len(all_records)
        all_records = [r for r in all_records if r["timestamp"] >= since_ts]
        if before != len(all_records):
            print(f"Filtered {before - len(all_records)} records earlier than "
                  f"{args.since}", file=sys.stderr)

    sessions = split_sessions(all_records, args.gap_seconds) if all_records else 0

    # ---- Summary ----
    print(f"\n=== Summary ===", file=sys.stderr)
    print(f"  scan mode        : {'reverse' if use_reverse else 'forward'}",
          file=sys.stderr)
    print(f"  blocks scanned   : {scanned}", file=sys.stderr)
    print(f"  last data block  : {last_data_block}", file=sys.stderr)
    print(f"  records found    : {len(all_records)}", file=sys.stderr)
    print(f"  sessions         : {sessions}", file=sys.stderr)
    if scanned and elapsed:
        print(f"  elapsed          : {elapsed:.1f}s "
              f"({scanned / elapsed:.1f} blocks/s)", file=sys.stderr)

    if all_records:
        for rtype, n in sorted(count_by_type(all_records).items()):
            print(f"    record_type 0x{rtype:04X} : {n}", file=sys.stderr)
        by_session = {}
        for r in all_records:
            by_session.setdefault(r["session"], []).append(r)
        for sid in sorted(by_session):
            recs = by_session[sid]
            print(f"  session {sid:2d}: {len(recs):4d} records  "
                  f"{recs[0]['timestamp']:%Y-%m-%d %H:%M:%S} → "
                  f"{recs[-1]['timestamp']:%Y-%m-%d %H:%M:%S}",
                  file=sys.stderr)

    # ---- Write CSV ----
    print(f"\nWriting {args.out} ...", file=sys.stderr)
    try:
        with open(args.out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "session", "record_type", "record", "timestamp",
                "temperature_c", "rh_percent", "dewpoint_c", "wetbulb_c",
                "co_ppm", "co2_ppm", "extra",
            ])
            for r in all_records:
                w.writerow([
                    r["session"],
                    f"0x{r['record_type']:04X}",
                    r["record"],
                    r["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                    f"{r['temperature_c']:.1f}",
                    f"{r['rh_percent']:.2f}",
                    f"{r['dewpoint_c']:.1f}",
                    f"{r['wetbulb_c']:.1f}",
                    r["co_ppm"],
                    r["co2_ppm"],
                    r["extra"],
                ])
    except Exception as e:
        print(f"CSV write failed: {e}", file=sys.stderr)
        _run_cleanup()
        return

    print(f"Done — {len(all_records)} rows in {args.out} "
          f"({elapsed:.1f}s)", file=sys.stderr)
    if _shutdown:
        print("(partial export due to interruption)", file=sys.stderr)

    _run_cleanup()


if __name__ == "__main__":
    main()
