"""Tests for DSP module."""

import pytest
import numpy as np
from numpy.typing import NDArray

from echobaud.dsp import (
    raised_cosine_taper,
    apply_taper,
    generate_chirp,
    Modulator,
    Demodulator,
    goertzel_power,
    goertzel_demodulate_symbol,
    detect_preamble,
    modulate_packet,
    demodulate_packet,
    compute_goertzel_coeffs,
    GoertzelState,
)
from echobaud.config import (
    SYMBOL_SAMPLES,
    TAPER_SAMPLES,
    SAMPLE_RATE,
    AUDIBLE_PROFILE,
    ULTRASONIC_PROFILE,
    get_profile,
)


def test_raised_cosine_taper():
    """Test raised-cosine taper properties (0 to 1)."""
    taper = raised_cosine_taper(10)
    
    assert len(taper) == 10
    assert taper[0] == 0.0
    assert taper[-1] == 1.0
    # Not symmetric - goes 0 to 1 (fade in)
    # Fade out is created by reversing in apply_taper
    assert np.all(taper >= 0) and np.all(taper <= 1)
    # Monotonically increasing
    assert np.all(np.diff(taper) >= 0)


def test_apply_taper():
    """Test symbol taper application."""
    symbol = np.ones(SYMBOL_SAMPLES, dtype=np.float32)
    tapered = apply_taper(symbol)
    
    assert len(tapered) == SYMBOL_SAMPLES
    assert tapered[0] == 0.0
    assert tapered[-1] == 0.0
    assert tapered[TAPER_SAMPLES] == 1.0
    assert tapered[-TAPER_SAMPLES - 1] == 1.0
    # Middle should be unchanged
    mid = SYMBOL_SAMPLES // 2
    assert tapered[mid] == 1.0


def test_generate_chirp():
    """Test chirp preamble generation."""
    for profile in [AUDIBLE_PROFILE, ULTRASONIC_PROFILE]:
        chirp = generate_chirp(profile)
        
        expected_samples = int(profile.chirp_duration * SAMPLE_RATE)
        assert len(chirp) == expected_samples
        assert chirp.dtype == np.float32
        assert np.max(np.abs(chirp)) <= 1.0
        
        # Should start and end near zero (tapered)
        assert abs(chirp[0]) < 0.1
        assert abs(chirp[-1]) < 0.1


def test_modulator_creation():
    """Test modulator creation for both profiles."""
    for profile_name in ["audible", "ultrasonic"]:
        mod = Modulator.create(profile_name)
        
        assert mod.profile.name == profile_name
        assert mod.tones.shape == (4, SYMBOL_SAMPLES)
        assert mod.tone_frequencies == get_profile(profile_name).tones
        
        # Each tone should be tapered
        for tone in mod.tones:
            assert tone[0] == 0.0
            assert tone[-1] == 0.0


def test_modulate_bits():
    """Test bit modulation produces correct output length."""
    mod = Modulator.create("audible")
    
    # 1 byte = 4 symbols = 4 * 882 samples
    bits = b"\x00"  # 00 00 00 00
    audio = mod.modulate_bits(bits)
    assert len(audio) == 4 * SYMBOL_SAMPLES
    
    # 255 bytes = 1020 symbols
    bits = bytes(range(255))
    audio = mod.modulate_bits(bits)
    assert len(audio) == 1020 * SYMBOL_SAMPLES


def test_modulate_frame():
    """Test frame modulation."""
    mod = Modulator.create("audible")
    
    frame = bytes(range(255))
    audio = mod.modulate_frame(frame)
    
    expected = 255 * 4 * SYMBOL_SAMPLES
    assert len(audio) == expected


def test_generate_preamble():
    """Test preamble generation."""
    mod = Modulator.create("audible")
    preamble = mod.generate_preamble()
    
    expected = int(0.1 * SAMPLE_RATE)
    assert len(preamble) == expected


def test_generate_packet():
    """Test complete packet generation."""
    mod = Modulator.create("audible")
    frame = bytes(range(255))
    
    packet = mod.generate_packet(frame)
    
    preamble_len = int(0.1 * SAMPLE_RATE)
    frame_len = 255 * 4 * SYMBOL_SAMPLES
    assert len(packet) == preamble_len + frame_len


def test_demodulator_creation():
    """Test demodulator creation."""
    for profile_name in ["audible", "ultrasonic"]:
        demod = Demodulator.create(profile_name)
        
        assert demod.profile.name == profile_name
        assert len(demod.states) == 4
        assert len(demod.preamble) > 0
        assert demod.noise_floor > 0


def test_goertzel_coeffs():
    """Test Goertzel coefficient computation."""
    states = compute_goertzel_coeffs(AUDIBLE_PROFILE)
    
    assert len(states) == 4
    for state in states:
        assert isinstance(state, GoertzelState)
        assert state.k > 0
        assert state.coeff != 0
        assert state.scaling > 0


def test_goertzel_power():
    """Test Goertzel power computation."""
    states = compute_goertzel_coeffs(AUDIBLE_PROFILE)
    
    # Generate a pure tone at first frequency
    freq = AUDIBLE_PROFILE.tones[0]
    t = np.arange(SYMBOL_SAMPLES, dtype=np.float32) / SAMPLE_RATE
    tone = np.sin(2 * np.pi * freq * t).astype(np.float32)
    tone = apply_taper(tone)
    
    powers = [goertzel_power(tone, s) for s in states]
    
    # First tone should have highest power
    assert np.argmax(powers) == 0
    # Power should be significant
    assert powers[0] > 0.1


