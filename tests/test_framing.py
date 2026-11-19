"""Tests for framing module."""

import pytest

from echobaud.framing import (
    encode_frame,
    decode_frame,
    create_frames,
    chunk_data,
    prepare_file_transfer,
    parse_file_transfer,
    sanitize_filename,
    ReassemblyBuffer,
    Frame,
    FRAME_OVERHEAD,
    MAX_PAYLOAD_SIZE,
    FLAG_ENCRYPTED,
    FLAG_FILE_TRANSFER,
    FLAG_LAST_CHUNK,
    CRCMismatchError,
    InvalidMagicError,
    InvalidVersionError,
    PayloadTooLargeError,
)
from echobaud.config import RS_K


def test_encode_decode_roundtrip():
    """Test basic frame encode/decode round-trip."""
    payload = b"Hello, World!"
    frame = encode_frame(
        payload=payload,
        chunk_id=0,
        total_chunks=1,
        is_last_chunk=True,
    )
    
    decoded = decode_frame(frame)
    
    assert decoded.payload == payload
    assert decoded.chunk_id == 0
    assert decoded.total_chunks == 1
    assert decoded.is_last_chunk
    assert not decoded.is_encrypted
    assert not decoded.is_file_transfer


def test_encode_decode_with_flags():
    """Test frame with all flags set."""
    payload = b"Encrypted file data"
    frame = encode_frame(
        payload=payload,
        chunk_id=5,
        total_chunks=10,
        is_encrypted=True,
        is_file_transfer=True,
        is_last_chunk=False,
    )
    
    decoded = decode_frame(frame)
    
    assert decoded.payload == payload
    assert decoded.chunk_id == 5
    assert decoded.total_chunks == 10
    assert decoded.is_encrypted
    assert decoded.is_file_transfer
    assert not decoded.is_last_chunk


def test_multi_chunk_frames():
    """Test creating and decoding multiple frames."""
    large_payload = b"X" * 500  # Larger than MAX_PAYLOAD_SIZE (187)
    frames = create_frames(large_payload)
    
    # Should create 3 chunks (500 / 187 = 2.67)
    assert len(frames) == 3
    
    # Decode all and reassemble
    reassembled = b""
    for f in frames:
        decoded = decode_frame(f)
        reassembled += decoded.payload
    
    assert reassembled == large_payload
    
    # Check chunk IDs and flags
    for i, f in enumerate(frames):
        decoded = decode_frame(f)
        assert decoded.chunk_id == i
        assert decoded.total_chunks == 3
        if i == 2:
            assert decoded.is_last_chunk
        else:
            assert not decoded.is_last_chunk


def test_crc_error_detection():
    """Test CRC32 catches corruption."""
    payload = b"Test payload"
    frame = encode_frame(payload, chunk_id=0, total_chunks=1)
    
    # Corrupt payload
    bad_frame = bytearray(frame)
    bad_frame[10] ^= 0xFF
    
    with pytest.raises(CRCMismatchError):
        decode_frame(bytes(bad_frame))


def test_magic_error_detection():
    """Test magic byte validation."""
    payload = b"Test"
    frame = encode_frame(payload, chunk_id=0, total_chunks=1)
    
    # Corrupt magic
    bad_frame = bytearray(frame)
    bad_frame[0] = 0xFF
    bad_frame[1] = 0xFF
    
    with pytest.raises(InvalidMagicError):
        decode_frame(bytes(bad_frame))


def test_version_error():
    """Test version validation."""
    payload = b"Test"
    frame = encode_frame(payload, chunk_id=0, total_chunks=1)
    
    # Corrupt version (byte 2, upper 4 bits)
    bad_frame = bytearray(frame)
    bad_frame[2] = 0xF0  # Version 15
    
    with pytest.raises(InvalidVersionError):
        decode_frame(bytes(bad_frame))


def test_payload_too_large():
    """Test payload size limit enforcement."""
    # MAX_PAYLOAD_SIZE is 187
    large_payload = b"X" * (MAX_PAYLOAD_SIZE + 1)
    
    with pytest.raises(PayloadTooLargeError):
        encode_frame(large_payload, chunk_id=0, total_chunks=1)


