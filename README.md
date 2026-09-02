# WCDMA SIB scanner

Passive WCDMA (UMTS FDD) broadcast decoder. A USRP B210 captures IQ; the
scanner does cell search, BCH Viterbi, and ASN.1 SIB decode. No user-plane,
no IMSI, no transmit.

[English](#english) | [Türkçe](#türkçe)

---

## English

### Hardware

- USRP B210 on **USB 3**
- Python **3.10+**
- RX antenna: `RX2` if present, else `TX/RX`
- First free UHD device (no serial lock). Optional: `--serial`

### Install (Debian / Ubuntu)

```bash
./setup.sh
./run-scan.sh --check
```

`setup.sh` installs `python3-uhd`, `uhd-host`, NumPy, SciPy, and `asn1tools`.
If it adds you to group `usrp`, log out and back in.

### Run

```bash
./run-scan.sh
./run-scan.sh --band 1 --uarfcn 10813
./run-scan.sh --band 8 --uarfcn 2997 3018
./run-scan.sh --iq --band 8 --uarfcn 2938 2963
./run-scan.sh -g 0 --uarfcn 10813
```

No flags: Turkey defaults `10813 10838 2997 2938 2963 3018`.

`--band 1` or `--band 8` alone uses the known Turkey list for that band.
Other bands need `--uarfcn`.

Stdout is one cell block per UARFCN (gr-gsm style):

```
UARFCN 10813  2162.6 MHz  PSC 483  Ec/No  -4.3  RSCP  -63.4  B1  Turkcell
    PLMN 28601  CID 139474100  RNC 2128
    WCDMA 2997: 182 220 270 ...
    WCDMA 10838: 483 151 73 ...
    LTE 100 550 1651 2850 6400
    GSM 59/21 57/21 58/36 ...
```

### Options

| Flag | Default | |
|------|---------|-|
| `--band N` | | FDD 1, 2, 4, 5, 6, 8, 19 |
| `--uarfcn …` | | Downlink UARFCNs |
| `-g` / `--gain` | 40 | RX gain dB |
| `--duration` | 3 | Capture seconds |
| `--iq` | off | Keep timestamped IQ (see below) |
| `--serial` | first free | UHD serial |
| `--skip-sib` | off | Cell search only |
| `--check` | | Deps + radio, then exit |
| `-o` | `results/` | JSON / CSV / log dir |

### IQ files

Without `--iq`, each UARFCN overwrites `captures/uarfcn_<N>.cfile`.

With `--iq`, the capture is kept:

`captures/<YYYYMMDD-HHMMSS>_uarfcn_<N>.cfile`

~180 MB per UARFCN at 3 s. Use `--iq` when you need the raw capture later.

Scan summaries are always timestamped:

`results/<YYYYMMDD-HHMMSS>.{json,csv,log}`

### Bands (3GPP TS 25.101)

`F_DL = N/5 + FDL_Offset`

| Band | Name | DL UARFCN |
|------|------|-----------|
| 1 | 2100 | 10562–10838 |
| 2 | 1900 | 9662–9938 |
| 4 | 1700 | 1537–1738 |
| 5 | 850 | 4357–4458 |
| 6 | 800 | 4387–4413 |
| 8 | 900 | 2937–3088 |
| 19 | 850 | 712–763 |

Ambiguous extra UARFCNs (B5/B6/B19 overlap) need `--band`.

Turkey lists (`--band 1` or `8` without `--uarfcn`):

- Turkcell: 10563 10588 10613 10638 10813 10838 2997 3018
- Vodafone TR: 10663 10688 10713
- Türk Telekom: 10738 10763 10788 2938 2963

### Tests

```bash
python3 tests/test_wcdma_bands.py
python3 tests/test_viterbi_numpy.py
python3 tests/test_wcdma_generators.py
python3 tests/test_combined_payloads.py
```

### Layout

| File | Role |
|------|------|
| `setup.sh` | apt + pip deps |
| `run-scan.sh` | CLI wrapper (`UHD_IMAGES_DIR`) |
| `scan_3g.py` | Capture → search → BCH → SIB |
| `uhd_capture.py` | B210 IQ (zero-IF, 15.36→7.68 Msps) |
| `wcdma_bands.py` | UARFCN / band table |
| `wcdma_cellsearch.py` | P-SCH / S-SCH / CPICH |
| `wcdma_bch_decode.py` | P-CCPCH, Viterbi, CRC16 |
| `wcdma_si_assembler.py` | SIB reassembly |
| `wcdma_rrc_asn1/` | 3GPP TS 25.331 (not GPLv3; see NOTICE) |

ASN.1 compile cache: `~/.cache/wcdma-sib-scan/` (built on first scan).

### License

GPLv3. ASN.1 files in `wcdma_rrc_asn1/` are 3GPP/ETSI and **excluded** from
GPLv3 (see `NOTICE`).

---

## Türkçe

Pasif WCDMA (UMTS FDD) yayın çözücü. B210 IQ alır; hücre arama, BCH Viterbi
ve ASN.1 SIB okur. Kullanıcı trafiği / IMSI yok, verici yok.

```bash
./setup.sh
./run-scan.sh --check
./run-scan.sh --band 8 --uarfcn 2997
./run-scan.sh --iq --band 8 --uarfcn 2938 2963
```

`--iq` yoksa `captures/uarfcn_<N>.cfile` üzerine yazar. `--iq` varsa damgalı
kopya kalır (~180 MB / UARFCN). Sonuçlar her zaman `results/` altında damgalı.

`setup.sh` seni `usrp` grubuna eklerse oturumu kapatıp aç.
