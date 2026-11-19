# EchoBaud

**Developer:** Agi Hacker  
**Email:** agiprogrammer@gmail.com  
**Motto:** Build, not Bought

---

**Air-gapped acoustic modem for encrypted text/file transfer via speakers and microphone.**

Zero Wi-Fi, Bluetooth, internet, or cables. Pure Python 3.10+, deterministic DSP, production-grade.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         ECHOBAUD SIGNAL PIPELINE                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  SEND PATH                                                                  │
│  ────────                                                                   │
│  ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌──────────┐       │
│  │  TEXT   │──▶│ ENCRYPT │──▶│  FRAME  │──▶│   FEC   │──▶│ MODULATE │       │
│  │ / FILE  │   │AES-256  │   │ + CRC32 │   │RS(255,  │   │ 4-FSK +  │       │
│  │         │   │  GCM    │   │ + CHUNK │   │  201)   │   │  CHIRP   │       │
│  └─────────┘   └─────────┘   └─────────┘   └─────────┘   └──────────┘       │
│       │            │            │            │             │                │
│       ▼            ▼            ▼            ▼             ▼                │
│  plaintext    ciphertext   200B frames  255B codewords  44.1 kHz float32    │
│  (or binary)  (salt+nonce) 188B payload   54 parity      audio buffer       │
│                                                                             │
│  RECEIVE PATH                                                               │
│  ─────────────                                                              │
│  ┌──────────┐   ┌─────────┐   ┌──────────┐   ┌─────────┐   ┌──────────┐     │
│  │ CAPTURE  │──▶│ SYNC    │──▶│DEMODULATE│──▶│  FEC    │──▶│ DEFRAME  │     │
│  │  AUDIO   │   │ CHIRP   │   │  4-FSK   │   │  DECODE │   │+ DECRYPT │     │
│  │ 44.1kHz  │   │  CORR   │   │ GOERTZEL │   │ RS DEC  │   │ CRC+AES  │     │
│  └──────────┘   └─────────┘   └──────────┘   └─────────┘   └──────────┘     │
│       │            │            │            │             │                │
│       ▼            ▼            ▼            ▼             ▼                │
│  float32      sample idx   2-bit syms   201B msg      plaintext or          │
│  buffer       + corr       (0-3)       (or fail)     saved file             │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Frame Format (Pre-RS, 200 bytes)

```
┌──────┬──────────┬──────────┬──────────┬───────────┬───────────┬────────┐
│MAGIC │VER_FLAGS │ CHUNK_ID │ TOTAL_CH │PAYLOAD_LEN│  PAYLOAD  │ CRC32  │
│ 2B   │   1B     │   2B     │   2B     │   2B      │  188B     │  4B    │
│0xEB90│ v1|flags │  0..N-1  │    N     │   0..188  │   data    │ IEEE   │
└──────┴──────────┴──────────┴──────────┴───────────┴───────────┴────────┘
Total: 13 bytes overhead + 188 bytes payload = 200 bytes = RS_K
```

**Flags (VER_FLAGS lower 4 bits):**
- `0x01` ENCRYPTED — Payload is AES-256-GCM encrypted
- `0x02` FILE_TRANSFER — Payload contains filename + file data
- `0x04` LAST_CHUNK — Final chunk in multi-chunk transfer

## DSP Specifications

| Parameter | Audible (Default) | Ultrasonic (Experimental) |
|-----------|-------------------|---------------------------|
| **Sample Rate** | 44,100 Hz | 44,100 Hz |
| **Modulation** | 4-FSK (2 bits/symbol) | 4-FSK (2 bits/symbol) |
| **Symbol Duration** | 20 ms (882 samples) | 20 ms (882 samples) |
| **Baud Rate** | 50 baud | 50 baud |
| **Raw Bit Rate** | 100 bps | 100 bps |
| **Tone Frequencies** | 1200, 1900, 2600, 3300 Hz | 17500, 18167, 18833, 19500 Hz |
| **Tone Spacing** | 700 Hz (>> 50 Hz min) | 667 Hz (>> 50 Hz min) |
| **Chirp Preamble** | 1000→4000 Hz, 100 ms | 17000→20000 Hz, 100 ms |
| **Taper Window** | Raised-cosine, 10% (2 ms) each edge | Same |
| **Sync Detection** | Normalized XCorr (FFT), threshold 0.25 | Same, threshold 0.30 |
| **Demodulation** | Goertzel algorithm (4 tones) | Goertzel algorithm (4 tones) |
| **Squelch Threshold** | 0.015 (adaptive) | 0.020 (adaptive) |

