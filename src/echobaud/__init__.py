"""
EchoBaud - Air-gapped acoustic modem for encrypted text/file transfer
via speakers and microphone (zero Wi-Fi, Bluetooth, internet, or cables).
"""

__version__ = "1.0.0"
__author__ = "EchoBaud Team"

from .config import (
    SAMPLE_RATE,
    SYMBOL_DURATION,
    BAUD_RATE,
    RAW_BIT_RATE,
    get_profile,
    AUDIBLE_PROFILE,
    ULTRASONIC_PROFILE,
)
from .crypto import create_crypto_context, WrongPassphraseError, TamperedCiphertextError
from .framing import (
    create_frames,
    decode_frame,
    prepare_file_transfer,
    parse_file_transfer,
    ReassemblyBuffer,
    Frame,
)
from .fec import rs_encode, rs_decode, UncorrectableError, fec_encode_frames, fec_decode_frames
from .dsp import (
    Modulator,
    Demodulator,
    modulate_packet,
    demodulate_packet,
)
from .audio_io import (
    play_audio,
    record_audio,
    write_wav,
    read_wav,
    AudioCapture,
)
from .cli import main

__all__ = [
    # Config
    "SAMPLE_RATE",
    "SYMBOL_DURATION",
    "BAUD_RATE",
    "RAW_BIT_RATE",
    "get_profile",
    "AUDIBLE_PROFILE",
    "ULTRASONIC_PROFILE",
    # Crypto
    "create_crypto_context",
    "WrongPassphraseError",
    "TamperedCiphertextError",
    # Framing
    "create_frames",
    "decode_frame",
    "prepare_file_transfer",
    "parse_file_transfer",
    "ReassemblyBuffer",
    "Frame",
    # FEC
    "rs_encode",
    "rs_decode",
    "UncorrectableError",
    "fec_encode_frames",
    "fec_decode_frames",
    # DSP
    "Modulator",
    "Demodulator",
    "modulate_packet",
    "demodulate_packet",
    # Audio I/O
    "play_audio",
    "record_audio",
    "write_wav",
    "read_wav",
    "AudioCapture",
    # CLI
    "main",
]