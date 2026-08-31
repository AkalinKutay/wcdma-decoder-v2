# WCDMA/UMTS Passive Neighbor Cell Decoder
# Copyright (C) 2026 06kutay <https://github.com/06kutay>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import json
import asn1tools
import sys

def get_sib_type_by_sfn(sfn_prime):
    m = sfn_prime % 32
    if 2 <= m <= 9:
        return 'systemInformationBlockType5'
    elif 11 <= m <= 13:
        return 'extensionType'  # SIB19 is transmitted as extensionType
    elif 14 <= m <= 30:
        return 'systemInformationBlockType11'
    return 'unknown'


FDD_DL_UARFCN_RANGES = (
    (10562, 10838),  # Band 1
    (2937, 3088),    # Band 8
)


def is_fdd_dl_uarfcn(uarfcn):
    try:
        u = int(uarfcn)
    except (TypeError, ValueError):
        return False
    return any(lo <= u <= hi for lo, hi in FDD_DL_UARFCN_RANGES)


def sib_bitstring_bits(data, bit_length):
    """Unpack an ASN.1 BIT STRING (bytes, length) to a '0'/'1' string.

    SIB-Data-fixed is SIZE(222) in a 28-byte buffer (224 bits). Left-align
    (first 222 bits) keeps two unused LSBs of the last byte; those two bits
    sit on the BCH block tail and are the bits that desynchronized SIB11
    versus TEMS (10838 -> 10774). Using the 6 LSBs of the last byte drops
    the two noisy MSBs instead.
    """
    raw = "".join(f"{b:08b}" for b in data)
    if bit_length != 222 or len(raw) < 224:
        return raw[:bit_length]
    return raw[:216] + raw[218:224]


def concatenate_bits(segments):
    """Concatenates (bytes, bit_length) segments to a byte string."""
    all_bits = "".join(sib_bitstring_bits(data, n) for data, n in segments)
    if len(all_bits) % 8:
        all_bits += "0" * (8 - len(all_bits) % 8)
    return bytes(int(all_bits[i:i + 8], 2) for i in range(0, len(all_bits), 8))


def _bits_to_bytes(bits):
    if len(bits) % 8:
        bits += "0" * (8 - len(bits) % 8)
    return bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))


def _sib11_decode_score(db, blob):
    try:
        sib = db.decode("SysInfoType11", blob)
    except Exception:
        return -10000, None
    control = sib.get("measurementControlSysInfo") or {}
    hcs = control.get("use-of-HCS") or (None, {})
    if not (isinstance(hcs, tuple) and hcs[0] == "hcs-not-used"):
        return -1000, sib
    hcs_val = hcs[1] or {}
    quality = hcs_val.get("cellSelectQualityMeasure") or (None, {})
    qv = quality[1] or {} if isinstance(quality, tuple) else {}
    intra = (
        ((qv.get("intraFreqMeasurementSysInfo") or {}).get("intraFreqCellInfoSI-List") or {})
        .get("newIntraFreqCellList")
        or []
    )
    intra_pscs = []
    for cell in intra:
        mode, val = (cell.get("cellInfo") or {}).get("modeSpecificInfo") or (None, {})
        psc = (val or {}).get("primaryCPICH-Info", {}).get("primaryScramblingCode")
        if mode == "fdd" and psc is not None:
            intra_pscs.append(int(psc))
    serving_psc = intra_pscs[0] if intra_pscs else None

    cells = (
        ((qv.get("interFreqMeasurementSysInfo") or {}).get("interFreqCellInfoSI-List") or {})
        .get("newInterFreqCellList")
        or []
    )
    score = 0
    first_freq_psc = None
    for cell in cells:
        mode, val = (cell.get("cellInfo") or {}).get("modeSpecificInfo") or (None, {})
        if mode == "tdd":
            score -= 50
            continue
        psc = (val or {}).get("primaryCPICH-Info", {}).get("primaryScramblingCode")
        if psc is not None:
            score += 1
            if psc in intra_pscs:
                score += 8
        freq = cell.get("frequencyInfo")
        if not freq:
            continue
        fm, fv = freq.get("modeSpecificInfo") or (None, {})
        if fm != "fdd":
            score -= 20
            continue
        if first_freq_psc is None and psc is not None:
            first_freq_psc = int(psc)
        ul = (fv or {}).get("uarfcn-UL")
        dl = (fv or {}).get("uarfcn-DL")
        if ul is not None and dl == ul + 950 and is_fdd_dl_uarfcn(dl):
            score += 20
        elif ul is not None and dl == ul + 225 and is_fdd_dl_uarfcn(dl):
            score += 20
        elif is_fdd_dl_uarfcn(dl):
            score += 5
        else:
            score -= 10
    # Co-sited other carrier is usually listed first with frequencyInfo.
    if serving_psc is not None and first_freq_psc == serving_psc:
        score += 15
    irat = hcs_val.get("interRATMeasurementSysInfo") or {}
    gsm_list = (irat.get("interRATCellInfoList") or {}).get("newInterRATCellList") or []
    for cell in gsm_list:
        tech, val = cell.get("technologySpecificInfo") or (None, {})
        if tech != "gsm":
            score -= 40
            continue
        arfcn = (val or {}).get("bcch-ARFCN")
        if _gsm_arfcn_ok(arfcn):
            score += 25
        else:
            score -= 15
        if (val or {}).get("frequency-band") == "pcs1900BandUsed":
            score -= 25
    return score, sib


