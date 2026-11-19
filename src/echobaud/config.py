"""
EchoBaud Modem Configuration

Defines all physical layer parameters, modem profiles, and constants.
All DSP math is deterministic and documented with explicit derivations.
"""

from dataclasses import dataclass
from typing import Final


# =============================================================================
# Global Constants
# =============================================================================

SAMPLE_RATE: Final[int] = 44100  # Hz, CD quality, widely supported
MAGIC_BYTES: Final[bytes] = b"\xEB\x90"  # Frame magic: 0xEB90
PROTOCOL_VERSION: Final[int] = 1  # Version 1, flags in lower bits
MAX_PAYLOAD_BYTES: Final[int] = 188  # Max payload per frame (pre-RS), fits in RS_K with 13-byte overhead
RS_N: Final[int] = 255  # Reed-Solomon codeword length (bytes)
RS_K: Final[int] = 201  # Reed-Solomon data length (bytes)
RS_PARITY: Final[int] = RS_N - RS_K  # 54 parity bytes (even), corrects 27 byte errors
CRC32_POLY: Final[int] = 0xEDB88320  # Standard CRC-32 (IEEE 802.3)

# -----------------------------------------------------------------------------
# Symbol Timing Derivation
# -----------------------------------------------------------------------------
# Target: Robust Goertzel demodulation on cheap laptop mics
# Minimum cycles for frequency resolution: ~10-15 cycles at lowest tone
# Audible lowest tone: 1200 Hz -> period = 0.833 ms
# 20 ms symbol = 24 cycles at 1200 Hz (excellent for Goertzel)
# Baud rate = 1 / 0.020 = 50 baud
# 4-FSK = 2 bits/symbol -> 100 bps raw bit rate
# -----------------------------------------------------------------------------

SYMBOL_DURATION: Final[float] = 0.020  # seconds (20 ms)
SYMBOL_SAMPLES: Final[int] = int(SAMPLE_RATE * SYMBOL_DURATION)  # 882 samples
BAUD_RATE: Final[float] = 1.0 / SYMBOL_DURATION  # 50 symbols/sec
RAW_BIT_RATE: Final[float] = BAUD_RATE * 2  # 100 bps (2 bits/symbol)

# Raised-cosine taper: 10% of symbol each edge = 2 ms = 88 samples
TAPER_RATIO: Final[float] = 0.10
TAPER_SAMPLES: Final[int] = int(SYMBOL_SAMPLES * TAPER_RATIO)  # 88 samples
assert TAPER_SAMPLES * 2 < SYMBOL_SAMPLES, "Taper too long for symbol"


# =============================================================================
# Modem Profiles
# =============================================================================

@dataclass(frozen=True, slots=True)
class ModemProfile:
    """Immutable modem profile with all physical-layer parameters."""
    name: str
    # 4-FSK tone frequencies in Hz [00, 01, 10, 11]
    tones: tuple[float, float, float, float]
    # Linear FM chirp preamble: (start_hz, end_hz, duration_sec)
    chirp_start: float
    chirp_end: float
    chirp_duration: float
    # Squelch threshold: normalized energy floor (0.0-1.0) for tone detection
    squelch_threshold: float
    # Correlation threshold for preamble detection (0.0-1.0)
    sync_threshold: float


# -----------------------------------------------------------------------------
# AUDIBLE Profile (Default)
# -----------------------------------------------------------------------------
# Tone selection criteria:
# 1. Within 1200-3400 Hz (telephony band, works on all laptop speakers/mics)
# 2. Minimum spacing >= 1/SYMBOL_DURATION = 50 Hz for orthogonality
# 3. We use ~700 Hz spacing for massive margin against Doppler/skew
# 4. Avoid harmonics: no tone is integer multiple of another
# 5. All tones > 1000 Hz to avoid DC/1/f noise, < 3400 Hz for mic response
# -----------------------------------------------------------------------------
# f0 = 1200 Hz  (00)
# f1 = 1900 Hz  (01)  diff = 700 Hz
# f2 = 2600 Hz  (10)  diff = 700 Hz
# f3 = 3300 Hz  (11)  diff = 700 Hz
# All pairwise differences >= 700 Hz >> 50 Hz minimum
# -----------------------------------------------------------------------------

