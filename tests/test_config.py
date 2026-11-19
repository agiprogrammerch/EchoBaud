"""Tests for config module."""

import pytest

from echobaud.config import (
    SAMPLE_RATE,
    SYMBOL_DURATION,
    SYMBOL_SAMPLES,
    BAUD_RATE,
    RAW_BIT_RATE,
    TAPER_SAMPLES,
    TAPER_RATIO,
    MAX_PAYLOAD_BYTES,
    RS_N,
    RS_K,
    RS_PARITY,
    FRAME_OVERHEAD_BYTES,
    MAX_FRAME_BYTES_PRE_RS,
    AUDIBLE_PROFILE,
    ULTRASONIC_PROFILE,
    PROFILES,
    get_profile,
    validate_config,
)


def test_global_constants():
    """Test global constant values."""
    assert SAMPLE_RATE == 44100
    assert SYMBOL_DURATION == 0.020
    assert SYMBOL_SAMPLES == 882
    assert BAUD_RATE == 50.0
    assert RAW_BIT_RATE == 100.0
    assert TAPER_RATIO == 0.10
    assert TAPER_SAMPLES == 88
    assert MAX_PAYLOAD_BYTES == 188
    assert RS_N == 255
    assert RS_K == 201
    assert RS_PARITY == 54
    assert FRAME_OVERHEAD_BYTES == 13
    assert MAX_FRAME_BYTES_PRE_RS == 201


def test_audible_profile():
    """Test audible profile parameters."""
    p = AUDIBLE_PROFILE
    assert p.name == "audible"
    assert p.tones == (1200.0, 1900.0, 2600.0, 3300.0)
    assert p.chirp_start == 1000.0
    assert p.chirp_end == 4000.0
    assert p.chirp_duration == 0.100
    assert p.squelch_threshold == 0.015
    assert p.sync_threshold == 0.25


def test_ultrasonic_profile():
    """Test ultrasonic profile parameters."""
    p = ULTRASONIC_PROFILE
    assert p.name == "ultrasonic"
    assert p.tones == (17500.0, 18167.0, 18833.0, 19500.0)
    assert p.chirp_start == 17000.0
    assert p.chirp_end == 20000.0
    assert p.chirp_duration == 0.100
    assert p.squelch_threshold == 0.020
    assert p.sync_threshold == 0.30


def test_get_profile():
    """Test profile lookup."""
    assert get_profile("audible") == AUDIBLE_PROFILE
    assert get_profile("AUDIBLE") == AUDIBLE_PROFILE
    assert get_profile("ultrasonic") == ULTRASONIC_PROFILE
    
    with pytest.raises(ValueError):
        get_profile("invalid")


def test_tone_orthogonality():
    """Test that tone spacing exceeds minimum for orthogonality."""
    min_spacing = 1.0 / SYMBOL_DURATION  # 50 Hz
    
    for profile in PROFILES.values():
        tones = profile.tones
        for i in range(4):
            for j in range(i + 1, 4):
                spacing = abs(tones[i] - tones[j])
                assert spacing >= min_spacing * 2, \
                    f"{profile.name}: tones {i},{j} spacing {spacing:.1f} Hz < 2x minimum {min_spacing*2:.1f} Hz"


def test_tones_within_nyquist():
    """Test all tones are below Nyquist frequency."""
    nyquist = SAMPLE_RATE / 2
    
    for profile in PROFILES.values():
        for tone in profile.tones:
            assert tone < nyquist, f"{profile.name}: tone {tone} Hz exceeds Nyquist {nyquist} Hz"
        
        assert profile.chirp_start < nyquist
        assert profile.chirp_end < nyquist


def test_validate_config():
    """Test configuration validation passes."""
    validate_config()  # Should not raise


def test_profiles_dict():
    """Test PROFILES registry."""
    assert "audible" in PROFILES
    assert "ultrasonic" in PROFILES
    assert len(PROFILES) == 2