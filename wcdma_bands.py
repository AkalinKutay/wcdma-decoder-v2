# WCDMA/UMTS Passive Neighbor Cell Decoder
# Copyright (C) 2026 06kutay <https://github.com/06kutay>
#
# 3GPP TS 25.101 / 25.104 UARFCN (FDD downlink):
#   F_DL = N/5 + FDL_Offset

from __future__ import annotations

# extra_dl: additional UARFCNs (Table 5.1A). extra_dl_offset if different from dl_offset.
BANDS: dict[int, dict] = {
    1: {
        "name": "2100",
        "dl_offset": 0.0,
        "dl_lo": 10562,
        "dl_hi": 10838,
        "ul_offset": 0.0,
        "ul_lo": 9612,
        "ul_hi": 9888,
        "duplex_mhz": 190.0,
        "extra_dl": (),
        "extra_dl_offset": 0.0,
    },
    2: {
        "name": "1900",
        "dl_offset": 0.0,
        "dl_lo": 9662,
        "dl_hi": 9938,
        "ul_offset": 0.0,
        "ul_lo": 9262,
        "ul_hi": 9538,
        "duplex_mhz": 80.0,
        "extra_dl": tuple(range(412, 688, 25)),
        "extra_dl_offset": 1850.1,
    },
    4: {
        "name": "1700",
        "dl_offset": 1805.0,
        "dl_lo": 1537,
        "dl_hi": 1738,
        "ul_offset": 1450.0,
        "ul_lo": 1312,
        "ul_hi": 1513,
        "duplex_mhz": 400.0,
        "extra_dl": tuple(range(1887, 2088, 25)),
        "extra_dl_offset": 1735.1,
    },
    5: {
        "name": "850",
        "dl_offset": 0.0,
        "dl_lo": 4357,
        "dl_hi": 4458,
        "ul_offset": 0.0,
        "ul_lo": 4132,
        "ul_hi": 4233,
        "duplex_mhz": 45.0,
        "extra_dl": tuple(range(1007, 1088, 5)),
        "extra_dl_offset": 670.1,
    },
    6: {
        "name": "800",
        "dl_offset": 0.0,
        "dl_lo": 4387,
        "dl_hi": 4413,
        "ul_offset": 0.0,
        "ul_lo": 4162,
        "ul_hi": 4188,
        "duplex_mhz": 45.0,
        "extra_dl": (1037, 1062),
        "extra_dl_offset": 670.1,
    },
    8: {
        "name": "900",
        "dl_offset": 340.0,
        "dl_lo": 2937,
        "dl_hi": 3088,
        "ul_offset": 340.0,
        "ul_lo": 2712,
        "ul_hi": 2863,
        "duplex_mhz": 45.0,
        "extra_dl": (),
        "extra_dl_offset": 340.0,
    },
    19: {
        "name": "850",
        "dl_offset": 735.0,
        "dl_lo": 712,
        "dl_hi": 763,
        "ul_offset": 770.0,
        "ul_lo": 312,
        "ul_hi": 363,
        "duplex_mhz": 45.0,
        "extra_dl": tuple(range(4377, 4414)),
        "extra_dl_offset": 0.0,
    },
}

SUPPORTED_BANDS = tuple(BANDS)
# AD9361 analog filter tops out at 56 MHz. 61.44 Msps trips
# "sample rate > analog frontend filter bandwidth". Stay at 30.72.
B210_USABLE_HZ = 30.72e6
B210_RATES = (15.36e6, 30.72e6)
B210_ANALOG_MAX_HZ = 56e6
TR_UARFCNS = {
    1: (10563, 10588, 10613, 10638, 10813, 10838, 10663, 10688, 10713, 10738, 10763, 10788),
    8: (2997, 3018, 2938, 2963),
}
DEFAULT_SCAN_UARFCNS = (10813, 10838, 2997, 2938, 2963, 3018)


def _in_general_dl(n: int, spec: dict) -> bool:
    return spec["dl_lo"] <= n <= spec["dl_hi"]


def _in_extra_dl(n: int, spec: dict) -> bool:
    extra = spec.get("extra_dl") or ()
    return n in extra


def bands_for_uarfcn(uarfcn: int) -> list[int]:
    n = int(uarfcn)
    found = []
    for band, spec in BANDS.items():
        if _in_general_dl(n, spec) or _in_extra_dl(n, spec):
            found.append(band)
    return found


def band_of_uarfcn(uarfcn: int) -> int:
    found = bands_for_uarfcn(uarfcn)
    if len(found) == 1:
        return found[0]
    if not found:
        raise ValueError(f"UARFCN {uarfcn} not in supported FDD bands {SUPPORTED_BANDS}")
    raise ValueError(
        f"UARFCN {uarfcn} matches bands {found}; pass --band "
        + " or ".join(str(b) for b in found)
    )


