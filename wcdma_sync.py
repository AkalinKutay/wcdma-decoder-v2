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

import pickle
from pathlib import Path

import numpy as np
import scipy.signal

# WCDMA RRC occupied ~4.68 MHz. Adjacent UARFCN is 5 MHz away; an 8 MHz analog
# front-end plus 7.68 Msps lets that neighbour's lower shoulder into baseband
# (10788 decoded 10813's BCH: same CID/PSC, RSCP ~55 dB down).
_CHANNEL_TAPS = None


def wcdma_channel_taps(sample_rate=7.68e6, cutoff_hz=3.2e6, ntaps=129):
    global _CHANNEL_TAPS
    key = (float(sample_rate), float(cutoff_hz), int(ntaps))
    if _CHANNEL_TAPS is None or _CHANNEL_TAPS[0] != key:
        taps = scipy.signal.firwin(ntaps, cutoff_hz, fs=sample_rate, pass_zero="lowpass")
        _CHANNEL_TAPS = (key, taps.astype(np.float32))
    return _CHANNEL_TAPS[1]


def apply_channel_filter(iq_data, sample_rate=7.68e6):
    """Low-pass around DC so a 5 MHz-offset WCDMA neighbour does not decode as serving."""
    taps = wcdma_channel_taps(sample_rate)
    x = np.asarray(iq_data, dtype=np.complex64)
    y = scipy.signal.fftconvolve(x, taps, mode="same")
    return y.astype(np.complex64)

