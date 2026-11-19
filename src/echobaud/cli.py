"""
EchoBaud CLI Module

Command-line interface for the acoustic modem.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray

from .config import SAMPLE_RATE, get_profile, PROFILES
from .crypto import create_crypto_context, WrongPassphraseError
from .framing import (
    create_frames,
    prepare_file_transfer,
    parse_file_transfer,
    ReassemblyBuffer,
    sanitize_filename,
)
from .fec import fec_encode_frames, fec_decode_frames, UncorrectableError
from .dsp import modulate_packet, demodulate_packet
from .audio_io import (
    play_audio,
    record_audio,
    write_wav,
    read_wav,
    AudioCapture,
    HAS_SOUNDDEVICE,
    PortAudioError,
    NoAudioDeviceError,
)


# =============================================================================
# Constants
# =============================================================================

DEFAULT_OUT_DIR: Final[Path] = Path("./received")
BENCHMARK_SNR_LEVELS: Final[list[float]] = [20.0, 15.0, 10.0, 5.0, 0.0, -5.0]


# =============================================================================
# Core Send/Receive Logic
# =============================================================================

def send_data(
    data: bytes,
    profile_name: str = "audible",
    passphrase: str | None = None,
    is_file: bool = False,
    wav_out: Path | None = None,
    play_live: bool = False,
    device: int | None = None,
) -> FloatArray:
    """
    Encode, encrypt, frame, FEC, and modulate data.
    
    Returns:
        Modulated audio signal (float32)
    """
    # Encrypt if passphrase provided
    crypto = create_crypto_context(passphrase)
    if passphrase:
        data = crypto.encrypt(data)
    
    # Frame
    frames = create_frames(
        data,
        is_encrypted=passphrase is not None,
        is_file_transfer=is_file,
    )
    
    # FEC encode
    codewords = fec_encode_frames(frames)
    
    # Modulate each codeword into packets
    packets = []
    for cw in codewords:
        packet = modulate_packet(cw, profile_name)
        packets.append(packet)
    
    # Concatenate packets with small gap
    gap_samples = int(0.05 * SAMPLE_RATE)  # 50ms gap
    gap = np.zeros(gap_samples, dtype=np.float32)
    
    full_audio = np.concatenate([p for i, p in enumerate(packets) for p in ([packets[i]] + ([gap] if i < len(packets) - 1 else []))])
    
    # Normalize to prevent clipping
    max_val = np.max(np.abs(full_audio))
    if max_val > 0.95:
        full_audio = full_audio * (0.95 / max_val)
    
    # Write WAV if requested
    if wav_out:
        write_wav(wav_out, full_audio)
        print(f"WAV written to {wav_out} ({len(full_audio)/SAMPLE_RATE:.1f}s)")
    
    # Play live if requested
    if play_live:
        if not HAS_SOUNDDEVICE:
            raise PortAudioError("sounddevice not available for live playback")
        print("Playing audio...")
        play_audio(full_audio, device=device, blocking=True)
        print("Playback complete.")
    
    return full_audio


def receive_data(
    audio: FloatArray,
    profile_name: str = "audible",
    passphrase: str | None = None,
    out_dir: Path = DEFAULT_OUT_DIR,
) -> tuple[bytes | None, str | None]:
    """
    Demodulate, FEC decode, deframe, decrypt, and reassemble data.
    
    Returns:
        (payload_bytes, filename) where filename is None for text, or sanitized filename for files
    """
    crypto = create_crypto_context(passphrase)
    demod_profile = get_profile(profile_name)
    
    # Find all preambles in the audio
    from .dsp import Demodulator
    demod = Demodulator.create(profile_name)
    
    search_pos = 0
    codewords = []
    preamble_indices = []
    
    while True:
        result = demod.find_preamble(audio, search_pos)
        if result is None:
            break
        
        preamble_idx, correlation = result
        try:
            frame = demod.demodulate_from_preamble(audio, preamble_idx)
            codewords.append(frame)
            preamble_indices.append(preamble_idx)
            # Continue searching after this frame
            search_pos = preamble_idx + len(demod.preamble) + 255 * 4 * 882  # rough frame length
        except Exception as e:
            print(f"Warning: Failed to decode frame at {preamble_idx}: {e}")
            search_pos = preamble_idx + 1
    
    if not codewords:
        raise ValueError("No valid packets found in audio")
    
    print(f"Found {len(codewords)} packet(s)")
    
    # FEC decode
    try:
        frames = fec_decode_frames(codewords)
    except UncorrectableError as e:
        raise ValueError(f"FEC decode failed: {e}")
    
    # Deframe and reassemble
    if len(frames) == 1:
        # Single frame
        from .framing import decode_frame
        frame = decode_frame(frames[0])
        payload = frame.payload
        
        if frame.is_file_transfer:
            filename, file_data = parse_file_transfer(payload)
            payload = file_data
        else:
            filename = None
    else:
        # Multi-frame: use reassembly buffer
        first_frame = None
        from .framing import decode_frame
        for f in frames:
            frame = decode_frame(f)
            if first_frame is None:
                first_frame = frame
                buf = ReassemblyBuffer(frame.total_chunks)
            buf.add_frame(frame)
        
        if not buf.is_complete():
            missing = buf.get_missing_chunks()
            raise ValueError(f"Incomplete transfer: missing chunks {missing}")
        
        payload = buf.get_reassembled_payload()
        filename = buf.filename
    
    # Decrypt
    try:
        payload = crypto.decrypt(payload)
    except WrongPassphraseError as e:
        raise ValueError(str(e))
    
    return payload, filename


# =============================================================================
# CLI Commands
# =============================================================================

def cmd_send(args: argparse.Namespace) -> int:
    """Handle 'send' command."""
    # Prepare data
    if args.text is not None:
        data = args.text.encode("utf-8")
        is_file = False
    elif args.file:
        data, _ = prepare_file_transfer(Path(args.file))
        is_file = True
    else:
        print("Error: Must provide --text or --file", file=sys.stderr)
        return 1
    
    # Determine if we should play live
    play_live = args.wav_out is None and not args.no_play
    
    try:
        send_data(
            data=data,
            profile_name=args.mode,
            passphrase=args.passphrase,
            is_file=is_file,
            wav_out=Path(args.wav_out) if args.wav_out else None,
            play_live=play_live,
            device=args.device,
        )
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_listen(args: argparse.Namespace) -> int:
    """Handle 'listen' command."""
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        if args.wav_in:
            # Read from WAV file
            audio, sr = read_wav(args.wav_in)
            if sr != SAMPLE_RATE:
                print(f"Warning: WAV sample rate {sr} != {SAMPLE_RATE}, results may be poor")
        else:
            # Live recording
            if not HAS_SOUNDDEVICE:
                print("Error: sounddevice not available for live recording", file=sys.stderr)
                return 1
            print(f"Recording for {args.duration}s... (Ctrl+C to stop early)")
            try:
                audio = record_audio(args.duration, device=args.device)
            except KeyboardInterrupt:
                print("\nRecording stopped.")
                return 0
        
        payload, filename = receive_data(
            audio=audio,
            profile_name=args.mode,
            passphrase=args.passphrase,
            out_dir=out_dir,
        )
        
        if payload is None:
            print("No data received")
            return 1
        
        if filename:
            # File transfer
            out_path = out_dir / sanitize_filename(filename)
            # Avoid overwriting
            counter = 1
            base = out_path.stem
            ext = out_path.suffix
            while out_path.exists():
                out_path = out_dir / f"{base}_{counter}{ext}"
                counter += 1
            
            out_path.write_bytes(payload)
            print(f"File saved to {out_path} ({len(payload)} bytes)")
        else:
            # Text output
            try:
                text = payload.decode("utf-8")
                print(text)
            except UnicodeDecodeError:
                print(f"Received binary data ({len(payload)} bytes), not valid UTF-8")
                print("Use --file to save as file instead")
        
        return 0
        
    except WrongPassphraseError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 1


def cmd_benchmark(args: argparse.Namespace) -> int:
    """Handle 'benchmark' command."""
    from .config import MAX_FRAME_BYTES_POST_RS, SYMBOLS_PER_FRAME, FRAME_DURATION_SEC, RS_K, RS_N, RS_PARITY
    import tracemalloc
    
    profile = get_profile(args.mode)
    print(f"EchoBaud Benchmark - {args.mode.upper()} profile")
    print(f"  Sample rate: {SAMPLE_RATE} Hz")
    print(f"  Symbol duration: 20 ms (50 baud, 100 bps raw)")
    print(f"  4-FSK tones: {[f'{t:.0f}Hz' for t in profile.tones]}")
    print(f"  Chirp: {profile.chirp_start:.0f}-{profile.chirp_end:.0f} Hz, 100 ms")
    print(f"  RS({RS_N},{RS_K}): {RS_PARITY} parity bytes, corrects {RS_PARITY//2} byte errors")
    print(f"  Frame: {RS_N} bytes = {SYMBOLS_PER_FRAME} symbols = {FRAME_DURATION_SEC:.1f}s audio")
    print()
    
    modulator = None
    demod = None
    
    # Pre-generate test frame: random RS_K-byte message, RS-encoded to RS_N bytes
    rng = np.random.default_rng(42)
    test_message = bytes(rng.integers(0, 256, RS_K, dtype=np.uint8))
    from .fec import rs_encode
    test_frame = rs_encode(test_message)  # This is the RS_N-byte codeword
    
    for snr_db in BENCHMARK_SNR_LEVELS:
        print(f"Testing SNR: {snr_db:+.0f} dB")
        
        # Create modulator/demodulator once
        if modulator is None:
            from .dsp import Modulator, Demodulator
            modulator = Modulator.create(args.mode)
            demod = Demodulator.create(args.mode)
        
        # Modulate
        packet = modulator.generate_packet(test_frame)
        
        # Apply channel effects first: filtering (speaker/mic) then attenuation
        if args.mode == "audible":
            kernel = np.ones(3, dtype=np.float32) / 3.0
        else:
            kernel = np.ones(5, dtype=np.float32) / 5.0
        filtered = np.convolve(packet, kernel, mode="same").astype(np.float32)
        
        # Apply 50% attenuation (simulates path loss)
        attenuated = filtered * 0.5
        
        # Use fixed seed per SNR level for reproducibility
        snr_rng = np.random.default_rng(1000 + int(snr_db * 10))
        
        # Add AWGN at specified SNR relative to RECEIVED signal power (after channel)
        received_power = np.mean(attenuated ** 2)
        noise_power = received_power / (10 ** (snr_db / 10))
        noise = snr_rng.normal(0, np.sqrt(noise_power), len(attenuated)).astype(np.float32)
        test_audio = attenuated + noise
        
        # Add fixed silence offset (50ms) for consistency
        offset = int(0.05 * SAMPLE_RATE)
        test_audio = np.concatenate([
            np.zeros(offset, dtype=np.float32),
            test_audio,
            np.zeros(int(0.1 * SAMPLE_RATE), dtype=np.float32),
        ])
        
        # Measure memory
        tracemalloc.start()
        
        # Demodulate
        try:
            result = demod.find_preamble(test_audio)
            if result is None:
                print(f"  FAIL: Preamble not detected")
                tracemalloc.stop()
                continue
            
            preamble_idx, corr = result
            decoded = demod.demodulate_from_preamble(test_audio, preamble_idx)
            
            # Count byte errors before FEC (compare RS codewords)
            errors = sum(1 for a, b in zip(decoded, test_frame) if a != b)
            
            # FEC decode
            from .fec import rs_decode
            try:
                corrected = rs_decode(decoded)
                fec_success = corrected == test_message
            except UncorrectableError:
                fec_success = False
            
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            
            print(f"  Preamble correlation: {corr:.3f}")
            print(f"  Raw byte errors: {errors}/{RS_N}")
            print(f"  FEC recovery: {'PASS' if fec_success else 'FAIL'}")
            print(f"  Peak memory: {peak / 1024 / 1024:.1f} MB")
            
        except Exception as e:
            tracemalloc.stop()
            print(f"  ERROR: {e}")
    
    print("\nBenchmark complete.")
    return 0


# =============================================================================
# Argument Parser
# =============================================================================

def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="echobaud",
        description="EchoBaud - Air-gapped acoustic modem for encrypted text/file transfer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Send text (audible, plays through speakers)
  echobaud send --text "Hello, world!"
  
  # Send file (ultrasonic, save to WAV)
  echobaud send --file document.pdf --mode ultrasonic --wav-out transfer.wav
  
  # Receive from WAV file
  echobaud listen --wav-in transfer.wav --out-dir ./received
  
  # Live receive with passphrase
  echobaud listen --passphrase "secret123"
  
  # Benchmark modem performance
  echobaud benchmark --mode audible
        """,
    )
    
    parser.add_argument(
        "--mode",
        choices=list(PROFILES.keys()),
        default="audible",
        help="Modem profile (default: audible)",
    )
    parser.add_argument(
        "--passphrase",
        help="Passphrase for AES-256-GCM encryption",
    )
    parser.add_argument(
        "--device",
        type=int,
        help="Audio device index (default: system default)",
    )
    
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # Send command
    send_parser = subparsers.add_parser("send", help="Transmit data")
    send_parser.add_argument("--text", help="Text to send")
    send_parser.add_argument("--file", help="File to send")
    send_parser.add_argument("--wav-out", help="Save modulated audio to WAV file instead of playing")
    send_parser.add_argument("--no-play", action="store_true", help="Don't play audio (use with --wav-out)")
    send_parser.set_defaults(func=cmd_send)
    
    # Listen command
    listen_parser = subparsers.add_parser("listen", help="Receive data")
    listen_parser.add_argument("--wav-in", help="Read audio from WAV file instead of microphone")
    listen_parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Output directory for received files")
    listen_parser.add_argument("--duration", type=float, default=30.0, help="Recording duration in seconds (live mode)")
    listen_parser.set_defaults(func=cmd_listen)
    
    # Benchmark command
    bench_parser = subparsers.add_parser("benchmark", help="Run performance benchmark")
    bench_parser.set_defaults(func=cmd_benchmark)
    
    return parser


def main() -> int:
    parser = create_parser()
    args = parser.parse_args()
    
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())