"""Tests for audio_io module."""

import pytest
import numpy as np
from pathlib import Path
import tempfile

from echobaud.audio_io import (
    write_wav,
    read_wav,
    list_audio_devices,
    get_default_devices,
    validate_device,
    play_audio,
    stop_playback,
    record_audio,
    AudioCapture,
    HAS_SOUNDDEVICE,
    PortAudioError,
    NoAudioDeviceError,
)
from echobaud.config import SAMPLE_RATE


def test_write_read_wav_float32():
    """Test WAV write/read round-trip with float32."""
    audio = np.random.uniform(-0.5, 0.5, 44100).astype(np.float32)
    
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        temp_path = Path(f.name)
    
    try:
        write_wav(temp_path, audio)
        read_audio, sr = read_wav(temp_path)
        
        assert sr == SAMPLE_RATE
        assert read_audio.dtype == np.float32
        assert len(read_audio) == len(audio)
        assert np.allclose(read_audio, audio, atol=1e-6)
    finally:
        temp_path.unlink(missing_ok=True)


def test_write_read_wav_int16():
    """Test reading int16 WAV files."""
    audio = np.random.uniform(-0.5, 0.5, 44100).astype(np.float32)
    int16_audio = (audio * 32767).astype(np.int16)
    
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        temp_path = Path(f.name)
    
    try:
        # Write as int16 using scipy directly
        from scipy.io import wavfile
        wavfile.write(str(temp_path), SAMPLE_RATE, int16_audio)
        
        read_audio, sr = read_wav(temp_path)
        
        assert sr == SAMPLE_RATE
        assert read_audio.dtype == np.float32
        assert np.allclose(read_audio, audio, atol=1e-4)
    finally:
        temp_path.unlink(missing_ok=True)


def test_write_read_wav_stereo():
    """Test reading stereo WAV (should take first channel)."""
    audio_l = np.random.uniform(-0.5, 0.5, 44100).astype(np.float32)
    audio_r = np.random.uniform(-0.5, 0.5, 44100).astype(np.float32)
    stereo = np.column_stack([audio_l, audio_r])
    
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        temp_path = Path(f.name)
    
    try:
        from scipy.io import wavfile
        wavfile.write(str(temp_path), SAMPLE_RATE, stereo)
        
        read_audio, sr = read_wav(temp_path)
        
        assert sr == SAMPLE_RATE
        assert read_audio.ndim == 1
        assert len(read_audio) == len(audio_l)
        assert np.allclose(read_audio, audio_l, atol=1e-6)
    finally:
        temp_path.unlink(missing_ok=True)


def test_read_wav_normalizes_integer():
    """Test that integer WAV formats are normalized to [-1, 1]."""
    # Test uint8
    audio_uint8 = np.random.randint(0, 256, 44100, dtype=np.uint8)
    
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        temp_path = Path(f.name)
    
    try:
        from scipy.io import wavfile
        wavfile.write(str(temp_path), SAMPLE_RATE, audio_uint8)
        
        read_audio, sr = read_wav(temp_path)
        
        assert read_audio.dtype == np.float32
        assert np.max(read_audio) <= 1.0
        assert np.min(read_audio) >= -1.0
    finally:
        temp_path.unlink(missing_ok=True)


def test_list_audio_devices():
    """Test device listing (if sounddevice available)."""
    if HAS_SOUNDDEVICE:
        devices = list_audio_devices()
        assert isinstance(devices, list)
        for dev in devices:
            assert "index" in dev
            assert "name" in dev
            assert "max_input_channels" in dev
            assert "max_output_channels" in dev
    else:
        with pytest.raises(PortAudioError):
            list_audio_devices()


def test_get_default_devices():
    """Test default device retrieval."""
    if HAS_SOUNDDEVICE:
        input_dev, output_dev = get_default_devices()
        # May be None if no defaults set
        assert input_dev is None or isinstance(input_dev, int)
        assert output_dev is None or isinstance(output_dev, int)
    else:
        input_dev, output_dev = get_default_devices()
        assert input_dev is None
        assert output_dev is None


def test_validate_device():
    """Test device validation."""
    if HAS_SOUNDDEVICE:
        devices = list_audio_devices()
        if devices:
            # Test valid input device
            input_devs = [d for d in devices if d["max_input_channels"] > 0]
            if input_devs:
                idx = validate_device(input_devs[0]["index"], "input")
                assert idx == input_devs[0]["index"]
            
            # Test valid output device
            output_devs = [d for d in devices if d["max_output_channels"] > 0]
            if output_devs:
                idx = validate_device(output_devs[0]["index"], "output")
                assert idx == output_devs[0]["index"]
            
            # Test invalid index
            with pytest.raises(NoAudioDeviceError):
                validate_device(9999, "input")
    else:
        with pytest.raises(PortAudioError):
            validate_device(None, "input")


def test_audio_capture_context_manager():
    """Test AudioCapture as context manager (if sounddevice available)."""
    if not HAS_SOUNDDEVICE:
        pytest.skip("sounddevice not available")
    
    try:
        with AudioCapture() as capture:
            assert capture._stream is not None
            assert capture._stream.active
        # Should be stopped after exit
    except PortAudioError:
        pytest.skip("No audio input device available")


def test_audio_capture_get_recent():
    """Test AudioCapture.get_recent()."""
    if not HAS_SOUNDDEVICE:
        pytest.skip("sounddevice not available")
    
    try:
        with AudioCapture(buffer_duration=0.5) as capture:
            import time
            time.sleep(0.1)
            recent = capture.get_recent(0.05)
            assert len(recent) > 0
            assert len(recent) <= int(0.05 * SAMPLE_RATE)
    except PortAudioError:
        pytest.skip("No audio input device available")


def test_play_audio_requires_sounddevice():
    """Test play_audio raises without sounddevice."""
    if not HAS_SOUNDDEVICE:
        with pytest.raises(PortAudioError):
            play_audio(np.zeros(100, dtype=np.float32))


def test_record_audio_requires_sounddevice():
    """Test record_audio raises without sounddevice."""
    if not HAS_SOUNDDEVICE:
        with pytest.raises(PortAudioError):
            record_audio(1.0)