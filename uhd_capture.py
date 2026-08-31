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
ANALOG_BW_HZ = 8e6


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


def uarfcn_to_freq_mhz(uarfcn: int) -> float:
    if 10562 <= uarfcn <= 10838:
        return 2110.0 + 0.2 * (uarfcn - 10562)
    if 2937 <= uarfcn <= 3088:
        return 925.0 + 0.2 * (uarfcn - 2937)
    raise ValueError(f"unsupported UARFCN {uarfcn} (Band 1 / Band 8 only)")


def band_of(uarfcn: int) -> str:
    if 10562 <= uarfcn <= 10838:
        return "b1"
    return "b8"


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


def main() -> int:
    parser = argparse.ArgumentParser(description="UHD B210 IQ capture (first free device)")
    parser.add_argument("--uarfcn", type=int, required=True)
    parser.add_argument("--duration", type=float, default=3.0)
    parser.add_argument("--gain", type=float, default=32.0)
    parser.add_argument("--serial", type=str, default=None, help="Optional serial; default = first free USRP")
    parser.add_argument("--antenna", type=str, default="auto")
    parser.add_argument("--output", type=str, required=True)
    parser.add_argument("--low-if", action="store_true", help="Tune LO DL-2.4 MHz (debug only)")
    args = parser.parse_args()

    import uhd

    apply_uhd_images()
    dl_mhz = uarfcn_to_freq_mhz(args.uarfcn)
    dl_hz = dl_mhz * 1e6
    lo_hz = dl_hz - LOW_IF_HZ if args.low_if else dl_hz
    n = int(SAMP_RATE * args.duration)

    usrp, serial = open_free_usrp(args.serial)
    antenna = pick_antenna(usrp, args.antenna)
    print(
        f"UARFCN {args.uarfcn}  DL {dl_mhz:.1f} MHz  LO {lo_hz / 1e6:.1f} MHz  "
        f"serial={serial} antenna={antenna} gain={args.gain} dB  {args.duration}s"
    )

    usrp.set_rx_antenna(antenna, 0)
    usrp.set_rx_rate(SAMP_RATE, 0)
    try:
        usrp.set_rx_bandwidth(ANALOG_BW_HZ, 0)
    except Exception as exc:
        print(f"warning: set_rx_bandwidth failed: {exc}")
    usrp.set_rx_freq(uhd.types.TuneRequest(lo_hz), 0)
    usrp.set_rx_gain(args.gain, 0)
    time.sleep(0.3)

    t0 = time.time()
    samples = usrp.recv_num_samps(n, lo_hz, SAMP_RATE, [0], args.gain)
    elapsed = time.time() - t0
    samples = np.asarray(samples, dtype=np.complex64)
    if samples.ndim == 2:
        samples = samples[0]

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    samples.tofile(out)

    meta = {
        "uarfcn": args.uarfcn,
        "frequency_mhz": dl_mhz,
        "lo_frequency_hz": lo_hz,
        "low_if_hz": LOW_IF_HZ if args.low_if else 0.0,
        "analog_bw_hz": ANALOG_BW_HZ,
        "sample_rate_hz": SAMP_RATE,
        "duration_seconds": args.duration,
        "gain_db": args.gain,
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
