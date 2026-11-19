"""
EchoBaud Audio I/O Module

Handles live audio playback/capture via sounddevice (PortAudio)
and lossless WAV file read/write via scipy.io.wavfile.

All functions work with float32 NumPy arrays normalized to [-1, 1].
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray

try:
    import sounddevice as sd
    HAS_SOUNDDEVICE = True
except ImportError:
    HAS_SOUNDDEVICE = False
    sd = None  # type: ignore

from scipy.io import wavfile

from .config import (
    SAMPLE_RATE,
    AUDIO_BLOCK_SIZE,
    INPUT_BUFFER_SAMPLES,
    WAV_SUBTYPE,
    WAV_SAMPLE_WIDTH,
)


# =============================================================================
# Type Aliases
# =============================================================================

FloatArray = NDArray[np.float32]


# =============================================================================
# Exceptions
# =============================================================================

class AudioIOError(Exception):
    """Base exception for audio I/O errors."""
    pass


class NoAudioDeviceError(AudioIOError):
    """No suitable audio input/output device found."""
    pass


class PortAudioError(AudioIOError):
    """PortAudio/sounddevice error."""
    pass


# =============================================================================
# Device Management
# =============================================================================

def list_audio_devices() -> list[dict]:
    """
    List available audio devices.
    
    Returns:
        List of device info dicts with keys: index, name, max_input_channels, max_output_channels
    """
    if not HAS_SOUNDDEVICE:
        raise PortAudioError("sounddevice not available")
    
    devices = sd.query_devices()
    result = []
    for i, dev in enumerate(devices):
        result.append({
            "index": i,
            "name": dev["name"],
            "max_input_channels": dev["max_input_channels"],
            "max_output_channels": dev["max_output_channels"],
            "default_samplerate": dev["default_samplerate"],
        })
    return result


def get_default_devices() -> tuple[int | None, int | None]:
    """
    Get default input and output device indices.
    
    Returns:
        (input_device_index, output_device_index) or (None, None) if not available
    """
    if not HAS_SOUNDDEVICE:
        return None, None
    
    try:
        default_input = sd.default.device[0]
        default_output = sd.default.device[1]
        return default_input, default_output
    except Exception:
        return None, None


def validate_device(device_id: int | None, kind: str) -> int:
    """
    Validate and resolve device index.
    
    Args:
        device_id: Device index or None for default
        kind: "input" or "output"
        
    Returns:
        Validated device index
        
    Raises:
        NoAudioDeviceError: If no suitable device found
    """
    if not HAS_SOUNDDEVICE:
        raise PortAudioError("sounddevice module not available")
    
    devices = sd.query_devices()
    
    if device_id is not None:
        if device_id < 0 or device_id >= len(devices):
            raise NoAudioDeviceError(f"Invalid {kind} device index: {device_id}")
        dev = devices[device_id]
        max_ch = dev[f"max_{kind}_channels"]
        if max_ch < 1:
            raise NoAudioDeviceError(f"Device {device_id} has no {kind} channels")
        return device_id
    
    # Try default
    try:
        default_idx = sd.default.device[0 if kind == "input" else 1]
        if default_idx >= 0:
            dev = devices[default_idx]
            if dev[f"max_{kind}_channels"] >= 1:
                return default_idx
    except Exception:
        pass
    
    # Find first working device
    for i, dev in enumerate(devices):
        if dev[f"max_{kind}_channels"] >= 1:
            return i
    
    raise NoAudioDeviceError(f"No {kind} audio device found")


# =============================================================================
# WAV File I/O
# =============================================================================

def write_wav(filepath: Path | str, audio: FloatArray, sample_rate: int = SAMPLE_RATE) -> None:
    """
    Write float32 audio to WAV file (32-bit float PCM).
    
    Args:
        filepath: Output file path
        audio: Audio data as float32 array in range [-1, 1]
        sample_rate: Sample rate (default 44100)
    """
    audio = np.asarray(audio, dtype=np.float32)
    # scipy.io.wavfile.write expects float32 in [-1, 1] for 'FLOAT' subtype
    wavfile.write(str(filepath), sample_rate, audio)


def read_wav(filepath: Path | str) -> tuple[FloatArray, int]:
    """
    Read WAV file as float32 audio.
    
    Args:
        filepath: Input file path
        
    Returns:
        (audio_data, sample_rate)
    """
    sample_rate, data = wavfile.read(str(filepath))
    
    # Convert to float32 in [-1, 1]
    if data.dtype == np.float32:
        audio = data
    elif data.dtype == np.int16:
        audio = data.astype(np.float32) / 32768.0
    elif data.dtype == np.int32:
        audio = data.astype(np.float32) / 2147483648.0
    elif data.dtype == np.uint8:
        audio = (data.astype(np.float32) - 128.0) / 128.0
    else:
        # Try to convert
        audio = data.astype(np.float32)
        if audio.max() > 1.0 or audio.min() < -1.0:
            # Assume integer, normalize
            max_val = np.iinfo(data.dtype).max if np.issubdtype(data.dtype, np.integer) else 1.0
            audio = audio / max_val
    
    # Ensure mono (take first channel if stereo)
    if audio.ndim > 1:
        audio = audio[:, 0]
    
    return audio.astype(np.float32), sample_rate


# =============================================================================
# Live Playback
# =============================================================================

def play_audio(
    audio: FloatArray,
    sample_rate: int = SAMPLE_RATE,
    device: int | None = None,
    blocking: bool = True,
) -> None:
    """
    Play audio through output device.
    
    Args:
        audio: Audio data as float32 array
        sample_rate: Sample rate
        device: Output device index (None for default)
        blocking: If True, wait until playback finishes
        
    Raises:
        PortAudioError: If playback fails
    """
    if not HAS_SOUNDDEVICE:
        raise PortAudioError("sounddevice not available for playback")
    
    device = validate_device(device, "output")
    audio = np.asarray(audio, dtype=np.float32)
    
    try:
        if blocking:
            sd.play(audio, samplerate=sample_rate, device=device, blocking=True)
        else:
            sd.play(audio, samplerate=sample_rate, device=device)
    except sd.PortAudioError as e:
        raise PortAudioError(f"Playback failed: {e}") from e


def stop_playback() -> None:
    """Stop any ongoing playback."""
    if HAS_SOUNDDEVICE:
        sd.stop()


# =============================================================================
# Live Capture
# =============================================================================

class AudioCapture:
    """
    Continuous non-blocking audio capture using sounddevice callback.
    
    Maintains a circular buffer of recent audio for real-time processing.
    """
    
    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        device: int | None = None,
        block_size: int = AUDIO_BLOCK_SIZE,
        buffer_duration: float = 2.0,
    ):
        if not HAS_SOUNDDEVICE:
            raise PortAudioError("sounddevice not available for capture")
        
        self.sample_rate = sample_rate
        self.device = validate_device(device, "input")
        self.block_size = block_size
        self.buffer_size = int(sample_rate * buffer_duration)
        
        # Circular buffer
        self._buffer = np.zeros(self.buffer_size, dtype=np.float32)
        self._write_pos = 0
        self._stream: sd.InputStream | None = None
        self._overflow_count = 0
    
    def start(self) -> None:
        """Start capture stream."""
        if self._stream is not None:
            return
        
        def callback(indata: FloatArray, frames: int, time, status) -> None:
            if status:
                self._overflow_count += 1
            # indata shape: (frames, channels)
            data = indata[:, 0] if indata.ndim > 1 else indata
            self._write_buffer(data)
        
        self._stream = sd.InputStream(
            device=self.device,
            channels=1,
            samplerate=self.sample_rate,
            blocksize=self.block_size,
            dtype=np.float32,
            callback=callback,
        )
        self._stream.start()
    
    def stop(self) -> None:
        """Stop capture stream."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
    
    def _write_buffer(self, data: FloatArray) -> None:
        """Write data to circular buffer."""
        n = len(data)
        if n >= self.buffer_size:
            # Data larger than buffer, keep only last buffer_size samples
            self._buffer[:] = data[-self.buffer_size:]
            self._write_pos = 0
            return
        
        end_pos = self._write_pos + n
        if end_pos <= self.buffer_size:
            # Single contiguous write
            self._buffer[self._write_pos:end_pos] = data
        else:
            # Wrap around
            first_part = self.buffer_size - self._write_pos
            self._buffer[self._write_pos:] = data[:first_part]
            self._buffer[:n - first_part] = data[first_part:]
        
        self._write_pos = end_pos % self.buffer_size
    
    def get_recent(self, duration: float) -> FloatArray:
        """
        Get most recent audio samples.
        
        Args:
            duration: Seconds of audio to retrieve
            
        Returns:
            Audio array (newest samples at the end)
        """
        n_samples = min(int(duration * self.sample_rate), self.buffer_size)
        if n_samples == 0:
            return np.array([], dtype=np.float32)
        
        start = (self._write_pos - n_samples) % self.buffer_size
        if start < self._write_pos:
            return self._buffer[start:self._write_pos].copy()
        else:
            # Wrapped
            return np.concatenate([
                self._buffer[start:],
                self._buffer[:self._write_pos]
            ])
    
    def get_all(self) -> FloatArray:
        """Get entire buffer contents (oldest first)."""
        if self._write_pos == 0:
            return self._buffer.copy()
        return np.concatenate([
            self._buffer[self._write_pos:],
            self._buffer[:self._write_pos]
        ])
    
    def clear(self) -> None:
        """Clear buffer."""
        self._buffer.fill(0.0)
        self._write_pos = 0
    
    def __enter__(self) -> "AudioCapture":
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