def uarfcn_in_band(uarfcn: int, band: int) -> bool:
    spec = BANDS.get(int(band))
    if spec is None:
        return False
    n = int(uarfcn)
    return _in_general_dl(n, spec) or _in_extra_dl(n, spec)


def uarfcn_to_dl_mhz(uarfcn: int, band: int | None = None) -> float:
    n = int(uarfcn)
    if band is None:
        band = band_of_uarfcn(n)
    spec = BANDS[int(band)]
    if _in_general_dl(n, spec):
        return n / 5.0 + spec["dl_offset"]
    if _in_extra_dl(n, spec):
        return n / 5.0 + float(spec.get("extra_dl_offset", spec["dl_offset"]))
    raise ValueError(f"UARFCN {n} not in Band {band}")


def is_fdd_dl_uarfcn(uarfcn) -> bool:
    try:
        n = int(uarfcn)
    except (TypeError, ValueError):
        return False
    return bool(bands_for_uarfcn(n))


def ul_dl_pair_ok(ul, dl) -> bool:
    """True if UL/DL UARFCN pair matches a supported band duplex in MHz."""
    try:
        ul_n, dl_n = int(ul), int(dl)
    except (TypeError, ValueError):
        return False
    if not is_fdd_dl_uarfcn(dl_n):
        return False
    for band in bands_for_uarfcn(dl_n):
        spec = BANDS[band]
        fdl = uarfcn_to_dl_mhz(dl_n, band)
        ful = None
        if spec["ul_lo"] <= ul_n <= spec["ul_hi"]:
            ful = ul_n / 5.0 + spec["ul_offset"]
        if ful is None:
            continue
        if abs((fdl - ful) - spec["duplex_mhz"]) <= 0.15:
            return True
    return False


def band_dl_range_mhz(band: int) -> tuple[float, float]:
    """Primary DL span (TS 25.101 general table), not extra UARFCNs."""
    spec = BANDS[int(band)]
    lo = spec["dl_lo"] / 5.0 + spec["dl_offset"]
    hi = spec["dl_hi"] / 5.0 + spec["dl_offset"]
    return (float(lo), float(hi))


def mhz_to_uarfcn(freq_mhz: float, band: int) -> int:
    spec = BANDS[int(band)]
    n = int(round(5.0 * (float(freq_mhz) - spec["dl_offset"])))
    n = max(spec["dl_lo"], min(spec["dl_hi"], n))
    return n


def uarfcns_primary(band: int) -> list[int]:
    spec = BANDS[int(band)]
    return list(range(spec["dl_lo"], spec["dl_hi"] + 1))


def _pick_rate(span_hz: float) -> float:
    span = float(span_hz)
    if span <= 15.36e6:
        return 15.36e6
    return 30.72e6


def survey_hops(band: int, usable_hz: float = B210_USABLE_HZ) -> list[dict]:
    """LO hops that cover the primary DL. Rate never exceeds analog 56 MHz."""
    f_lo_mhz, f_hi_mhz = band_dl_range_mhz(band)
    f_lo = f_lo_mhz * 1e6
    f_hi = f_hi_mhz * 1e6
    usable = float(usable_hz)
    hops: list[dict] = []
    start = f_lo
    overlap = 4e6
    while start < f_hi - 1e3:
        end = min(f_hi, start + usable)
        span = end - start
        center = 0.5 * (start + end)
        rate = _pick_rate(span)
        analog = min(B210_ANALOG_MAX_HZ, rate)
        hops.append({
            "center_hz": float(center),
            "f_lo_hz": float(start),
            "f_hi_hz": float(end),
            "rate": rate,
            "analog_bw": float(analog),
        })
        if end >= f_hi:
            break
        start = end - overlap
    return hops


def resolve_scan_list(bands: list[int] | None, uarfcns: list[int] | None) -> list[int]:
    bands = [int(b) for b in (bands or [])]
    uarfcns = [int(u) for u in (uarfcns or [])]
    for b in bands:
        if b not in BANDS:
            raise ValueError(f"unsupported band {b} (supported: {SUPPORTED_BANDS})")
    if not bands and not uarfcns:
        return list(DEFAULT_SCAN_UARFCNS)
    if uarfcns:
        if bands:
            allowed = set(bands)
            for u in uarfcns:
                hit = set(bands_for_uarfcn(u)) & allowed
                if not hit:
                    raise ValueError(f"UARFCN {u} is not in band(s) {sorted(allowed)}")
        else:
            for u in uarfcns:
                band_of_uarfcn(u)
        return uarfcns
    out: list[int] = []
    missing = []
    for b in bands:
        defaults = TR_UARFCNS.get(b)
        if not defaults:
            missing.append(b)
            continue
        out.extend(defaults)
    if missing:
        raise ValueError(
            "--band "
            + ",".join(str(b) for b in missing)
            + " has no default UARFCNs; pass --uarfcn"
        )
    return out
