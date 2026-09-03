# WCDMA/UMTS Passive Neighbor Cell Decoder
# Copyright (C) 2026 06kutay <https://github.com/06kutay>

import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from wcdma_bands import (
    band_dl_range_mhz,
    band_of_uarfcn,
    mhz_to_uarfcn,
    resolve_scan_list,
    survey_hops,
    uarfcn_to_dl_mhz,
    ul_dl_pair_ok,
)


def test_3gpp_dl():
    assert abs(uarfcn_to_dl_mhz(10813, 1) - 2162.6) < 1e-6
    assert abs(uarfcn_to_dl_mhz(2997, 8) - 939.4) < 1e-6
    assert abs(uarfcn_to_dl_mhz(712, 19) - 877.4) < 1e-6
    assert abs(uarfcn_to_dl_mhz(1537, 4) - 2112.4) < 1e-6
    assert abs(uarfcn_to_dl_mhz(4357, 5) - 871.4) < 1e-6
    assert band_of_uarfcn(10813) == 1
    assert band_of_uarfcn(2997) == 8
    assert band_of_uarfcn(712) == 19


def test_cli_resolve():
    assert 10813 in resolve_scan_list([1], None)
    assert 2997 in resolve_scan_list([8], None)
    assert resolve_scan_list([8], [2997]) == [2997]
    try:
        resolve_scan_list([1], [2997])
        assert False, "band mismatch must fail"
    except ValueError:
        pass
    try:
        resolve_scan_list([2], None)
        assert False, "band 2 without uarfcn must fail"
    except ValueError:
        pass


def test_duplex():
    assert ul_dl_pair_ok(9888, 10838)
    assert ul_dl_pair_ok(2712, 2937)


def test_band_range_and_hops():
    lo, hi = band_dl_range_mhz(8)
    assert abs(lo - 927.4) < 1e-6
    assert abs(hi - 957.6) < 1e-6
    assert mhz_to_uarfcn(939.4, 8) == 2997
    assert mhz_to_uarfcn(2162.6, 1) == 10813
    hops8 = survey_hops(8)
    assert len(hops8) == 1
    assert hops8[0]["rate"] == 30.72e6
    assert hops8[0]["analog_bw"] <= hops8[0]["rate"]
    hops1 = survey_hops(1)
    assert len(hops1) == 2
    assert all(h["rate"] <= 30.72e6 for h in hops1)
    assert all(h["analog_bw"] <= 56e6 for h in hops1)
    b1_lo, b1_hi = band_dl_range_mhz(1)
    span1 = hops1[-1]["f_hi_hz"] - hops1[0]["f_lo_hz"]
    assert span1 >= (b1_hi - b1_lo) * 1e6 - 1e3


def _band8_iq_with_wcdma(rng, n, fs, lo, uarfcn_hz, amp=4.0):
    import numpy as np
    from scipy.signal import firwin, lfilter

    iq = (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64) * 0.05
    t = np.arange(n) / fs
    bump = (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64)
    taps = firwin(129, 1.8e6, fs=fs, pass_zero="lowpass").astype(np.float32)
    bump = lfilter(taps, 1.0, bump).astype(np.complex64)
    return iq + amp * bump * np.exp(1j * 2 * np.pi * (uarfcn_hz - lo) * t)


def test_psd_finds_unknown_uarfcn():
    import numpy as np
    from wcdma_band_scan import find_candidate_uarfcns, format_psd_top

    fs = 30.72e6
    lo = 942.5e6
    n = int(0.08 * fs)
    rng = np.random.default_rng(0)
    iq = _band8_iq_with_wcdma(rng, n, fs, lo, 939.4e6)
    found = find_candidate_uarfcns(iq, fs, lo, 8, 927.4e6, 957.6e6, margin_db=4.0)
    assert 2997 in found or any(abs(u - 2997) <= 5 for u in found)
    top = format_psd_top(iq, fs, lo, 8, 927.4e6, 957.6e6)
    assert "2997" in top
    # 5 MHz spacing in the log, not six adjacent 200 kHz bins.
    assert "2998" not in top and "2996" not in top


def test_psd_ignores_gsm_tone():
    import numpy as np
    from wcdma_band_scan import find_candidate_uarfcns, format_psd_top

    fs = 30.72e6
    lo = 942.5e6
    n = int(0.08 * fs)
    rng = np.random.default_rng(1)
    iq = _band8_iq_with_wcdma(rng, n, fs, lo, 939.4e6, amp=3.0)
    t = np.arange(n) / fs
    # Strong GSM-like tone at 954.2 MHz (UARFCN 3071). Mean-window scoring
    # used to list 3071 first and hide 2997.
    iq = iq + (0.8 * np.exp(1j * 2 * np.pi * (954.2e6 - lo) * t)).astype(np.complex64)
    found = find_candidate_uarfcns(iq, fs, lo, 8, 927.4e6, 957.6e6, margin_db=4.0)
    assert 2997 in found or any(abs(u - 2997) <= 5 for u in found)
    top = format_psd_top(iq, fs, lo, 8, 927.4e6, 957.6e6)
    assert "2997" in top
    first = int(top.split()[2])
    assert abs(first - 2997) <= 5, top


def test_one_bit_uarfcn_slip():
    from wcdma_si_assembler import _drop_one_bit_uarfcn_slips

    rows = [
        {"uarfcn": 10813, "psc": 453},
        {"uarfcn": 10789, "psc": 483},  # 10788 xor 1; 10788 = 10813-25
        {"uarfcn": 3018, "psc": 220},
    ]
    kept = _drop_one_bit_uarfcn_slips(rows, serving_uarfcn=2997)
    uarfcns = {n["uarfcn"] for n in kept}
    assert 10813 in uarfcns
    assert 3018 in uarfcns
    assert 10789 not in uarfcns


if __name__ == "__main__":
    test_3gpp_dl()
    test_cli_resolve()
    test_duplex()
    test_band_range_and_hops()
    test_psd_finds_unknown_uarfcn()
    test_psd_ignores_gsm_tone()
    test_one_bit_uarfcn_slip()
    print("  -> wcdma_bands tests PASSED")