def record_audio(
    duration: float,
    sample_rate: int = SAMPLE_RATE,
    device: int | None = None,
) -> FloatArray:
    """
    Record audio for a fixed duration (blocking).
    
    Args:
        duration: Recording duration in seconds
        sample_rate: Sample rate
        device: Input device index (None for default)
        
    Returns:
        Recorded audio as float32 array
    """
    if not HAS_SOUNDDEVICE:
        raise PortAudioError("sounddevice not available for recording")
    
    device = validate_device(device, "input")
    n_samples = int(duration * sample_rate)
    
    try:
        audio = sd.rec(
            n_samples,
            samplerate=sample_rate,
            channels=1,
            dtype=np.float32,
            device=device,
        )
        sd.wait()
        return audio[:, 0] if audio.ndim > 1 else audio
    except sd.PortAudioError as e:
        raise PortAudioError(f"Recording failed: {e}") from e


# =============================================================================
# Testing
# =============================================================================

def _test_audio_io() -> None:
    """Internal audio I/O tests (WAV only, no live devices)."""
    import tempfile
    
    # Test WAV round-trip
    test_audio = np.random.uniform(-0.5, 0.5, 44100).astype(np.float32)
    
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        temp_path = Path(f.name)
    
    try:
        write_wav(temp_path, test_audio)
        read_audio, sr = read_wav(temp_path)
        assert sr == SAMPLE_RATE
        assert np.allclose(read_audio, test_audio, atol=1e-6)
        
        # Test with int16 WAV
        int16_audio = (test_audio * 32767).astype(np.int16)
        wavfile.write(str(temp_path), SAMPLE_RATE, int16_audio)
        read_audio2, sr2 = read_wav(temp_path)
        assert sr2 == SAMPLE_RATE
        assert np.allclose(read_audio2, test_audio, atol=1e-4)
        
        print("WAV I/O tests passed.")
    finally:
        temp_path.unlink(missing_ok=True)
    
    # Test device listing (if sounddevice available)
    if HAS_SOUNDDEVICE:
        devices = list_audio_devices()
        print(f"Found {len(devices)} audio devices")
        for d in devices[:3]:
            print(f"  [{d['index']}] {d['name']} in:{d['max_input_channels']} out:{d['max_output_channels']}")
    
    print("Audio I/O tests passed.")


if __name__ == "__main__":
    _test_audio_io()