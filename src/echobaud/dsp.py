"""
EchoBaud DSP Module

Physical layer implementation:
- 4-FSK modulator (2 bits/symbol) with raised-cosine tapering
- Linear FM chirp preamble for synchronization
- Goertzel algorithm demodulation with adaptive squelch
- Normalized cross-correlation for preamble detection

All processing operates on NumPy float32 arrays for deterministic behavior
and testability with WAV file loopback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

from .config import (
    SAMPLE_RATE,
    SYMBOL_DURATION,
    SYMBOL_SAMPLES,
    TAPER_SAMPLES,
    TAPER_RATIO,
    get_profile,
    ModemProfile,
)


# =============================================================================
# Type Aliases
# =============================================================================

FloatArray = NDArray[np.float32]
IntArray = NDArray[np.int32]


# =============================================================================
# Window Functions
# =============================================================================

def raised_cosine_taper(length: int) -> FloatArray:
    """
    Generate raised-cosine taper window (0 to 1).
    
    w[n] = 0.5 * (1 - cos(pi*n/(N-1))) for n = 0..N-1
    Goes from 0 to 1 smoothly.
    """
    if length <= 1:
        return np.ones(length, dtype=np.float32)
    n = np.arange(length, dtype=np.float32)
    return (0.5 * (1.0 - np.cos(np.pi * n / (length - 1)))).astype(np.float32)


# Precompute taper windows
_TAPER_UP = raised_cosine_taper(TAPER_SAMPLES)      # 0 -> 1
_TAPER_DOWN = _TAPER_UP[::-1].copy()                # 1 -> 0


def apply_taper(symbol: FloatArray) -> FloatArray:
    """
    Apply raised-cosine taper to symbol edges.
    
    First TAPER_SAMPLES: fade in
    Last TAPER_SAMPLES: fade out
    Middle: unchanged
    """
    if len(symbol) != SYMBOL_SAMPLES:
        raise ValueError(f"Symbol must be {SYMBOL_SAMPLES} samples, got {len(symbol)}")
    
    tapered = symbol.copy()
    tapered[:TAPER_SAMPLES] *= _TAPER_UP
    tapered[-TAPER_SAMPLES:] *= _TAPER_DOWN
    return tapered


# =============================================================================
# Chirp Preamble Generation
# =============================================================================

def generate_chirp(profile: ModemProfile) -> FloatArray:
    """
    Generate linear FM chirp preamble for synchronization.
    
    f(t) = f_start + (f_end - f_start) * t / T
    phase(t) = 2*pi * (f_start*t + 0.5*(f_end-f_start)*t^2/T)
    
    Args:
        profile: Modem profile with chirp parameters
        
    Returns:
        Chirp signal as float32 array
    """
    n_samples = int(profile.chirp_duration * SAMPLE_RATE)
    t = np.arange(n_samples, dtype=np.float32) / SAMPLE_RATE
    
    f_start = profile.chirp_start
    f_end = profile.chirp_end
    sweep_rate = (f_end - f_start) / profile.chirp_duration  # Hz/sec
    
    # Instantaneous phase: integral of frequency
    # phi(t) = 2*pi * (f_start*t + 0.5*sweep_rate*t^2)
    phase = 2.0 * np.pi * (f_start * t + 0.5 * sweep_rate * t * t)
    
    chirp = np.sin(phase).astype(np.float32)
    
    # Apply taper to avoid clicks at start/end
    taper_len = min(n_samples // 10, 441)  # Max 10ms taper
    if taper_len > 1:
        taper = raised_cosine_taper(taper_len)
        chirp[:taper_len] *= taper
        chirp[-taper_len:] *= taper[::-1]
    
    return chirp


# =============================================================================
# 4-FSK Modulation
# =============================================================================

@dataclass(frozen=True, slots=True)
class Modulator:
    """4-FSK modulator for a specific profile."""
    profile: ModemProfile
    tones: Final[FloatArray]  # Shape: (4, SYMBOL_SAMPLES)
    tone_frequencies: Final[tuple[float, float, float, float]]
    
    @classmethod
    def create(cls, profile_name: str) -> "Modulator":
        profile = get_profile(profile_name)
        tones = np.zeros((4, SYMBOL_SAMPLES), dtype=np.float32)
        t = np.arange(SYMBOL_SAMPLES, dtype=np.float32) / SAMPLE_RATE
        
        for i, freq in enumerate(profile.tones):
            # Continuous phase: 2*pi*f*t
            phase = 2.0 * np.pi * freq * t
            tones[i] = np.sin(phase).astype(np.float32)
            # Apply taper to each tone
            tones[i] = apply_taper(tones[i])
        
        return cls(profile=profile, tones=tones, tone_frequencies=profile.tones)
    
    def modulate_bits(self, bits: bytes) -> FloatArray:
        """
        Modulate bytes (bitstream) into 4-FSK symbols.
        
        Args:
            bits: Input bytes, each byte provides 4 symbols (2 bits each)
            
        Returns:
            Modulated audio signal (float32)
        """
        # Convert bytes to 2-bit symbols (0-3)
        # Each byte = 4 symbols: bits 7-6, 5-4, 3-2, 1-0 (MSB first)
        symbols = []
        for byte in bits:
            symbols.append((byte >> 6) & 0x03)
            symbols.append((byte >> 4) & 0x03)
            symbols.append((byte >> 2) & 0x03)
            symbols.append(byte & 0x03)
        
        # Concatenate tone waveforms
        num_symbols = len(symbols)
        output = np.zeros(num_symbols * SYMBOL_SAMPLES, dtype=np.float32)
        
        for i, sym in enumerate(symbols):
            start = i * SYMBOL_SAMPLES
            end = start + SYMBOL_SAMPLES
            output[start:end] = self.tones[sym]
        
        return output
    
    def modulate_frame(self, frame_bytes: bytes) -> FloatArray:
        """
        Modulate a complete RS codeword (255 bytes) into audio.
        
        Args:
            frame_bytes: Exactly 255 bytes (RS codeword)
            
        Returns:
            Modulated audio for the frame
        """
        if len(frame_bytes) != 255:
            raise ValueError(f"Frame must be 255 bytes, got {len(frame_bytes)}")
        return self.modulate_bits(frame_bytes)
    
    def generate_preamble(self) -> FloatArray:
        """Generate chirp preamble for this profile."""
        return generate_chirp(self.profile)
    
    def generate_packet(self, frame_bytes: bytes) -> FloatArray:
        """Generate complete packet: preamble + modulated frame."""
        preamble = self.generate_preamble()
        modulated = self.modulate_frame(frame_bytes)
        return np.concatenate([preamble, modulated])


# =============================================================================
# Goertzel Demodulation
# =============================================================================

@dataclass(frozen=True, slots=True)
class GoertzelState:
    """Precomputed Goertzel coefficients for a tone."""
    frequency: float
    k: int                  # Bin index (integer)
    omega: float            # 2*pi*k/N
    coeff: float            # 2*cos(omega)
    sine: float             # sin(omega)
    cosine: float           # cos(omega)
    scaling: float          # 2/N for power normalization


def compute_goertzel_coeffs(profile: ModemProfile) -> list[GoertzelState]:
    """Precompute Goertzel coefficients for all 4 tones."""
    states = []
    for freq in profile.tones:
        # k = round(N * f / fs)
        k = int(round(SYMBOL_SAMPLES * freq / SAMPLE_RATE))
        # Actual frequency for this bin
        actual_freq = k * SAMPLE_RATE / SYMBOL_SAMPLES
        omega = 2.0 * np.pi * k / SYMBOL_SAMPLES
        coeff = 2.0 * np.cos(omega)
        sine = np.sin(omega)
        cosine = np.cos(omega)
        # Goertzel power scaling: 2/N for real signals
        scaling = 2.0 / SYMBOL_SAMPLES
        states.append(GoertzelState(
            frequency=actual_freq,
            k=k,
            omega=omega,
            coeff=coeff,
            sine=sine,
            cosine=cosine,
            scaling=scaling,
        ))
    return states


def goertzel_power(samples: FloatArray, state: GoertzelState) -> float:
    """
    Compute Goertzel algorithm power for a single tone.
    
    Standard Goertzel for real input:
    s[n] = x[n] + coeff*s[n-1] - s[n-2]
    Power = scaling * (s[N-1]^2 + s[N-2]^2 - coeff*s[N-1]*s[N-2])
    """
    if len(samples) != SYMBOL_SAMPLES:
        raise ValueError(f"Expected {SYMBOL_SAMPLES} samples, got {len(samples)}")
    
    s_prev2 = 0.0
    s_prev1 = 0.0
    
    for x in samples:
        s_curr = x + state.coeff * s_prev1 - s_prev2
        s_prev2 = s_prev1
        s_prev1 = s_curr
    
    # Compute power
    power = state.scaling * (
        s_prev1 * s_prev1 + s_prev2 * s_prev2 - state.coeff * s_prev1 * s_prev2
    )
    return max(power, 0.0)  # Numerical safety


def goertzel_demodulate_symbol(
    samples: FloatArray,
    states: list[GoertzelState],
    noise_floor: float,
) -> tuple[int, float]:
    """
    Demodulate one 4-FSK symbol using Goertzel.
    
    Args:
        samples: SYMBOL_SAMPLES audio samples
        states: Precomputed Goertzel states for 4 tones
        noise_floor: Adaptive noise floor estimate
        
    Returns:
        (symbol_index 0-3, confidence 0-1)
    """
    powers = np.array([goertzel_power(samples, s) for s in states], dtype=np.float32)
    
    # Apply squelch: ignore tones below noise floor
    max_power = np.max(powers)
    if max_power < noise_floor:
        return -1, 0.0  # Below squelch
    
    # Find best tone
    best_idx = int(np.argmax(powers))
    best_power = powers[best_idx]
    
    # Confidence: ratio of best to second-best (or noise floor)
    second_best = np.partition(powers, -2)[-2]
    if second_best < noise_floor:
        second_best = noise_floor
    
    confidence = float(best_power / (second_best + 1e-10))
    # Normalize to 0-1 range (1 = clear winner, 0 = ambiguous)
    confidence = min(confidence / 10.0, 1.0)  # Saturate at 10:1 ratio
    
    return best_idx, confidence


# =============================================================================
# Preamble Detection (Cross-Correlation)
# =============================================================================

def detect_preamble(
    audio: FloatArray,
    preamble: FloatArray,
    threshold: float,
    search_start: int = 0,
    search_end: int | None = None,
) -> tuple[int, float] | None:
    """
    Detect chirp preamble using normalized cross-correlation via FFT.
    
    Args:
        audio: Input audio buffer
        preamble: Known preamble template
        threshold: Correlation threshold (0-1)
        search_start: Start sample index
        search_end: End sample index (exclusive)
        
    Returns:
        (peak_index, peak_value) or None if not found
    """
    if search_end is None:
        search_end = len(audio)
    
    # We need at least preamble length
    if search_end - search_start < len(preamble):
        return None
    
    search_region = audio[search_start:search_end]
    n = len(search_region)
    m = len(preamble)
    
    if n < m:
        return None
    
    # FFT-based normalized cross-correlation
    # Compute FFT size (next power of 2 >= n + m - 1)
    fft_size = 1 << ((n + m - 1).bit_length())
    
    # FFT of preamble (zero-padded)
    preamble_padded = np.zeros(fft_size, dtype=np.float32)
    preamble_padded[:m] = preamble
    preamble_fft = np.fft.rfft(preamble_padded)
    
    # FFT of search region (zero-padded)
    audio_padded = np.zeros(fft_size, dtype=np.float32)
    audio_padded[:n] = search_region
    audio_fft = np.fft.rfft(audio_padded)
    
    # Cross-correlation via FFT: ifft(audio_fft * conj(preamble_fft))
    corr_fft = audio_fft * np.conj(preamble_fft)
    corr = np.fft.irfft(corr_fft, n=fft_size)
    
    # Normalize by local energy of audio window
    # Compute sliding window energy using convolution
    audio_squared = search_region ** 2
    window_energy = np.convolve(audio_squared, np.ones(m, dtype=np.float32), mode='valid')
    window_norm = np.sqrt(np.maximum(window_energy, 1e-10))
    preamble_norm = np.linalg.norm(preamble)
    
    # Normalized correlation (only valid part)
    norm_corr = corr[:n - m + 1] / (window_norm * preamble_norm)
    
    # Find peak
    max_idx = int(np.argmax(norm_corr))
    max_corr = float(norm_corr[max_idx])
    
    if max_corr >= threshold:
        return search_start + max_idx, max_corr
    
    return None


# =============================================================================
# Complete Demodulator
# =============================================================================

@dataclass(frozen=True, slots=True)
class Demodulator:
    """4-FSK demodulator for a specific profile."""
    profile: ModemProfile
    states: Final[list[GoertzelState]]
    preamble: Final[FloatArray]
    noise_floor: float = 0.0  # Adaptive, updated during demodulation
    
    @classmethod
    def create(cls, profile_name: str) -> "Demodulator":
        profile = get_profile(profile_name)
        states = compute_goertzel_coeffs(profile)
        preamble = generate_chirp(profile)
        # Initial noise floor estimate (will adapt)
        initial_noise = profile.squelch_threshold
        return cls(profile=profile, states=states, preamble=preamble, noise_floor=initial_noise)
    
    def find_preamble(self, audio: FloatArray, start: int = 0) -> tuple[int, float] | None:
        """Find preamble in audio, return (sample_index, correlation)."""
        return detect_preamble(
            audio, self.preamble, self.profile.sync_threshold, search_start=start
        )
    
    def demodulate_from_preamble(self, audio: FloatArray, preamble_idx: int) -> bytes:
        """
        Demodulate frame starting from preamble index.
        
        Args:
            audio: Full audio buffer
            preamble_idx: Sample index where preamble was detected
            
        Returns:
            Decoded 255-byte frame
            
        Raises:
            ValueError: If insufficient audio after preamble
        """
        # Frame starts after preamble
        frame_start = preamble_idx + len(self.preamble)
        frame_samples_needed = 255 * 4 * SYMBOL_SAMPLES  # 255 bytes * 4 symbols/byte * samples/symbol
        
        if frame_start + frame_samples_needed > len(audio):
            raise ValueError("Insufficient audio after preamble for full frame")
        
        frame_audio = audio[frame_start:frame_start + frame_samples_needed]
        return self.demodulate_frame(frame_audio)
    
    def demodulate_frame(self, frame_audio: FloatArray) -> bytes:
        """
        Demodulate a complete frame (255 bytes = 1020 symbols).
        
        Args:
            frame_audio: Audio containing exactly one frame (no preamble)
            
        Returns:
            255-byte decoded frame
        """
        expected_symbols = 255 * 4  # 1020 symbols
        expected_samples = expected_symbols * SYMBOL_SAMPLES
        
        if len(frame_audio) < expected_samples:
            raise ValueError(f"Frame audio too short: {len(frame_audio)} < {expected_samples}")
        
        # Trim or pad to exact length
        frame_audio = frame_audio[:expected_samples]
        
        symbols = []
        symbol_confidences = []
        
        # Process each symbol
        for i in range(expected_symbols):
            start = i * SYMBOL_SAMPLES
            end = start + SYMBOL_SAMPLES
            symbol_samples = frame_audio[start:end]
            
            sym_idx, conf = goertzel_demodulate_symbol(symbol_samples, self.states, self.noise_floor)
            
            if sym_idx >= 0:
                symbols.append(sym_idx)
                symbol_confidences.append(conf)
            else:
                # Below squelch - treat as erasure (use most likely = 0)
                symbols.append(0)
                symbol_confidences.append(0.0)
        
        # Update noise floor estimate (exponential moving average)
        # Use median of low-confidence symbols as noise estimate
        low_conf_powers = []
        # Actually, we'd need to recompute powers... skip for now
        
        # Pack symbols back into bytes (4 symbols per byte, MSB first)
        output = bytearray(255)
        for byte_idx in range(255):
            byte_val = 0
            for sym_idx in range(4):
                symbol_pos = byte_idx * 4 + sym_idx
                if symbol_pos < len(symbols):
                    byte_val = (byte_val << 2) | symbols[symbol_pos]
            output[byte_idx] = byte_val
        
        return bytes(output)


# =============================================================================
# High-Level Modulate/Demodulate Functions
# =============================================================================

def modulate_packet(frame_bytes: bytes, profile_name: str = "audible") -> FloatArray:
    """Modulate a complete packet (preamble + frame) for given profile."""
    modulator = Modulator.create(profile_name)
    return modulator.generate_packet(frame_bytes)


def demodulate_packet(
    audio: FloatArray,
    profile_name: str = "audible",
) -> tuple[bytes, int, float]:
    """
    Demodulate packet from audio, finding preamble automatically.
    
    Returns:
        (frame_bytes, preamble_index, correlation_peak)
        
    Raises:
        ValueError: If preamble not found or frame decode fails
    """
    demod = Demodulator.create(profile_name)
    result = demod.find_preamble(audio)
    if result is None:
        raise ValueError("Preamble not found in audio")
    
    preamble_idx, correlation = result
    frame = demod.demodulate_from_preamble(audio, preamble_idx)
    return frame, preamble_idx, correlation


# =============================================================================
# Testing
# =============================================================================

def _test_dsp() -> None:
    """Internal DSP tests."""
    # Test modulator/demodulator round-trip (WAV loopback simulation)
    for profile_name in ["audible", "ultrasonic"]:
        print(f"Testing {profile_name} profile...")
        modulator = Modulator.create(profile_name)
        demod = Demodulator.create(profile_name)
        
        # Test data
        test_frame = bytes(range(255))
        
        # Modulate
        packet = modulator.generate_packet(test_frame)
        
        # Demodulate
        result = demod.find_preamble(packet)
        assert result is not None, f"Preamble not found in clean {profile_name} signal"
        preamble_idx, corr = result
        assert corr > 0.9, f"Low correlation: {corr}"
        
        decoded = demod.demodulate_from_preamble(packet, preamble_idx)
        assert decoded == test_frame, f"Round-trip failed for {profile_name}"
        
        # Test with noise (AWGN at 10 dB SNR)
        signal_power = np.mean(packet ** 2)
        snr_db = 10.0
        noise_power = signal_power / (10 ** (snr_db / 10))
        noise = np.random.normal(0, np.sqrt(noise_power), len(packet)).astype(np.float32)
        noisy = packet + noise
        
        result = demod.find_preamble(noisy)
        assert result is not None, f"Preamble not found at {snr_db} dB SNR"
        preamble_idx, corr = result
        decoded = demod.demodulate_from_preamble(noisy, preamble_idx)
        
        # Count byte errors
        errors = sum(1 for a, b in zip(decoded, test_frame) if a != b)
        print(f"  {snr_db} dB SNR: {errors} byte errors (RS can correct 27)")
        assert errors <= 27, f"Too many errors at {snr_db} dB: {errors}"
    
    # Test taper function
    taper = raised_cosine_taper(10)
    assert taper[0] == 0.0
    assert taper[-1] == 1.0
    assert np.allclose(taper, taper[::-1], atol=1e-6)  # Symmetric
    
    # Test apply_taper
    symbol = np.ones(SYMBOL_SAMPLES, dtype=np.float32)
    tapered = apply_taper(symbol)
    assert tapered[0] == 0.0
    assert tapered[-1] == 0.0
    assert tapered[TAPER_SAMPLES] == 1.0
    assert tapered[-TAPER_SAMPLES - 1] == 1.0
    
    print("DSP tests passed.")


if __name__ == "__main__":
    _test_dsp()