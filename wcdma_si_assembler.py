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
import itertools
import asn1tools
import sys

from wcdma_bands import is_fdd_dl_uarfcn, ul_dl_pair_ok

# Complete SIBs carried in completeSIB-List / completeSIB / nested combined payloads.
_COMPLETE_SIB_ASN = {
    "masterInformationBlock": ("MIB", "MasterInformationBlock"),
    "systemInformationBlockType1": ("SIB1", "SysInfoType1"),
    "systemInformationBlockType2": ("SIB2", "SysInfoType2"),
    "systemInformationBlockType3": ("SIB3", "SysInfoType3"),
    "systemInformationBlockType4": ("SIB4", "SysInfoType4"),
    "systemInformationBlockType6": ("SIB6", "SysInfoType6"),
    "systemInformationBlockType7": ("SIB7", "SysInfoType7"),
    "systemInformationBlockType12": ("SIB12", "SysInfoType12"),
    "schedulingBlock1": ("SB1", "SysInfoTypeSB1"),
    "systemInformationBlockTypeSB1": ("SB1", "SysInfoTypeSB1"),
}

# Segmented SIBs. SIB12 is complete on some cells and segmented on others.
_SEGMENTED_SIB_ASN = {
    "systemInformationBlockType5": ("SIB5", "SysInfoType5"),
    "extensionType": ("SIB19", "SysInfoType19"),
    "systemInformationBlockType11": ("SIB11", "SysInfoType11"),
    "systemInformationBlockType12": ("SIB12", "SysInfoType12"),
}

_DEFAULT_SEG_COUNTS = {
    "systemInformationBlockType5": 3,
    "extensionType": 2,
    "systemInformationBlockType11": 11,
    "systemInformationBlockType12": 2,
}

_COMBINED_PAYLOADS = (
    "completeAndFirst",
    "lastAndFirst",
    "lastAndComplete",
    "lastAndCompleteAndFirst",
)

_SEGMENT_PAYLOADS = (
    "firstSegment",
    "firstSegmentShort",
    "subsequentSegment",
    "lastSegmentShort",
    "lastSegmentLong",
    "lastSegment",
)

def get_sib_type_by_sfn(sfn_prime):
    m = sfn_prime % 32
    if 2 <= m <= 9:
        return 'systemInformationBlockType5'
    elif 11 <= m <= 13:
        return 'extensionType'  # SIB19 is transmitted as extensionType
    elif 14 <= m <= 30:
        return 'systemInformationBlockType11'
    return 'unknown'


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
        if ul is not None and ul_dl_pair_ok(ul, dl):
            score += 20
        elif is_fdd_dl_uarfcn(dl):
            score += 5
        else:
            score -= 10
    # Co-sited other carrier is usually listed first with frequencyInfo.
    if serving_psc is not None and first_freq_psc == serving_psc:
        score += 15
    if len(intra) >= 32:
        score -= 60
    n_intra_bad = 0
    for i, cell in enumerate(intra):
        cid = cell.get("intraFreqCellID")
        mode, val = (cell.get("cellInfo") or {}).get("modeSpecificInfo") or (None, {})
        psc = (val or {}).get("primaryCPICH-Info", {}).get("primaryScramblingCode")
        if mode != "fdd" or psc is None or cid != i:
            n_intra_bad += 1
    score -= 40 * n_intra_bad
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
        msi = ((val or {}).get("cellSelectionReselectionInfo") or {}).get("modeSpecificInfo")
        if isinstance(msi, tuple) and msi[0] not in (None, "gsm"):
            score -= 30
    return score, sib


def _gsm_arfcn_ok(arfcn):
    try:
        a = int(arfcn)
    except (TypeError, ValueError):
        return False
    return (0 <= a <= 124) or (512 <= a <= 885) or (975 <= a <= 1023)


