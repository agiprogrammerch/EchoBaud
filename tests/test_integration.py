"""Integration tests for full EchoBaud pipeline."""

import pytest
import numpy as np
from pathlib import Path
import tempfile

from echobaud import (
    create_crypto_context,
    create_frames,
    fec_encode_frames,
    fec_decode_frames,
    modulate_packet,
    demodulate_packet,
    write_wav,
    read_wav,
    prepare_file_transfer,
    parse_file_transfer,
    ReassemblyBuffer,
    decode_frame,
    rs_decode,
    Demodulator,
)
from echobaud.config import RS_N, RS_K


def test_text_roundtrip_wav_loopback():
    """Test complete text send/receive via WAV loopback."""
    text = "Hello, EchoBaud! This is a test message with unicode: 🎉 中文"
    data = text.encode("utf-8")
    
    # Send path
    frames = create_frames(data, is_encrypted=False, is_file_transfer=False)
    codewords = fec_encode_frames(frames)
    
    packets = [modulate_packet(cw, "audible") for cw in codewords]
    
    # Concatenate with gap
    gap = np.zeros(int(0.05 * 44100), dtype=np.float32)
    full_audio = np.concatenate([p for i, p in enumerate(packets) for p in ([p] + ([gap] if i < len(packets) - 1 else []))])
    
    # Normalize
    max_val = np.max(np.abs(full_audio))
    if max_val > 0.95:
        full_audio = full_audio * (0.95 / max_val)
    
    # Write and read WAV (simulated acoustic channel)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        temp_path = Path(f.name)
    
    try:
        write_wav(temp_path, full_audio)
        received_audio, sr = read_wav(temp_path)
        assert sr == 44100
        
        # Receive path - demodulate each packet
        # For simplicity, demodulate the concatenated audio
        # In reality, we'd detect each preamble
        from echobaud.dsp import Demodulator
        demod = Demodulator.create("audible")
        
        search_pos = 0
        decoded_frames = []
        
        while True:
            result = demod.find_preamble(received_audio, search_pos)
            if result is None:
                break
            
            preamble_idx, corr = result
            try:
                frame = demod.demodulate_from_preamble(received_audio, preamble_idx)
                decoded_frames.append(frame)
                search_pos = preamble_idx + len(demod.preamble) + 255 * 4 * 882
            except Exception:
                search_pos = preamble_idx + 1
        
        assert len(decoded_frames) == 1
        
        # FEC decode
        decoded_codewords = fec_decode_frames(decoded_frames)
        
        # Deframe
        frame = decode_frame(decoded_codewords[0])
        payload = frame.payload
        
        # Verify
        assert payload == data
        decoded_text = payload.decode("utf-8")
        assert decoded_text == text
        
    finally:
        temp_path.unlink(missing_ok=True)


def test_encrypted_text_roundtrip():
    """Test encrypted text send/receive."""
    text = "Secret message"
    passphrase = "my-secret-passphrase"
    data = text.encode("utf-8")
    
    # Encrypt
    crypto = create_crypto_context(passphrase)
    encrypted = crypto.encrypt(data)
    
    # Frame
    frames = create_frames(encrypted, is_encrypted=True, is_file_transfer=False)
    codewords = fec_encode_frames(frames)
    
    # Modulate
    packet = modulate_packet(codewords[0], "audible")
    
    # Demodulate
    decoded_frame, _, _ = demodulate_packet(packet, "audible")
    
    # FEC decode
    corrected = rs_decode(decoded_frame)
    
    # Deframe
    frame = decode_frame(corrected)
    assert frame.is_encrypted
    
    # Decrypt
    decrypted = crypto.decrypt(frame.payload)
    assert decrypted == data


def test_wrong_passphrase_rejected():
    """Test wrong passphrase is cleanly rejected."""
    text = "Secret"
    data = text.encode("utf-8")
    
    crypto = create_crypto_context("correct")
    encrypted = crypto.encrypt(data)
    
    frames = create_frames(encrypted, is_encrypted=True)
    codewords = fec_encode_frames(frames)
    packet = modulate_packet(codewords[0], "audible")
    
    decoded_frame, _, _ = demodulate_packet(packet, "audible")
    corrected = rs_decode(decoded_frame)
    frame = decode_frame(corrected)
    
    crypto_wrong = create_crypto_context("wrong")
    with pytest.raises(Exception) as exc_info:
        crypto_wrong.decrypt(frame.payload)
    
    assert "Wrong passphrase" in str(exc_info.value) or "corrupted" in str(exc_info.value)


