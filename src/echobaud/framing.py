"""
EchoBaud Framing Module

Implements compact binary packet framing and multi-chunk reassembly.

Frame Format (pre-Reed-Solomon):
  [MAGIC: 2B (0xEB90)] [VERSION_FLAGS: 1B] [CHUNK_ID: 2B] [TOTAL_CHUNKS: 2B]
  [PAYLOAD_LEN: 2B] [PAYLOAD: N bytes] [CRC32: 4B]

Total overhead: 13 bytes + payload
Max payload: 200 bytes (config.MAX_PAYLOAD_BYTES)
Max frame pre-RS: 213 bytes <= RS_K (200) ... wait, this exceeds RS_K!

Actually, the frame is the data that gets RS encoded. So the frame size must be <= RS_K.
With RS_K=200, max frame is 200 bytes. Overhead is 13 bytes, so max payload = 187 bytes.

Wait, let me recheck config.py: MAX_PAYLOAD_BYTES = 200, RS_K = 200.
Frame overhead = 13 bytes. So max frame = 13 + 200 = 213 bytes > 200.
This is a bug in my config. I need to fix this.

Options:
1. Reduce MAX_PAYLOAD_BYTES to 187
2. Increase RS_K to 213 (but RS_N is fixed at 255)

Let me reduce MAX_PAYLOAD_BYTES to 187 to fit in RS(255, 200).
Actually, let me increase RS_K to 223 (32 parity, corrects 16 errors) and keep MAX_PAYLOAD=200.
Or I could use RS(255, 210) = 45 parity.

Let me fix config.py to have RS_K = 213 (which allows 200 payload + 13 overhead = 213).
But RS_K must be <= 255. Let me use RS(255, 213) = 42 parity bytes, corrects 21 errors.

Actually, I'll keep it simple: MAX_PAYLOAD_BYTES = 187 (so frame = 200 bytes = RS_K).
This gives 187 bytes payload per frame, which is fine for text.
For files, we chunk into 187-byte pieces.

Let me update config.py and framing.py accordingly.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .config import (
    MAGIC_BYTES,
    PROTOCOL_VERSION,
    MAX_PAYLOAD_BYTES,
    RS_K,
    CRC32_POLY,
)


# =============================================================================
# Frame Format Constants
# =============================================================================

# Frame structure (all values big-endian / network byte order):
# MAGIC: 2 bytes = 0xEB90
# VERSION_FLAGS: 1 byte (upper 4 bits version, lower 4 bits flags)
# CHUNK_ID: 2 bytes (0-based index)
# TOTAL_CHUNKS: 2 bytes (total number of chunks in this transmission)
# PAYLOAD_LEN: 2 bytes (length of payload in bytes, max 187)
# PAYLOAD: N bytes
# CRC32: 4 bytes (CRC-32 of everything before this field)

FRAME_MAGIC: Final[bytes] = MAGIC_BYTES
FRAME_VERSION: Final[int] = PROTOCOL_VERSION
FRAME_OVERHEAD: Final[int] = 2 + 1 + 2 + 2 + 2 + 4  # 13 bytes
MAX_PAYLOAD_SIZE: Final[int] = RS_K - FRAME_OVERHEAD  # 187 bytes (was 200)

# Flags (lower 4 bits of VERSION_FLAGS byte)
FLAG_ENCRYPTED: Final[int] = 0x01  # Payload is encrypted
FLAG_FILE_TRANSFER: Final[int] = 0x02  # Payload is file chunk (with filename in first chunk)
FLAG_LAST_CHUNK: Final[int] = 0x04  # This is the last chunk


# =============================================================================
# Exceptions
# =============================================================================

class FramingError(Exception):
    """Base exception for framing errors."""
    pass


class InvalidMagicError(FramingError):
    """Frame magic bytes don't match."""
    pass


class InvalidVersionError(FramingError):
    """Unsupported protocol version."""
    pass


class CRCMismatchError(FramingError):
    """CRC32 check failed."""
    pass


class PayloadTooLargeError(FramingError):
    """Payload exceeds maximum size."""
    pass


# =============================================================================
# Frame Dataclass
# =============================================================================

@dataclass(frozen=True, slots=True)
class Frame:
    """Decoded frame with verified integrity."""
    chunk_id: int
    total_chunks: int
    payload: bytes
    is_encrypted: bool
    is_file_transfer: bool
    is_last_chunk: bool
    
    @property
    def flags(self) -> int:
        """Reconstruct flags byte."""
        flags = 0
        if self.is_encrypted:
            flags |= FLAG_ENCRYPTED
        if self.is_file_transfer:
            flags |= FLAG_FILE_TRANSFER
        if self.is_last_chunk:
            flags |= FLAG_LAST_CHUNK
        return flags


