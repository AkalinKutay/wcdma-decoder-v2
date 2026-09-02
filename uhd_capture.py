#!/usr/bin/python3
"""USRP B210 IQ capture. Zero-IF at the WCDMA DL. Uses the first free UHD device."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np

LOW_IF_HZ = 2.4e6
SAMP_RATE = 7.68e6
# Capture above Nyquist so a 5 MHz-offset neighbour (10788 vs 10813) does not
# alias onto DC. Channel-filter then decimate to the 7.68 Msps decoder rate.
SAMP_RATE_RX = 15.36e6
ANALOG_BW_HZ = 8e6
_DECIM_TAPS = None


def _decim_taps():
    global _DECIM_TAPS
    if _DECIM_TAPS is None:
        import scipy.signal

        # Keep ±3.0 MHz (own WCDMA ~±2.34 MHz). Adjacent at ±5 MHz must not leak:
        # 10813 is ~47 dB above a 10788 tune, so stopband needs ≫ 50 dB.
        _DECIM_TAPS = scipy.signal.firwin(
            1025,
            3.0e6,
            fs=SAMP_RATE_RX,
            pass_zero="lowpass",
            window=("kaiser", 8.6),
        ).astype(np.float32)
    return _DECIM_TAPS


def channelize_to_decoder_rate(iq_hi):
    """15.36 Msps → LPF → 7.68 Msps. Rejects the next 5 MHz WCDMA carrier."""
    import scipy.signal

    x = np.asarray(iq_hi, dtype=np.complex64)
    y = scipy.signal.fftconvolve(x, _decim_taps(), mode="same")
    return y[::2].astype(np.complex64)


def find_uhd_images_dir() -> Path | None:
    env = os.environ.get("UHD_IMAGES_DIR")
    if env and (Path(env) / "usrp_b200_fw.hex").is_file():
        return Path(env)
    root = Path("/usr/share/uhd")
    if (root / "images" / "usrp_b200_fw.hex").is_file():
        return root / "images"
    if root.is_dir():
        for p in sorted(root.glob("*/images")):
            if (p / "usrp_b200_fw.hex").is_file():
                return p
    return None


def apply_uhd_images() -> Path | None:
    found = find_uhd_images_dir()
    if found:
        os.environ["UHD_IMAGES_DIR"] = str(found)
    return found


def uarfcn_to_freq_mhz(uarfcn: int, band: int | None = None) -> float:
    from wcdma_bands import uarfcn_to_dl_mhz

    return uarfcn_to_dl_mhz(uarfcn, band)


def band_of(uarfcn: int) -> str:
    from wcdma_bands import band_of_uarfcn

    return str(band_of_uarfcn(uarfcn))


def open_free_usrp(serial: str | None = None):
    """Open the first UHD device that is not busy. Optional serial filter."""
    import uhd

    apply_uhd_images()
    addrs = list(uhd.find(""))
    if not addrs:
        raise RuntimeError("no UHD device found (plug a B210, USB3)")
    if serial:
        ordered = [a for a in addrs if a.get("serial") == serial]
        if not ordered:
            raise RuntimeError(f"USRP serial {serial} not found")
        addrs = ordered
    last_err = None
    for addr in addrs:
        ser = addr.get("serial") or ""
        args = f"serial={ser}" if ser else str(addr)
        try:
            usrp = uhd.usrp.MultiUSRP(args)
            return usrp, ser or "unknown"
        except Exception as exc:
            last_err = exc
            continue
    raise RuntimeError(f"no free USRP (all busy?): {last_err}")


def pick_antenna(usrp, requested: str) -> str:
    try:
        available = list(usrp.get_rx_antennas(0))
    except Exception:
        available = []
    if requested and requested != "auto" and requested in available:
        return requested
    for name in ("RX2", "TX/RX"):
        if name in available:
            return name
    return available[0] if available else requested


def capture_iq(
    usrp,
    uarfcn: int,
    duration: float,
    gain: float,
    output,
    antenna: str = "RX2",
    serial: str = "",
    low_if: bool = False,
    settle_s: float = 0.3,
    band: int | None = None,
):
    """Tune, capture duration seconds, write .cfile + .json, return complex64 IQ."""
    import uhd

    apply_uhd_images()
    dl_mhz = uarfcn_to_freq_mhz(uarfcn, band)
    dl_hz = dl_mhz * 1e6
    lo_hz = dl_hz - LOW_IF_HZ if low_if else dl_hz
    n_rx = int(SAMP_RATE_RX * duration)
    print(
        f"UARFCN {uarfcn}  DL {dl_mhz:.1f} MHz  LO {lo_hz / 1e6:.1f} MHz  "
        f"serial={serial} antenna={antenna} gain={gain} dB  {duration}s  "
        f"rx={SAMP_RATE_RX/1e6:.2f}→{SAMP_RATE/1e6:.2f} Msps"
    )
    usrp.set_rx_antenna(antenna, 0)
    usrp.set_rx_rate(SAMP_RATE_RX, 0)
    try:
        usrp.set_rx_bandwidth(ANALOG_BW_HZ, 0)
    except Exception as extra:
        print(f"warning: set_rx_bandwidth failed: {extra}")
    usrp.set_rx_freq(uhd.types.TuneRequest(lo_hz), 0)
    usrp.set_rx_gain(gain, 0)
    if settle_s:
        time.sleep(settle_s)

    t0 = time.time()
    samples = usrp.recv_num_samps(n_rx, lo_hz, SAMP_RATE_RX, [0], gain)
    elapsed = time.time() - t0
    samples = np.asarray(samples, dtype=np.complex64)
    if samples.ndim == 2:
        samples = samples[0]
    samples = channelize_to_decoder_rate(samples)

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    samples.tofile(out)
    meta = {
        "uarfcn": uarfcn,
        "frequency_mhz": dl_mhz,
        "lo_frequency_hz": lo_hz,
        "low_if_hz": LOW_IF_HZ if low_if else 0.0,
        "analog_bw_hz": ANALOG_BW_HZ,
        "sample_rate_hz": SAMP_RATE,
        "sample_rate_rx_hz": SAMP_RATE_RX,
        "duration_seconds": duration,
        "gain_db": gain,
        "antenna_port": antenna,
        "samples_captured": int(samples.size),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sdr_serial": serial,
        "sdr_label": "USRP",
        "capture_wall_seconds": round(elapsed, 3),
    }
    Path(str(out) + ".json").write_text(json.dumps(meta, indent=2) + "\n")
    rms = float(np.sqrt(np.mean(np.abs(samples) ** 2)))
    print(f"wrote {out}  samples={samples.size}  rms={rms:.4g}  wall={elapsed:.2f}s")
    return samples


def main() -> int:
    parser = argparse.ArgumentParser(description="UHD B210 IQ capture (first free device)")
    parser.add_argument("--uarfcn", type=int, required=True)
    parser.add_argument("--band", type=int, default=None, help="FDD band if UARFCN is ambiguous")
    parser.add_argument("--duration", type=float, default=3.0)
    parser.add_argument("--gain", type=float, default=40.0)
    parser.add_argument("--serial", type=str, default=None, help="Optional serial; default = first free USRP")
    parser.add_argument("--antenna", type=str, default="auto")
    parser.add_argument("--output", type=str, required=True)
    parser.add_argument("--low-if", action="store_true", help="Tune LO DL-2.4 MHz (debug only)")
    args = parser.parse_args()

    apply_uhd_images()
    usrp, serial = open_free_usrp(args.serial)
    antenna = pick_antenna(usrp, args.antenna)
    capture_iq(
        usrp,
        args.uarfcn,
        args.duration,
        args.gain,
        args.output,
        antenna=antenna,
        serial=serial,
        low_if=args.low_if,
        band=args.band,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
