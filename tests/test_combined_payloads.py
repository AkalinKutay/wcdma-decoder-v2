# WCDMA/UMTS Passive Neighbor Cell Decoder
# Copyright (C) 2026 06kutay <https://github.com/06kutay>

import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from wcdma_si_assembler import _iter_payload_parts


def test_iter_complete_and_first():
    parts = list(
        _iter_payload_parts(
            "completeAndFirst",
            {
                "completeSIB-List": [
                    {
                        "sib-Type": "systemInformationBlockType3",
                        "sib-Data-variable": (b"\x00", 168),
                    }
                ],
                "firstSegment": {
                    "sib-Type": "systemInformationBlockType5",
                    "seg-Count": 4,
                    "sib-Data-variable": (b"\x01", 29),
                },
            },
        )
    )
    assert [p[0] for p in parts] == ["complete", "segment"]
    assert parts[0][1]["sib-Type"] == "systemInformationBlockType3"
    assert parts[1][1]["sib-Type"] == "systemInformationBlockType5"
    assert parts[1][1]["seg_idx"] == 0
    assert parts[1][1]["seg_count"] == 4
    assert parts[1][1]["bit_data"][1] == 29


def test_iter_last_and_first():
    parts = list(
        _iter_payload_parts(
            "lastAndFirst",
            {
                "lastSegmentShort": {
                    "sib-Type": "systemInformationBlockType11",
                    "segmentIndex": 9,
                    "sib-Data-variable": (b"\x02", 78),
                },
                "firstSegment": {
                    "sib-Type": "systemInformationBlockType12",
                    "seg-Count": 2,
                    "sib-Data-variable": (b"\x03", 119),
                },
            },
        )
    )
    assert [p[0] for p in parts] == ["segment", "segment"]
    assert parts[0][1]["sib-Type"] == "systemInformationBlockType11"
    assert parts[0][1]["seg_idx"] == 9
    assert parts[1][1]["sib-Type"] == "systemInformationBlockType12"
    assert parts[1][1]["seg_idx"] == 0


def test_tt_saved_bch_has_neighbors():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    path = os.path.join(os.path.dirname(__file__), "fixtures", "uarfcn_2938_sc144.bch.json")
    assert os.path.exists(path), path
    sys.path.insert(0, root)
    from wcdma_scan_report import load_rrc_asn1, decode_sib_data

    info = decode_sib_data(path, load_rrc_asn1())
    assert info["mcc"] == "286" and info["mnc"] == "03"
    assert info["cell_id"] == "22871005"
    assert 144 in info["wcdma_intra_pscs"]
    assert any(n["uarfcn"] == 10738 for n in info["wcdma_neighbors"])
    assert any(g["arfcn"] == 535 for g in info["gsm_neighbors"])
    detail = info["detail"]
    assert detail["lac"] == 40614
    assert detail["sib3"]["q_rxlevmin_dbm"] == -115
    assert detail["sib3"]["q_qualmin_db"] == -20
    assert detail["sib7"]["ul_interference_dbm"] == -96
    assert "SIB11" in detail["sibs"]
    from scan_3g import print_cell
    import io
    from contextlib import redirect_stdout
    cell = {
        "uarfcn": 2938,
        "freq_mhz": 927.6,
        "band": "8",
        "psc": 144,
        "ecno": -4.05,
        "rscp": -80.66,
        "operator": "Turk Telekom",
        "plmn": "28603",
        "cid": "22871005",
        "rnc": "348",
        "local_cid": info["local_cell_id"],
        "wcdma_neighbors": info["wcdma_neighbors"],
        "lte_neighbors": info["lte_neighbors"],
        "gsm_neighbors": info["gsm_neighbors"],
        "detail": detail,
        "sibs": info["sibs"],
        "status": "ok",
        "note": "",
        "serial": "test",
        "antenna": "RX2",
        "gain_db": 40,
        "code_group": 18,
        "slot_timing": 1,
        "frame_timing": 2,
        "cfo_hz": -12.3,
        "samples": 100,
        "rms": 0.01,
        "iq_file": "captures/uarfcn_2938.cfile",
        "bch_file": "captures/uarfcn_2938_sc144.bch.json",
        "bch_blocks": 120,
        "extra_cells": [],
    }
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_cell(cell, detail=True)
    text = buf.getvalue()
    assert "LAC" in text and "40614" in text
    assert "qRxLevMin" in text and "-115" in text
    assert "UL girişim" in text and "-96" in text
    assert "SIB11" in text


def test_iter_standalone_first_unchanged():
    parts = list(
        _iter_payload_parts(
            "firstSegment",
            {
                "sib-Type": "systemInformationBlockType11",
                "seg-Count": 11,
                "sib-Data-fixed": (b"\x00" * 28, 222),
            },
        )
    )
    assert len(parts) == 1
    assert parts[0][1]["seg_idx"] == 0
    assert parts[0][1]["bit_data"][1] == 222


if __name__ == "__main__":
    test_iter_complete_and_first()
    test_iter_last_and_first()
    test_iter_standalone_first_unchanged()
    test_tt_saved_bch_has_neighbors()
    print("  -> combined payload walker tests PASSED")