AUDIBLE_PROFILE: Final[ModemProfile] = ModemProfile(
    name="audible",
    tones=(1200.0, 1900.0, 2600.0, 3300.0),
    chirp_start=1000.0,
    chirp_end=4000.0,
    chirp_duration=0.100,  # 100 ms preamble
    squelch_threshold=0.015,  # Adaptive noise floor multiplier
    sync_threshold=0.25,      # Normalized cross-correlation peak (lower for noisy channels)
)


# -----------------------------------------------------------------------------
# ULTRASONIC Profile (Experimental)
# -----------------------------------------------------------------------------
# Tone selection criteria:
# 1. Within 17500-19500 Hz (near Nyquist at 44.1 kHz, avoid aliasing)
# 2. Minimum spacing >= 50 Hz for orthogonality
# 3. We use ~667 Hz spacing for margin
# 4. All tones well above human hearing, below 22.05 kHz Nyquist
# -----------------------------------------------------------------------------
# f0 = 17500 Hz  (00)
# f1 = 18167 Hz  (01)  diff = 667 Hz
# f2 = 18833 Hz  (10)  diff = 666 Hz
# f3 = 19500 Hz  (11)  diff = 667 Hz
# -----------------------------------------------------------------------------

ULTRASONIC_PROFILE: Final[ModemProfile] = ModemProfile(
    name="ultrasonic",
    tones=(17500.0, 18167.0, 18833.0, 19500.0),
    chirp_start=17000.0,
    chirp_end=20000.0,
    chirp_duration=0.100,  # 100 ms preamble
    squelch_threshold=0.020,  # Higher: ultrasonic has more ambient noise
    sync_threshold=0.30,      # Slightly lower: harder to correlate at high freq
)


# Profile registry
PROFILES: Final[dict[str, ModemProfile]] = {
    "audible": AUDIBLE_PROFILE,
    "ultrasonic": ULTRASONIC_PROFILE,
}


def get_profile(name: str) -> ModemProfile:
    """Get modem profile by name, case-insensitive."""
    key = name.lower()
    if key not in PROFILES:
        raise ValueError(f"Unknown profile: {name}. Available: {list(PROFILES.keys())}")
    return PROFILES[key]


# =============================================================================
# Derived Constants (Computed at Import)
# =============================================================================

# Chirp samples per profile
AUDIBLE_CHIRP_SAMPLES: Final[int] = int(AUDIBLE_PROFILE.chirp_duration * SAMPLE_RATE)  # 4410
ULTRASONIC_CHIRP_SAMPLES: Final[int] = int(ULTRASONIC_PROFILE.chirp_duration * SAMPLE_RATE)  # 4410

# Goertzel coefficients precomputed per profile (k = N * f / fs)
# These are computed in dsp.py at runtime for the active profile

# Maximum frame size calculations
# Frame overhead (pre-RS): MAGIC(2) + VER(1) + CHUNK_ID(2) + TOTAL(2) + PAYLOAD_LEN(2) + CRC32(4) = 13 bytes
FRAME_OVERHEAD_BYTES: Final[int] = 13
MAX_FRAME_BYTES_PRE_RS: Final[int] = FRAME_OVERHEAD_BYTES + MAX_PAYLOAD_BYTES  # 200 bytes = RS_K
assert MAX_FRAME_BYTES_PRE_RS == RS_K, "Frame must exactly fill RS codeword"

# Post-RS frame size (always RS_N = 255 bytes)
MAX_FRAME_BYTES_POST_RS: Final[int] = RS_N

# Modulated audio duration per frame
# 255 bytes * 8 bits/byte / 2 bits/symbol = 1020 symbols
SYMBOLS_PER_FRAME: Final[int] = (RS_N * 8) // 2  # 1020 symbols
FRAME_DURATION_SEC: Final[float] = SYMBOLS_PER_FRAME * SYMBOL_DURATION  # 20.4 seconds
FRAME_SAMPLES: Final[int] = SYMBOLS_PER_FRAME * SYMBOL_SAMPLES  # 900,180 samples