def _sib11_structure_clean(sib):
    """True if SIB11 looks like a finished FDD list, not a desynced PER tail."""
    if not sib:
        return False
    control = sib.get("measurementControlSysInfo") or {}
    hcs = control.get("use-of-HCS") or (None, {})
    if not (isinstance(hcs, tuple) and hcs[0] == "hcs-not-used"):
        return False
    hcs_val = hcs[1] or {}
    quality = hcs_val.get("cellSelectQualityMeasure") or (None, {})
    if not (isinstance(quality, tuple) and quality[0] in ("cpich-RSCP", "cpich-Ec-N0")):
        return False
    qv = quality[1] or {}
    intra = (
        ((qv.get("intraFreqMeasurementSysInfo") or {}).get("intraFreqCellInfoSI-List") or {})
        .get("newIntraFreqCellList")
        or []
    )
    if not intra or len(intra) >= 32:
        return False
    for i, cell in enumerate(intra):
        cid = cell.get("intraFreqCellID")
        mode, val = (cell.get("cellInfo") or {}).get("modeSpecificInfo") or (None, {})
        psc = (val or {}).get("primaryCPICH-Info", {}).get("primaryScramblingCode")
        if mode != "fdd" or psc is None or cid != i:
            return False
    for cell in (
        ((qv.get("interFreqMeasurementSysInfo") or {}).get("interFreqCellInfoSI-List") or {})
        .get("newInterFreqCellList")
        or []
    ):
        mode, _val = (cell.get("cellInfo") or {}).get("modeSpecificInfo") or (None, {})
        if mode == "tdd":
            return False
        freq = cell.get("frequencyInfo")
        if freq:
            fm, fv = freq.get("modeSpecificInfo") or (None, {})
            if fm == "fdd" and not is_fdd_dl_uarfcn((fv or {}).get("uarfcn-DL")):
                return False
            if fm not in (None, "fdd"):
                return False
    for cell in ((hcs_val.get("interRATMeasurementSysInfo") or {}).get("interRATCellInfoList") or {}).get(
        "newInterRATCellList"
    ) or []:
        tech, val = cell.get("technologySpecificInfo") or (None, {})
        if tech in (None, "absent"):
            continue
        if tech != "gsm" or not val:
            return False
        if val.get("frequency-band") == "pcs1900BandUsed":
            return False
        if not _gsm_arfcn_ok(val.get("bcch-ARFCN")):
            return False
    return True


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

    seed_score, seed_sib = _sib11_decode_score(db, assemble(seeded))
    if seed_score > -1000 and _sib11_structure_clean(seed_sib):
        return "".join(seeded)

    beam = [(seeded, seed_score)]
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


def _bit_data_of(seg):
    if not isinstance(seg, dict):
        return None
    return seg.get("sib-Data-fixed") or seg.get("sib-Data-variable")


def _iter_payload_parts(payload_type, payload_val, sfn_prime=None):
    """Walk a SystemInformation-BCH payload CHOICE.

    Turk Telekom (and 3GPP in general) pack a last/complete SIB and the next
    first segment into one BCH block: completeAndFirst, lastAndFirst,
    lastAndComplete, lastAndCompleteAndFirst. Nested firstSegment is
    FirstSegmentShort (sib-Data-variable, 1..214 bits), not FirstSegment.
    """
    if not payload_val:
        return
    if payload_type == "completeSIB-List":
        for item in payload_val:
            yield ("complete", item)
        return
    if payload_type == "completeSIB":
        yield ("complete", payload_val)
        return
    if payload_type in _COMBINED_PAYLOADS and isinstance(payload_val, dict):
        if "lastSegmentShort" in payload_val:
            yield from _iter_payload_parts(
                "lastSegmentShort", payload_val["lastSegmentShort"], sfn_prime
            )
        if "completeSIB-List" in payload_val:
            yield from _iter_payload_parts(
                "completeSIB-List", payload_val["completeSIB-List"], sfn_prime
            )
        if "firstSegment" in payload_val:
            yield from _iter_payload_parts(
                "firstSegmentShort", payload_val["firstSegment"], sfn_prime
            )
        return
    if payload_type not in _SEGMENT_PAYLOADS or not isinstance(payload_val, dict):
        return
    sib_type = payload_val.get("sib-Type") or (
        get_sib_type_by_sfn(sfn_prime) if sfn_prime is not None else None
    )
    if payload_type in ("firstSegment", "firstSegmentShort"):
        seg_idx = 0
        seg_count = payload_val.get("seg-Count")
    else:
        seg_idx = payload_val.get("segmentIndex")
        seg_count = None
    yield (
        "segment",
        {
            "sib-Type": sib_type,
            "seg_idx": seg_idx,
            "bit_data": _bit_data_of(payload_val),
            "seg_count": seg_count,
        },
    )


