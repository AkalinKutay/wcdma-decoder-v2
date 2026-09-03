# WCDMA/UMTS Passive Neighbor Cell Decoder
# Copyright (C) 2026 06kutay <https://github.com/06kutay>
#
# Wideband band sweep: one (or few) LO hops, PSD candidate UARFCNs, DDC to 7.68 Msps.

from __future__ import annotations

import math

import numpy as np

from wcdma_bands import uarfcn_in_band, uarfcn_to_dl_mhz, uarfcns_primary

DECODER_FS = 7.68e6
PROBE_SECONDS = 0.25


def welch_two_sided(iq, fs: float, nperseg: int = 8192):
    """Two-sided PSD. freqs are baseband Hz (LO at 0)."""
    x = np.asarray(iq, dtype=np.complex64)
    nperseg = int(min(nperseg, len(x)))
    if nperseg < 64:
        raise ValueError("IQ too short for PSD")
    win = np.hanning(nperseg).astype(np.float32)
    step = max(nperseg // 2, 1)
    acc = np.zeros(nperseg, dtype=np.float64)
    nseg = 0
    for i in range(0, len(x) - nperseg + 1, step):
        spec = np.fft.fftshift(np.fft.fft(x[i : i + nperseg] * win, n=nperseg))
        acc += np.abs(spec) ** 2
        nseg += 1
        if nseg >= 24:
            break
    acc /= max(nseg, 1)
    freqs = np.fft.fftshift(np.fft.fftfreq(nperseg, 1.0 / fs))
    return freqs, acc


def score_uarfcns(iq, fs: float, lo_hz: float, band: int, f_lo_hz: float, f_hi_hz: float):
    """25th-percentile PSD in ±1.8 MHz around each primary UARFCN.

    Percentile, not mean: a 200 kHz GSM spike must not outrank a 5 MHz WCDMA
    plateau. Returns [(uarfcn, linear_power)].
    """
    n = min(len(iq), int(0.20 * fs))
    freqs, psd = welch_two_sided(iq[:n], fs)
    scores = []
    for u in uarfcns_primary(band):
        fc = uarfcn_to_dl_mhz(u, band) * 1e6
        if fc < f_lo_hz - 1e3 or fc > f_hi_hz + 1e3:
            continue
        off = fc - lo_hz
        mask = np.abs(freqs - off) <= 1.8e6
        if not np.any(mask):
            continue
        scores.append((int(u), float(np.percentile(psd[mask], 25))))
    return scores


def _spaced_top(db: dict[int, float], min_sep: int, max_n: int, floor=None) -> list[int]:
    """Strongest UARFCNs at least min_sep apart (≈4 MHz at min_sep=20)."""
    ordered = sorted(db, key=lambda u: -db[u])
    kept: list[int] = []
    for u in ordered:
        if floor is not None and db[u] < floor:
            continue
        if any(abs(u - k) < min_sep for k in kept):
            continue
        kept.append(u)
        if len(kept) >= max_n:
            break
    return kept


def find_candidate_uarfcns(
    iq,
    fs: float,
    lo_hz: float,
    band: int,
    f_lo_hz: float,
    f_hi_hz: float,
    margin_db: float = 4.0,
    min_sep: int = 20,
    max_n: int = 8,
) -> list[int]:
    """Local PSD peaks on the 200 kHz raster. Falls back to strongest 5 MHz-spaced bins."""
    scores = score_uarfcns(iq, fs, lo_hz, band, f_lo_hz, f_hi_hz)
    if not scores:
        return []
    db = {u: 10.0 * math.log10(s + 1e-30) for u, s in scores}
    noise_db = float(np.percentile(list(db.values()), 20))
    kept = _spaced_top(db, min_sep, max_n, floor=noise_db + margin_db)
    if kept:
        return kept
    # Band filled with similar energy (percentile threshold fails): still probe peaks.
    return _spaced_top(db, min_sep, min(6, max_n))


def format_psd_top(iq, fs, lo_hz, band, f_lo_hz, f_hi_hz, n: int = 6, min_sep: int = 20) -> str:
    scores = score_uarfcns(iq, fs, lo_hz, band, f_lo_hz, f_hi_hz)
    if not scores:
        return "PSD empty"
    db = {u: 10.0 * math.log10(s + 1e-30) for u, s in scores}
    noise_db = float(np.percentile(list(db.values()), 20))
    bits = []
    for u in _spaced_top(db, min_sep, n):
        bits.append(f"{u} {db[u] - noise_db:+.1f}dB")
    return "PSD top " + "  ".join(bits)


def hop_covers_uarfcn(uarfcn: int, band: int, hop: dict) -> bool:
    if not uarfcn_in_band(int(uarfcn), band):
        return False
    fc = uarfcn_to_dl_mhz(int(uarfcn), band) * 1e6
    return hop["f_lo_hz"] - 1e3 <= fc <= hop["f_hi_hz"] + 1e3


def intra_band_neighbor_uarfcns(cell: dict, band: int) -> list[int]:
    """Unique SIB11/12 DL UARFCNs in this FDD band. Not LTE/GSM, not invented."""
    out: list[int] = []
    seen: set[int] = set()
    for n in cell.get("wcdma_neighbors") or []:
        try:
            u = int(n.get("uarfcn"))
        except (TypeError, ValueError):
            continue
        if u in seen or not uarfcn_in_band(u, band):
            continue
        seen.add(u)
        out.append(u)
    return out


def ddc_to_decoder(iq, fs: float, lo_hz: float, target_hz: float, seconds=None):
    """Mix to target DL and decimate to 7.68 Msps."""
    from scipy.signal import resample_poly

    x = np.asarray(iq, dtype=np.complex64)
    if seconds is not None:
        x = x[: int(float(seconds) * fs) + 16]
    offset = float(target_hz) - float(lo_hz)
    n = len(x)
    t = np.arange(n, dtype=np.float64) / float(fs)
    mixed = x * np.exp(-1j * 2 * np.pi * offset * t).astype(np.complex64)
    up = int(round(DECODER_FS))
    down = int(round(fs))
    g = math.gcd(up, down)
    y = resample_poly(mixed, up // g, down // g)
    return np.asarray(y, dtype=np.complex64)