def test_file_transfer_roundtrip():
    """Test file transfer with filename preservation."""
    file_content = b"This is file content\nWith multiple lines\nAnd binary: \x00\xff\xfe"
    
    with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".bin") as f:
        f.write(file_content)
        temp_path = Path(f.name)
    
    try:
        # Prepare file transfer
        payload, filename = prepare_file_transfer(temp_path)
        assert filename.endswith(".bin")
        
        # Frame
        frames = create_frames(payload, is_encrypted=False, is_file_transfer=True)
        codewords = fec_encode_frames(frames)
        
        # Modulate
        packet = modulate_packet(codewords[0], "audible")
        
        # Demodulate
        decoded_frame, _, _ = demodulate_packet(packet, "audible")
        corrected = rs_decode(decoded_frame)
        
        # Deframe
        frame = decode_frame(corrected)
        assert frame.is_file_transfer
        
        # Parse file transfer
        parsed_filename, parsed_content = parse_file_transfer(frame.payload)
        
        assert parsed_filename == filename
        assert parsed_content == file_content
        
    finally:
        temp_path.unlink(missing_ok=True)


def test_multi_chunk_file_transfer():
    """Test multi-chunk file transfer with reassembly."""
    # Create file larger than one frame (187 bytes payload)
    file_content = b"X" * 500  # Will need 3 chunks
    
    with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".dat") as f:
        f.write(file_content)
        temp_path = Path(f.name)
    
    try:
        payload, filename = prepare_file_transfer(temp_path)
        
        frames = create_frames(payload, is_file_transfer=True)
        assert len(frames) == 3
        
        codewords = fec_encode_frames(frames)
        packets = [modulate_packet(cw, "audible") for cw in codewords]
        
        # Demodulate all
        demod = Demodulator.create("audible")
        decoded_codewords = []
        
        for packet in packets:
            result = demod.find_preamble(packet)
            assert result is not None
            preamble_idx, _ = result
            frame = demod.demodulate_from_preamble(packet, preamble_idx)
            decoded_codewords.append(frame)
        
        # FEC decode
        frames_decoded = fec_decode_frames(decoded_codewords)
        
        # Reassemble
        buf = ReassemblyBuffer(total_chunks=3)
        for f in frames_decoded:
            frame = decode_frame(f)
            buf.add_frame(frame)
        
        assert buf.is_complete()
        reassembled = buf.get_reassembled_payload()
        
        # Parse file transfer
        parsed_filename, parsed_content = parse_file_transfer(reassembled)
        
        assert parsed_filename == filename
        assert parsed_content == file_content
        
    finally:
        temp_path.unlink(missing_ok=True)


def test_out_of_order_chunk_reassembly():
    """Test reassembly handles out-of-order chunks."""
    payload = b"Y" * 400  # 3 chunks
    frames = create_frames(payload, is_file_transfer=True)
    codewords = fec_encode_frames(frames)
    
    # Shuffle
    import random
    shuffled = codewords.copy()
    random.shuffle(shuffled)
    
    buf = ReassemblyBuffer(total_chunks=3)
    for cw in shuffled:
        # Simulate FEC decode + deframe
        corrected = rs_decode(cw)
        frame = decode_frame(corrected)
        buf.add_frame(frame)
    
    assert buf.is_complete()
    assert buf.get_reassembled_payload() == payload