# Preamble + frame total
TOTAL_FRAME_DURATION_SEC_AUDIBLE: Final[float] = AUDIBLE_PROFILE.chirp_duration + FRAME_DURATION_SEC
TOTAL_FRAME_DURATION_SEC_ULTRASONIC: Final[float] = ULTRASONIC_PROFILE.chirp_duration + FRAME_DURATION_SEC


# =============================================================================
# Audio I/O Constants
# =============================================================================

# sounddevice callback block size (affects latency)
AUDIO_BLOCK_SIZE: Final[int] = 1024  # ~23 ms at 44.1 kHz
# Input buffer for continuous capture (circular buffer)
INPUT_BUFFER_DURATION: Final[float] = 2.0  # seconds
INPUT_BUFFER_SAMPLES: Final[int] = int(SAMPLE_RATE * INPUT_BUFFER_DURATION)  # 88200

# WAV file format
WAV_SUBTYPE: Final[str] = "FLOAT"  # 32-bit float WAV
WAV_SAMPLE_WIDTH: Final[int] = 4  # bytes


# =============================================================================
# Validation
# =============================================================================

def validate_config() -> None:
    """Validate all configuration invariants."""
    # Tone orthogonality check
    for profile in PROFILES.values():
        tones = profile.tones
        min_spacing = min(abs(tones[i] - tones[j]) for i in range(4) for j in range(i+1, 4))
        required_spacing = 1.0 / SYMBOL_DURATION  # 50 Hz
        assert min_spacing >= required_spacing * 2, \
            f"{profile.name}: tone spacing {min_spacing:.1f} Hz < 2x required {required_spacing*2:.1f} Hz"
        
        # All tones within Nyquist
        assert all(t < SAMPLE_RATE / 2 for t in tones), \
            f"{profile.name}: tone exceeds Nyquist ({SAMPLE_RATE/2} Hz)"
        
        # Chirp within Nyquist
        assert profile.chirp_start < SAMPLE_RATE / 2 and profile.chirp_end < SAMPLE_RATE / 2, \
            f"{profile.name}: chirp exceeds Nyquist"
    
    # RS parameters
    assert RS_N == 255, "RS_N must be 255 for GF(256)"
    assert RS_K < RS_N, "RS_K must be < RS_N"
    assert (RS_N - RS_K) % 2 == 0, "RS parity must be even (for reedsolo)"
    assert MAX_FRAME_BYTES_PRE_RS <= RS_K, "Frame too large for RS codeword"
    
    # Symbol timing
    assert SYMBOL_SAMPLES > TAPER_SAMPLES * 2, "Symbol too short for tapers"
    assert SYMBOL_DURATION > 0.005, "Symbol duration too short for demodulation"
    
    print("Configuration validation passed.")


if __name__ == "__main__":
    validate_config()
    print(f"Sample Rate: {SAMPLE_RATE} Hz")
    print(f"Symbol Duration: {SYMBOL_DURATION*1000:.1f} ms ({SYMBOL_SAMPLES} samples)")
    print(f"Baud Rate: {BAUD_RATE:.1f} baud ({RAW_BIT_RATE:.0f} bps raw)")
    print(f"RS Codeword: ({RS_N}, {RS_K}) = {RS_PARITY} parity bytes")
    print(f"Max Payload: {MAX_PAYLOAD_BYTES} bytes")
    print(f"Frame Duration: {FRAME_DURATION_SEC:.1f} sec ({SYMBOLS_PER_FRAME} symbols)")
    print()
    for p in PROFILES.values():
        print(f"{p.name.upper()} Profile:")
        print(f"  Tones: {[f'{t:.0f}Hz' for t in p.tones]}")
        print(f"  Chirp: {p.chirp_start:.0f}-{p.chirp_end:.0f} Hz, {p.chirp_duration*1000:.0f} ms")
        print(f"  Squelch: {p.squelch_threshold}, Sync: {p.sync_threshold}")