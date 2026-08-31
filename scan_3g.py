#!/usr/bin/python3
"""WCDMA UARFCN scanner: capture, cell search, BCH/SIB, print like gr-gsm.

Each UARFCN prints one cell block to stdout as soon as it is done.
Timestamped files go to results/<YYYYMMDD-HHMMSS>.{json,csv,log}.

  python3 scan_3g.py
  python3 scan_3g.py --uarfcn 10813
  python3 scan_3g.py --check
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CAPTURE_PY = ROOT / "uhd_capture.py"
DEFAULT_OUT_DIR = ROOT / "results"

DEFAULT_UARFCNS = (10813, 10838, 2997, 2938, 2963, 3018)
GAIN = {"b1": 32.0, "b8": 45.0}
ECNO_MIN_DB = -22.0
OPERATORS = {
    ("286", "01"): "Turkcell",
    ("286", "1"): "Turkcell",
    ("286", "02"): "Vodafone",
    ("286", "2"): "Vodafone",
    ("286", "03"): "Turk Telekom",
    ("286", "3"): "Turk Telekom",
}


def band_of(uarfcn: int) -> str:
    if 10562 <= uarfcn <= 10838:
        return "b1"
    if 2937 <= uarfcn <= 3088:
        return "b8"
    raise ValueError(f"UARFCN {uarfcn} not Band 1 or Band 8")


def freq_mhz(uarfcn: int) -> float:
    if band_of(uarfcn) == "b1":
        return 2110.0 + 0.2 * (uarfcn - 10562)
    return 925.0 + 0.2 * (uarfcn - 2937)


def scan_stamp(when: datetime | None = None) -> str:
    return (when or datetime.now().astimezone()).strftime("%Y%m%d-%H%M%S")


def run_quiet(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, text


def load_json(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text())


def group_wcdma(neighbors: list[dict]) -> dict[int, list[int]]:
    by_u: dict[int, list[int]] = defaultdict(list)
    for n in neighbors:
        u, psc = n.get("uarfcn"), n.get("psc")
        if u is None or psc is None:
            continue
        by_u[int(u)].append(int(psc))
    return dict(sorted(by_u.items()))


def print_cell(cell: dict) -> None:
    if cell.get("psc") is None:
        print(
            f"UARFCN {cell['uarfcn']:5d}  {cell['freq_mhz']:7.1f} MHz  "
            f"B{cell['band']}  {cell.get('status') or 'empty'}"
            + (f"  {cell['note']}" if cell.get("note") else "")
        )
        return
    op = cell.get("operator") or ""
    print(
        f"UARFCN {cell['uarfcn']:5d}  {cell['freq_mhz']:7.1f} MHz  "
        f"PSC {cell['psc']:3d}  Ec/No {cell['ecno']:5.1f}  "
        f"RSCP {cell['rscp']:6.1f}  B{cell['band']}"
        + (f"  {op}" if op else "")
    )
    if cell.get("plmn") or cell.get("cid"):
        bits = []
        if cell.get("plmn"):
            bits.append(f"PLMN {cell['plmn']}")
        if cell.get("cid"):
            bits.append(f"CID {cell['cid']}")
        if cell.get("rnc"):
            bits.append(f"RNC {cell['rnc']}")
        print("    " + "  ".join(bits))
    wmap = group_wcdma(cell.get("wcdma_neighbors") or [])
    for u, pscs in wmap.items():
        print(f"    WCDMA {u}: " + " ".join(str(p) for p in pscs))
    lte = cell.get("lte_neighbors") or []
    if lte:
        print("    LTE " + " ".join(str(n.get("earfcn")) for n in lte))
    gsm = cell.get("gsm_neighbors") or []
    if gsm:
        print(
            "    GSM "
            + " ".join(f"{n.get('arfcn')}/{n.get('bsic')}" for n in gsm)
        )
    if cell.get("note") and cell.get("status") != "ok":
        print(f"    {cell['note']}")


def compile_asn():
    sys.path.insert(0, str(ROOT))
    import asn1tools  # noqa: WPS433

    files = [
        str(ROOT / "wcdma_rrc_asn1" / n)
        for n in (
            "Constant-definitions.asn",
            "Class-definitions.asn",
            "InformationElements.asn",
            "PDU-definitions.asn",
            "Internode-definitions.asn",
        )
    ]
    return asn1tools.compile_files(files, "uper")


def scan_uarfcn(
    uarfcn: int,
    duration: float,
    skip_sib: bool,
    captures: Path,
    db,
    serial: str | None = None,
) -> tuple[dict, str]:
    band = band_of(uarfcn)
    cell: dict = {
        "uarfcn": uarfcn,
        "freq_mhz": round(freq_mhz(uarfcn), 1),
        "band": "1" if band == "b1" else "8",
        "serial": "",
        "gain_db": GAIN[band],
        "status": "empty",
        "note": "",
        "psc": None,
        "ecno": None,
        "rscp": None,
        "plmn": "",
        "operator": "",
        "cid": "",
        "rnc": "",
        "wcdma_neighbors": [],
        "lte_neighbors": [],
        "gsm_neighbors": [],
    }
    log: list[str] = [f"===== UARFCN {uarfcn} ====="]
    cfile = captures / f"uarfcn_{uarfcn}.cfile"
    print(
        f"# {uarfcn}  {cell['freq_mhz']:.1f} MHz  B{cell['band']}  "
        f"gain={cell['gain_db']:.0f}",
        file=sys.stderr,
        flush=True,
    )

    cap_cmd = [
        sys.executable,
        str(CAPTURE_PY),
        "--uarfcn",
        str(uarfcn),
        "--duration",
        str(duration),
        "--gain",
        str(cell["gain_db"]),
        "--output",
        str(cfile),
    ]
    if serial:
        cap_cmd.extend(["--serial", serial])
    rc, text = run_quiet(
        cap_cmd,
        ROOT,
        timeout=max(90, int(duration) + 60),
    )
    log.append(text)
    if rc != 0 or not cfile.exists():
        cell["status"] = "capture-fail"
        cell["note"] = f"capture exit {rc}"
        return cell, "\n".join(log)

    rc, text = run_quiet(
        [sys.executable, "wcdma_cellsearch.py", "--input", str(cfile)],
        ROOT,
        timeout=300,
    )
    log.append(text)
    results = load_json(Path(str(cfile) + ".results.json"))
    found = (results or {}).get("cells") or []
    if not found:
        cell["status"] = "no-cell"
        cell["note"] = "cell search empty"
        return cell, "\n".join(log)

    top = found[0]
    cell["psc"] = int(top["scrambling_code"])
    cell["ecno"] = round(float(top["cpich_ecno_db"]), 2)
    cell["rscp"] = round(float(top["cpich_rscp_dbm"]), 2)
    if cell["ecno"] < ECNO_MIN_DB:
        cell["status"] = "weak"
        cell["note"] = f"Ec/No {cell['ecno']:.1f} < {ECNO_MIN_DB:.0f}"
        return cell, "\n".join(log)
    cell["status"] = "cell"
    if skip_sib:
        return cell, "\n".join(log)

    bch_path = captures / f"uarfcn_{uarfcn}_sc{cell['psc']}.bch.json"
    rc, text = run_quiet(
        [
            sys.executable,
            "wcdma_bch_decode.py",
            "--input",
            str(cfile),
            "--output",
            str(bch_path),
            "--max-frames",
            "96",
        ],
        ROOT,
        timeout=600,
    )
    log.append(text)
    if rc != 0 or not bch_path.exists():
        cell["status"] = "bch-fail"
        cell["note"] = f"BCH exit {rc}"
        return cell, "\n".join(log)

    sys.path.insert(0, str(ROOT))
    import contextlib
    import io

    import wcdma_scan_report as report  # noqa: WPS433

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sib = report.decode_sib_data(str(bch_path), db)
    log.append(buf.getvalue())
    if not sib:
        cell["status"] = "sib-fail"
        cell["note"] = "SIB assemble empty"
        return cell, "\n".join(log)

    cell["status"] = "ok"
    if sib.get("mcc") not in (None, "N/A"):
        mnc = str(sib["mnc"])
        if mnc.isdigit() and len(mnc) == 1:
            mnc = "0" + mnc
        cell["plmn"] = f"{sib['mcc']}{mnc}"
        cell["operator"] = OPERATORS.get((str(sib["mcc"]), str(sib["mnc"])), "")
    if sib.get("cell_id") not in (None, "N/A"):
        cell["cid"] = str(sib["cell_id"])
    if sib.get("rnc_id") not in (None, "N/A"):
        cell["rnc"] = str(sib["rnc_id"])
    cell["wcdma_neighbors"] = sib.get("wcdma_neighbors") or []
    cell["lte_neighbors"] = sib.get("lte_neighbors") or []
    cell["gsm_neighbors"] = sib.get("gsm_neighbors") or []
    return cell, "\n".join(log)


def write_csv(path: Path, cells: list[dict]) -> None:
    cols = [
        "uarfcn",
        "freq_mhz",
        "band",
        "psc",
        "ecno",
        "rscp",
        "plmn",
        "operator",
        "cid",
        "rnc",
        "status",
        "note",
    ]
    lines = [",".join(cols)]
    for cell in cells:
        row = []
        for col in cols:
            val = cell.get(col)
            text = "" if val is None else str(val)
            if any(ch in text for ch in ",\"\n"):
                text = '"' + text.replace('"', '""') + '"'
            row.append(text)
        lines.append(",".join(row))
    path.write_text("\n".join(lines) + "\n")


def preflight() -> int:
    import uhd_capture

    errors: list[str] = []
    if sys.version_info < (3, 10):
        errors.append(f"python 3.10+ required (have {sys.version.split()[0]})")
    for mod in ("numpy", "scipy", "asn1tools", "uhd"):
        try:
            __import__(mod)
        except ImportError:
            errors.append(f"missing python module: {mod}")
    img = uhd_capture.apply_uhd_images()
    if img is None:
        errors.append("UHD FPGA images not found (set UHD_IMAGES_DIR or install uhd-host)")
    else:
        print(f"UHD_IMAGES_DIR={img}", file=sys.stderr)
    try:
        import uhd

        n = len(list(uhd.find("")))
        print(f"UHD devices: {n}", file=sys.stderr)
        if n == 0:
            errors.append("no UHD device (plug a B210 on USB3)")
    except Exception as exc:
        errors.append(f"UHD enumerate failed: {exc}")
    for name in ("captures", "results"):
        p = ROOT / name
        try:
            p.mkdir(parents=True, exist_ok=True)
            t = p / ".write_test"
            t.write_text("ok")
            t.unlink()
        except OSError as exc:
            errors.append(f"cannot write {p}: {exc}")
    if errors:
        for e in errors:
            print(f"FAIL  {e}", file=sys.stderr)
        return 1
    print("check ok", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WCDMA UARFCN scanner (first free USRP)")
    parser.add_argument(
        "--uarfcn",
        type=int,
        nargs="+",
        default=list(DEFAULT_UARFCNS),
        help="UARFCN list (default: 10813 10838 2997 2938 2963 3018)",
    )
    parser.add_argument("--duration", type=float, default=3.0, help="IQ capture seconds (default 3)")
    parser.add_argument("--skip-sib", action="store_true")
    parser.add_argument("--serial", type=str, default=None, help="Optional USRP serial; default = first free")
    parser.add_argument("--check", action="store_true", help="Verify deps and radios, then exit")
    parser.add_argument("-o", "--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    import uhd_capture

    uhd_capture.apply_uhd_images()
    if args.check:
        return preflight()

    captures = ROOT / "captures"
    captures.mkdir(parents=True, exist_ok=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    started = datetime.now().astimezone()
    stamp = scan_stamp(started)
    paths = {
        "json": args.out_dir / f"{stamp}.json",
        "csv": args.out_dir / f"{stamp}.csv",
        "log": args.out_dir / f"{stamp}.log",
    }

    db = None if args.skip_sib else compile_asn()
    cells: list[dict] = []
    logs: list[str] = []
    t0 = time.time()

    def persist(status: str) -> None:
        payload = {
            "stamp": stamp,
            "status": status,
            "started": started.isoformat(),
            "finished": datetime.now().astimezone().isoformat(),
            "uarfcns": args.uarfcn,
            "duration": args.duration,
            "skip_sib": args.skip_sib,
            "cells": cells,
        }
        paths["json"].write_text(json.dumps(payload, indent=2, default=str) + "\n")
        write_csv(paths["csv"], cells)
        paths["log"].write_text("\n\n".join(logs) + "\n")

    try:
        for uarfcn in args.uarfcn:
            try:
                cell, log = scan_uarfcn(
                    uarfcn, args.duration, args.skip_sib, captures, db, args.serial
                )
            except subprocess.TimeoutExpired:
                cell = {
                    "uarfcn": uarfcn,
                    "freq_mhz": freq_mhz(uarfcn),
                    "band": "1" if band_of(uarfcn) == "b1" else "8",
                    "status": "timeout",
                    "note": "timeout",
                    "psc": None,
                }
                log = f"===== UARFCN {uarfcn} TIMEOUT ====="
            cells.append(cell)
            logs.append(log)
            print_cell(cell)
            persist("partial" if uarfcn != args.uarfcn[-1] else "complete")
    except KeyboardInterrupt:
        persist("interrupted")
        print("interrupted", file=sys.stderr)
        return 130

    elapsed = time.time() - t0
    n_ok = sum(1 for c in cells if c.get("status") in ("ok", "cell"))
    print(
        f"# wrote {paths['json']} {paths['csv']} {paths['log']}  "
        f"cells={n_ok}/{len(cells)}  {elapsed:.0f}s",
        file=sys.stderr,
    )
    return 0 if n_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
