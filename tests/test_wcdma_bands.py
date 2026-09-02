# WCDMA/UMTS Passive Neighbor Cell Decoder
# Copyright (C) 2026 06kutay <https://github.com/06kutay>

import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from wcdma_bands import (
    band_of_uarfcn,
    resolve_scan_list,
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
    test_one_bit_uarfcn_slip()
    print("  -> wcdma_bands tests PASSED")