def test_goertzel_demodulate_symbol():
    """Test single symbol demodulation."""
    demod = Demodulator.create("audible")
    
    # Test each symbol
    for sym_idx in range(4):
        # Create pure tone for this symbol
        freq = AUDIBLE_PROFILE.tones[sym_idx]
        t = np.arange(SYMBOL_SAMPLES, dtype=np.float32) / SAMPLE_RATE
        tone = np.sin(2 * np.pi * freq * t).astype(np.float32)
        tone = apply_taper(tone)
        
        detected, conf = goertzel_demodulate_symbol(tone, demod.states, demod.noise_floor)
        
        assert detected == sym_idx, f"Expected symbol {sym_idx}, got {detected}"
        assert conf > 0.5, f"Low confidence: {conf}"


def test_detect_preamble():
    """Test preamble detection via cross-correlation."""
    demod = Demodulator.create("audible")
    preamble = demod.preamble
    
    # Create audio with preamble at known position
    offset = 1000
    audio = np.zeros(offset + len(preamble) + 100, dtype=np.float32)
    audio[offset:offset + len(preamble)] = preamble
    
    result = detect_preamble(audio, preamble, threshold=0.5)
    
    assert result is not None
    idx, corr = result
    assert idx == offset
    assert corr > 0.9


def test_detect_preamble_not_found():
    """Test preamble detection fails on noise."""
    demod = Demodulator.create("audible")
    
    # Random noise
    noise = np.random.normal(0, 0.1, 10000).astype(np.float32)
    
    result = detect_preamble(noise, demod.preamble, threshold=0.5)
    assert result is None


def test_modulate_demodulate_roundtrip():
    """Test full modulate/demodulate round-trip (clean)."""
    for profile_name in ["audible", "ultrasonic"]:
        frame = bytes(range(255))
        
        packet = modulate_packet(frame, profile_name)
        
        decoded, preamble_idx, corr = demodulate_packet(packet, profile_name)
        
        assert corr > 0.9, f"Low correlation: {corr}"
        assert decoded == frame, f"Round-trip failed for {profile_name}"


def test_modulate_demodulate_with_noise():
    """Test demodulation with AWGN at various SNR."""
    profile_name = "audible"
    frame = bytes(range(255))
    
    packet = modulate_packet(frame, profile_name)
    
    for snr_db in [20.0, 15.0, 10.0, 5.0]:
        signal_power = np.mean(packet ** 2)
        noise_power = signal_power / (10 ** (snr_db / 10))
        noise = np.random.normal(0, np.sqrt(noise_power), len(packet)).astype(np.float32)
        noisy = packet + noise
        
        decoded, preamble_idx, corr = demodulate_packet(noisy, profile_name)
        
        # Count errors before FEC
        errors = sum(1 for a, b in zip(decoded, frame) if a != b)
        
        print(f"  {snr_db} dB: {errors} errors, corr={corr:.3f}")
        
        # At 10 dB and above, should have <= 27 errors (RS correctable)
        if snr_db >= 10.0:
            assert errors <= 27, f"Too many errors at {snr_db} dB: {errors}"


def test_modulate_demodulate_with_attenuation_and_filter():
    """Test with attenuation and low-pass filter (simulating cheap hardware)."""
    profile_name = "audible"
    frame = bytes(range(255))
    
    packet = modulate_packet(frame, profile_name)
    
    # 50% attenuation
    attenuated = packet * 0.5
    
    # Mild low-pass filter (3-tap moving average)
    kernel = np.ones(3, dtype=np.float32) / 3.0
    filtered = np.convolve(attenuated, kernel, mode="same").astype(np.float32)
    
    # Add some noise
    noise = np.random.normal(0, 0.01, len(filtered)).astype(np.float32)
    test_audio = filtered + noise
    
    decoded, preamble_idx, corr = demodulate_packet(test_audio, profile_name)
    
    errors = sum(1 for a, b in zip(decoded, frame) if a != b)
    print(f"  Attenuated+filtered: {errors} errors, corr={corr:.3f}")
    
    assert errors <= 27, f"Too many errors with attenuation/filter: {errors}"


def test_demodulator_find_preamble():
    """Test Demodulator.find_preamble method."""
    demod = Demodulator.create("audible")
    frame = bytes(range(255))
    packet = modulate_packet(frame, "audible")
    
    result = demod.find_preamble(packet)
    assert result is not None
    idx, corr = result
    assert corr > 0.9


def test_demodulator_demodulate_from_preamble():
    """Test Demodulator.demodulate_from_preamble method."""
    demod = Demodulator.create("audible")
    frame = bytes(range(255))
    packet = modulate_packet(frame, "audible")
    
    result = demod.find_preamble(packet)
    assert result is not None
    preamble_idx, _ = result
    
    decoded = demod.demodulate_from_preamble(packet, preamble_idx)
    assert decoded == frame


def test_demodulator_insufficient_audio():
    """Test demodulator raises on insufficient audio after preamble."""
    demod = Demodulator.create("audible")
    preamble = demod.preamble
    
    # Only preamble, no frame data
    audio = np.concatenate([preamble, np.zeros(100, dtype=np.float32)])
    
    result = demod.find_preamble(audio)
    assert result is not None
    preamble_idx, _ = result
    
    with pytest.raises(ValueError, match="Insufficient audio"):
        demod.demodulate_from_preamble(audio, preamble_idx)