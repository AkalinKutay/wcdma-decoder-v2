#!/usr/bin/env python3
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

import os
import sys
import glob
import json
import hashlib
import pickle
import re
from datetime import datetime
from pathlib import Path
import asn1tools
import wcdma_si_assembler

ASN_FILE_NAMES = (
    "Constant-definitions.asn",
    "Class-definitions.asn",
    "InformationElements.asn",
    "PDU-definitions.asn",
    "Internode-definitions.asn",
)


def asn1_files(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parent
    return [root / "wcdma_rrc_asn1" / n for n in ASN_FILE_NAMES]


def load_rrc_asn1(root=None):
    """Compile TS 25.331 ASN.1, reusing a pickle cache keyed by file mtimes."""
    files = asn1_files(root)
    stamp_parts = [
        sys.version,
        getattr(asn1tools, "__version__", ""),
    ]
    for path in files:
        st = path.stat()
        stamp_parts.append(f"{path.name}:{st.st_mtime_ns}:{st.st_size}")
    key = hashlib.sha256("|".join(stamp_parts).encode()).hexdigest()[:16]
    cache_dir = Path.home() / ".cache" / "wcdma-sib-scan"
    cache_path = cache_dir / f"asn1-{key}.pkl"
    if cache_path.exists():
        try:
            with open(cache_path, "rb") as fh:
                return pickle.load(fh)
        except Exception:
            pass
    db = asn1tools.compile_files([str(p) for p in files], "uper")
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "wb") as fh:
            pickle.dump(db, fh, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        pass
    return db

def uarfcn_to_freq(uarfcn):
    try:
        from wcdma_bands import uarfcn_to_dl_mhz

        return uarfcn_to_dl_mhz(int(uarfcn))
    except Exception:
        return 0.0

def earfcn_to_freq_band(earfcn):
    freq_mhz = 0.0
    band_str = "Unknown"
    if 0 <= earfcn <= 599:
        freq_mhz = 2110.0 + 0.1 * earfcn
        band_str = "Band 1"
    elif 1200 <= earfcn <= 1949:
        freq_mhz = 1805.0 + 0.1 * (earfcn - 1200)
        band_str = "Band 3"
    elif 2750 <= earfcn <= 3449:
        freq_mhz = 2620.0 + 0.1 * (earfcn - 2750)
        band_str = "Band 7"
    elif 6150 <= earfcn <= 6449:
        freq_mhz = 791.0 + 0.1 * (earfcn - 6150)
        band_str = "Band 20"
    return freq_mhz, band_str

def format_cell_id(bit_tuple):
    if not bit_tuple:
        return None, None, None
    data, bit_len = bit_tuple
    val = int.from_bytes(data, 'big') >> (len(data) * 8 - bit_len)
    rnc_id = val >> 16
    cell_id = val & 0xFFFF
    return val, rnc_id, cell_id


def _choice(val):
    if isinstance(val, tuple) and len(val) == 2:
        return val[0], val[1]
    return None, val


def _bit_int(val):
    if val is None:
        return None
    if isinstance(val, tuple) and len(val) == 2 and isinstance(val[0], (bytes, bytearray)):
        data, nbits = val[0], int(val[1])
        return int.from_bytes(data, "big") >> (len(data) * 8 - nbits)
    if isinstance(val, (bytes, bytearray)):
        return int.from_bytes(val, "big")
    if isinstance(val, int):
        return val
    return None


def _nas_bytes(val):
    if isinstance(val, (bytes, bytearray)):
        return bytes(val)
    if isinstance(val, tuple) and val and isinstance(val[0], (bytes, bytearray)):
        return bytes(val[0])
    return b""


def _sib_short(name):
    mapping = {
        "sysInfoType1": "SIB1",
        "sysInfoType2": "SIB2",
        "sysInfoType3": "SIB3",
        "sysInfoType4": "SIB4",
        "sysInfoType5": "SIB5",
        "sysInfoType6": "SIB6",
        "sysInfoType7": "SIB7",
        "sysInfoType11": "SIB11",
        "sysInfoType12": "SIB12",
        "sysInfoTypeSB1": "SB1",
        "schedulingBlock1": "SB1",
        "systemInfoType19": "SIB19",
        "systemInfoType11bis": "SIB11bis",
        "systemInformationBlockType11bis": "SIB11bis",
    }
    return mapping.get(str(name), str(name))


def extract_sib_detail(sibs):
    """Flatten decoded SIBs into a small dict for --detail print."""
    out = {
        "sibs": sorted(sibs.keys()) if sibs else [],
        "mib_tag": None,
        "schedule": [],
        "lac": None,
        "cs": {},
        "ps": {},
        "drx": None,
        "ura": [],
        "sib3": {},
        "sib5": {},
        "sib7": {},
        "sib11": {},
        "sib19_utra": {},
        "qoffsets": [],
    }
    if not sibs:
        return out

    mib = sibs.get("MIB") or {}
    out["mib_tag"] = mib.get("mib-ValueTag")
    for item in mib.get("sibSb-ReferenceList") or []:
        kind, tag = _choice(item.get("sibSb-Type"))
        sch = ((item.get("scheduling") or {}).get("scheduling")) or {}
        pos_k, pos_v = _choice(sch.get("sib-Pos"))
        out["schedule"].append({
            "sib": _sib_short(kind),
            "tag": tag,
            "seg": sch.get("segCount"),
            "rep": pos_k,
            "pos": pos_v,
        })
    ext = mib
    for key in (
        "v690NonCriticalExtensions",
        "v6b0NonCriticalExtensions",
        "v860NonCriticalExtensions",
    ):
        ext = (ext or {}).get(key) or {}
    mib860 = ext.get("masterInformationBlock-v860ext") or {}
    for item in mib860.get("extSIBTypeInfoSchedulingInfo-List") or []:
        kind, tag = _choice(item.get("extensionSIB-Type2") or item.get("extensionSIB-Type"))
        sch = ((item.get("schedulingInfo") or {}).get("scheduling")) or {}
        pos_k, pos_v = _choice(sch.get("sib-Pos"))
        vtag = item.get("valueTagInfo")
        if isinstance(vtag, tuple):
            vtag = vtag[1]
        out["schedule"].append({
            "sib": _sib_short(kind),
            "tag": vtag if tag is None else tag,
            "seg": sch.get("segCount"),
            "rep": pos_k,
            "pos": pos_v,
        })

    sib1 = sibs.get("SIB1") or {}
    lac_b = _nas_bytes(sib1.get("cn-CommonGSM-MAP-NAS-SysInfo"))
    if len(lac_b) >= 2:
        out["lac"] = int.from_bytes(lac_b[:2], "big")
    for item in sib1.get("cn-DomainSysInfoList") or []:
        nas = b""
        cn_type = item.get("cn-Type")
        _k, raw = _choice(cn_type)
        nas = _nas_bytes(raw)
        drx = item.get("cn-DRX-CycleLengthCoeff")
        if drx is not None:
            out["drx"] = drx
        ident = item.get("cn-DomainIdentity")
        if ident == "cs-domain":
            out["cs"] = {
                "t3212": nas[0] if nas else None,
                "att": bool(nas[1] & 0x01) if len(nas) > 1 else None,
            }
        elif ident == "ps-domain":
            out["ps"] = {
                "rac": nas[0] if nas else None,
                "nmo": nas[1] if len(nas) > 1 else None,
            }

    for ura in (sibs.get("SIB2") or {}).get("ura-IdentityList") or []:
        val = _bit_int(ura)
        if val is not None:
            out["ura"].append(val)

    sib3 = sibs.get("SIB3") or {}
    sel = sib3.get("cellSelectReselectInfo") or {}
    qkind, qval = _choice(sel.get("cellSelectQualityMeasure"))
    fdd_k, fdd = _choice(sel.get("modeSpecificInfo"))
    fdd = fdd or {}
    barred_k, barred_v = _choice((sib3.get("cellAccessRestriction") or {}).get("cellBarred"))
    ac = (sib3.get("cellAccessRestriction") or {}).get("accessClassBarredList") or []
    q_rx = fdd.get("q-RxlevMin")
    s_intra = fdd.get("s-Intrasearch")
    s_inter = fdd.get("s-Intersearch")
    q_hyst = sel.get("q-Hyst-l-S")
    rats = []
    for rat in fdd.get("rat-List") or []:
        rats.append({
            "rat": rat.get("rat-Identifier"),
            "s_search": rat.get("s-SearchRAT"),
        })
    out["sib3"] = {
        "quality": qkind,
        "q_qualmin_db": fdd.get("q-QualMin"),
        "q_rxlevmin_dbm": (q_rx * 2 + 1) if q_rx is not None else None,
        "q_hyst_db": (q_hyst * 2) if q_hyst is not None else None,
        "t_reselect_s": sel.get("t-Reselection-S"),
        "s_intra_db": (s_intra * 2) if s_intra is not None else None,
        "s_inter_db": (s_inter * 2) if s_inter is not None else None,
        "max_ul_tx_dbm": sel.get("maxAllowedUL-TX-Power"),
        "barred": barred_k,
        "reserved": (sib3.get("cellAccessRestriction") or {}).get("cellReservedForOperatorUse"),
        "ac_barred": sum(1 for x in ac if x and x != "notBarred"),
        "ac_total": len(ac),
        "rats": rats,
        "sib4indicator": sib3.get("sib4indicator"),
    }

    sib5 = sibs.get("SIB5") or {}
    _mk, mval = _choice(sib5.get("modeSpecificInfo"))
    pccpch_k, pccpch = _choice(sib5.get("primaryCCPCH-Info"))
    prach_sf = None
    prach_sc = None
    for prach in sib5.get("prach-SystemInformationList") or []:
        _pk, pinfo = _choice((prach.get("prach-RACH-Info") or {}).get("modeSpecificInfo"))
        pinfo = pinfo or {}
        prach_sf = pinfo.get("availableSF")
        prach_sc = pinfo.get("preambleScramblingCodeWordNumber")
        break
    sccpch = []
    for scc in sib5.get("sCCPCH-SystemInformationList") or []:
        _sk, sinfo = _choice((scc.get("secondaryCCPCH-Info") or {}).get("modeSpecificInfo"))
        sinfo = sinfo or {}
        sf_k, sf_v = _choice(sinfo.get("sf-AndCodeNumber"))
        sccpch.append({"sf": sf_k, "code": sf_v, "sttd": sinfo.get("sttd-Indicator")})
    out["sib5"] = {
        "pich_db": sib5.get("pich-PowerOffset"),
        "aich_db": (mval or {}).get("aich-PowerOffset") if isinstance(mval, dict) else None,
        "tx_div": (pccpch or {}).get("tx-DiversityIndicator") if isinstance(pccpch, dict) else None,
        "prach_sf": prach_sf,
        "prach_sc": prach_sc,
        "sccpch": sccpch,
    }

    sib7 = sibs.get("SIB7") or {}
    _uk, uval = _choice(sib7.get("modeSpecificInfo"))
    out["sib7"] = {
        "ul_interference_dbm": (uval or {}).get("ul-Interference") if isinstance(uval, dict) else None,
    }

    def _walk_offsets(sib):
        rows = []
        control = (sib or {}).get("measurementControlSysInfo") or {}
        hcs_k, hcs_v = _choice(control.get("use-of-HCS"))
        q_k, q_v = _choice((hcs_v or {}).get("cellSelectQualityMeasure"))
        intra = ((q_v or {}).get("intraFreqMeasurementSysInfo") or {})
        intra_list = (intra.get("intraFreqCellInfoSI-List") or {}).get("newIntraFreqCellList") or []
        if not intra_list and isinstance(q_v, dict):
            intra_list = q_v.get("newIntraFreqCellList") or []
        for cell in intra_list:
            mode, val = _choice((cell.get("cellInfo") or {}).get("modeSpecificInfo"))
            psc = (val or {}).get("primaryCPICH-Info", {}).get("primaryScramblingCode")
            cio = (cell.get("cellInfo") or {}).get("cellIndividualOffset", 0)
            if mode == "fdd" and psc is not None:
                rows.append({
                    "psc": int(psc),
                    "qoffset_db": (cio or 0) * 0.5,
                    "id": cell.get("intraFreqCellID"),
                })
        return hcs_k, q_k, rows

    hcs_k = None
    q_k = None
    offsets = []
    for key in ("SIB11", "SIB12", "SIB11bis"):
        if not sibs.get(key):
            continue
        hk, qk, rows = _walk_offsets(sibs[key])
        if hcs_k is None:
            hcs_k, q_k = hk, qk
        offsets.extend(rows)
    out["sib11"] = {"hcs": hcs_k, "quality": q_k}
    seen_off = set()
    for row in offsets:
        if row["psc"] in seen_off:
            continue
        seen_off.add(row["psc"])
        if row["qoffset_db"]:
            out["qoffsets"].append(row)

    sib19 = sibs.get("SIB19") or {}
    utra = sib19.get("utra-PriorityInfoList") or {}
    serving = utra.get("utra-ServingCell") or {}
    if serving:
        out["sib19_utra"] = {
            "priority": serving.get("priority"),
            "s_priority_search1_db": (serving.get("s-PrioritySearch1") or 0) * 2
            if serving.get("s-PrioritySearch1") is not None else None,
            "thresh_serving_low_db": (serving.get("threshServingLow") or 0) * 2
            if serving.get("threshServingLow") is not None else None,
        }
    return out


def decode_sib_data(bch_json_path, db):
    """
    Decodes SIBs from a given BCH json file.
    """
    try:
        sibs = wcdma_si_assembler.assemble_sibs_from_json(bch_json_path, db)
    except Exception as e:
        print(f"[-] SIB birleştirme hatası ({bch_json_path}): {e}")
        return None
        
    info = {
        "mcc": "N/A",
        "mnc": "N/A",
        "cell_id": "N/A",
        "rnc_id": "N/A",
        "local_cell_id": "N/A",
        "wcdma_neighbors": [],
        "wcdma_intra_pscs": [],
        "lte_neighbors": [],
        "gsm_neighbors": []
    }
    
    if 'MIB' in sibs:
        mib = sibs['MIB']
        plmn_type = mib.get('plmn-Type', (None, {}))
        if plmn_type[0] == 'gsm-MAP':
            plmn_id = plmn_type[1].get('plmn-Identity', {})
            mcc_list = plmn_id.get('mcc', [])
            mnc_list = plmn_id.get('mnc', [])
            info["mcc"] = "".join(str(x) for x in mcc_list)
            info["mnc"] = "".join(str(x) for x in mnc_list)
            
    if 'SIB3' in sibs:
        sib3 = sibs['SIB3']
        val, rnc_id, cell_id = format_cell_id(sib3.get('cellIdentity'))
        if val is not None:
            info["cell_id"] = str(val)
            info["rnc_id"] = str(rnc_id)
            info["local_cell_id"] = str(cell_id)
            
    serving_u = None
    try:
        m = re.search(r"uarfcn_(\d+)", Path(bch_json_path).name)
        if m:
            serving_u = int(m.group(1))
    except Exception:
        pass

    intra, inter, gsm = [], [], []
    if sibs.get("SIB11"):
        intra.extend(wcdma_si_assembler.extract_sib11_fdd_intrafreq(sibs["SIB11"]))
        inter.extend(wcdma_si_assembler.extract_sib11_fdd_interfreq(sibs["SIB11"], serving_uarfcn=serving_u))
        gsm.extend(wcdma_si_assembler.extract_sib11_gsm(sibs["SIB11"]))
    if sibs.get("SIB11bis"):
        intra.extend(wcdma_si_assembler.extract_sib11bis_fdd_intrafreq(sibs["SIB11bis"]))
        inter.extend(wcdma_si_assembler.extract_sib11bis_fdd_interfreq(sibs["SIB11bis"], serving_uarfcn=serving_u))
        gsm.extend(wcdma_si_assembler.extract_sib11bis_gsm(sibs["SIB11bis"]))
    if sibs.get("SIB12"):
        intra.extend(wcdma_si_assembler.extract_sib11_fdd_intrafreq(sibs["SIB12"]))
        inter.extend(wcdma_si_assembler.extract_sib11_fdd_interfreq(sibs["SIB12"], serving_uarfcn=serving_u))
        gsm.extend(wcdma_si_assembler.extract_sib11_gsm(sibs["SIB12"]))

    seen_psc = set()
    info["wcdma_intra_pscs"] = [p for p in intra if not (p in seen_psc or seen_psc.add(p))]
    seen_inter = set()
    info["wcdma_neighbors"] = []
    for n in inter:
        key = (n.get("uarfcn"), n.get("psc"))
        if key in seen_inter:
            continue
        seen_inter.add(key)
        info["wcdma_neighbors"].append(n)
    seen_gsm = set()
    info["gsm_neighbors"] = []
    for n in gsm:
        key = (n.get("arfcn"), n.get("bsic"))
        if key in seen_gsm:
            continue
        seen_gsm.add(key)
        info["gsm_neighbors"].append(n)
                                
    if 'SIB19' in sibs:
        sib19 = sibs['SIB19']
        lte_list = sib19.get('eutra-FrequencyAndPriorityInfoList', [])
        for item in lte_list:
            earfcn = item.get('earfcn')
            bandwidth = item.get('measurementBandwidth')
            priority = item.get('priority')
            q_rx_lev_min = item.get('qRxLevMinEUTRA')
            
            bw_map = {
                'mbw6': '1.4 MHz',
                'mbw15': '3 MHz',
                'mbw25': '5 MHz',
                'mbw50': '10 MHz',
                'mbw75': '15 MHz',
                'mbw100': '20 MHz'
            }
            bw_str = bw_map.get(bandwidth, str(bandwidth))
            
            freq_mhz, band_str = earfcn_to_freq_band(earfcn)
            
            info["lte_neighbors"].append({
                "earfcn": earfcn,
                "band": band_str,
                "freq_mhz": freq_mhz,
                "bw": bw_str,
                "priority": priority,
                "q_rx_lev_min": q_rx_lev_min,
                "q_rx_lev_min_dbm": (q_rx_lev_min * 2) if q_rx_lev_min is not None else None,
                "thresh_x_high_db": (item.get("threshXhigh") * 2)
                if item.get("threshXhigh") is not None else None,
                "thresh_x_low_db": (item.get("threshXlow") * 2)
                if item.get("threshXlow") is not None else None,
                "eutra_detection": item.get("eutraDetection"),
            })

    info["sibs"] = sorted(sibs.keys())
    info["detail"] = extract_sib_detail(sibs)
    return info

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Birleşik Tarama Raporu Oluşturucu")
    parser.add_argument("--uarfcns", type=int, nargs="+", help="Sadece bu UARFCN'lerin sonuçlarını raporla")
    args = parser.parse_args()

    print("[*] Birleşik Tarama Raporu Oluşturucu")

    try:
        db = load_rrc_asn1()
    except Exception as e:
        print(f"[-] ASN.1 derleme hatası: {e}")
        sys.exit(1)
        
    results_files = glob.glob("captures/*.results.json")
    
    # Filter by uarfcns if specified
    if args.uarfcns:
        filtered_files = []
        for rf in results_files:
            try:
                with open(rf, "r") as f:
                    data = json.load(f)
                uarfcn = data.get("uarfcn")
                if uarfcn in args.uarfcns:
                    filtered_files.append(rf)
            except Exception:
                pass
        results_files = filtered_files

    if not results_files:
        print("[-] Raporlanacak aktif results.json dosyası bulunamadı!")
        sys.exit(1)
        
    scanned_cells = []
    wcdma_neighbors_all = []
    lte_neighbors_all = []
    gsm_neighbors_all = []
    scanned_uarfcns = set()
    
    for rf in results_files:
        with open(rf, "r") as f:
            data = json.load(f)
            
        uarfcn = data.get("uarfcn")
        if uarfcn:
            scanned_uarfcns.add(int(uarfcn))
            
        freq_mhz = data.get("frequency_mhz", 0.0)
        
        prefix = rf.replace(".results.json", "")
        
        for cell in data.get("cells", []):
            sc = cell["scrambling_code"]
            ecno = cell["cpich_ecno_db"]
            rscp = cell["cpich_rscp_dbm"]
            grp = cell["code_group"]
            timing = cell["frame_timing_sample"]
            cfo = cell["frequency_correction_hz"]
            
            # Check for bch file
            bch_filename = prefix.replace(".cfile", f"_sc{sc}.bch.json")
            if not os.path.exists(bch_filename):
                bch_filename = prefix + f"_sc{sc}.bch.json"
            if not os.path.exists(bch_filename):
                bch_filename = rf.replace(".results.json", f"_sc{sc}.bch.json")
            if not os.path.exists(bch_filename):
                # Fallback to general .bch.json
                bch_filename = rf.replace(".results.json", ".bch.json")
                
            sib_info = None
            if os.path.exists(bch_filename):
                # Only use if this bch file belongs to the current cell or is legacy general
                # We assume wcdma_scan.py writes cell-specific bch files correctly
                sib_info = decode_sib_data(bch_filename, db)
                
            cell_summary = {
                "uarfcn": uarfcn,
                "freq_mhz": freq_mhz,
                "sc": sc,
                "grp": grp,
                "ecno": ecno,
                "rscp": rscp,
                "timing": timing,
                "cfo": cfo,
                "bch_status": "Çözüldü" if sib_info else "Çözülmedi",
                "mcc": sib_info["mcc"] if sib_info else "N/A",
                "mnc": sib_info["mnc"] if sib_info else "N/A",
                "cell_id": sib_info["cell_id"] if sib_info else "N/A",
                "rnc_id": sib_info["rnc_id"] if sib_info else "N/A",
                "local_cell_id": sib_info["local_cell_id"] if sib_info else "N/A"
            }
            scanned_cells.append(cell_summary)
            
            if sib_info:
                # Add to neighbor list collections
                serving_uarfcn = int(uarfcn) if uarfcn is not None else None
                for psc in sib_info.get("wcdma_intra_pscs") or []:
                    if serving_uarfcn is None or int(psc) == int(sc):
                        continue
                    wcdma_neighbors_all.append({
                        "serving_sc": sc,
                        "cell_id": None,
                        "uarfcn": serving_uarfcn,
                        "psc": int(psc),
                    })
                for n in sib_info["wcdma_neighbors"]:
                    wcdma_neighbors_all.append({
                        "serving_sc": sc,
                        "cell_id": n["cell_id"],
                        "uarfcn": n["uarfcn"],
                        "psc": n["psc"]
                    })
                for n in sib_info["lte_neighbors"]:
                    lte_neighbors_all.append({
                        "serving_sc": sc,
                        "earfcn": n["earfcn"],
                        "band": n["band"],
                        "freq_mhz": n["freq_mhz"],
                        "bw": n["bw"],
                        "priority": n["priority"],
                        "q_rx_lev_min": n["q_rx_lev_min"]
                    })
                for n in sib_info["gsm_neighbors"]:
                    gsm_neighbors_all.append({
                        "serving_sc": sc,
                        "cell_id": n["cell_id"],
                        "arfcn": n["arfcn"],
                        "bsic": n["bsic"],
                        "band": n["band"]
                    })

    # Find discovered but not scanned UARFCNs
    discovered_uarfcns = set()
    for n in wcdma_neighbors_all:
        if wcdma_si_assembler.is_fdd_dl_uarfcn(n.get("uarfcn")):
            discovered_uarfcns.add(int(n["uarfcn"]))
            
    unscanned_uarfcns = discovered_uarfcns - scanned_uarfcns
    
    # Sort tables
    scanned_cells = sorted(scanned_cells, key=lambda x: (x["uarfcn"], -x["ecno"]))
    
    # Generate Output File
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_filename = f"captures/wcdma_scan_report_{now_str}.txt"
    
    report = []
    report.append("==================================================================================")
    report.append(f"               WCDMA/UMTS TEK KOMUTLU SAHA TARAMA BİRLEŞİK RAPORU")
    report.append(f"               Rapor Tarihi: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append("==================================================================================")
    report.append("")
    
    # Table 1: Detected Cells
    report.append("TABLO 1: TESPİT EDİLEN HÜCRELER (SERVING & DETECTED)")
    report.append("-" * 115)
    report.append(f"| {'UARFCN':6s} | {'Frekans':8s} | {'PSC':3s} | {'Ec/N0':6s} | {'RSCP':8s} | {'BCH':8s} | {'PLMN':7s} | {'Cell ID':8s} | {'RNC ID':6s} | {'L-Cell ID':9s} |")
    report.append("-" * 115)
    for c in scanned_cells:
        plmn = f"{c['mcc']}-{c['mnc']}" if c['mcc'] != "N/A" else "N/A"
        report.append(f"| {c['uarfcn']:6} | {c['freq_mhz']:6.1f} MHz | {c['sc']:3d} | {c['ecno']:5.1f}dB | {c['rscp']:6.1f}dBm | {c['bch_status']:8s} | {plmn:7s} | {c['cell_id']:8s} | {c['rnc_id']:6s} | {c['local_cell_id']:9s} |")
    report.append("-" * 115)
    report.append("")
    
    # Table 2: WCDMA Neighbors (SIB11)
    report.append("TABLO 2: WCDMA SIB11 INTER-FREQUENCY KOMŞULAR")
    report.append("-" * 85)
    report.append(f"| {'Serving PSC':11s} | {'Neighbor ID':11s} | {'UARFCN':6s} | {'DL Frequency':13s} | {'Neighbor PSC':12s} |")
    report.append("-" * 85)
    if wcdma_neighbors_all:
        for n in wcdma_neighbors_all:
            cell_id_str = str(n['cell_id']) if n['cell_id'] is not None else "N/A"
            u_val = n['uarfcn']
            freq_mhz = uarfcn_to_freq(u_val)
            report.append(f"| {n['serving_sc']:11d} | {cell_id_str:>11s} | {u_val:6d} | {freq_mhz:8.1f} MHz   | {n['psc']:12d} |")
    else:
        report.append(f"| {'-':11s} | {'-':11s} | {'-':6s} | {'-':13s} | {'-':12s} |")
    report.append("-" * 85)
    report.append("")
    
    # Table 3: LTE Neighbors (SIB19)
    report.append("TABLO 3: LTE SIB19 NEIGHBOR FREQUENCIES (E-UTRA)")
    report.append("-" * 95)
    report.append(f"| {'Serving PSC':11s} | {'EARFCN':6s} | {'Band':7s} | {'Frequency':10s} | {'Bandwidth':10s} | {'Priority':8s} | {'Min Rx':6s} |")
    report.append("-" * 95)
    if lte_neighbors_all:
        for n in lte_neighbors_all:
            report.append(f"| {n['serving_sc']:11d} | {n['earfcn']:6d} | {n['band']:7s} | {n['freq_mhz']:6.1f} MHz | {n['bw']:10s} | {n['priority']:8d} | {n['q_rx_lev_min']:4d}dB |")
    else:
        report.append(f"| {'-':11s} | {'-':6s} | {'-':7s} | {'-':10s} | {'-':10s} | {'-':8s} | {'-':6s} |")
    report.append("-" * 95)
    report.append("")
    
    # Table 4: GSM Neighbors (SIB11)
    report.append("TABLO 4: GSM SIB11 INTER-RAT KOMŞULAR")
    report.append("-" * 65)
    report.append(f"| {'Serving PSC':11s} | {'ARFCN':5s} | {'BSIC':4s} | {'Frequency Band':20s} |")
    report.append("-" * 65)
    if gsm_neighbors_all:
        for n in gsm_neighbors_all:
            report.append(f"| {n['serving_sc']:11d} | {n['arfcn']:5d} | {n['bsic']:4s} | {n['band']:20s} |")
    else:
        report.append(f"| {'-':11s} | {'-':5s} | {'-':4s} | {'-':20s} |")
    report.append("-" * 65)
    report.append("")
    
    # Table 5: Discovery Summary
    report.append("TABLO 5: KEŞİF VE RESCAN ÖNERİLERİ (SIB11'den Çıkan Taranmamış UARFCN'ler)")
    report.append("-" * 80)
    report.append(f"| {'UARFCN':6s} | {'DL Frequency':13s} | {'Önerilen Tarama Komutu':55s} |")
    report.append("-" * 80)
    if unscanned_uarfcns:
        for u in sorted(unscanned_uarfcns):
            freq_mhz = uarfcn_to_freq(u)
            cmd_suggestion = f"python3 wcdma_scan.py --uarfcn {u}"
            report.append(f"| {u:6d} | {freq_mhz:8.1f} MHz   | {cmd_suggestion:55s} |")
    else:
        report.append(f"| {'-':6s} | {'-':13s} | {'Tüm komşu taşıyıcılar taranmış durumda.':55s} |")
    report.append("-" * 80)
    report.append("")
    
    # Write report to text file
    report_content = "\n".join(report)
    with open(report_filename, "w") as f:
        f.write(report_content)
        
    print(f"\033[92m[+] Birleşik metin raporu başarıyla yazıldı: {report_filename}\033[0m")
    
    # Print the same report in colored format to terminal
    print("\n" + report_content.replace("TABLO 1", "\033[96mTABLO 1\033[0m")
                              .replace("TABLO 2", "\033[96mTABLO 2\033[0m")
                              .replace("TABLO 3", "\033[96mTABLO 3\033[0m")
                              .replace("TABLO 4", "\033[96mTABLO 4\033[0m")
                              .replace("TABLO 5", "\033[93mTABLO 5\033[0m"))

if __name__ == "__main__":
    main()
