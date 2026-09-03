# GROK.md — wcdma-decoder-v2 session handoff

Yeni oturumda **önce bu dosyayı oku**. Asıl iş burası: `/home/redeye/icc/wcdma-decoder-v2`.
Orijinal `icc/wcdma-decoder` ve `icc/scripts` **dokunulmaz** (kullanıcı istemedikçe bugfix port etme).

GitHub: https://github.com/AkalinKutay/wcdma-decoder-v2  
Local branch `master` → remote `main`. Push: `git push origin master:main` (token chat’e yazdırma).

Tarih: 2026-09-03.

Context **~500k’ya yaklaşınca** yeni oturuma geç. Önce bu dosyayı güncelle (`git status`, son canlı koşu), commit et, sonra alttaki **New session prompt** bloğunu yeni sohbete yapıştır.

---

## Ne bu proje

Pasif RX WCDMA FDD BCH/SIB decoder. Python/NumPy. USRP B210. TX yok, IMSI yok, user-plane yok.

Akış: IQ → P-SCH / S-SCH / CPICH → P-CCPCH → Viterbi R=1/2 K=9 → CRC16 → ASN.1 UPER TS 25.331.

CLI terminal-first (`scan_2g.py` gibi). Aşırı orkestrasyon / kısa capture / SIB11 beam-search kapatma / uydurma komşu **yasak**.

---

## Donanım / ortam

- İki lab B210: Band 1 serial **2511050**, Band 8 **2505103**, anten **RX2**.
- v2 seri kilitlemez: ilk boş USRP (`--serial` opsiyonel).
- `UHD_IMAGES_DIR=/usr/share/uhd/4.10.0/images`
- Python 3.10, `python3-uhd`, numpy, scipy, asn1tools.
- Kurulum: `./setup.sh` sonra `./run-scan.sh --check`
- LO **3GPP**: `F_DL = N/5 + FDL_Offset`. Eski Lime 2.4 MHz low-IF yok. Mix 0 Hz.
- Capture: 15.36 Msps → Kaiser-1025 LPF 3.0 MHz → decim 2 → **7.68 Msps**. Analog BW tek-kanalda 8 MHz.
- Gain default **40**. Süre default **3 s** (SIB11 için kısaltma).
- ASN pickle: `~/.cache/wcdma-sib-scan/`

---

## Komutlar

```bash
cd /home/redeye/icc/wcdma-decoder-v2
./run-scan.sh --check
./run-scan.sh --band 8 --uarfcn 2997          # bilinen UARFCN
./run-scan.sh --band 1                        # TR B1 listesi
./run-scan.sh --iq --band 8 --uarfcn 2938     # damgalı IQ arşiv
./run-scan.sh --detail --band 8 --uarfcn 2938 # renkli tablolar
./run-scan.sh --band-scan 8                   # tüm Bant 8 süpür + decode
```

| Bayrak | Anlam |
|--------|--------|
| `--band N` | 1 2 4 5 6 8 19. Tek başına 1 veya 8 → TR listesi. Diğer bant `--uarfcn` ister. |
| `--uarfcn` | DL UARFCN listesi |
| `--band-scan N` | Tüm bandı süpür, bilinmeyen UARFCN, decode. Hedef **15–20 s/bant**. TR liste **kullanmaz**. |
| `--iq` | `captures/<stamp>_uarfcn_<N>.cfile` sakla. Yoksa `captures/uarfcn_<N>.cfile` üzerine yaz. |
| `--detail` | NAS / reselection / kanal / PHY tablosu. GSM tablosunda **bant kolonu yok**. |
| `-g` / `--gain` | default 40 |
| `--duration` | default 3 |

Sonuç: `results/YYYYMMDD-HHMMSS.{json,csv,log}`

---

## Bant tablosu (3GPP)

`wcdma_bands.py`. `F_DL = N/5 + offset`.

| Band | MHz | UARFCN | Not |
|------|-----|--------|-----|
| 1 | 2100 | 10562–10838 | TR liste var |
| 2 | 1900 | 9662–9938 | `--uarfcn` şart |
| 4 | 1700 | 1537–1738 | duplex 400 MHz (ΔN=225 değil) |
| 5 | 850 | 4357–4458 | B6/B19 extra örtüşme → `--band` |
| 6 | 800 | 4387–4413 | |
| 8 | 900 | 2937–3088 | TR liste var |
| 19 | 850 | 712–763 | |

