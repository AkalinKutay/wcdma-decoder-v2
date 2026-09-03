#!/usr/bin/python3
"""WCDMA UARFCN scanner: capture, cell search, BCH/SIB, print like gr-gsm.

Each UARFCN prints one cell block to stdout as soon as it is done.
Timestamped files go to results/<YYYYMMDD-HHMMSS>.{json,csv,log}.
IQ: default overwrites captures/uarfcn_<N>.cfile; --iq keeps
captures/<stamp>_uarfcn_<N>.cfile.

  python3 scan_3g.py --band 1 --uarfcn 10813
  python3 scan_3g.py --band 8 --uarfcn 2997
  python3 scan_3g.py --iq --band 8 --uarfcn 2938 2963
  python3 scan_3g.py --detail --band 8 --uarfcn 2938
  python3 scan_3g.py --check
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
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
  ./run-scan.sh --detail --band 8 --uarfcn 2938
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


def _use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return bool(sys.stdout.isatty())


def _c(code: str, text) -> str:
    if not _use_color():
        return str(text)
    return f"\033[{code}m{text}\033[0m"


def _dash(val, suffix: str = "") -> str:
    if val is None or val == "":
        return "-"
    return f"{val}{suffix}"


def _rep_human(rep, pos) -> str:
    if not rep:
        return "-"
    period = str(rep).replace("rep", "")
    if pos is None:
        return f"her {period} çerçeve"
    return f"her {period} çerçeve, konum {pos}"


def _hcs_human(val) -> str:
    if val == "hcs-not-used":
        return "yok"
    if val == "hcs-used":
        return "var"
    return _dash(val)


def _quality_human(val) -> str:
    if val == "cpich-Ec-N0":
        return "Ec/Io"
    if val == "cpich-RSCP":
        return "RSCP"
    return _dash(val)


def _barred_human(val) -> str:
    if val == "notBarred":
        return "hayır"
    if val == "barred":
        return "evet"
    return _dash(val)


def _reserved_human(val) -> str:
    if val == "notReserved":
        return "hayır"
    if val == "reserved":
        return "evet"
    return _dash(val)


def _nmo_human(val) -> str:
    if val == 0:
        return "0  (CS+PS birlikte)"
    if val == 1:
        return "1  (CS ve PS ayrı)"
    return _dash(val)


def _t3212_human(val) -> str:
    if val is None:
        return "-"
    if val == 0:
        return "0  (periyodik güncelleme kapalı)"
    return f"{val}  ({int(val) * 6} dakika)"


def _vis_len(text: str) -> int:
    return len(re.sub(r"\033\[[0-9;]*m", "", str(text)))


def _pad(text: str, width: int) -> str:
    return str(text) + " " * max(0, width - _vis_len(text))


def _wrap(text: str, width: int) -> list[str]:
    text = str(text)
    if _vis_len(text) <= width:
        return [text]
    lines: list[str] = []
    cur = ""
    for part in text.split():
        trial = (cur + " " + part).strip()
        if _vis_len(trial) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = part
    if cur:
        lines.append(cur)
    return lines or [text]


def _kv_table(rows: list[tuple[str, str]]) -> None:
    rows = [(str(k), str(v)) for k, v in rows if v not in (None, "", "-")]
    if not rows:
        return
    lw = max(_vis_len(k) for k, _v in rows)
    lw = min(max(lw, 18), 40)
    vw = min(max((_vis_len(v) for _k, v in rows), default=12), 56)
    wrapped = [(k, _wrap(v, vw)) for k, v in rows]
    vw = max(vw, max(_vis_len(line) for _k, lines in wrapped for line in lines))
    vw = min(vw, 56)
    top = "┌" + "─" * (lw + 2) + "┬" + "─" * (vw + 2) + "┐"
    mid = "├" + "─" * (lw + 2) + "┼" + "─" * (vw + 2) + "┤"
    bot = "└" + "─" * (lw + 2) + "┴" + "─" * (vw + 2) + "┘"
    print("  " + _c("36", top))
    print(
        "  "
        + _c("36", "│")
        + " "
        + _c("1;33", _pad("Alan", lw))
        + " "
        + _c("36", "│")
        + " "
        + _c("1;33", _pad("Değer", vw))
        + " "
        + _c("36", "│")
    )
    print("  " + _c("36", mid))
    for key, lines in wrapped:
        for i, line in enumerate(lines):
            print(
                "  "
                + _c("36", "│")
                + " "
                + _c("2;37", _pad(key if i == 0 else "", lw))
                + " "
                + _c("36", "│")
                + " "
                + _c("1;37", _pad(line, vw))
                + " "
                + _c("36", "│")
            )
    print("  " + _c("36", bot))


def _grid(headers: list[str], rows: list[list[str]], serving_row: int | None = None) -> None:
    if not rows:
        print("  " + _c("2;37", "(yok)"))
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    def fmt_row(cols: list[str], header: bool = False, serving: bool = False) -> str:
        parts = []
        for i, col in enumerate(cols):
            text = _pad(str(col), widths[i])
            if header:
                text = _c("1;33", text)
            elif serving:
                text = _c("1;32", text)
            else:
                text = _c("1;37", text)
            parts.append(text)
        line = " " + _c("36", "│") + " "
        return "  " + _c("36", "│") + " " + line.join(parts) + " " + _c("36", "│")
    top = "┌" + "┬".join("─" * (w + 2) for w in widths) + "┐"
    sep = "├" + "┼".join("─" * (w + 2) for w in widths) + "┤"
    bot = "└" + "┴".join("─" * (w + 2) for w in widths) + "┘"
    print("  " + _c("36", top))
    print(fmt_row(headers, header=True))
    print("  " + _c("36", sep))
    for i, row in enumerate(rows):
        print(fmt_row(row, serving=(serving_row is not None and i == serving_row)))
    print("  " + _c("36", bot))


def _section(title: str) -> None:
    print()
    print("  " + _c("1;36", "▸ " + title))


def _neigh_mhz(uarfcn) -> str:
    try:
        return f"{freq_mhz(int(uarfcn)):.1f}"
    except Exception:
        return "-"


def print_detail(cell: dict) -> None:
    d = cell.get("detail") or {}
    s3 = d.get("sib3") or {}
    s5 = d.get("sib5") or {}
    s7 = d.get("sib7") or {}
    s11 = d.get("sib11") or {}
    cs = d.get("cs") or {}
    ps = d.get("ps") or {}
    utra = d.get("sib19_utra") or {}

    banner = (
        f"  UARFCN {cell.get('uarfcn')}   "
        f"Bant {cell.get('band')}   "
        f"{cell.get('freq_mhz')} MHz   "
        f"PSC {cell.get('psc')}   "
        f"{cell.get('operator') or ''}"
    ).rstrip()
    rule = "━" * max(56, min(len(banner) + 2, 78))
    print()
    print(_c("1;36", "  " + rule))
    print(_c("1;37", banner))
    print(_c("1;36", "  " + rule))

    _section("Sinyal")
    _kv_table([
        ("Ec/Io  (kalite)", _dash(cell.get("ecno"), " dB") if cell.get("ecno") is not None else None),
        ("RSCP  (seviye)", _dash(cell.get("rscp"), " dBm")),
        ("Durum", _dash(cell.get("status"))),
        ("Not", cell.get("note") or None),
    ])

    _section("Kimlik")
    _kv_table([
        ("Operatör", cell.get("operator") or None),
        ("PLMN  (ülke+şebeke)", cell.get("plmn") or None),
        ("CID  (28 bit hücre no)", cell.get("cid") or None),
        ("RNC  (denetleyici)", cell.get("rnc") or None),
        ("Yerel hücre no", cell.get("local_cid") or None),
        ("LAC  (konum alanı)", d.get("lac")),
        ("URA  (kayıt alanı)", " ".join(str(u) for u in (d.get("ura") or [])) or None),
    ])

    _section("Radyo / senkron")
    _kv_table([
        ("USRP seri", cell.get("serial") or None),
        ("Anten", cell.get("antenna") or None),
        ("Kazanç", _dash(cell.get("gain_db"), " dB") if cell.get("gain_db") is not None else None),
        ("Kod grubu", cell.get("code_group")),
        ("Slot zamanı", _dash(cell.get("slot_timing"), " sample") if cell.get("slot_timing") is not None else None),
        ("Çerçeve zamanı", _dash(cell.get("frame_timing"), " sample") if cell.get("frame_timing") is not None else None),
        ("CFO  (frekans kayması)", _dash(cell.get("cfo_hz"), " Hz") if cell.get("cfo_hz") is not None else None),
        ("IQ örnek sayısı", cell.get("samples")),
        ("IQ RMS", cell.get("rms")),
        ("IQ dosyası", cell.get("iq_file") or None),
        ("BCH dosyası", cell.get("bch_file") or None),
        ("BCH blok sayısı", cell.get("bch_blocks")),
    ])

    extras = cell.get("extra_cells") or []
    if extras:
        _section("Aynı taşıyıcıdaki diğer hücreler  (sadece CPICH)")
        _grid(
            ["PSC", "Ec/Io", "RSCP"],
            [
                [
                    str(x.get("psc")),
                    _dash(x.get("ecno"), " dB"),
                    _dash(x.get("rscp"), " dBm"),
                ]
                for x in extras
            ],
        )

    _section("Çekirdek ağ  (SIB1)")
    att = cs.get("att")
    _kv_table([
        ("LAC  (konum alanı)", d.get("lac")),
        ("T3212  (konum güncelleme)", _t3212_human(cs.get("t3212")) if cs else None),
        ("ATT  (açıkken güncelle)", "açık" if att else ("kapalı" if att is False else None)),
        ("RAC  (paket alanı)", ps.get("rac")),
        ("NMO  (ağ kipi)", _nmo_human(ps.get("nmo")) if ps.get("nmo") is not None else None),
        ("DRX  (uyku çevrimi)", d.get("drx")),
    ])

    _section("Hücre seçimi / reselection  (SIB3)")
    _kv_table([
        ("Ölçüm", _quality_human(s3.get("quality"))),
        ("qQualMin  (min. Ec/Io)", _dash(s3.get("q_qualmin_db"), " dB") if s3.get("q_qualmin_db") is not None else None),
        ("qRxLevMin  (min. RSCP)", _dash(s3.get("q_rxlevmin_dbm"), " dBm") if s3.get("q_rxlevmin_dbm") is not None else None),
        ("qHyst  (histerezis)", _dash(s3.get("q_hyst_db"), " dB") if s3.get("q_hyst_db") is not None else None),
        ("tResel  (bekleme)", _dash(s3.get("t_reselect_s"), " s") if s3.get("t_reselect_s") is not None else None),
        ("sIntra  (aynı frekans eşiği)", _dash(s3.get("s_intra_db"), " dB") if s3.get("s_intra_db") is not None else None),
        ("sInter  (diğer frekans eşiği)", _dash(s3.get("s_inter_db"), " dB") if s3.get("s_inter_db") is not None else None),
        ("Max UL güç", _dash(s3.get("max_ul_tx_dbm"), " dBm") if s3.get("max_ul_tx_dbm") is not None else None),
        ("Hücre engelli mi", _barred_human(s3.get("barred"))),
        ("Operatöre rezerve", _reserved_human(s3.get("reserved"))),
        (
            "Erişim sınıfı engeli",
            f"{s3.get('ac_barred') or 0}/{s3.get('ac_total')}" if s3.get("ac_total") else None,
        ),
        (
            "GSM arama eşiği",
            next(
                (f"sSearch {r.get('s_search')}" for r in (s3.get("rats") or []) if r.get("rat") == "gsm"),
                None,
            ),
        ),
    ])

    _section("Ortak kanallar  (SIB5 / SIB7)")
    sccpch = ", ".join(
        f"{str(s.get('sf') or '').replace('sf', 'SF')}/{s.get('code')}"
        for s in (s5.get("sccpch") or [])
    ) or None
    _kv_table([
        ("PICH  (paging gücü)", _dash(s5.get("pich_db"), " dB") if s5.get("pich_db") is not None else None),
        ("AICH  (erişim onayı)", _dash(s5.get("aich_db"), " dB") if s5.get("aich_db") is not None else None),
        ("Tx çeşitlilik", "açık" if s5.get("tx_div") else ("kapalı" if s5.get("tx_div") is False else None)),
        ("PRACH  (erişim)", str(s5.get("prach_sf") or "").replace("sfpr", "SF") or None),
        ("PRACH preamble kodu", s5.get("prach_sc")),
        ("SCCPCH  (SF/kod)", sccpch),
        ("UL girişim", _dash(s7.get("ul_interference_dbm"), " dBm") if s7.get("ul_interference_dbm") is not None else None),
        ("HCS", _hcs_human(s11.get("hcs"))),
        ("Komşu ölçümü", _quality_human(s11.get("quality"))),
    ])

    if utra:
        _section("UTRA öncelik  (SIB19)")
        _kv_table([
            ("Öncelik", utra.get("priority")),
            ("sPrioritySearch", _dash(utra.get("s_priority_search1_db"), " dB") if utra.get("s_priority_search1_db") is not None else None),
            ("threshServingLow", _dash(utra.get("thresh_serving_low_db"), " dB") if utra.get("thresh_serving_low_db") is not None else None),
        ])

    qoff = {int(r["psc"]): r.get("qoffset_db") for r in (d.get("qoffsets") or [])}
    serving_u = cell.get("uarfcn")
    serving_psc = cell.get("psc")
    wcdma_rows: list[list[str]] = []
    serving_idx = None
    if serving_u is not None and serving_psc is not None:
        serving_idx = 0
        wcdma_rows.append([
            str(serving_u),
            _neigh_mhz(serving_u),
            str(serving_psc),
            (f"{qoff.get(int(serving_psc)):g} dB" if int(serving_psc) in qoff else "0 dB"),
            "bu hücre",
        ])
    seen = {(int(serving_u), int(serving_psc))} if serving_u is not None and serving_psc is not None else set()
    for n in cell.get("wcdma_neighbors") or []:
        u, psc = n.get("uarfcn"), n.get("psc")
        if u is None or psc is None:
            continue
        key = (int(u), int(psc))
        if key in seen:
            continue
        seen.add(key)
        kind = "aynı frekans" if serving_u is not None and int(u) == int(serving_u) else "diğer frekans"
        wcdma_rows.append([
            str(int(u)),
            _neigh_mhz(u),
            str(int(psc)),
            (f"{qoff[int(psc)]:g} dB" if int(psc) in qoff else "-"),
            kind,
        ])
    _section("WCDMA komşular")
    _grid(
        ["UARFCN", "MHz", "PSC", "qOffset", "Tür"],
        wcdma_rows,
        serving_row=serving_idx,
    )

    gsm_rows = []
    for n in cell.get("gsm_neighbors") or []:
        gsm_rows.append([
            str(n.get("arfcn") if n.get("arfcn") is not None else "-"),
            str(n.get("bsic") if n.get("bsic") is not None else "-"),
            str(n.get("cell_id") if n.get("cell_id") is not None else "-"),
        ])
    _section("GSM komşular")
    _grid(["ARFCN", "BSIC", "Liste no"], gsm_rows)

    lte_rows = []
    for n in cell.get("lte_neighbors") or []:
        qmin = n.get("q_rx_lev_min_dbm")
        if qmin is None and n.get("q_rx_lev_min") is not None:
            qmin = int(n["q_rx_lev_min"]) * 2
        thresh = "-"
        if n.get("thresh_x_high_db") is not None:
            thresh = f"{n.get('thresh_x_high_db')}/{n.get('thresh_x_low_db')}"
        lte_rows.append([
            str(n.get("earfcn") if n.get("earfcn") is not None else "-"),
            str(n.get("band") or "-"),
            f"{n['freq_mhz']:.1f}" if n.get("freq_mhz") else "-",
            str(n.get("bw") or "-"),
            str(n.get("priority") if n.get("priority") is not None else "-"),
            _dash(qmin, " dBm") if qmin is not None else "-",
            thresh,
        ])
    _section("LTE komşular  (SIB19)")
    _grid(
        ["EARFCN", "Bant", "MHz", "BW", "Öncelik", "qRxLevMin", "threshX yüksek/düşük"],
        lte_rows,
    )

    sched_rows = []
    for s in d.get("schedule") or []:
        sched_rows.append([
            str(s.get("sib") or "-"),
            _rep_human(s.get("rep"), s.get("pos")),
            str(s.get("seg") if s.get("seg") is not None else "1"),
            str(s.get("tag") if s.get("tag") is not None else "-"),
        ])
    _section("SIB yayın planı  (MIB)")
    _kv_table([
        ("MIB etiket", d.get("mib_tag")),
        ("Çözülen SIB'ler", "  ".join(d.get("sibs") or cell.get("sibs") or []) or None),
    ])
    if sched_rows:
        _grid(["SIB", "Tekrar", "Parça", "Etiket"], sched_rows)
    print()


def print_cell(cell: dict, detail: bool = False) -> None:
    if cell.get("psc") is None:
        print(
            f"UARFCN {cell['uarfcn']:5d}  {cell['freq_mhz']:7.1f} MHz  "
            f"B{cell['band']}  {cell.get('status') or 'empty'}"
            + (f"  {cell['note']}" if cell.get("note") else "")
        )
        if detail:
            print_detail(cell)
        return
    op = cell.get("operator") or ""
    print(
        f"UARFCN {cell['uarfcn']:5d}  {cell['freq_mhz']:7.1f} MHz  "
        f"PSC {cell['psc']:3d}  Ec/No {cell['ecno']:5.1f}  "
        f"RSCP {cell['rscp']:6.1f}  B{cell['band']}"
        + (f"  {op}" if op else "")
    )
    if detail:
        print_detail(cell)
        return
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
    detail: bool = False,
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
        "antenna": antenna or "",
        "code_group": None,
        "slot_timing": None,
        "frame_timing": None,
        "cfo_hz": None,
        "samples": None,
        "rms": None,
        "local_cid": "",
        "bch_blocks": None,
        "extra_cells": [],
        "sibs": [],
        "detail": {},
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
        cell["antenna"] = antenna or cell.get("antenna") or ""
        cell["samples"] = int(iq_data.size)
        cell["rms"] = round(float(np.sqrt(np.mean(np.abs(iq_data) ** 2))), 5)
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
            search = wcdma_cellsearch.search_capture(
                str(cfile), iq_data=iq_data, max_cells=4 if detail else 1
            )
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
    if top.get("code_group") is not None:
        cell["code_group"] = int(top["code_group"])
    if top.get("slot_timing_sample") is not None:
        cell["slot_timing"] = int(top["slot_timing_sample"])
    if top.get("frame_timing_sample") is not None:
        cell["frame_timing"] = int(top["frame_timing_sample"])
    if top.get("frequency_correction_hz") is not None:
        cell["cfo_hz"] = round(float(top["frequency_correction_hz"]), 2)
    extras = []
    for other in found[1:]:
        extras.append({
            "psc": int(other["scrambling_code"]),
            "ecno": round(float(other["cpich_ecno_db"]), 2),
            "rscp": round(float(other["cpich_rscp_dbm"]), 2),
        })
    cell["extra_cells"] = extras
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
    cell["bch_blocks"] = len(blocks) if blocks else 0
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
    if sib.get("local_cell_id") not in (None, "N/A"):
        cell["local_cid"] = str(sib["local_cell_id"])
    cell["sibs"] = sib.get("sibs") or []
    cell["detail"] = sib.get("detail") or {}
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
    parser.add_argument(
        "--detail",
        action="store_true",
        help="Print NAS, reselection, channels, PHY sync, extra same-carrier cells",
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
            "detail": args.detail,
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
                detail=args.detail,
            )
            cells.append(cell)
            logs.append(log)
            print_cell(cell, detail=args.detail)
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
