# WCDMA neighbour decoder

Passive WCDMA (UMTS FDD) broadcast decoder. IQ from a USRP B210, then cell
search, BCH Viterbi, and ASN.1 SIB11/SIB19. No user-plane, no IMSI.

## Install (Debian/Ubuntu)

```bash
sudo apt install python3-uhd python3-numpy python3-scipy
python3 -m pip install --user asn1tools
```

The B210 needs USB 3 and UHD FPGA images (`usrp_b200_fw.hex`). The scanner
sets `UHD_IMAGES_DIR` if it finds `/usr/share/uhd/*/images`.

Add your user to the `usrp` group (or install UHD udev rules) so the radio
opens without root.

## Run

```bash
./run-scan.sh --check
./run-scan.sh --uarfcn 10813
./run-scan.sh
```

Default UARFCNs: `10813 10838 2997 2938 2963 3018` (Band 1 + Band 8).

The first **free** USRP is used (no serial lock). Optional: `--serial <id>`.
Stdout is one cell block per UARFCN (gr-gsm style). Files:

`results/YYYYMMDD-HHMMSS.{json,csv,log}`

Double-click `run-scan.sh` (mark executable) or `scan-3g.desktop` (Allow Launching).

## Layout

| File | Role |
|------|------|
| `scan_3g.py` | CLI |
| `uhd_capture.py` | B210 IQ (python3-uhd, zero-IF) |
| `wcdma_cellsearch.py` | P-SCH / S-SCH / CPICH |
| `wcdma_bch_decode.py` | P-CCPCH, Viterbi, CRC16 |
| `wcdma_si_assembler.py` | SIB reassembly + SIB11 repair |
| `wcdma_rrc_asn1/` | 3GPP TS 25.331 (not GPLv3; see NOTICE) |

## License

GPLv3. ASN.1 files in `wcdma_rrc_asn1/` are 3GPP/ETSI and **excluded** from GPLv3
(see `NOTICE`).