### Reed-Solomon FEC
- **Code**: RS(255, 201) over GF(256), primitive polynomial 0x11D
- **Parity**: 54 bytes (even, required by reedsolo)
- **Correction**: 27 byte errors per 255-byte codeword
- **Overhead**: 21.2% (54/255)

### Encryption (Optional)
- **Algorithm**: AES-256-GCM
- **KDF**: PBKDF2-HMAC-SHA256, 100,000 iterations
- **Salt**: 16 bytes (random per message)
- **Nonce**: 12 bytes (random per message)
- **Tag**: 16 bytes (appended to ciphertext)
- **Format**: `SALT(16) || NONCE(12) || CIPHERTEXT || TAG(16)`

## Performance (Benchmark Results)

### Audible Profile
| SNR | Preamble Corr | Raw Errors | FEC Recovery | Peak RAM |
|-----|---------------|------------|--------------|----------|
| +20 dB | 0.995 | 0/255 | ✅ PASS | 41.3 MB |
| +15 dB | 0.984 | 0/255 | ✅ PASS | 41.3 MB |
| +10 dB | 0.952 | 0/255 | ✅ PASS | 41.3 MB |
| +5 dB  | 0.867 | 0/255 | ✅ PASS | 41.3 MB |
| 0 dB   | 0.704 | 0/255 | ✅ PASS | 41.3 MB |
| -5 dB  | 0.497 | 0/255 | ✅ PASS | 41.3 MB |

### Ultrasonic Profile
| SNR | Preamble Corr | Raw Errors | FEC Recovery | Peak RAM |
|-----|---------------|------------|--------------|----------|
| +20 dB | 0.752 | 0/255 | ✅ PASS | 41.3 MB |
| +15 dB | 0.748 | 0/255 | ✅ PASS | 41.3 MB |
| +10 dB | 0.718 | 0/255 | ✅ PASS | 41.3 MB |
| +5 dB  | 0.661 | 0/255 | ✅ PASS | 41.3 MB |
| 0 dB   | 0.535 | 0/255 | ✅ PASS | 41.3 MB |
| -5 dB  | 0.378 | 6/255 | ✅ PASS | 41.3 MB |

**Effective throughput**: ~4.9 bps (188 bytes payload / 20.5 sec frame time)  
**Peak RAM**: < 42 MB (well under 150 MB target)

## Installation

### Recommended: `pipx` (isolated, on PATH)
```bash
# Install pipx if needed: apt-get install pipx  |  brew install pipx
pipx install /path/to/echobaud
echobaud send --text "Hello"
```

### Alternative: Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install -e /path/to/echobaud
echobaud send --text "Hello"
```

### System Dependencies (Linux)
```bash
# PortAudio for sounddevice
apt-get install portaudio19-dev python3-dev
# Fedora: dnf install portaudio-devel python3-devel
# Arch: pacman -S portaudio python
```

## Quick Start

```bash
# Send text (plays through speakers)
echobaud send --text "Hello, air-gapped world!"

# Send text to WAV file (for transfer via USB/QR/any method)
echobaud send --text "Hello!" --wav-out message.wav

# Send file to WAV
echobaud send --file document.pdf --wav-out transfer.wav

# Receive from WAV (text prints to stdout)
echobaud listen --wav-in message.wav

# Receive file from WAV (saves to ./received/ with original filename)
echobaud listen --wav-in transfer.wav --out-dir ./received

# Live receive from microphone (30 seconds)
echobaud listen --duration 30
```

## Encryption (AES-256-GCM)

```bash
# Sender: encrypt with passphrase
echobaud --passphrase "my-secret" send --text "Classified info" --wav-out secret.wav

# Receiver: decrypt with same passphrase
echobaud --passphrase "my-secret" listen --wav-in secret.wav