# WCDMA TS 25.213 Table 4: Allocation of SSCs for secondary SCH
WCDMA_SSC_ALLOCATION = [
    [1, 1, 2, 8, 9, 10, 15, 8, 10, 16, 2, 7, 15, 7, 16],  # Group 0
    [1, 1, 5, 16, 7, 3, 14, 16, 3, 10, 5, 12, 14, 12, 10],  # Group 1
    [1, 2, 1, 15, 5, 5, 12, 16, 6, 11, 2, 16, 11, 15, 12],  # Group 2
    [1, 2, 3, 1, 8, 6, 5, 2, 5, 8, 4, 4, 6, 3, 7],  # Group 3
    [1, 2, 16, 6, 6, 11, 15, 5, 12, 1, 15, 12, 16, 11, 2],  # Group 4
    [1, 3, 4, 7, 4, 1, 5, 5, 3, 6, 2, 8, 7, 6, 8],  # Group 5
    [1, 4, 11, 3, 4, 10, 9, 2, 11, 2, 10, 12, 12, 9, 3],  # Group 6
    [1, 5, 6, 6, 14, 9, 10, 2, 13, 9, 2, 5, 14, 1, 13],  # Group 7
    [1, 6, 10, 10, 4, 11, 7, 13, 16, 11, 13, 6, 4, 1, 16],  # Group 8
    [1, 6, 13, 2, 14, 2, 6, 5, 5, 13, 10, 9, 1, 14, 10],  # Group 9
    [1, 7, 8, 5, 7, 2, 4, 3, 8, 3, 2, 6, 6, 4, 5],  # Group 10
    [1, 7, 10, 9, 16, 7, 9, 15, 1, 8, 16, 8, 15, 2, 2],  # Group 11
    [1, 8, 12, 9, 9, 4, 13, 16, 5, 1, 13, 5, 12, 4, 8],  # Group 12
    [1, 8, 14, 10, 14, 1, 15, 15, 8, 5, 11, 4, 10, 5, 4],  # Group 13
    [1, 9, 2, 15, 15, 16, 10, 7, 8, 1, 10, 8, 2, 16, 9],  # Group 14
    [1, 9, 15, 6, 16, 2, 13, 14, 10, 11, 7, 4, 5, 12, 3],  # Group 15
    [1, 10, 9, 11, 15, 7, 6, 4, 16, 5, 2, 12, 13, 3, 14],  # Group 16
    [1, 11, 14, 4, 13, 2, 9, 10, 12, 16, 8, 5, 3, 15, 6],  # Group 17
    [1, 12, 12, 13, 14, 7, 2, 8, 14, 2, 1, 13, 11, 8, 11],  # Group 18
    [1, 12, 15, 5, 4, 14, 3, 16, 7, 8, 6, 2, 10, 11, 13],  # Group 19
    [1, 15, 4, 3, 7, 6, 10, 13, 12, 5, 14, 16, 8, 2, 11],  # Group 20
    [1, 16, 3, 12, 11, 9, 13, 5, 8, 2, 14, 7, 4, 10, 15],  # Group 21
    [2, 2, 5, 10, 16, 11, 3, 10, 11, 8, 5, 13, 3, 13, 8],  # Group 22
    [2, 2, 12, 3, 15, 5, 8, 3, 5, 14, 12, 9, 8, 9, 14],  # Group 23
    [2, 3, 6, 16, 12, 16, 3, 13, 13, 6, 7, 9, 2, 12, 7],  # Group 24
    [2, 3, 8, 2, 9, 15, 14, 3, 14, 9, 5, 5, 15, 8, 12],  # Group 25
    [2, 4, 7, 9, 5, 4, 9, 11, 2, 14, 5, 14, 11, 16, 16],  # Group 26
    [2, 4, 13, 12, 12, 7, 15, 10, 5, 2, 15, 5, 13, 7, 4],  # Group 27
    [2, 5, 9, 9, 3, 12, 8, 14, 15, 12, 14, 5, 3, 2, 15],  # Group 28
    [2, 5, 11, 7, 2, 11, 9, 4, 16, 7, 16, 9, 14, 14, 4],  # Group 29
    [2, 6, 2, 13, 3, 3, 12, 9, 7, 16, 6, 9, 16, 13, 12],  # Group 30
    [2, 6, 9, 7, 7, 16, 13, 3, 12, 2, 13, 12, 9, 16, 6],  # Group 31
    [2, 7, 12, 15, 2, 12, 4, 10, 13, 15, 13, 4, 5, 5, 10],  # Group 32
    [2, 7, 14, 16, 5, 9, 2, 9, 16, 11, 11, 5, 7, 4, 14],  # Group 33
    [2, 8, 5, 12, 5, 2, 14, 14, 8, 15, 3, 9, 12, 15, 9],  # Group 34
    [2, 9, 13, 4, 2, 13, 8, 11, 6, 4, 6, 8, 15, 15, 11],  # Group 35
    [2, 10, 3, 2, 13, 16, 8, 10, 8, 13, 11, 11, 16, 3, 5],  # Group 36
    [2, 11, 15, 3, 11, 6, 14, 10, 15, 10, 6, 7, 7, 14, 3],  # Group 37
    [2, 16, 4, 5, 16, 14, 7, 11, 4, 11, 14, 9, 9, 7, 5],  # Group 38
    [3, 3, 4, 6, 11, 12, 13, 6, 12, 14, 4, 5, 13, 5, 14],  # Group 39
    [3, 3, 6, 5, 16, 9, 15, 5, 9, 10, 6, 4, 15, 4, 10],  # Group 40
    [3, 4, 5, 14, 4, 6, 12, 13, 5, 13, 6, 11, 11, 12, 14],  # Group 41
    [3, 4, 9, 16, 10, 4, 16, 15, 3, 5, 10, 5, 15, 6, 6],  # Group 42
    [3, 4, 16, 10, 5, 10, 4, 9, 9, 16, 15, 6, 3, 5, 15],  # Group 43
    [3, 5, 12, 11, 14, 5, 11, 13, 3, 6, 14, 6, 13, 4, 4],  # Group 44
    [3, 6, 4, 10, 6, 5, 9, 15, 4, 15, 5, 16, 16, 9, 10],  # Group 45
    [3, 7, 8, 8, 16, 11, 12, 4, 15, 11, 4, 7, 16, 3, 15],  # Group 46
    [3, 7, 16, 11, 4, 15, 3, 15, 11, 12, 12, 4, 7, 8, 16],  # Group 47
    [3, 8, 7, 15, 4, 8, 15, 12, 3, 16, 4, 16, 12, 11, 11],  # Group 48
    [3, 8, 15, 4, 16, 4, 8, 7, 7, 15, 12, 11, 3, 16, 12],  # Group 49
    [3, 10, 10, 15, 16, 5, 4, 6, 16, 4, 3, 15, 9, 6, 9],  # Group 50
    [3, 13, 11, 5, 4, 12, 4, 11, 6, 6, 5, 3, 14, 13, 12],  # Group 51
    [3, 14, 7, 9, 14, 10, 13, 8, 7, 8, 10, 4, 4, 13, 9],  # Group 52
    [5, 5, 8, 14, 16, 13, 6, 14, 13, 7, 8, 15, 6, 15, 7],  # Group 53
    [5, 6, 11, 7, 10, 8, 5, 8, 7, 12, 12, 10, 6, 9, 11],  # Group 54
    [5, 6, 13, 8, 13, 5, 7, 7, 6, 16, 14, 15, 8, 16, 15],  # Group 55
    [5, 7, 9, 10, 7, 11, 6, 12, 9, 12, 11, 8, 8, 6, 10],  # Group 56
    [5, 9, 6, 8, 10, 9, 8, 12, 5, 11, 10, 11, 12, 7, 7],  # Group 57
    [5, 10, 10, 12, 8, 11, 9, 7, 8, 9, 5, 12, 6, 7, 6],  # Group 58
    [5, 10, 12, 6, 5, 12, 8, 9, 7, 6, 7, 8, 11, 11, 9],  # Group 59
    [5, 13, 15, 15, 14, 8, 6, 7, 16, 8, 7, 13, 14, 5, 16],  # Group 60
    [9, 10, 13, 10, 11, 15, 15, 9, 16, 12, 14, 13, 16, 14, 11],  # Group 61
    [9, 11, 12, 15, 12, 9, 13, 13, 11, 14, 10, 16, 15, 14, 16],  # Group 62
    [9, 12, 10, 15, 13, 14, 9, 14, 15, 11, 11, 13, 12, 16, 10],  # Group 63
]