def _iter_bch_parts(bch_data, db):
    for frame_idx, info in sorted(bch_data.items(), key=lambda x: int(x[0])):
        hex_str = info["hex"] if isinstance(info, dict) else info
        try:
            decoded = db.decode("SystemInformation-BCH", bytes.fromhex(hex_str))
        except Exception:
            continue
        payload_type, payload_val = decoded["payload"]
        sfn_prime = decoded.get("sfn-Prime")
        for kind, item in _iter_payload_parts(payload_type, payload_val, sfn_prime):
            yield frame_idx, kind, item


def collect_extension_occurrences(bch_data, db):
    """Assemble extensionType SIBs as SFN-ordered occurrences.

    SIB19 and SIB11bis both use BCH sib-Type=extensionType. Mixing their
    first/last segments (30+196, 222+176, ...) produces junk UPER. Chain
    each firstSegment to the subsequent/last of the same occurrence.
    """
    open_occs = []
    finished = []
    for _frame_idx, kind, item in _iter_bch_parts(bch_data, db):
        if kind != "segment" or item.get("sib-Type") != "extensionType":
            continue
        idx = item.get("seg_idx")
        data = item.get("bit_data")
        count = item.get("seg_count")
        if idx is None or data is None:
            continue
        if idx == 0 and count:
            occ = {"count": int(count), "segs": {0: data}}
            if occ["count"] == 1:
                finished.append(occ)
            else:
                open_occs.append(occ)
            continue
        for occ in open_occs:
            if idx < occ["count"] and idx not in occ["segs"]:
                occ["segs"][idx] = data
                if all(i in occ["segs"] for i in range(occ["count"])):
                    finished.append(occ)
                    open_occs.remove(occ)
                break
    return finished


def _sib19_clean(sib):
    if not sib:
        return False
    utra = sib.get("utra-PriorityInfoList") or {}
    if utra.get("utran-TDD-FrequencyList"):
        return False
    for item in utra.get("utran-FDD-FrequencyList") or []:
        if not is_fdd_dl_uarfcn(item.get("uarfcn")):
            return False
    eutra = sib.get("eutra-FrequencyAndPriorityInfoList") or []
    gsm = sib.get("gsm-PriorityInfoList") or []
    return bool(eutra or gsm or utra.get("utran-FDD-FrequencyList"))


def _sib11bis_clean(sib):
    meas = (sib or {}).get("measurementControlSysInfo") or {}
    hcs = meas.get("use-of-HCS") or (None, {})
    if not (isinstance(hcs, tuple) and hcs[0] == "hcs-not-used"):
        return False
    hv = hcs[1] or {}
    quality = hv.get("cellSelectQualityMeasure") or (None, {})
    qv = quality[1] or {} if isinstance(quality, tuple) else {}
    n_fdd = 0
    for cell in (qv.get("newIntraFreqCellList") or []) + (qv.get("newInterFreqCellList") or []):
        mode, val = (cell.get("cellInfo") or {}).get("modeSpecificInfo") or (None, {})
        if mode == "tdd":
            return False
        psc = (val or {}).get("primaryCPICH-Info", {}).get("primaryScramblingCode")
        if mode == "fdd" and psc is not None:
            n_fdd += 1
        freq = cell.get("frequencyInfo")
        if freq:
            fm, fv = freq.get("modeSpecificInfo") or (None, {})
            if fm == "fdd" and not is_fdd_dl_uarfcn((fv or {}).get("uarfcn-DL")):
                return False
            if fm not in (None, "fdd"):
                return False
    n_gsm = 0
    for cell in hv.get("newInterRATCellList") or []:
        tech, val = cell.get("technologySpecificInfo") or (None, {})
        if tech in (None, "absent"):
            continue
        if tech != "gsm" or not val:
            return False
        if val.get("frequency-band") == "pcs1900BandUsed":
            return False
        if not _gsm_arfcn_ok(val.get("bcch-ARFCN")):
            return False
        n_gsm += 1
    return n_fdd > 0 or n_gsm > 0