def _gsm_arfcn_ok(arfcn):
    try:
        a = int(arfcn)
    except (TypeError, ValueError):
        return False
    return (0 <= a <= 124) or (512 <= a <= 885) or (975 <= a <= 1023)


def repair_sib11_segment_tails(seg_bits, db):
    """Beam-search the last two bits of each subsequent 222-bit segment.

    Those bits sit on the BCH tail. Greedy search locked onto 482 / junk GSM;
    a small beam plus a '11' seed recovers serving-PSC match and a clean
    DCS1800 IRAT list (verified against TEMS on UARFCN 10813).
    """
    n = len(seg_bits)
    if n < 3:
        return "".join(seg_bits)
    idxs = [i for i in range(1, n - 1) if len(seg_bits[i]) == 222]
    if not idxs:
        return "".join(seg_bits)

    def assemble(bits_list):
        return _bits_to_bytes("".join(bits_list))

    seeded = list(seg_bits)
    for i in idxs:
        seeded[i] = seeded[i][:-2] + "11"

    beam = [(seeded, _sib11_decode_score(db, assemble(seeded))[0])]
    width = 8
    for i in idxs:
        nxt = []
        for cur, _sc0 in beam:
            for x in range(4):
                cand = f"{x:02b}"
                trial = list(cur)
                trial[i] = trial[i][:-2] + cand
                sc, _ = _sib11_decode_score(db, assemble(trial))
                nxt.append((trial, sc))
        nxt.sort(key=lambda t: -t[1])
        seen = set()
        beam = []
        for trial, sc in nxt:
            key = "".join(trial)
            if key in seen:
                continue
            seen.add(key)
            beam.append((trial, sc))
            if len(beam) >= width:
                break
    return "".join(beam[0][0])