def generate_psc():
    """
    PSC sequence generation exactly as per 3GPP TS 25.213 Sec 5.2.3.1.
    Returns 256 complex elements.
    """
    a = np.array([1, 1, 1, 1, 1, 1, -1, -1, 1, -1, 1, -1, 1, -1, -1, 1], dtype=np.complex64)
    # Golay complementary sequence modulation
    psc_chips = np.zeros(256, dtype=np.complex64)
    mod = np.array([1, 1, 1, -1, -1, 1, -1, -1, 1, 1, 1, -1, 1, -1, 1, 1], dtype=np.complex64)
    for i in range(16):
        psc_chips[i*16 : (i+1)*16] = mod[i] * a
    
    C_psc = (1 + 1j) * psc_chips
    return C_psc

def generate_hadamard_matrix(k):
    """
    Recursive generation of Hadamard matrix H_k. H_8 has size 256x256.
    """
    if k == 0:
        return np.array([[1]], dtype=np.int8)
    h_prev = generate_hadamard_matrix(k - 1)
    return np.block([[h_prev, h_prev], [h_prev, -h_prev]])

def generate_ssc(k):
    """
    SSC sequence generation exactly as per 3GPP TS 25.213 Sec 5.2.3.2.
    k represents the SSC index (1 to 16).
    Returns 256 complex elements.
    """
    # Initialize basic sequences a and b
    a = np.array([1, 1, 1, 1, 1, 1, -1, -1, 1, -1, 1, -1, 1, -1, -1, 1], dtype=np.int8)
    b = np.zeros(16, dtype=np.int8)
    b[:8] = a[:8]
    b[8:] = -a[8:]
    
    # Generate modulating sequence z of length 256
    z_blocks = np.array([1, 1, 1, -1, 1, 1, -1, -1, 1, -1, 1, -1, -1, -1, -1, -1], dtype=np.int8)
    z = np.zeros(256, dtype=np.int8)
    for i in range(16):
        z[i*16 : (i+1)*16] = z_blocks[i] * b
        
    # Get Hadamard sequence H_m where m = 16 * (k - 1)
    H_256 = generate_hadamard_matrix(8)
    m = 16 * (k - 1)
    h_m = H_256[m]
    
    ssc_chips = h_m * z
    C_ssc = (1 + 1j) * ssc_chips
    return C_ssc

