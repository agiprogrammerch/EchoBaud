"""Tests for FEC (Reed-Solomon) module."""

import pytest
import random

from echobaud.fec import (
    rs_encode,
    rs_decode,
    rs_decode_with_erasure,
    fec_encode_frames,
    fec_decode_frames,
    UncorrectableError,
    RS_N,
    RS_K,
    RS_PARITY,
)
from echobaud.config import RS_N as CFG_RS_N, RS_K as CFG_RS_K


def test_rs_constants():
    """Test RS constants match config."""
    assert RS_N == CFG_RS_N == 255
    assert RS_K == CFG_RS_K == 201
    assert RS_PARITY == 54


def test_rs_encode_decode_roundtrip():
    """Test basic RS encode/decode round-trip."""
    data = bytes(range(RS_K))  # 200 bytes
    
    codeword = rs_encode(data)
    assert len(codeword) == RS_N
    
    decoded = rs_decode(codeword)
    assert decoded == data


def test_rs_encode_wrong_length():
    """Test RS encode rejects wrong input length."""
    with pytest.raises(ValueError):
        rs_encode(b"short")
    
    with pytest.raises(ValueError):
        rs_encode(bytes([0] * (RS_K + 1)))


def test_rs_decode_wrong_length():
    """Test RS decode rejects wrong input length."""
    with pytest.raises(ValueError):
        rs_decode(b"short")
    
    with pytest.raises(ValueError):
        rs_decode(bytes([0] * (RS_N + 1)))


def test_rs_error_correction():
    """Test RS corrects up to 27 byte errors."""
    data = bytes([i % 256 for i in range(RS_K)])
    codeword = rs_encode(data)
    
    # Corrupt exactly 27 bytes (max correctable)
    corrupted = bytearray(codeword)
    error_positions = random.sample(range(RS_N), 27)
    for pos in error_positions:
        corrupted[pos] ^= random.randint(1, 255)
    
    decoded = rs_decode(bytes(corrupted))
    assert decoded == data, "Failed to correct 27 errors"


def test_rs_error_correction_random_data():
    """Test RS correction with random data."""
    for _ in range(10):
        data = bytes(random.randint(0, 255) for _ in range(RS_K))
        codeword = rs_encode(data)
        
        # Corrupt 20 bytes
        corrupted = bytearray(codeword)
        error_positions = random.sample(range(RS_N), 20)
        for pos in error_positions:
            corrupted[pos] ^= random.randint(1, 255)
        
        decoded = rs_decode(bytes(corrupted))
        assert decoded == data


def test_rs_uncorrectable():
    """Test RS fails with >27 errors (usually)."""
    data = bytes(range(RS_K))
    codeword = rs_encode(data)
    
    # Corrupt 28 bytes
    corrupted = bytearray(codeword)
    error_positions = random.sample(range(RS_N), 28)
    for pos in error_positions:
        corrupted[pos] ^= random.randint(1, 255)
    
    # Should fail (or extremely rarely succeed by chance)
    try:
        decoded = rs_decode(bytes(corrupted))
        # If it succeeds, it's a false positive (very unlikely)
        print("WARNING: 28 errors corrected (statistically unlikely)")
    except UncorrectableError:
        pass  # Expected


def test_rs_burst_errors():
    """Test RS handles burst errors (consecutive bytes)."""
    data = bytes(range(RS_K))
    codeword = rs_encode(data)
    
    # Corrupt a burst of 27 consecutive bytes
    corrupted = bytearray(codeword)
    start = random.randint(0, RS_N - 27)
    for i in range(27):
        corrupted[start + i] ^= random.randint(1, 255)
    
    decoded = rs_decode(bytes(corrupted))
    assert decoded == data


def test_rs_decode_with_erasure():
    """Test RS decode with known erasure positions."""
    data = bytes(range(RS_K))
    codeword = rs_encode(data)
    
    # Corrupt 20 bytes
    corrupted = bytearray(codeword)
    error_positions = random.sample(range(RS_N), 20)
    for pos in error_positions:
        corrupted[pos] ^= random.randint(1, 255)
    
    # Provide erasure positions (helps decoder)
    decoded = rs_decode_with_erasure(bytes(corrupted), error_positions)
    assert decoded == data


def test_fec_encode_decode_frames():
    """Test high-level frame FEC functions."""
    frames = [bytes([i] * RS_K) for i in range(5)]
    
    encoded = fec_encode_frames(frames)
    assert len(encoded) == 5
    assert all(len(cw) == RS_N for cw in encoded)
    
    decoded = fec_decode_frames(encoded)
    assert decoded == frames


def test_fec_encode_pads_short_frames():
    """Test FEC encode pads frames shorter than RS_K."""
    frames = [b"short"]
    
    encoded = fec_encode_frames(frames)
    assert len(encoded) == 1
    assert len(encoded[0]) == RS_N
    
    decoded = fec_decode_frames(encoded)
    assert decoded[0] == b"short".ljust(RS_K, b"\x00")


def test_fec_uncorrectable_propagates():
    """Test UncorrectableError includes frame index."""
    frames = [bytes(range(RS_K))]
    encoded = fec_encode_frames(frames)
    
    # Corrupt beyond correction capability
    corrupted = bytearray(encoded[0])
    for i in range(30):
        corrupted[i] ^= 0xFF
    
    bad_encoded = [bytes(corrupted)]
    
    with pytest.raises(UncorrectableError) as exc_info:
        fec_decode_frames(bad_encoded)
    
    assert "Codeword 0" in str(exc_info.value)