def assemble_sibs_from_json(bch_json_path, db):
    """
    Reads BCH transport blocks from JSON, reassembles segmented SIBs,
    and returns a dictionary of successfully decoded SIB structures.
    """
    with open(bch_json_path, "r") as f:
        bch_data = json.load(f)

    decoded_sibs = {}
    
    # Store segments for each segmented SIB type
    # Structure: { sib_type: { segment_index: (bytes, bit_length) } }
    sib_segments = {
        'systemInformationBlockType5': {},
        'extensionType': {},  # SIB19
        'systemInformationBlockType11': {}
    }
    
    # Expected segment counts
    expected_counts = {
        'systemInformationBlockType5': 3,
        'extensionType': 2,
        'systemInformationBlockType11': 11
    }

    # First pass: decode complete SIBs and collect segments
    for frame_idx, info in sorted(bch_data.items(), key=lambda x: int(x[0])):
        hex_str = info["hex"]
        raw_bytes = bytes.fromhex(hex_str)
        try:
            decoded = db.decode('SystemInformation-BCH', raw_bytes)
            payload_type = decoded['payload'][0]
            payload_val = decoded['payload'][1]
            sfn_prime = decoded['sfn-Prime']
            
            if not payload_val:
                continue
                
            if payload_type == 'completeSIB-List':
                # completeSIB-List contains multiple complete SIBs
                for item in payload_val:
                    sib_type = item['sib-Type']
                    sib_data_val = item.get('sib-Data-fixed') or item.get('sib-Data-variable')
                    if not sib_data_val:
                        continue
                    sib_data, bit_len = sib_data_val
                    
                    # Decode complete SIB immediately
                    try:
                        if sib_type == 'masterInformationBlock':
                            decoded_sibs['MIB'] = db.decode('MasterInformationBlock', sib_data)
                        elif sib_type == 'systemInformationBlockType1':
                            decoded_sibs['SIB1'] = db.decode('SysInfoType1', sib_data)
                        elif sib_type == 'systemInformationBlockType2':
                            decoded_sibs['SIB2'] = db.decode('SysInfoType2', sib_data)
                        elif sib_type == 'systemInformationBlockType3':
                            decoded_sibs['SIB3'] = db.decode('SysInfoType3', sib_data)
                        elif sib_type == 'systemInformationBlockType4':
                            decoded_sibs['SIB4'] = db.decode('SysInfoType4', sib_data)
                        elif sib_type == 'systemInformationBlockType7':
                            decoded_sibs['SIB7'] = db.decode('SysInfoType7', sib_data)
                        elif sib_type == 'systemInformationBlockTypeSB1':
                            decoded_sibs['SB1'] = db.decode('SysInfoTypeSB1', sib_data)
                    except Exception as e:
                        print(f"Error decoding complete SIB {sib_type} in frame {frame_idx}: {e}")
                        
            elif payload_type in ['firstSegment', 'subsequentSegment', 'lastSegmentShort', 'lastSegmentLong']:
                if not isinstance(payload_val, dict):
                    continue
                sib_type = payload_val.get('sib-Type') or get_sib_type_by_sfn(sfn_prime)
                if sib_type not in sib_segments:
                    continue

                seg_idx = None
                bit_data = None

                if payload_type == 'firstSegment':
                    seg_idx = 0
                    bit_data = payload_val['sib-Data-fixed']
                    expected_counts[sib_type] = payload_val['seg-Count']
                elif payload_type == 'subsequentSegment':
                    seg_idx = payload_val['segmentIndex']
                    bit_data = payload_val['sib-Data-fixed']
                elif payload_type in ['lastSegmentShort', 'lastSegmentLong']:
                    seg_idx = payload_val['segmentIndex']
                    bit_data = payload_val.get('sib-Data-variable') or payload_val.get('sib-Data-fixed')

                if seg_idx is None or bit_data is None:
                    continue
                prev = sib_segments[sib_type].get(seg_idx)
                # Prefer a full 222-bit subsequent over a short last-segment
                # that collided on the same index from another SIB.
                if prev is None or bit_data[1] > prev[1]:
                    sib_segments[sib_type][seg_idx] = bit_data
                    
        except Exception as e:
            # print(f"Warning: Failed to decode BCH payload in frame {frame_idx}: {e}")
            continue

    # Second pass: Reassemble and decode segmented SIBs
    for sib_type, segments in sib_segments.items():
        count = expected_counts[sib_type]
        missing = [idx for idx in range(count) if idx not in segments]
        
        friendly_name = {
            'systemInformationBlockType5': 'SIB5',
            'extensionType': 'SIB19',
            'systemInformationBlockType11': 'SIB11'
        }[sib_type]
        
        if not missing:
            print(f"[+] All {count} segments present for {friendly_name}. Reassembling...")
            ordered_segments = [segments[idx] for idx in range(count)]
            try:
                if sib_type == 'systemInformationBlockType11':
                    seg_bits = [sib_bitstring_bits(d, n) for d, n in ordered_segments]
                    assembled_bytes = _bits_to_bytes(repair_sib11_segment_tails(seg_bits, db))
                    decoded_sibs['SIB11'] = db.decode('SysInfoType11', assembled_bytes)
                elif sib_type == 'systemInformationBlockType5':
                    assembled_bytes = concatenate_bits(ordered_segments)
                    decoded_sibs['SIB5'] = db.decode('SysInfoType5', assembled_bytes)
                elif sib_type == 'extensionType':
                    assembled_bytes = concatenate_bits(ordered_segments)
                    decoded_sibs['SIB19'] = db.decode('SysInfoType19', assembled_bytes)
                print(f"[+] Successfully decoded {friendly_name}!")
            except Exception as e:
                print(f"[-] Failed to decode reassembled {friendly_name}: {e}")
        else:
            print(f"[-] {friendly_name} is incomplete: missing segments {missing}")

    return decoded_sibs