TR varsayılan:

- Turkcell: 10563 10588 10613 10638 10813 10838 2997 3018
- Vodafone: 10663 10688 10713
- Türk Telekom: 10738 10763 10788 2938 2963

TEMS altın 10813 SC **483**: CID 139474100 RNC 2128; inter 10838/2997; LTE 100 550 1651 2850 6400.

---

## Kritik parser bilgisi (kaybetme)

Vodafone 10813/2997 **sade** BCH: `completeSIB-List`, `firstSegment` (222 bit), `subsequent`, `lastSegmentShort`.

Türk Telekom 2938/2963 **sıkıştırılmış** CHOICE:

- `completeAndFirst`, `lastAndFirst` (+ ASN’de `lastAndComplete`, `lastAndCompleteAndFirst`)
- leftover first = `FirstSegmentShort` (1–214 bit), 222-bit First değil
- SIB19 ve SIB11bis ikisi de BCH `extensionType` — SFN ile ayır, tek kovaya yapıştırma
- SIB3 CID nested complete list’te; SIB12 complete olarak da gelir

Assembler: `wcdma_si_assembler.py` `_iter_payload_parts`. Fixture: `tests/fixtures/uarfcn_2938_sc144.bch.json` (CID 22871005, RNC 348, PLMN 28603).

1-bit UARFCN slip filtresi: listed DL veya listed±25 Hamming-1. **serving±25 çapa değil** (3018’i 2997+25=3022’den yanlış düşürür).

5 MHz komşu sızıntısı: aynı PSC ±25 → `status=adjacent`. 10788 vs 10813. 3GPP LO sonrası lab’da 10788 boş.

BCH complete = UPER decode başarısı, segment varlığı değil.

---

## `--band-scan` (canlı doğrulama eksik)

Amaç: kullanıcı bandı seçer, **bilinmeyen UARFCN** dahil tek komutta süpürülür, adaylar hemen decode. Bütçe 15–20 s.

Dosyalar: `wcdma_band_scan.py`, `wcdma_bands.survey_hops`, `uhd_capture.capture_wideband`, `scan_3g.run_band_scan`.

Akış:

1. B210 analog **max 56 MHz**. **61.44 Msps kullanma** (UHD uyarısı: rate > analog filter). Rate **15.36 veya 30.72**. Analog BW = rate.
2. Bant 8 (~30.2 MHz) **1 hop** 30.72 Msps LO 942.5 MHz. Bant 1 (~55 MHz) **2 hop** 30.72, 4 MHz overlap.
3. 3 s wideband IQ (RAM; `--iq` yoksa 1 GB cfile yazma).
4. PSD 200 kHz raster, ±1.8 MHz enerji, 20. yüzdelik gürültü + 4 dB, min ayrım 20 UARFCN (~4 MHz).
5. Eşik boşsa en güçlü 5 MHz aralıklı tepeler yine P-SCH’e gider.
6. Aday: 0.25 s DDC → cell search. Ec/Io ≥ −22 ise tam DDC 7.68 Msps → mevcut BCH/SIB.

### Son canlı koşu (2026-09-03 10:08, lab)

```
./run-scan.sh --band-scan 8
wideband LO 942.50  30.72 Msps  samples=92160000  rms=0.0329  wall=3.10s
candidates (none)
cells=0  ~6s
```

IQ **boş değildi** (rms 0.033). Eski PSD medyan+8 dB, WCDMA 5 MHz platosu eşiği yutmuş, P-SCH hiç çalışmamış. Eşik/fallback **kodda düzeltildi, canlı tekrar denenmedi.**

Yeni oturumda ilk iş (PSD eşiği kodda düzeltildi, canlı tekrar yok):

```bash
./run-scan.sh --band-scan 8
```

Stderr’de `rms=` ve `PSD top 2997 +X dB …` görünmeli, sonra probe/decode. Lab’da B8 yoksa PSD düz + P-SCH zayıf → 0 hücre doğru. Dışarıda 2938/2963/2997/3018 beklenir.

Hâlâ sorun olursa: DDC karışım işareti (`target-lo`), `recv_num_samps` rate, kenar UARFCN (2938 LO’dan −14.9 MHz, Nyquist ±15.36).

---

## `--detail` (commit `adbd34d`, push yok)