def _decode_extension_occurrence(ordered_segs, db):
    blob = concatenate_bits(list(ordered_segs))
    try:
        sib19 = db.decode("SysInfoType19", blob)
        if _sib19_clean(sib19):
            return "SIB19", sib19
    except Exception:
        pass
    try:
        sib11bis = db.decode("SysInfoType11bis", blob)
        if _sib11bis_clean(sib11bis):
            return "SIB11bis", sib11bis
    except Exception:
        pass
    return None, None


def _add_segment_candidate(store, counts, sib_type, seg_idx, bit_data, seg_count):
    if sib_type not in store or seg_idx is None or bit_data is None:
        return
    lst = store[sib_type].setdefault(seg_idx, [])
    if bit_data not in lst:
        lst.append(bit_data)
    if seg_count:
        counts.setdefault(sib_type, set()).add(int(seg_count))


def _segment_combos(candidates, count):
    lists = [candidates[i] for i in range(count)]
    ncomb = 1
    for lst in lists:
        ncomb *= len(lst)
    if ncomb <= 64:
        yield from itertools.product(*lists)
        return
    seen = set()
    ranks = max(len(lst) for lst in lists)
    for longer_first in (True, False):
        for rank in range(ranks):
            combo = []
            for lst in lists:
                ordered = sorted(lst, key=lambda b: (-b[1] if longer_first else b[1]))
                combo.append(ordered[min(rank, len(ordered) - 1)])
            key = tuple((b[1], b[0]) for b in combo)
            if key not in seen:
                seen.add(key)
                yield tuple(combo)


def _counts_to_try(candidates, expected_set, default):
    counts = []
    for c in list(expected_set or ()) + [default, (max(candidates) + 1 if candidates else None)]:
        if c and c not in counts and all(i in candidates for i in range(c)):
            counts.append(c)
    return counts


def _decode_segmented_sib(sib_type, candidates, expected_set, db):
    """Return decoded SIB or None. Tries first/last length variants (short vs 222)."""
    spec = _SEGMENTED_SIB_ASN.get(sib_type)
    if not spec or not candidates:
        return None
    friendly, asn_name = spec
    best_sib = None
    best_score = None
    for count in _counts_to_try(candidates, expected_set, _DEFAULT_SEG_COUNTS.get(sib_type)):
        for combo in _segment_combos(candidates, count):
            try:
                if sib_type == "systemInformationBlockType11":
                    seg_bits = [sib_bitstring_bits(d, n) for d, n in combo]
                    blob = _bits_to_bytes(repair_sib11_segment_tails(seg_bits, db))
                    score, sib = _sib11_decode_score(db, blob)
                    if sib is None:
                        continue
                    if best_sib is None or score > best_score:
                        best_sib, best_score = sib, score
                        if score > -1000 and _sib11_structure_clean(sib):
                            return sib
                else:
                    blob = concatenate_bits(list(combo))
                    return db.decode(asn_name, blob)
            except Exception:
                continue
    return best_sib


