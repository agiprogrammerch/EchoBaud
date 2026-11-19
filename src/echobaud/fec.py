"""
EchoBaud Forward Error Correction Module

Wraps Reed-Solomon (RS) encoding/decoding using the `reedsolo` library.
RS(255, 200) over GF(256) provides 55 parity bytes, correcting up to 27 byte errors
per 255-byte codeword. This handles burst noise from room acoustics without retransmission.
"""

from __future__ import annotations

from typing import Final

import reedsolo

from .config import RS_N, RS_K, RS_PARITY


# =============================================================================
# Constants
# =============================================================================

# RS Codec parameters
# GF(256) with primitive polynomial x^8 + x^4 + x^3 + x^2 + 1 (0x11D)
# This is the standard used by reedsolo.RSCodec
RS_PRIMITIVE_POLY: Final[int] = 0x11D
RS_GENERATOR_ROOT: Final[int] = 1  # Root of generator polynomial (alpha^1)
RS_FCR: Final[int] = 1  # First consecutive root


# =============================================================================
# RS Codec Instance
# =============================================================================

# Create a single shared codec instance
# reedsolo.RSCodec(nsym) creates RS(255, 255-nsym) by default
_rs_codec = reedsolo.RSCodec(RS_PARITY, nsize=RS_N, prim=RS_PRIMITIVE_POLY)


# =============================================================================
# Exceptions
# =============================================================================

class FECError(Exception):
    """Base exception for FEC errors."""
    pass


class UncorrectableError(FECError):
    """Raised when Reed-Solomon cannot correct all errors."""
    pass


# =============================================================================
# Encoding / Decoding
# =============================================================================

def rs_encode(data: bytes) -> bytes:
    """
    Encode data with Reed-Solomon (255, 200).
    
    Args:
        data: Input data (exactly RS_K = 200 bytes)
        
    Returns:
        RS codeword (exactly RS_N = 255 bytes)
        
    Raises:
        ValueError: If input length != RS_K
    """
    if len(data) != RS_K:
        raise ValueError(f"RS encode requires exactly {RS_K} bytes, got {len(data)}")
    
    # reedsolo returns the full codeword (data + parity)
    codeword = _rs_codec.encode(data)
    assert len(codeword) == RS_N, f"Codeword length {len(codeword)} != {RS_N}"
    return bytes(codeword)


def rs_decode(codeword: bytes) -> bytes:
    """
    Decode and error-correct Reed-Solomon codeword.
    
    Args:
        codeword: RS codeword (exactly RS_N = 255 bytes)
        
    Returns:
        Corrected data (exactly RS_K = 200 bytes)
        
    Raises:
        UncorrectableError: If errors exceed correction capability
        ValueError: If input length != RS_N
    """
    if len(codeword) != RS_N:
        raise ValueError(f"RS decode requires exactly {RS_N} bytes, got {len(codeword)}")
    
    try:
        # reedsolo.decode returns (corrected_data, corrected_parity, error_positions)
        # or just corrected_data depending on version
        result = _rs_codec.decode(codeword)
        # Handle different return types across reedsolo versions
        if isinstance(result, tuple):
            corrected_data = result[0]
        else:
            corrected_data = result
        
        corrected_bytes = bytes(corrected_data)
        if len(corrected_bytes) != RS_K:
            raise ValueError(f"Decoded length {len(corrected_bytes)} != {RS_K}")
        return corrected_bytes
        
    except reedsolo.ReedSolomonError as e:
        raise UncorrectableError(f"RS decode failed: {e}") from e


def rs_decode_with_erasure(codeword: bytes, erasure_positions: list[int]) -> bytes:
    """
    Decode with known erasure positions (for advanced use).
    
    Args:
        codeword: RS codeword (255 bytes)
        erasure_positions: List of byte indices known to be erased
        
    Returns:
        Corrected data (200 bytes)
    """
    if len(codeword) != RS_N:
        raise ValueError(f"RS decode requires exactly {RS_N} bytes, got {len(codeword)}")
    
    try:
        result = _rs_codec.decode(codeword, erase_pos=erasure_positions)
        if isinstance(result, tuple):
            corrected_data = result[0]
        else:
            corrected_data = result
        return bytes(corrected_data)
    except reedsolo.ReedSolomonError as e:
        raise UncorrectableError(f"RS decode with erasures failed: {e}") from e


# =============================================================================
# High-Level Frame FEC
# =============================================================================

def fec_encode_frames(frames: list[bytes]) -> list[bytes]:
    """
    Apply RS encoding to a list of frames.
    
    Each frame must be exactly RS_K bytes (200). The frame encoding in framing.py
    ensures this by padding if necessary (though our frames are exactly 200 bytes).
    
    Args:
        frames: List of pre-RS frames (each exactly RS_K bytes)
        
    Returns:
        List of RS codewords (each RS_N bytes)
    """
    encoded = []
    for frame in frames:
        if len(frame) != RS_K:
            # Pad with zeros if needed (shouldn't happen with proper framing)
            frame = frame.ljust(RS_K, b"\x00")
        encoded.append(rs_encode(frame))
    return encoded


def fec_decode_frames(codewords: list[bytes]) -> list[bytes]:
    """
    Apply RS decoding to a list of codewords.
    
    Args:
        codewords: List of RS codewords (each RS_N bytes)
        
    Returns:
        List of decoded frames (each RS_K bytes)
        
    Raises:
        UncorrectableError: If any codeword has uncorrectable errors
    """
    decoded = []
    for i, cw in enumerate(codewords):
        try:
            decoded.append(rs_decode(cw))
        except UncorrectableError as e:
            raise UncorrectableError(f"Codeword {i}: {e}") from e
    return decoded


# =============================================================================
# Testing
# =============================================================================

def _test_fec() -> None:
    """Internal FEC tests."""
    # Test basic encode/decode
    data = bytes(range(RS_K))  # 200 bytes
    codeword = rs_encode(data)
    assert len(codeword) == RS_N
    decoded = rs_decode(codeword)
    assert decoded == data
    
    # Test error correction: corrupt up to 27 bytes
    import random
    corrupted = bytearray(codeword)
    error_positions = random.sample(range(RS_N), 27)
    for pos in error_positions:
        corrupted[pos] ^= random.randint(1, 255)
    decoded = rs_decode(bytes(corrupted))
    assert decoded == data, "Failed to correct 27 errors"
    
    # Test 28 errors should fail (may occasionally succeed by chance)
    corrupted = bytearray(codeword)
    error_positions = random.sample(range(RS_N), 28)
    for pos in error_positions:
        corrupted[pos] ^= random.randint(1, 255)
    try:
        rs_decode(bytes(corrupted))
        # Might succeed by chance, but very unlikely
        print("WARNING: 28 errors corrected (unlikely but possible)")
    except UncorrectableError:
        pass  # Expected
    
    # Test frame FEC
    frames = [bytes([i] * RS_K) for i in range(5)]
    encoded = fec_encode_frames(frames)
    assert len(encoded) == 5
    assert all(len(cw) == RS_N for cw in encoded)
    
    decoded_frames = fec_decode_frames(encoded)
    assert decoded_frames == frames
    
    print("FEC tests passed.")


if __name__ == "__main__":
    _test_fec()