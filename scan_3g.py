#!/usr/bin/python3
"""WCDMA UARFCN scanner: capture, cell search, BCH/SIB, print like gr-gsm.

Each UARFCN prints one cell block to stdout as soon as it is done.
Timestamped files go to results/<YYYYMMDD-HHMMSS>.{json,csv,log}.
IQ: default overwrites captures/uarfcn_<N>.cfile; --iq keeps
captures/<stamp>_uarfcn_<N>.cfile.

  python3 scan_3g.py --band 1 --uarfcn 10813
  python3 scan_3g.py --band 8 --uarfcn 2997
  python3 scan_3g.py --iq --band 8 --uarfcn 2938 2963
  python3 scan_3g.py --check
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_OUT_DIR = ROOT / "results"

DEFAULT_GAIN_DB = 40.0
HELP_EPILOG = """\
FDD bands (3GPP TS 25.101):  F_DL = N/5 + FDL_Offset
  --band 1   2100   UARFCN 10562-10838
  --band 2   1900   UARFCN 9662-9938
  --band 4   1700   UARFCN 1537-1738
  --band 5    850   UARFCN 4357-4458
  --band 6    800   UARFCN 4387-4413
  --band 8    900   UARFCN 2937-3088
  --band 19   850   UARFCN 712-763

  ./run-scan.sh --band 1 --uarfcn 10813
  ./run-scan.sh --band 8 --uarfcn 2997
  ./run-scan.sh --iq --band 8 --uarfcn 2938 2963
  ./run-scan.sh --uarfcn 10813

known Turkey UARFCNs (--band 1 or 8 without --uarfcn):
  TURKCELL      10563 10588 10613 10638 10813 10838 2997 3018
  VODAFONE TR   10663 10688 10713
  TURK TELEKOM  10738 10763 10788 2938 2963