def collect_sib_segments(bch_data, db):
    """Map segmented SIB type -> {index: [(bytes, nbits), ...]} and expected counts."""
    sib_segments = {t: {} for t in _SEGMENTED_SIB_ASN}
    expected_counts = {t: set() for t in _SEGMENTED_SIB_ASN}
    for _frame_idx, info in sorted(bch_data.items(), key=lambda x: int(x[0])):
        hex_str = info["hex"] if isinstance(info, dict) else info
        try:
            decoded = db.decode("SystemInformation-BCH", bytes.fromhex(hex_str))
        except Exception:
            continue
        payload_type, payload_val = decoded["payload"]
        sfn_prime = decoded.get("sfn-Prime")
        for kind, item in _iter_payload_parts(payload_type, payload_val, sfn_prime):
            if kind != "segment":
                continue
            _add_segment_candidate(
                sib_segments,
                expected_counts,
                item.get("sib-Type"),
                item.get("seg_idx"),
                item.get("bit_data"),
                item.get("seg_count"),
            )
    return sib_segments, expected_counts


_SIB_COMPLETE_CACHE = {}


def sib_broadcast_complete(bch_data, db):
    """True when SIB5 + SIB11 + SIB19 segments are present and UPER-decode."""
    segs, expected = collect_sib_segments(bch_data, db)
    needed = (
        "systemInformationBlockType5",
        "extensionType",
        "systemInformationBlockType11",
    )
    cache_key = []
    for sib_type in needed:
        have = segs.get(sib_type) or {}
        counts = _counts_to_try(
            have, expected.get(sib_type), _DEFAULT_SEG_COUNTS.get(sib_type)
        )
        if not counts:
            return False
        cache_key.append(
            (sib_type, counts[0], tuple((i, tuple(n for _d, n in have[i])) for i in range(counts[0])))
        )
    cache_key = tuple(cache_key)
    if cache_key in _SIB_COMPLETE_CACHE:
        return _SIB_COMPLETE_CACHE[cache_key]
    ok = True
    for sib_type in needed:
        if sib_type == "extensionType":
            found_19 = False
            for occ in collect_extension_occurrences(bch_data, db):
                ordered = [occ["segs"][i] for i in range(occ["count"])]
                kind, _sib = _decode_extension_occurrence(ordered, db)
                if kind == "SIB19":
                    found_19 = True
                    break
            if not found_19:
                ok = False
                break
            continue
        if _decode_segmented_sib(sib_type, segs[sib_type], expected.get(sib_type), db) is None:
            ok = False
            break
    _SIB_COMPLETE_CACHE[cache_key] = ok
    return ok


def _decode_complete_item(decoded_sibs, item, db, frame_idx=None):
    if not isinstance(item, dict):
        return
    spec = _COMPLETE_SIB_ASN.get(item.get("sib-Type"))
    if not spec:
        return
    friendly, asn_name = spec
    if friendly in decoded_sibs:
        return
    sib_data_val = item.get("sib-Data-fixed") or item.get("sib-Data-variable")
    if not sib_data_val:
        return
    sib_data, _bit_len = sib_data_val
    try:
        decoded_sibs[friendly] = db.decode(asn_name, sib_data)
    except Exception as e:
        print(f"Error decoding complete SIB {item.get('sib-Type')} in frame {frame_idx}: {e}")