def generate_gold_sequences(length=262143):
    """
    18-bit LFSR Gold Code sequence generator.
    x and y registers run as per polynomials in TS 25.213 Sec 5.2.2.
    """
    x = np.zeros(length, dtype=np.uint8)
    y = np.zeros(length, dtype=np.uint8)
    
    # Initial state
    x[0] = 1
    y[:18] = 1
    
    # Run LFSR for x: x(i+18) = x(i+7) + x(i) mod 2
    for i in range(length - 18):
        x[i+18] = x[i+7] ^ x[i]
        
    # Run LFSR for y: y(i+18) = y(i+10) + y(i+7) + y(i+5) + y(i) mod 2
    for i in range(length - 18):
        y[i+18] = y[i+10] ^ y[i+7] ^ y[i+5] ^ y[i]
        
    return x, y


def _load_gold_registers():
    cache = Path.home() / ".cache" / "wcdma-sib-scan" / "gold-262143.pkl"
    if cache.exists():
        try:
            with open(cache, "rb") as fh:
                x, y = pickle.load(fh)
            if len(x) == 262143 and len(y) == 262143:
                return x, y
        except Exception:
            pass
    x, y = generate_gold_sequences()
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        with open(cache, "wb") as fh:
            pickle.dump((x, y), fh, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        pass
    return x, y


# Global pre-generated registers to speed up execution
X_REG, Y_REG = _load_gold_registers()

def generate_scrambling_code(sc_num, length=38400):
    """
    Generates complex scrambling code sequence of given length (default 10ms frame = 38400 chips).
    Primary Scrambling Code index is sc_num (0 to 511), using Gold code index n = 16 * sc_num.
    """
    n = 16 * sc_num
    
    # Calculate Z_n(i) = x(i+n) ^ y(i)
    # Modulo operation is period 262143
    i = np.arange(length)
    idx_x = (i + n) % 262143
    idx_y = i % 262143
    
    val_real = X_REG[idx_x] ^ Y_REG[idx_y]
    Z_n_real = 1 - 2 * val_real.astype(np.int8)
    
    idx_x_imag = (i + n + 131072) % 262143
    idx_y_imag = (i + 131072) % 262143
    val_imag = X_REG[idx_x_imag] ^ Y_REG[idx_y_imag]
    Z_n_imag = 1 - 2 * val_imag.astype(np.int8)
    
    S_dl = Z_n_real + 1j * Z_n_imag
    return S_dl

# S-SCH / CPICH only need a short window. P-SCH already uses 100 ms.
# 20 frames = 200 ms covers several SCH cycles; SIB decode still uses the full capture.
MAX_CELLSEARCH_FRAMES = 20
_SSC_CONJ = None


def _ssc_conj_templates():
    global _SSC_CONJ
    if _SSC_CONJ is None:
        _SSC_CONJ = np.stack(
            [np.repeat(generate_ssc(k), 2).conj() for k in range(1, 17)]
        )
    return _SSC_CONJ


def _coarse_psch(iq_data, sample_rate):
    """P-SCH slot sync + coarse CFO on at most 100 ms. Returns offset, freq, folded profile."""
    import scipy.signal

    psc_mf = np.repeat(generate_psc(), 2)[::-1].conj()
    freq_grid = np.arange(-20000, 21000, 2000)
    max_sync_samples = min(len(iq_data), 150 * 5120)
    iq_sync_part = iq_data[:max_sync_samples]
    t_sync = np.arange(max_sync_samples) / sample_rate
    best_peak = -1.0
    best_offset = 0
    best_freq = 0
    best_folded = None
    for f in freq_grid:
        rotated_iq = iq_sync_part * np.exp(-1j * 2 * np.pi * f * t_sync)
        c = scipy.signal.fftconvolve(rotated_iq, psc_mf, mode="valid")
        n_slots = len(c) // 5120
        if n_slots == 0:
            raise ValueError("IQ veri kümesi slot eşiğinden (5120 sample) daha kısa!")
        folded = np.abs(c[: n_slots * 5120]).reshape(n_slots, 5120).sum(axis=0)
        peak_val = float(folded.max())
        if peak_val > best_peak:
            best_peak = peak_val
            best_offset = int(folded.argmax())
            best_freq = f
            best_folded = folded
    return best_offset, best_freq, best_folded


def _rotate_search_window(iq_data, sample_rate, best_offset, best_freq):
    """Mix only the samples needed for S-SCH + one CPICH frame (indices stay absolute)."""
    n_avail = (len(iq_data) - best_offset) // 76800
    if n_avail <= 0:
        raise ValueError("IQ veri kümesi frame eşiğinden (76800 sample) daha kısa!")
    n_frames = min(MAX_CELLSEARCH_FRAMES, n_avail)
    win_end = min(len(iq_data), best_offset + n_frames * 76800 + 76800 + 32)
    t_win = np.arange(win_end) / sample_rate
    iq_rotated = iq_data[:win_end] * np.exp(
        -1j * 2 * np.pi * best_freq * t_win
    )
    return iq_rotated, n_frames


def _ssc_corr_matrix(iq_rotated, slot_offset, n_frames):
    """(15, 16) incoherent SSC correlation, stacked slots x templates."""
    ssc_conj = _ssc_conj_templates()
    acc = np.zeros((15, 16), dtype=np.float64)
    n = len(iq_rotated)
    for f_idx in range(n_frames):
        start = slot_offset + f_idx * 76800
        if start + 76800 > n:
            break
        segs = np.asarray(iq_rotated[start : start + 76800]).reshape(15, 5120)[:, :512]
        acc += np.abs(segs @ ssc_conj.T)
    return acc


def _match_code_group(ssc_corr_matrix):
    best_score = -1.0
    best_group = -1
    best_shift = -1
    for g in range(64):
        alloc = WCDMA_SSC_ALLOCATION[g]
        for shift in range(15):
            score = 0.0
            for slot_idx in range(15):
                ssc_idx = alloc[(slot_idx + shift) % 15] - 1
                score += ssc_corr_matrix[slot_idx, ssc_idx]
            if score > best_score:
                best_score = score
                best_group = g
                best_shift = shift
    return best_group, best_shift, best_score


def _cpich_search(iq_rotated, frame_boundary, sc_num):
    """Delay -32..32, vectorized 150-symbol CPICH despread. Same energy as per-symbol np.sum."""
    gold_conj = np.repeat(generate_scrambling_code(sc_num, length=38400), 2).conj()
    n = len(iq_rotated)
    lo = max(0, frame_boundary - 32)
    hi = min(n, frame_boundary + 32 + 76800)
    if hi - lo < 76800:
        return 0, None, -1.0
    base = np.ascontiguousarray(iq_rotated[lo:hi])
    n_delays = len(base) - 76800 + 1
    windows = np.lib.stride_tricks.sliding_window_view(base, 76800)[:n_delays]
    desc = windows * gold_conj
    symbols = desc.reshape(n_delays, 150, 512).sum(axis=2)
    energy = np.sum(np.abs(symbols) ** 2, axis=1)
    best_i = int(energy.argmax())
    best_delay = (lo + best_i) - frame_boundary
    return best_delay, symbols[best_i], float(energy[best_i])


def perform_cell_search(iq_data, sample_rate=7.68e6, plot_diagnostics=False, plot_output_path="cell_search_diagnostics.png"):
    """
    Executes WCDMA 3-Step Offline Cell Search Pipeline on complex64 IQ sample array.
    """
    iq_data = apply_channel_filter(iq_data, sample_rate)
    # 1. Step 1: P-SCH Slot Synchronization & Coarse Freq Correction
    print("[Adım 1/3] P-SCH Slot senkronizasyonu başlatılıyor...")
    best_offset, best_freq, best_folded = _coarse_psch(iq_data, sample_rate)
    print(f"  -> Slot sınır kayması: {best_offset} sample (5120 sample içinde)")
    print(f"  -> Coarse frekans kayması: {best_freq} Hz (düzeltildi)")

    iq_rotated, num_frames = _rotate_search_window(
        iq_data, sample_rate, best_offset, best_freq
    )

    # 2. Step 2: S-SCH Frame Synchronization & Code Group Detection
    print("[Adım 2/3] S-SCH Frame senkronizasyonu ve kod grubu araması başlatılıyor...")
    ssc_corr_matrix = _ssc_corr_matrix(iq_rotated, best_offset, num_frames)
    best_group, best_shift, _best_score = _match_code_group(ssc_corr_matrix)

    # Calculate resolved frame boundary (start of slot #0)
    frame_offset_slots = (15 - best_shift) % 15
    frame_boundary = best_offset + frame_offset_slots * 5120

    print(f"  -> Çerçeve başlangıcı (slot #0): {frame_boundary} sample")
    print(f"  -> Tespit edilen Scrambling Code Grubu: {best_group} (64 grup içinden)")

    # 3. Step 3: CPICH Descrambling & Primary Scrambling Code Detection
    print("[Adım 3/3] Gold code descrambling ve Primary Scrambling Code (PSC) çözümü başlatılıyor...")

    if frame_boundary + 76800 > len(iq_rotated):
        for f_idx in range(num_frames):
            cand_start = best_offset + f_idx * 76800 + frame_offset_slots * 5120
            if cand_start + 76800 <= len(iq_rotated):
                frame_boundary = cand_start
                break

    best_sc_energy = -1.0
    resolved_sc = -1
    best_cpich_symbols = None
    best_fine_delay = 0
    candidates = [8 * best_group + k for k in range(8)]
    for sc_num in candidates:
        delay, symbols, energy = _cpich_search(iq_rotated, frame_boundary, sc_num)
        if symbols is None:
            continue
        if energy > best_sc_energy:
            best_sc_energy = energy
            resolved_sc = sc_num
            best_cpich_symbols = symbols
            best_fine_delay = delay

    frame_boundary = frame_boundary + best_fine_delay
    frame_iq = iq_rotated[frame_boundary : frame_boundary + 76800]
    
    # Fine CFO estimation using CPICH phase rotation between consecutive symbols
    phase_diffs = best_cpich_symbols[1:] * best_cpich_symbols[:-1].conj()
    mean_phase_diff = np.angle(np.sum(phase_diffs))
    fine_cfo_hz = mean_phase_diff * 15000.0 / (2 * np.pi)
    total_cfo_hz = best_freq + fine_cfo_hz
    
    print(f"  -> İnce zamanlama düzeltmesi: {best_fine_delay} sample (Çerçeve başlangıcı: {frame_boundary})")
    print(f"  -> Başarıyla çözülen Primary Scrambling Code: {resolved_sc}")
    print(f"  -> İnce frekans düzeltmesi: {fine_cfo_hz:.1f} Hz (Toplam CFO: {total_cfo_hz:.1f} Hz)")
    
    # 4. CPICH RSCP and Ec/No Quality Calculations
    # RSCP = |S_m|^2 / (512^2 * 2) = |S_m|^2 / 524288.0
    rscp_linear = np.mean(np.abs(best_cpich_symbols)**2) / 524288.0
    # Total received power RSSI of the input IQ samples
    rssi_linear = np.mean(np.abs(frame_iq)**2)
    
    # Quality ratio Ec/No (chip energy to total spectral density)
    ecno_linear = rscp_linear / (rssi_linear + 1e-12)
    
    # express in logarithmic scales (adding -30dBm uncalibrated conversion offset)
    rscp_dbm = 10.0 * np.log10(rscp_linear + 1e-12) - 30.0
    ecno_db = 10.0 * np.log10(ecno_linear + 1e-12)
    
    print(f"  -> CPICH RSCP: {rscp_dbm:.2f} dBm (uncalibrated)")
    print(f"  -> CPICH Ec/N0: {ecno_db:.2f} dB")
    
    # 5. Diagnostic Plotting (P-SCH and CPICH Constellation)
    if plot_diagnostics:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        
        plt.figure(figsize=(12, 5))
        
        # Left Panel: P-SCH Slot Folding Profile
        plt.subplot(1, 2, 1)
        plt.plot(best_folded, color='#0f82ff', linewidth=1.5)
        plt.axvline(x=best_offset, color='#ff3b30', linestyle='--', label=f'Slot Sınırı: {best_offset}')
        plt.title('P-SCH Slot Folding Profile (Slot Senkronizasyonu)')
        plt.xlabel('Slot İçi Örnek Sayısı (0 - 5119)')
        plt.ylabel('Korelasyon Genliği')
        plt.grid(True, linestyle=':', alpha=0.6)
        plt.legend()
        
        # Right Panel: CPICH QPSK Constellation (De-rotated)
        plt.subplot(1, 2, 2)
        t_symbols = np.arange(len(best_cpich_symbols)) / 15000.0
        best_cpich_symbols_derotated = best_cpich_symbols * np.exp(-1j * 2 * np.pi * fine_cfo_hz * t_symbols)
        mean_amp = np.mean(np.abs(best_cpich_symbols_derotated))
        normalized_symbols = best_cpich_symbols_derotated / (mean_amp + 1e-12)
        
        plt.scatter(normalized_symbols.real, normalized_symbols.imag, color='#34c759', alpha=0.6, edgecolors='none', label='CPICH Sembolleri')
        plt.axhline(0, color='grey', linestyle='--', linewidth=0.8)
        plt.axvline(0, color='grey', linestyle='--', linewidth=0.8)
        plt.title(f'CPICH QPSK Constellation (SC: {resolved_sc})')
        plt.xlabel('In-Phase (I)')
        plt.ylabel('Quadrature (Q)')
        plt.xlim([-2.5, 2.5])
        plt.ylim([-2.5, 2.5])
        plt.grid(True, linestyle=':', alpha=0.6)
        plt.legend()
        
        plt.tight_layout()
        plt.savefig(plot_output_path, dpi=200, bbox_inches='tight')
        plt.close()
        print(f"  -> Tanı grafiği ve CPICH Constellation kaydedildi: {plot_output_path}")
    
    return {
        "scrambling_code": resolved_sc,
        "code_group": best_group,
        "slot_timing_sample": int(best_offset),
        "frame_timing_sample": int(frame_boundary),
        "cpich_rscp_dbm": float(round(rscp_dbm, 2)),
        "cpich_ecno_db": float(round(ecno_db, 2)),
        "frequency_correction_hz": float(total_cfo_hz)
    }

def perform_multi_cell_search(iq_data, sample_rate=7.68e6, max_cells=4):
    """
    Executes WCDMA 3-Step Offline Cell Search Pipeline to detect multiple cells
    on the same carrier frequency. Returns a list of detected cell dictionaries
    sorted by CPICH Ec/No descending.
    """
    print("[Adım 1/3] P-SCH Slot senkronizasyonu başlatılıyor...")
    iq_data = apply_channel_filter(iq_data, sample_rate)
    best_offset, best_freq, best_folded = _coarse_psch(iq_data, sample_rate)
    print(f"  -> Dominant slot sınır kayması: {best_offset} sample")
    print(f"  -> Coarse frekans kayması: {best_freq} Hz (düzeltildi)")

    iq_rotated, _n_frames = _rotate_search_window(
        iq_data, sample_rate, best_offset, best_freq
    )

    peaks = []
    sorted_indices = np.argsort(best_folded)[::-1]
    for idx in sorted_indices:
        prev_idx = (idx - 1) % 5120
        next_idx = (idx + 1) % 5120
        if best_folded[idx] < best_folded[prev_idx] or best_folded[idx] < best_folded[next_idx]:
            continue
        too_close = False
        for p in peaks:
            diff = abs(int(idx) - p)
            if diff > 2560:
                diff = 5120 - diff
            if diff < 512:
                too_close = True
                break
        if not too_close:
            if best_folded[idx] > 0.15 * np.max(best_folded):
                peaks.append(int(idx))
            if len(peaks) >= max_cells:
                break

    print(f"  -> Tespit edilen aday slot zamanlama pik sayısı: {len(peaks)} (Offsets: {peaks})")

    cells_found = {}
    for slot_offset in peaks:
        n_avail = (len(iq_rotated) - slot_offset) // 76800
        if n_avail == 0:
            continue
        n_frames = min(MAX_CELLSEARCH_FRAMES, n_avail)
        ssc_corr_matrix = _ssc_corr_matrix(iq_rotated, slot_offset, n_frames)
        best_group, best_shift, _best_score = _match_code_group(ssc_corr_matrix)
        frame_offset_slots = (15 - best_shift) % 15
        frame_boundary = slot_offset + frame_offset_slots * 5120
        candidates = [8 * best_group + k for k in range(8)]
        for sc_num in candidates:
            best_fine_delay, best_cpich_symbols, _energy = _cpich_search(
                iq_rotated, frame_boundary, sc_num
            )
            if best_cpich_symbols is None:
                continue
            rscp_linear = np.mean(np.abs(best_cpich_symbols) ** 2) / 524288.0
            cell_frame_boundary = frame_boundary + best_fine_delay
            if cell_frame_boundary < 0 or cell_frame_boundary + 76800 > len(iq_rotated):
                continue
            frame_iq_eq = iq_rotated[cell_frame_boundary : cell_frame_boundary + 76800]
            rssi_linear = np.mean(np.abs(frame_iq_eq) ** 2)
            ecno_linear = rscp_linear / (rssi_linear + 1e-12)
            rscp_dbm = 10.0 * np.log10(rscp_linear + 1e-12) - 30.0
            ecno_db = 10.0 * np.log10(ecno_linear + 1e-12)
            phase_diffs = best_cpich_symbols[1:] * best_cpich_symbols[:-1].conj()
            mean_phase_diff = np.angle(np.sum(phase_diffs))
            fine_cfo_hz = mean_phase_diff * 15000.0 / (2 * np.pi)
            cell_cfo_hz = best_freq + fine_cfo_hz
            if ecno_db > -22.0:
                if sc_num not in cells_found or cells_found[sc_num]["cpich_ecno_db"] < ecno_db:
                    cells_found[sc_num] = {
                        "scrambling_code": sc_num,
                        "code_group": best_group,
                        "slot_timing_sample": int(slot_offset),
                        "frame_timing_sample": int(cell_frame_boundary),
                        "cpich_rscp_dbm": float(round(rscp_dbm, 2)),
                        "cpich_ecno_db": float(round(ecno_db, 2)),
                        "frequency_correction_hz": float(cell_cfo_hz),
                    }

    sorted_cells = sorted(cells_found.values(), key=lambda x: x["cpich_ecno_db"], reverse=True)
    return sorted_cells

