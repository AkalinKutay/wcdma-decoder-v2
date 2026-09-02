# WCDMA/UMTS Passive Neighbor Cell Decoder
# Copyright (C) 2026 06kutay <https://github.com/06kutay>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

import os
import sys
import time

import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from wcdma_bch import NEXT_STATES, OUTPUTS, decode_viterbi, decode_viterbi_ref


def conv_encode_soft(bits):
    """Rate 1/2 K=9 encoder → BPSK soft bits (positive = bit 0)."""
    state = 0
    out = np.empty(len(bits) * 2, dtype=np.float64)
    for i, u in enumerate(bits):
        u = int(u)
        v0, v1 = OUTPUTS[state, u]
        out[2 * i] = 1.0 - 2.0 * v0
        out[2 * i + 1] = 1.0 - 2.0 * v1
        state = int(NEXT_STATES[state, u])
    return out


def test_roundtrip_clean():
    rng = np.random.default_rng(0)
    data = rng.integers(0, 2, size=262, dtype=np.int8)
    bits = np.concatenate([data, np.zeros(8, dtype=np.int8)])
    soft = conv_encode_soft(bits)
    decoded = decode_viterbi(soft)
    assert decoded.shape == (270,)
    np.testing.assert_array_equal(decoded, bits)


def test_numpy_matches_ref_clean():
    rng = np.random.default_rng(1)
    data = rng.integers(0, 2, size=262, dtype=np.int8)
    bits = np.concatenate([data, np.zeros(8, dtype=np.int8)])
    soft = conv_encode_soft(bits)
    np.testing.assert_array_equal(decode_viterbi(soft), decode_viterbi_ref(soft))


def test_numpy_matches_ref_noisy():
    rng = np.random.default_rng(2)
    data = rng.integers(0, 2, size=262, dtype=np.int8)
    bits = np.concatenate([data, np.zeros(8, dtype=np.int8)])
    soft = conv_encode_soft(bits) + rng.normal(0.0, 0.35, size=540)
    np.testing.assert_array_equal(decode_viterbi(soft), decode_viterbi_ref(soft))


def test_numpy_faster_than_ref():
    rng = np.random.default_rng(3)
    data = rng.integers(0, 2, size=262, dtype=np.int8)
    bits = np.concatenate([data, np.zeros(8, dtype=np.int8)])
    soft = conv_encode_soft(bits)
    decode_viterbi(soft)
    decode_viterbi_ref(soft)
    t0 = time.perf_counter()
    for _ in range(20):
        decode_viterbi(soft)
    t_np = time.perf_counter() - t0
    t0 = time.perf_counter()
    for _ in range(20):
        decode_viterbi_ref(soft)
    t_ref = time.perf_counter() - t0
    print(f"  viterbi numpy {t_np/20*1e3:.2f} ms  ref {t_ref/20*1e3:.2f} ms")
    assert t_np < t_ref


if __name__ == "__main__":
    test_roundtrip_clean()
    print("  -> clean roundtrip PASSED")
    test_numpy_matches_ref_clean()
    print("  -> numpy == ref (clean) PASSED")
    test_numpy_matches_ref_noisy()
    print("  -> numpy == ref (noisy) PASSED")
    test_numpy_faster_than_ref()
    print("  -> numpy faster than ref PASSED")