def assemble_sibs_from_json(bch_json_path, db):
    """
    Reads BCH transport blocks from JSON, reassembles segmented SIBs,
    and returns a dictionary of successfully decoded SIB structures.
    """
    with open(bch_json_path, "r") as f:
        bch_data = json.load(f)

    decoded_sibs = {}

    for frame_idx, info in sorted(bch_data.items(), key=lambda x: int(x[0])):
        hex_str = info["hex"] if isinstance(info, dict) else info
        try:
            decoded = db.decode("SystemInformation-BCH", bytes.fromhex(hex_str))
        except Exception:
            continue
        payload_type, payload_val = decoded["payload"]
        sfn_prime = decoded.get("sfn-Prime")
        for kind, item in _iter_payload_parts(payload_type, payload_val, sfn_prime):
            if kind == "complete":
                _decode_complete_item(decoded_sibs, item, db, frame_idx)

    sib_segments, expected_counts = collect_sib_segments(bch_data, db)

    for sib_type, candidates in sib_segments.items():
        friendly, _asn_name = _SEGMENTED_SIB_ASN[sib_type]
        if sib_type == "extensionType":
            occs = collect_extension_occurrences(bch_data, db)
            got = []
            for occ in occs:
                ordered = [occ["segs"][i] for i in range(occ["count"])]
                kind, sib = _decode_extension_occurrence(ordered, db)
                if kind and kind not in decoded_sibs:
                    decoded_sibs[kind] = sib
                    got.append(kind)
                    print(f"[+] Successfully decoded {kind} ({occ['count']} segments)!")
            if not got:
                print("[-] Failed to decode extensionType (SIB19 / SIB11bis)")
            continue
        counts = _counts_to_try(
            candidates, expected_counts.get(sib_type), _DEFAULT_SEG_COUNTS.get(sib_type)
        )
        if not counts:
            if not candidates:
                continue
            have = sorted(candidates)
            print(f"[-] {friendly} is incomplete: have segments {have}")
            continue
        if friendly in decoded_sibs:
            continue
        print(f"[+] All {counts[0]} segments present for {friendly}. Reassembling...")
        sib = _decode_segmented_sib(sib_type, candidates, expected_counts.get(sib_type), db)
        if sib is not None:
            decoded_sibs[friendly] = sib
            print(f"[+] Successfully decoded {friendly}!")
        else:
            print(f"[-] Failed to decode reassembled {friendly}")

    return decoded_sibs


def extract_sib11_fdd_intrafreq(sib11):
    """FDD intra-frequency PSCs from SysInfoType11 newIntraFreqCellList.

    Intra neighbors live in measurementControlSysInfo, not in the inter-freq
    list. A desynced SIB11 tail fills the ASN.1 max of 32 with TDD / duplicate
    IDs; stop at the first broken entry so those are not reported.
    """
    pscs = []
    if not sib11:
        return pscs
    control = sib11.get("measurementControlSysInfo") or {}
    hcs = control.get("use-of-HCS") or (None, {})
    if not (isinstance(hcs, tuple) and hcs[0] == "hcs-not-used"):
        return pscs
    quality = (hcs[1] or {}).get("cellSelectQualityMeasure") or (None, {})
    if not (isinstance(quality, tuple) and quality[0] in ("cpich-RSCP", "cpich-Ec-N0")):
        return pscs
    intra_meas = (quality[1] or {}).get("intraFreqMeasurementSysInfo") or {}
    intra_list = intra_meas.get("intraFreqCellInfoSI-List") or {}
    seen_ids = set()
    for cell in intra_list.get("newIntraFreqCellList") or []:
        cid = cell.get("intraFreqCellID")
        mode, val = (cell.get("cellInfo") or {}).get("modeSpecificInfo") or (None, {})
        psc = (val or {}).get("primaryCPICH-Info", {}).get("primaryScramblingCode")
        if mode != "fdd" or psc is None or cid is None:
            break
        cid = int(cid)
        if cid in seen_ids or (seen_ids and cid <= max(seen_ids)):
            break
        seen_ids.add(cid)
        pscs.append(int(psc))
    return pscs


def extract_sib11_fdd_interfreq(sib11, serving_uarfcn=None):
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
    return _drop_one_bit_uarfcn_slips(neighbors, serving_uarfcn)


def _drop_one_bit_uarfcn_slips(neighbors, serving_uarfcn=None):
    """Drop DLs that are a 1-bit slip of another listed DL or of that DL ±5 MHz.

    Serving ±25 is not an anchor: 3018 is a real Band 8 neighbour of 2997
    but sits 1 bit from 2997+25.
    """
    listed = {int(n["uarfcn"]) for n in neighbors}
    anchors = set(listed)
    for u in listed:
        anchors.add(u + 25)
        anchors.add(u - 25)
    kept = []
    for n in neighbors:
        u = int(n["uarfcn"])
        if any(u != a and (u ^ a).bit_count() == 1 for a in anchors):
            continue
        kept.append(n)
    return kept