def test_max_payload():
    """Test maximum allowed payload works."""
    max_payload = b"X" * MAX_PAYLOAD_SIZE
    frame = encode_frame(max_payload, chunk_id=0, total_chunks=1)
    decoded = decode_frame(frame)
    assert decoded.payload == max_payload


def test_frame_size_matches_rs_k():
    """Test that max frame size equals RS_K."""
    max_payload = b"X" * MAX_PAYLOAD_SIZE
    frame = encode_frame(max_payload, chunk_id=0, total_chunks=1)
    assert len(frame) == RS_K


def test_chunk_data():
    """Test data chunking utility."""
    # Empty data
    assert chunk_data(b"") == [b""]
    
    # Exact fit
    assert chunk_data(b"ABC", 3) == [b"ABC"]
    
    # Multiple chunks
    assert chunk_data(b"ABCDEF", 2) == [b"AB", b"CD", b"EF"]
    
    # Partial last chunk
    assert chunk_data(b"ABCDE", 2) == [b"AB", b"CD", b"E"]


def test_prepare_parse_file_transfer():
    """Test file transfer payload preparation."""
    import tempfile
    
    with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".bin") as f:
        f.write(b"File content here")
        temp_path = f.name
    
    from pathlib import Path
    payload, filename = prepare_file_transfer(Path(temp_path))
    
    # Filename should be sanitized version of temp file name
    assert filename.endswith(".bin")
    
    parsed_name, file_data = parse_file_transfer(payload)
    assert parsed_name == filename
    assert file_data == b"File content here"
    
    import os
    os.unlink(temp_path)


def test_sanitize_filename():
    """Test filename sanitization."""
    assert sanitize_filename("normal.txt") == "normal.txt"
    assert sanitize_filename("../etc/passwd") == "passwd"
    assert sanitize_filename("file:name.txt") == "file_name.txt"
    assert sanitize_filename('file"name.txt') == "file_name.txt"
    assert sanitize_filename("file|name.txt") == "file_name.txt"
    assert sanitize_filename("") == "received_file"
    assert sanitize_filename("a" * 300) == "a" * 255
    
    # Null bytes and control chars removed
    assert sanitize_filename("file\x00name.txt") == "filename.txt"
    assert sanitize_filename("file\x1fname.txt") == "filename.txt"


def test_reassembly_buffer():
    """Test ReassemblyBuffer for multi-chunk transfers."""
    payload = b"X" * 500
    frames = create_frames(payload)
    
    # In-order
    buf = ReassemblyBuffer(total_chunks=3)
    for f in frames:
        from echobaud.framing import decode_frame
        frame = decode_frame(f)
        completed = buf.add_frame(frame)
    
    assert buf.is_complete()
    assert buf.get_reassembled_payload() == payload
    
    # Out of order
    buf2 = ReassemblyBuffer(total_chunks=3)
    for f in [frames[2], frames[0], frames[1]]:
        frame = decode_frame(f)
        completed = buf2.add_frame(frame)
    
    assert buf2.is_complete()
    assert buf2.get_reassembled_payload() == payload
    
    # Duplicates ignored
    buf3 = ReassemblyBuffer(total_chunks=3)
    buf3.add_frame(decode_frame(frames[0]))
    buf3.add_frame(decode_frame(frames[0]))  # Duplicate
    buf3.add_frame(decode_frame(frames[1]))
    buf3.add_frame(decode_frame(frames[2]))
    
    assert buf3.is_complete()
    assert buf3.received_count == 3


def test_reassembly_missing_chunks():
    """Test ReassemblyBuffer reports missing chunks."""
    payload = b"X" * 500
    frames = create_frames(payload)
    
    buf = ReassemblyBuffer(total_chunks=3)
    buf.add_frame(decode_frame(frames[0]))
    buf.add_frame(decode_frame(frames[2]))
    
    assert not buf.is_complete()
    assert buf.get_missing_chunks() == [1]


def test_empty_payload():
    """Test framing with empty payload."""
    frames = create_frames(b"")
    assert len(frames) == 1
    
    decoded = decode_frame(frames[0])
    assert decoded.payload == b""
    assert decoded.is_last_chunk