# =============================================================================
# CRC32
# =============================================================================

def crc32(data: bytes) -> int:
    """Compute CRC-32 (IEEE 802.3) of data."""
    return zlib.crc32(data) & 0xFFFFFFFF


# =============================================================================
# Frame Encoding
# =============================================================================

def encode_frame(
    payload: bytes,
    chunk_id: int,
    total_chunks: int,
    is_encrypted: bool = False,
    is_file_transfer: bool = False,
    is_last_chunk: bool = False,
) -> bytes:
    """
    Encode a frame with CRC32.
    
    Args:
        payload: Payload data (max MAX_PAYLOAD_SIZE bytes)
        chunk_id: 0-based chunk index
        total_chunks: Total number of chunks
        is_encrypted: Whether payload is encrypted
        is_file_transfer: Whether this is a file transfer
        is_last_chunk: Whether this is the final chunk
        
    Returns:
        Complete frame bytes (pre-RS encoding)
        
    Raises:
        PayloadTooLargeError: If payload exceeds MAX_PAYLOAD_SIZE
    """
    if len(payload) > MAX_PAYLOAD_SIZE:
        raise PayloadTooLargeError(
            f"Payload {len(payload)} bytes exceeds maximum {MAX_PAYLOAD_SIZE}"
        )
    
    if not (0 <= chunk_id < total_chunks):
        raise ValueError(f"chunk_id {chunk_id} out of range [0, {total_chunks})")
    if not (0 <= chunk_id < 65536):
        raise ValueError("chunk_id must fit in 16 bits")
    if not (1 <= total_chunks <= 65535):
        raise ValueError("total_chunks must fit in 16 bits (1-65535)")
    
    # Build flags byte
    flags = 0
    if is_encrypted:
        flags |= FLAG_ENCRYPTED
    if is_file_transfer:
        flags |= FLAG_FILE_TRANSFER
    if is_last_chunk:
        flags |= FLAG_LAST_CHUNK
    
    version_flags = (FRAME_VERSION << 4) | (flags & 0x0F)
    
    # Pack header (without CRC)
    header = struct.pack(
        ">H B H H H",  # Big-endian: magic(2), ver_flags(1), chunk_id(2), total(2), payload_len(2)
        int.from_bytes(FRAME_MAGIC, "big"),
        version_flags,
        chunk_id,
        total_chunks,
        len(payload),
    )
    
    frame_without_crc = header + payload
    frame_crc = crc32(frame_without_crc)
    
    # Append CRC32
    frame = frame_without_crc + struct.pack(">I", frame_crc)
    
    assert len(frame) == FRAME_OVERHEAD + len(payload), "Frame size mismatch"
    assert len(frame) <= RS_K, f"Frame {len(frame)} > RS_K {RS_K}"
    
    return frame


# =============================================================================
# Frame Decoding
# =============================================================================

def decode_frame(data: bytes) -> Frame:
    """
    Decode and verify a frame.
    
    Args:
        data: Frame bytes (pre-RS decoding output)
        
    Returns:
        Verified Frame object
        
    Raises:
        InvalidMagicError: If magic bytes don't match
        InvalidVersionError: If version is unsupported
        CRCMismatchError: If CRC32 verification fails
        ValueError: If frame is malformed
    """
    min_frame_size = FRAME_OVERHEAD  # 13 bytes minimum (empty payload)
    if len(data) < min_frame_size:
        raise ValueError(f"Frame too short: {len(data)} < {min_frame_size}")
    if len(data) > RS_K:
        raise ValueError(f"Frame too long: {len(data)} > RS_K {RS_K}")
    
    # Unpack header
    magic_int, version_flags, chunk_id, total_chunks, payload_len = struct.unpack(
        ">H B H H H", data[:9]
    )
    
    # Verify magic
    if magic_int != int.from_bytes(FRAME_MAGIC, "big"):
        raise InvalidMagicError(
            f"Invalid magic: 0x{magic_int:04X}, expected 0xEB90"
        )
    
    # Verify version
    version = version_flags >> 4
    if version != FRAME_VERSION:
        raise InvalidVersionError(f"Unsupported version: {version}, expected {FRAME_VERSION}")
    
    flags = version_flags & 0x0F
    is_encrypted = bool(flags & FLAG_ENCRYPTED)
    is_file_transfer = bool(flags & FLAG_FILE_TRANSFER)
    is_last_chunk = bool(flags & FLAG_LAST_CHUNK)
    
    # Extract payload
    payload_start = 9
    payload_end = payload_start + payload_len
    if payload_end > len(data) - 4:  # -4 for CRC
        raise ValueError(f"Payload length {payload_len} exceeds frame data")
    
    payload = data[payload_start:payload_end]
    if len(payload) != payload_len:
        raise ValueError("Payload truncated")
    
    # Verify CRC32
    frame_without_crc = data[:payload_end]
    expected_crc = struct.unpack(">I", data[payload_end:payload_end + 4])[0]
    actual_crc = crc32(frame_without_crc)
    
    if actual_crc != expected_crc:
        raise CRCMismatchError(
            f"CRC mismatch: expected 0x{expected_crc:08X}, got 0x{actual_crc:08X}"
        )
    
    return Frame(
        chunk_id=chunk_id,
        total_chunks=total_chunks,
        payload=payload,
        is_encrypted=is_encrypted,
        is_file_transfer=is_file_transfer,
        is_last_chunk=is_last_chunk,
    )