`print_detail`: renkli kutu tablo, Türkçe etiket + 3GPP adı. GSM komşu: ARFCN / BSIC / liste no — **bant yok**. TTY renk; `NO_COLOR=1` kapatır.

---

## IQ politikası

- `--iq` yok: `captures/uarfcn_<N>.cfile` üzerine yaz.
- `--iq` var: damgalı, silinmez. ~180 MB / 3 s / UARFCN.
- `captures/` ve `results/` gitignore.
- Fixture 38 KB BCH JSON testte durur; ham IQ GitHub’a gitmez.

---

## Test

```bash
python3 tests/test_wcdma_bands.py
python3 tests/test_viterbi_numpy.py
python3 tests/test_wcdma_generators.py
python3 tests/test_combined_payloads.py
```

---

## Dosya haritası

| Dosya | Rol |
|-------|-----|
| `scan_3g.py` | CLI, print, `--detail`, `--band-scan` |
| `run-scan.sh` | `UHD_IMAGES_DIR` + `scan_3g.py` |
| `setup.sh` | apt uhd/numpy/scipy + pip asn1tools |
| `uhd_capture.py` | tek UARFCN 15.36→7.68; `capture_wideband` |
| `wcdma_bands.py` | UARFCN, hop planı |
| `wcdma_band_scan.py` | PSD aday, DDC 7.68 |
| `wcdma_sync.py` | P-SCH/S-SCH/CPICH |
| `wcdma_si_assembler.py` | SIB birleştirme + TT combined payload |
| `wcdma_scan_report.py` | UPER extract + `extract_sib_detail` |
| `tests/fixtures/uarfcn_2938_sc144.bch.json` | TT parse regresyon |

---

## Bilinçli kısıtlar

- `SystemInformation2-BCH` okunmuyor.
- TT 2938/2963 SIB19 (LTE listesi) kısa first+last bazen UPER fail.
- Aynı taşıyıcı 2. PSC: `--detail` CPICH extra; BCH en güçlü PSC.
- Band 4 extra 1700 MHz `--band-scan` kapsamı **ana DL** (2112–2152). Extra ayrı LO.
- Token chat’e yapıştırma. PAT sohbette göründüyse revoke.

---

## Yeni oturum checklist

1. Bu GROK.md + `git status` / `git log -3`
2. Canlı doğrula: `./run-scan.sh --band-scan 8` (mümkünse `--band-scan 1`)
3. 20 s bütçe / yanlış UARFCN / DDC işareti → PSD vs P-SCH log
4. Kullanıcı “band tarama” derse TR liste değil `--band-scan`
5. Context 500k → GROK.md güncelle, commit, aşağıdaki prompt ile new session

Kullanıcı dili Türkçe; stdout kısa tarama İngilizce (UARFCN/PSC), `--detail` Türkçe etiket.

---

## Context 500k — new session geçişi

1. `git status` / son canlı koşuyu bu dosyanın ilgili bölümüne yaz.
2. Commit (kullanıcı isterse push).
3. Yeni sohbete **yalnızca** aşağıdaki kutuyu yapıştır (eski transcript taşıma).

### New session prompt

```
Read /home/redeye/icc/wcdma-decoder-v2/GROK.md first and follow it.

Workspace: /home/redeye/icc
Active project: /home/redeye/icc/wcdma-decoder-v2  (GitHub AkalinKutay/wcdma-decoder-v2, local master → origin/main)
Do not modify icc/wcdma-decoder or icc/scripts unless I ask to port a specific fix.

Passive RX WCDMA FDD BCH/SIB decoder, USRP B210, first free radio, no serial lock.
CLI: ./run-scan.sh  (scan_3g.py). Default gain 40, duration 3s. Do not shorten capture; do not disable SIB11 beam-search; do not invent SIB neighbors.

Immediate work: live-verify ./run-scan.sh --band-scan 8
Last lab run 2026-09-03 10:08 had IQ (rms 0.0329) but PSD returned no candidates; threshold/fallback was fixed in code and not re-run. Expect stderr "PSD top …" then P-SCH/decode. Band 8 outdoor should see 2938/2963/2997/3018 if on air.

Rules: terminal-first; no overly orchestrated scripts; --iq keeps timestamped IQ else overwrite uarfcn_N.cfile; --detail colored tables (no GSM band column); never 61.44 Msps (AD9361 analog max 56 MHz, use 15.36/30.72).

When context approaches 500k: update GROK.md (status + last live result), commit, give me this same new-session prompt.
```