def test_duplicate_chunk_handling():
    """Test reassembly ignores duplicate chunks."""
    payload = b"Z" * 400
    frames = create_frames(payload)
    codewords = fec_encode_frames(frames)
    
    buf = ReassemblyBuffer(total_chunks=3)
    
    # Add first chunk twice
    corrected = rs_decode(codewords[0])
    frame = decode_frame(corrected)
    buf.add_frame(frame)
    buf.add_frame(frame)  # Duplicate
    
    # Add remaining
    for cw in codewords[1:]:
        corrected = rs_decode(cw)
        frame = decode_frame(corrected)
        buf.add_frame(frame)
    
    assert buf.is_complete()
    assert buf.received_count == 3  # Duplicate not counted
    assert buf.get_reassembled_payload() == payload


def test_ultrasonic_profile_roundtrip():
    """Test ultrasonic profile works (simulated)."""
    data = b"Ultrasonic test"
    
    frames = create_frames(data)
    codewords = fec_encode_frames(frames)
    
    packet = modulate_packet(codewords[0], "ultrasonic")
    decoded_frame, _, corr = demodulate_packet(packet, "ultrasonic")
    
    assert corr > 0.9
    
    corrected = rs_decode(decoded_frame)
    frame = decode_frame(corrected)
    assert frame.payload == data


def test_noisy_acoustic_loopback():
    """Test with simulated noisy acoustic channel."""
    # Use fixed seed for reproducibility
    np.random.seed(42)
    
    data = b"Noise test " * 10  # ~100 bytes
    
    frames = create_frames(data)
    codewords = fec_encode_frames(frames)
    packet = modulate_packet(codewords[0], "audible")
    
    # Simulate acoustic channel:
    # 1. AWGN at 10 dB SNR
    signal_power = np.mean(packet ** 2)
    noise_power = signal_power / (10 ** (10.0 / 10))
    noise = np.random.normal(0, np.sqrt(noise_power), len(packet)).astype(np.float32)
    noisy = packet + noise
    
    # 2. 50% attenuation
    attenuated = noisy * 0.5
    
    # 3. Mild low-pass filter (cheap speaker/mic)
    kernel = np.ones(3, dtype=np.float32) / 3.0
    filtered = np.convolve(attenuated, kernel, mode="same").astype(np.float32)
    
    # 4. Random leading silence (up to 100ms)
    offset = np.random.randint(0, 4410)
    test_audio = np.concatenate([
        np.zeros(offset, dtype=np.float32),
        filtered,
        np.zeros(4410, dtype=np.float32),  # Trailing silence
    ])
    
    # Demodulate
    decoded_frame, preamble_idx, corr = demodulate_packet(test_audio, "audible")
    
    # FEC decode
    corrected = rs_decode(decoded_frame)
    frame = decode_frame(corrected)
    
    assert frame.payload == data
    assert corr > 0.3  # Correlation should still be decent (low due to heavy noise)


def test_stress_multiple_packets():
    """Test multiple packets in sequence."""
    messages = [
        b"First message",
        b"Second message is longer",
        b"Third",
    ]
    
    all_packets = []
    for msg in messages:
        frames = create_frames(msg)
        codewords = fec_encode_frames(frames)
        for cw in codewords:
            all_packets.append(modulate_packet(cw, "audible"))
    
    # Concatenate
    gap = np.zeros(int(0.05 * 44100), dtype=np.float32)
    full_audio = np.concatenate([
        p for i, p in enumerate(all_packets)
        for p in ([p] + ([gap] if i < len(all_packets) - 1 else []))
    ])
    
    # Demodulate all
    demod = Demodulator.create("audible")
    frame_samples = 255 * 4 * 882  # 900,180
    gap_samples = int(0.05 * 44100)  # 2205
    search_pos = 0
    received = []
    
    while True:
        result = demod.find_preamble(full_audio, search_pos)
        if result is None:
            break
        preamble_idx, _ = result
        try:
            frame = demod.demodulate_from_preamble(full_audio, preamble_idx)
            received.append(frame)
            # Next preamble starts after: preamble + frame + gap
            search_pos = preamble_idx + len(demod.preamble) + frame_samples + gap_samples
        except Exception:
            search_pos = preamble_idx + 1
    
    assert len(received) == len(messages)
    
    # Verify each
    for i, (msg, frame) in enumerate(zip(messages, received)):
        corrected = rs_decode(frame)
        decoded = decode_frame(corrected)
        assert decoded.payload == msg, f"Message {i} mismatch"