# Wrong passphrase fails cleanly:
# Error: Decryption failed. Wrong passphrase or corrupted data.
```

## Ultrasonic Mode (Near-Inaudible, Experimental)

```bash
# Requires good tweeters; shorter range but inaudible to humans
echobaud --mode ultrasonic send --text "Silent message" --wav-out ultra.wav
echobaud --mode ultrasonic listen --wav-in ultra.wav
```

## CLI Reference

```
echobaud send [--text STR | --file PATH] [--mode audible|ultrasonic]
              [--passphrase STR] [--wav-out FILE.wav] [--no-play] [--device INT]

echobaud listen [--wav-in FILE.wav] [--mode audible|ultrasonic]
                [--passphrase STR] [--out-dir DIR] [--duration SEC] [--device INT]

echobaud benchmark [--mode audible|ultrasonic]
```

**Global options:**
| Option | Description |
|--------|-------------|
| `--mode` | `audible` (default) or `ultrasonic` |
| `--passphrase` | Enable AES-256-GCM encryption |
| `--device` | PortAudio device index (default: system default) |

**Send options:**
| Option | Description |
|--------|-------------|
| `--text STR` | Text to transmit |
| `--file PATH` | File to transmit |
| `--wav-out FILE.wav` | Save modulated audio to WAV |
| `--no-play` | Don't play through speakers (use with `--wav-out`) |

**Listen options:**
| Option | Description |
|--------|-------------|
| `--wav-in FILE.wav` | Read audio from WAV file |
| `--out-dir DIR` | Output directory for files (default: `./received`) |
| `--duration SEC` | Recording time for live mode (default: 30) |

## Example Workflow

**Two laptops in same room, no network:**

```bash
# Laptop A: Send encrypted file
echobaud --passphrase "shared-secret" send --file plans.pdf --wav-out plans.wav

# Transfer plans.wav via USB/QR/any method to Laptop B

# Laptop B: Receive and decrypt
echobaud --passphrase "shared-secret" listen --wav-in plans.wav --out-dir .
```

**Direct acoustic transfer (laptops facing each other):**

```bash
# Laptop A (sender)
echobaud send --text "Meeting at 3pm in room 404"

# Laptop B (receiver) - starts listening, then A presses enter
echobaud listen --duration 25
```

## Architecture

```
src/echobaud/
├── __init__.py      # Package exports
├── config.py        # All DSP parameters, profiles, constants
├── crypto.py        # AES-256-GCM + PBKDF2 key derivation
├── framing.py       # Binary packet framing, chunking, reassembly
├── fec.py           # Reed-Solomon (255,201) wrapper
├── dsp.py           # 4-FSK modulate/demod, Goertzel, chirp sync
├── audio_io.py      # sounddevice I/O, WAV read/write
└── cli.py           # argparse CLI entry point
```

**Design Principles:**
- **Hardware decoupling**: All DSP operates on `np.float32` buffers; live I/O is a thin wrapper
- **Deterministic**: No ML, no adaptive algorithms that change behavior between runs
- **Testable**: Full WAV loopback testing in CI; no physical hardware needed for verification
- **Minimal deps**: Only `numpy`, `scipy`, `sounddevice`, `reedsolo`, `cryptography`

## Testing

```bash
# Run full test suite (88 tests)
pytest tests/ -v

# Key test categories:
# - test_config.py: DSP parameter validation
# - test_crypto.py: Encryption round-trips, wrong passphrase rejection
# - test_framing.py: Frame encode/decode, CRC, multi-chunk reassembly
# - test_fec.py: RS(255,201) correction up to 27 errors
# - test_dsp.py: Modulate/demodulate, Goertzel, chirp sync, noise resilience
# - test_audio_io.py: WAV read/write, device enumeration
# - test_integration.py: Full pipeline (text, file, encrypted, noisy channel)
```

## Security Considerations

- **Encryption is optional**: Use `--passphrase` for sensitive data
- **No authentication of sender**: Anyone can transmit; encrypt to ensure confidentiality
- **Replay protection**: Not implemented; add timestamps/nonces in application layer if needed
- **Side channels**: Audio emanations may be recorded by nearby devices (intentional for this modem)

## License

MIT License