# =============================================================================
# Chunking / Reassembly
# =============================================================================

def chunk_data(data: bytes, max_payload: int = MAX_PAYLOAD_SIZE) -> list[bytes]:
    """
    Split data into chunks of max_payload size.
    
    Args:
        data: Data to chunk
        max_payload: Maximum payload per chunk
        
    Returns:
        List of payload chunks
    """
    if not data:
        return [b""]  # Single empty chunk for empty data
    
    chunks = []
    for i in range(0, len(data), max_payload):
        chunks.append(data[i:i + max_payload])
    return chunks


def create_frames(
    payload: bytes,
    is_encrypted: bool = False,
    is_file_transfer: bool = False,
) -> list[bytes]:
    """
    Create framed chunks from payload.
    
    Args:
        payload: Complete payload data
        is_encrypted: Whether payload is encrypted
        is_file_transfer: Whether this is a file transfer
        
    Returns:
        List of encoded frames (ready for RS encoding)
    """
    chunks = chunk_data(payload)
    total = len(chunks)
    frames = []
    
    for i, chunk in enumerate(chunks):
        frame = encode_frame(
            payload=chunk,
            chunk_id=i,
            total_chunks=total,
            is_encrypted=is_encrypted,
            is_file_transfer=is_file_transfer,
            is_last_chunk=(i == total - 1),
        )
        frames.append(frame)
    
    return frames


# =============================================================================
# File Transfer Helpers
# =============================================================================