def extract_sib11_fdd_interfreq(sib11):
    """FDD inter-frequency neighbors from SysInfoType11.

    SIB11 ``newInterFreqCellList`` packs cells per frequency: ``frequencyInfo``
    is OPTIONAL and inherited from the previous FDD entry. TDD or out-of-band
    ``uarfcn-DL`` (a broken reassembly used to emit 10774/1881) clears
    inheritance so those values do not stick to later FDD PSCs.
    """
    neighbors = []
    if not sib11:
        return neighbors
    control = sib11.get("measurementControlSysInfo") or {}
    hcs = control.get("use-of-HCS") or (None, {})
    if not (isinstance(hcs, tuple) and hcs[0] == "hcs-not-used"):
        return neighbors
    quality = (hcs[1] or {}).get("cellSelectQualityMeasure") or (None, {})
    if not (isinstance(quality, tuple) and quality[0] in ("cpich-RSCP", "cpich-Ec-N0")):
        return neighbors
    inter_meas = (quality[1] or {}).get("interFreqMeasurementSysInfo") or {}
    inter_list = inter_meas.get("interFreqCellInfoSI-List") or {}
    current_dl = None
    for cell in inter_list.get("newInterFreqCellList") or []:
        cell_info = cell.get("cellInfo") or {}
        cell_mode, cell_val = cell_info.get("modeSpecificInfo") or (None, {})
        freq_info = cell.get("frequencyInfo")
        if freq_info is not None:
            freq_mode, freq_val = freq_info.get("modeSpecificInfo") or (None, {})
            if freq_mode == "fdd":
                dl = (freq_val or {}).get("uarfcn-DL")
                current_dl = dl if is_fdd_dl_uarfcn(dl) else None
            else:
                current_dl = None
        if cell_mode != "fdd" or not current_dl:
            if cell_mode != "fdd":
                current_dl = None
            continue
        psc = (cell_val or {}).get("primaryCPICH-Info", {}).get("primaryScramblingCode")
        if psc is None:
            continue
        neighbors.append({
            "cell_id": cell.get("interFreqCellID"),
            "uarfcn": int(current_dl),
            "psc": int(psc),
        })
    return neighbors


def extract_sib11_gsm(sib11):
    """GSM inter-RAT neighbors. IE sits next to cellSelectQualityMeasure, not inside it."""
    neighbors = []
    if not sib11:
        return neighbors
    control = sib11.get("measurementControlSysInfo") or {}
    hcs = control.get("use-of-HCS") or (None, {})
    if not (isinstance(hcs, tuple) and hcs[1]):
        return neighbors
    irat = (hcs[1] or {}).get("interRATMeasurementSysInfo") or {}
    for cell in (irat.get("interRATCellInfoList") or {}).get("newInterRATCellList") or []:
        tech, val = cell.get("technologySpecificInfo") or (None, {})
        if tech != "gsm" or not val:
            continue
        if not _gsm_arfcn_ok(val.get("bcch-ARFCN")):
            continue
        bsic = val.get("bsic") or {}
        ncc, bcc = bsic.get("ncc"), bsic.get("bcc")
        neighbors.append({
            "cell_id": cell.get("interRATCellID"),
            "arfcn": val.get("bcch-ARFCN"),
            "bsic": f"{ncc}{bcc}" if ncc is not None and bcc is not None else "N/A",
            "band": str(val.get("frequency-band", "Unknown")),
        })
    return neighbors
