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

import argparse
import os
import json
import numpy as np
import wcdma_sync

def search_capture(input_path, iq_data=None, plot=False, plot_out="cell_search_diagnostics.png", max_cells=1):
    """Run cell search on a .cfile (or preloaded IQ) and write <input>.results.json."""
    print(f"Karmaşık IQ verisi yükleniyor: {input_path}")
    meta_path = input_path + ".json"
    uarfcn = "Bilinmiyor"
    freq_mhz = 0.0
    sample_rate = 7.68e6
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r") as f:
                meta = json.load(f)
            uarfcn = meta.get("uarfcn", "Bilinmiyor")
            freq_mhz = meta.get("frequency_mhz", 0.0)
            sample_rate = meta.get("sample_rate_hz", 7.68e6)
            print("  -> Metadata başarıyla yüklendi:")
            print(f"     * UARFCN: {uarfcn}")
            print(f"     * Merkez Frekans: {freq_mhz} MHz")
            print(f"     * Sample Rate: {sample_rate / 1e6:.3f} Msps")
        except Exception as e:
            print(f"  -> Uyarı: Metadata dosyası okunamadı, varsayılanlar kullanılacak. Hata: {e}")

    if iq_data is None:
        iq_data = np.fromfile(input_path, dtype=np.complex64)
    print(f"  -> Toplam karmaşık örnek sayısı: {len(iq_data)}")
    if len(iq_data) < 76800:
        raise ValueError("IQ verisi en az 10 ms (76800 sample) uzunluğunda olmalıdır!")

    if max_cells and int(max_cells) > 1:
        found = wcdma_sync.perform_multi_cell_search(
            iq_data=iq_data,
            sample_rate=sample_rate,
            max_cells=int(max_cells),
        )
        if not found:
            result = wcdma_sync.perform_cell_search(
                iq_data=iq_data,
                sample_rate=sample_rate,
                plot_diagnostics=plot,
                plot_output_path=plot_out,
            )
            found = [result]
    else:
        result = wcdma_sync.perform_cell_search(
            iq_data=iq_data,
            sample_rate=sample_rate,
            plot_diagnostics=plot,
            plot_output_path=plot_out,
        )
        found = [result]
    cells = []
    for result in found:
        cells.append({
            "scrambling_code": result["scrambling_code"],
            "code_group": result["code_group"],
            "slot_timing_sample": result["slot_timing_sample"],
            "frame_timing_sample": result["frame_timing_sample"],
            "cpich_rscp_dbm": result["cpich_rscp_dbm"],
            "cpich_ecno_db": result["cpich_ecno_db"],
            "frequency_correction_hz": result["frequency_correction_hz"],
        })
    output_json = {
        "uarfcn": uarfcn,
        "frequency_mhz": freq_mhz,
        "cells": cells,
    }
    print("\nHücre Arama Analiz Sonuçları (JSON Formatında):")
    print(json.dumps(output_json, indent=2))
    out_json_path = input_path + ".results.json"
    with open(out_json_path, "w") as f:
        json.dump(output_json, f, indent=2)
    print(f"\nSonuçlar başarıyla kaydedildi: {out_json_path}")
    return output_json


def main():
    parser = argparse.ArgumentParser(description="WCDMA Offline Cell Search Orchestrator")
    parser.add_argument("--input", type=str, required=True, help="Ham IQ .cfile dosya yolu (complex64 formatında)")
    parser.add_argument("--plot", action="store_true", help="P-SCH slot peak tanı grafiğini kaydet")
    parser.add_argument("--plot-out", type=str, default="cell_search_diagnostics.png", help="Hata ayıklama grafik çıkış dosyası")
    args = parser.parse_args()
    if not os.path.exists(args.input):
        print(f"Hata: Girdi dosyası bulunamadı: {args.input}")
        return
    try:
        search_capture(args.input, plot=args.plot, plot_out=args.plot_out)
    except Exception as e:
        print(f"\nHata: Hücre arama işlemi başarısız oldu! Sistem Hatası: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