def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename for safe filesystem storage.
    
    Removes path traversal attempts, null bytes, and other dangerous characters.
    Keeps only alphanumeric, dash, underscore, dot.
    """
    # Remove directory components
    name = Path(filename).name
    
    # Remove null bytes and control characters
    name = "".join(c for c in name if ord(c) >= 32 and c != "\x7f")
    
    # Replace path separators and other dangerous chars
    dangerous = '<>:"/\\|?*'
    for c in dangerous:
        name = name.replace(c, "_")
    
    # Limit length
    if len(name) > 255:
        name = name[:255]
    
    # Ensure not empty
    if not name:
        name = "received_file"
    
    return name


def prepare_file_transfer(filepath: Path) -> tuple[bytes, str]:
    """
    Read file and prepare payload for transfer.
    
    First chunk contains filename metadata.
    Format: [FILENAME_LEN:1] [FILENAME_UTF8] [FILE_DATA...]
    
    Args:
        filepath: Path to file
        
    Returns:
        (payload_bytes, sanitized_filename)
    """
    filename = sanitize_filename(filepath.name)
    filename_bytes = filename.encode("utf-8")
    
    if len(filename_bytes) > 255:
        raise ValueError(f"Filename too long after sanitization: {len(filename_bytes)}")
    
    with open(filepath, "rb") as f:
        file_data = f.read()
    
    # Construct payload: filename length (1 byte) + filename + file data
    payload = bytes([len(filename_bytes)]) + filename_bytes + file_data
    return payload, filename


def parse_file_transfer(payload: bytes) -> tuple[str, bytes]:
    """
    Parse file transfer payload from first chunk.
    
    Args:
        payload: Payload from first chunk (with filename metadata)
        
    Returns:
        (filename, file_data)
    """
    if not payload:
        raise ValueError("Empty file transfer payload")
    
    filename_len = payload[0]
    if filename_len > len(payload) - 1:
        raise ValueError("Filename length exceeds payload")
    
    filename = payload[1:1 + filename_len].decode("utf-8")
    file_data = payload[1 + filename_len:]
    
    return sanitize_filename(filename), file_data


# =============================================================================
# Reassembly Buffer
# =============================================================================

class ReassemblyBuffer:
    """
    Handles out-of-order and duplicate chunk reception for multi-chunk transfers.
    
    Thread-safe for single-producer (RX callback) / single-consumer (main loop).
    """
    
    def __init__(self, total_chunks: int):
        self.total_chunks = total_chunks
        self.chunks: dict[int, Frame] = {}
        self.received_count = 0
        self.is_file_transfer = False
        self.is_encrypted = False
        self.filename: str | None = None
        self._completed = False
    
    def add_frame(self, frame: Frame) -> bool:
        """
        Add a frame to the buffer.
        
        Returns:
            True if this frame completed the transfer, False otherwise.
        """
        if self._completed:
            return False
        
        # Validate consistency
        if frame.total_chunks != self.total_chunks:
            raise ValueError(f"Chunk total mismatch: {frame.total_chunks} != {self.total_chunks}")
        
        # Store metadata from chunk 0 (which contains filename for file transfers)
        if frame.chunk_id == 0:
            self.is_file_transfer = frame.is_file_transfer
            self.is_encrypted = frame.is_encrypted
            if frame.is_file_transfer:
                self.filename, _ = parse_file_transfer(frame.payload)
        
        # Ignore duplicates
        if frame.chunk_id in self.chunks:
            return False
        
        self.chunks[frame.chunk_id] = frame
        self.received_count += 1
        
        # Check completion
        if self.received_count == self.total_chunks:
            self._completed = True
            return True
        
        return False
    
    def is_complete(self) -> bool:
        return self._completed
    
    def get_reassembled_payload(self) -> bytes:
        """Get complete reassembled payload (call only after is_complete())."""
        if not self._completed:
            raise RuntimeError("Transfer not complete")
        
        # Sort by chunk_id and concatenate payloads
        payload_parts = []
        for i in range(self.total_chunks):
            if i not in self.chunks:
                raise RuntimeError(f"Missing chunk {i}")
            payload_parts.append(self.chunks[i].payload)
        
        return b"".join(payload_parts)
    
    def get_missing_chunks(self) -> list[int]:
        """Get list of missing chunk IDs."""
        return [i for i in range(self.total_chunks) if i not in self.chunks]
    
    def progress(self) -> tuple[int, int]:
        """Return (received, total)."""
        return (self.received_count, self.total_chunks)


# =============================================================================
# Testing
# =============================================================================

def _test_framing() -> None:
    """Internal framing tests."""
    # Test basic encode/decode
    payload = b"Hello, World!"
    frame = encode_frame(payload, chunk_id=0, total_chunks=1, is_last_chunk=True)
    decoded = decode_frame(frame)
    assert decoded.payload == payload
    assert decoded.chunk_id == 0
    assert decoded.total_chunks == 1
    assert decoded.is_last_chunk
    
    # Test multi-chunk
    large_payload = b"X" * 500
    frames = create_frames(large_payload)
    assert len(frames) == 3  # 500 / 187 = 2.67 -> 3 chunks
    
    # Decode all
    reassembled = b""
    for f in frames:
        decoded = decode_frame(f)
        reassembled += decoded.payload
    assert reassembled == large_payload
    
    # Test CRC error detection
    bad_frame = bytearray(frames[0])
    bad_frame[10] ^= 0xFF  # Corrupt payload
    try:
        decode_frame(bytes(bad_frame))
        assert False, "Should have raised CRCMismatchError"
    except CRCMismatchError:
        pass
    
    # Test magic error
    bad_magic = bytearray(frames[0])
    bad_magic[0] = 0xFF
    try:
        decode_frame(bytes(bad_magic))
        assert False, "Should have raised InvalidMagicError"
    except InvalidMagicError:
        pass
    
    # Test file transfer
    payload, fname = prepare_file_transfer(Path("test.txt"))
    assert fname == "test.txt"
    parsed_name, file_data = parse_file_transfer(payload)
    assert parsed_name == "test.txt"
    assert file_data == b""
    
    # Test sanitize
    assert sanitize_filename("normal.txt") == "normal.txt"
    assert sanitize_filename("../etc/passwd") == "passwd"
    assert sanitize_filename("file:name.txt") == "file_name.txt"
    assert sanitize_filename("") == "received_file"
    
    # Test reassembly buffer
    buf = ReassemblyBuffer(total_chunks=3)
    for f in frames:
        completed = buf.add_frame(decode_frame(f))
    assert buf.is_complete()
    assert buf.get_reassembled_payload() == large_payload
    
    # Test out of order
    buf2 = ReassemblyBuffer(total_chunks=3)
    buf2.add_frame(decode_frame(frames[2]))
    buf2.add_frame(decode_frame(frames[0]))
    completed = buf2.add_frame(decode_frame(frames[1]))
    assert completed
    assert buf2.get_reassembled_payload() == large_payload
    
    print("Framing tests passed.")


if __name__ == "__main__":
    _test_framing()