def extract_sib11_gsm(sib11):
    """GSM inter-RAT neighbors. IE sits next to cellSelectQualityMeasure, not inside it.

    A desynced PER stream produces mixed is-2000 / pcs1900 / out-of-range ARFCN
    entries; one leftover value in the DCS range (e.g. 840) must not be kept.
    """
    neighbors = []
    if not sib11:
        return neighbors
    control = sib11.get("measurementControlSysInfo") or {}
    hcs = control.get("use-of-HCS") or (None, {})
    if not (isinstance(hcs, tuple) and hcs[1]):
        return neighbors
    irat = (hcs[1] or {}).get("interRATMeasurementSysInfo") or {}
    dirty = 0
    expect_id = 0
    for cell in (irat.get("interRATCellInfoList") or {}).get("newInterRATCellList") or []:
        tech, val = cell.get("technologySpecificInfo") or (None, {})
        if tech in (None, "absent"):
            continue
        if tech != "gsm" or not val:
            dirty += 1
            continue
        if val.get("frequency-band") == "pcs1900BandUsed":
            dirty += 1
            continue
        msi = (val.get("cellSelectionReselectionInfo") or {}).get("modeSpecificInfo")
        if isinstance(msi, tuple) and msi[0] not in (None, "gsm"):
            dirty += 1
            continue
        if not _gsm_arfcn_ok(val.get("bcch-ARFCN")):
            dirty += 1
            continue
        cid = cell.get("interRATCellID")
        if cid is not None and cid != expect_id:
            break
        if cid is not None:
            expect_id = int(cid) + 1
        bsic = val.get("bsic") or {}
        ncc, bcc = bsic.get("ncc"), bsic.get("bcc")
        neighbors.append({
            "cell_id": cid,
            "arfcn": val.get("bcch-ARFCN"),
            "bsic": f"{ncc}{bcc}" if ncc is not None and bcc is not None else "N/A",
            "band": str(val.get("frequency-band", "Unknown")),
        })
    if dirty:
        return []
    return neighbors


def _sib11bis_quality(sib):
    meas = (sib or {}).get("measurementControlSysInfo") or {}
    hcs = meas.get("use-of-HCS") or (None, {})
    if not (isinstance(hcs, tuple) and hcs[0] == "hcs-not-used"):
        return None, None
    quality = (hcs[1] or {}).get("cellSelectQualityMeasure") or (None, {})
    qv = quality[1] or {} if isinstance(quality, tuple) else {}
    return hcs[1], qv


def extract_sib11bis_fdd_intrafreq(sib11bis):
    """Extra intra-frequency PSCs from SysInfoType11bis."""
    pscs = []
    _hv, qv = _sib11bis_quality(sib11bis)
    if qv is None:
        return pscs
    seen = set()
    for cell in qv.get("newIntraFreqCellList") or []:
        mode, val = (cell.get("cellInfo") or {}).get("modeSpecificInfo") or (None, {})
        psc = (val or {}).get("primaryCPICH-Info", {}).get("primaryScramblingCode")
        if mode != "fdd" or psc is None:
            break
        psc = int(psc)
        if psc in seen:
            break
        seen.add(psc)
        pscs.append(psc)
    return pscs


def extract_sib11bis_fdd_interfreq(sib11bis, serving_uarfcn=None):
    """FDD inter-frequency neighbors from SysInfoType11bis (flat newInterFreqCellList)."""
    neighbors = []
    _hv, qv = _sib11bis_quality(sib11bis)
    if qv is None:
        return neighbors
    current_dl = None
    for cell in qv.get("newInterFreqCellList") or []:
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
    return _drop_one_bit_uarfcn_slips(neighbors, serving_uarfcn)


def extract_sib11bis_gsm(sib11bis):
    """GSM neighbors from SysInfoType11bis (newInterRATCellList next to quality)."""
    hv, _qv = _sib11bis_quality(sib11bis)
    if not hv:
        return []
    fake = {
        "measurementControlSysInfo": {
            "use-of-HCS": (
                "hcs-not-used",
                {"interRATMeasurementSysInfo": {"interRATCellInfoList": {"newInterRATCellList": hv.get("newInterRATCellList") or []}}},
            )
        }
    }
    return extract_sib11_gsm(fake)