"""
ECNO_MIN_DB = -22.0
OPERATORS = {
    ("286", "01"): "Turkcell",
    ("286", "1"): "Turkcell",
    ("286", "02"): "Vodafone",
    ("286", "2"): "Vodafone",
    ("286", "03"): "Turk Telekom",
    ("286", "3"): "Turk Telekom",
}


def band_of(uarfcn: int) -> int:
    from wcdma_bands import band_of_uarfcn

    return band_of_uarfcn(uarfcn)


def freq_mhz(uarfcn: int, band: int | None = None) -> float:
    from wcdma_bands import uarfcn_to_dl_mhz

    return uarfcn_to_dl_mhz(uarfcn, band)


def scan_stamp(when: datetime | None = None) -> str:
    return (when or datetime.now().astimezone()).strftime("%Y%m%d-%H%M%S")


def infer_serving_uarfcn(tuned: int, serving_psc: int, inter_neighbors: list[dict]) -> int | None:
    """SIB11 often lists a co-sited 5 MHz carrier with the same PSC.

    If that DL UARFCN is 25 channels (5 MHz) from an in-band candidate, that
    candidate is the serving carrier. Used to reject 10788 decoding 10813.
    """
    if serving_psc is None:
        return None
    for n in inter_neighbors or []:
        if n.get("psc") != serving_psc:
            continue
        co = n.get("uarfcn")
        if co is None:
            continue
        co = int(co)
        for cand in (co - 25, co + 25):
            try:
                band_of(cand)
            except ValueError:
                continue
            if cand != co:
                return cand
    return None


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
    import wcdma_scan_report as report  # noqa: WPS433

    return report.load_rrc_asn1(ROOT)


def scan_uarfcn(
    uarfcn: int,
    duration: float,
    skip_sib: bool,
    captures: Path,
    db,
    serial: str | None = None,
    usrp=None,
    antenna: str | None = None,
    gain_db: float = DEFAULT_GAIN_DB,
    band: int | None = None,
    stamp: str = "",
) -> tuple[dict, str]:
    if band is None:
        band = band_of(uarfcn)
    cell: dict = {
        "uarfcn": uarfcn,
        "freq_mhz": round(freq_mhz(uarfcn, band), 1),
        "band": str(band),
        "serial": "",
        "gain_db": float(gain_db),
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
        "iq_file": "",
        "bch_file": "",
    }
    log: list[str] = [f"===== UARFCN {uarfcn} ====="]
    prefix = f"{stamp}_uarfcn_{uarfcn}" if stamp else f"uarfcn_{uarfcn}"
    cfile = captures / f"{prefix}.cfile"
    cell["iq_file"] = str(cfile.relative_to(ROOT))
    print(
        f"# {uarfcn}  {cell['freq_mhz']:.1f} MHz  B{cell['band']}  "
        f"gain={cell['gain_db']:.0f}",
        file=sys.stderr,
        flush=True,
    )

    sys.path.insert(0, str(ROOT))
    import numpy as np
    import uhd_capture
    import wcdma_bch_decode
    import wcdma_cellsearch
    import wcdma_scan_report as report  # noqa: WPS433

    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            if usrp is None:
                usrp, opened_serial = uhd_capture.open_free_usrp(serial)
                antenna = uhd_capture.pick_antenna(usrp, antenna or "auto")
                serial = opened_serial
            iq_data = uhd_capture.capture_iq(
                usrp,
                uarfcn,
                duration,
                cell["gain_db"],
                cfile,
                antenna=antenna or "RX2",
                serial=serial or "",
                band=band,
            )
        log.append(buf.getvalue())
        cell["serial"] = serial or ""
    except Exception as exc:
        cell["status"] = "capture-fail"
        cell["note"] = f"capture error: {exc}"
        log.append(str(exc))
        return cell, "\n".join(log)
    if not cfile.exists():
        cell["status"] = "capture-fail"
        cell["note"] = "capture missing cfile"
        return cell, "\n".join(log)

    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            search = wcdma_cellsearch.search_capture(str(cfile), iq_data=iq_data)
        log.append(buf.getvalue())
    except Exception as exc:
        cell["status"] = "no-cell"
        cell["note"] = f"cell search error: {exc}"
        return cell, "\n".join(log)

    found = (search or {}).get("cells") or []
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

    bch_path = captures / f"{prefix}_sc{cell['psc']}.bch.json"
    cell["bch_file"] = str(bch_path.relative_to(ROOT))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        blocks = wcdma_bch_decode.decode_bch_capture(
            str(cfile),
            output_path=str(bch_path),
            iq_data=iq_data,
            max_frames=240,
            asn_db=db,
        )
    log.append(buf.getvalue())
    if not blocks or not bch_path.exists():
        cell["status"] = "bch-fail"
        cell["note"] = "BCH CRC empty"
        return cell, "\n".join(log)

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
    inter = sib.get("wcdma_neighbors") or []
    inferred = infer_serving_uarfcn(uarfcn, cell["psc"], inter)
    if inferred is not None and inferred != uarfcn:
        cell["status"] = "adjacent"
        cell["note"] = (
            f"SIB is UARFCN {inferred} PSC {cell['psc']} leaked into tuned {uarfcn}"
        )
        cell["plmn"] = ""
        cell["operator"] = ""
        cell["cid"] = ""
        cell["rnc"] = ""
        cell["wcdma_neighbors"] = []
        cell["lte_neighbors"] = []
        cell["gsm_neighbors"] = []
        return cell, "\n".join(log)

    wcdma = []
    for psc in sib.get("wcdma_intra_pscs") or []:
        if int(psc) == cell["psc"]:
            continue
        wcdma.append({"uarfcn": uarfcn, "psc": int(psc)})
    wcdma.extend(inter)
    cell["wcdma_neighbors"] = wcdma
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
        "iq_file",
        "bch_file",
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
    parser = argparse.ArgumentParser(
        description="WCDMA UARFCN scanner (first free USRP)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=HELP_EPILOG,
    )
    parser.add_argument(
        "--band",
        type=int,
        nargs="+",
        default=None,
        metavar="N",
        help="FDD band(s): 1 2 4 5 6 8 19 (with --uarfcn, or TR defaults for 1 and 8)",
    )
    parser.add_argument(
        "--uarfcn",
        type=int,
        nargs="*",
        default=None,
        help="UARFCN list (optional if --band 1 or 8)",
    )
    parser.add_argument("--duration", type=float, default=3.0, help="IQ capture seconds (default 3)")
    parser.add_argument(
        "-g",
        "--gain",
        type=float,
        default=DEFAULT_GAIN_DB,
        help=f"USRP RX gain dB (default {DEFAULT_GAIN_DB:.0f})",
    )
    parser.add_argument("--skip-sib", action="store_true")
    parser.add_argument(
        "--iq",
        action="store_true",
        help="Keep timestamped IQ (captures/<stamp>_uarfcn_<N>.cfile). Default overwrites captures/uarfcn_<N>.cfile",
    )
    parser.add_argument("--serial", type=str, default=None, help="Optional USRP serial; default = first free")
    parser.add_argument("--check", action="store_true", help="Verify deps and radios, then exit")
    parser.add_argument("-o", "--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    import uhd_capture

    uhd_capture.apply_uhd_images()
    if args.check:
        return preflight()

    from wcdma_bands import band_of_uarfcn, resolve_scan_list, uarfcn_in_band

    try:
        targets = resolve_scan_list(args.band, args.uarfcn)
    except ValueError as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 2

    band_hint: dict[int, int] = {}
    if args.band:
        for u in targets:
            for b in args.band:
                if uarfcn_in_band(u, b):
                    band_hint[u] = b
                    break
            else:
                band_hint[u] = band_of_uarfcn(u)
    else:
        for u in targets:
            band_hint[u] = band_of_uarfcn(u)

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
            "uarfcns": targets,
            "bands": args.band,
            "duration": args.duration,
            "gain_db": args.gain,
            "skip_sib": args.skip_sib,
            "iq": args.iq,
            "cells": cells,
        }
        paths["json"].write_text(json.dumps(payload, indent=2, default=str) + "\n")
        write_csv(paths["csv"], cells)
        paths["log"].write_text("\n\n".join(logs) + "\n")

    try:
        usrp, serial = uhd_capture.open_free_usrp(args.serial)
        antenna = uhd_capture.pick_antenna(usrp, "auto")
    except Exception as exc:
        print(f"FAIL  USRP open: {exc}", file=sys.stderr)
        return 1

    try:
        for uarfcn in targets:
            cell, log = scan_uarfcn(
                uarfcn,
                args.duration,
                args.skip_sib,
                captures,
                db,
                serial,
                usrp=usrp,
                antenna=antenna,
                gain_db=args.gain,
                band=band_hint[uarfcn],
                stamp=stamp if args.iq else "",
            )
            cells.append(cell)
            logs.append(log)
            print_cell(cell)
            persist("partial" if uarfcn != targets[-1] else "complete")
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
