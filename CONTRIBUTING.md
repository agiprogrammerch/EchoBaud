# Contributing to EchoBaud

Thank you for your interest in contributing to EchoBaud! This document outlines the process for contributing code, documentation, tests, and ideas.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Testing](#testing)
- [Code Style](#code-style)
- [Submitting Changes](#submitting-changes)
- [Architecture Overview](#architecture-overview)
- [Contact](#contact)

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you agree to uphold this code.

## Getting Started

### Prerequisites

- Python 3.10+
- PortAudio development libraries (for `sounddevice`)
- Git

### System Dependencies

```bash
# Debian/Ubuntu/Kali
apt-get install portaudio19-dev python3-dev

# Fedora
dnf install portaudio-devel python3-devel

# Arch
pacman -S portaudio python

# macOS
brew install portaudio
```

## Development Setup

```bash
# Fork and clone the repository
git clone https://github.com/agiprogrammerch/EchoBaud.git
cd EchoBaud

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install in development mode with test dependencies
pip install -e .[dev]

# Run tests to verify setup
pytest tests/ -v
```

## Making Changes

### Branching

- Create a feature branch from `main`: `git checkout -b feature/your-feature-name`
- Use descriptive branch names: `fix/preamble-detection`, `feat/ultrasonic-profile`, `docs/update-readme`

### Types of Contributions

1. **Bug fixes** - Fix incorrect behavior, crashes, or edge cases
2. **Features** - New profiles, modulation schemes, CLI options
3. **Performance** - Optimizations that maintain correctness
4. **Documentation** - README, docstrings, comments, examples
5. **Tests** - Unit tests, integration tests, stress tests
6. **Refactoring** - Code organization, type hints, dead code removal

### Commit Messages

Follow conventional commits:

```
<type>(<scope>): <short summary>

<body>

Fixes #<issue-number>
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `chore`, `ci`

Example:
```
feat(dsp): add 8-FSK modulation option

Increases raw bit rate to 150 bps at cost of reduced noise margin.
Adds new profile configuration and updates demodulator.

Fixes #42
```

## Testing

### Run All Tests

```bash
pytest tests/ -v
```

### Run Specific Test File

```bash
pytest tests/test_dsp.py -v
pytest tests/test_integration.py::test_noisy_acoustic_loopback -v
```

### Run with Coverage

```bash
pytest tests/ --cov=echobaud --cov-report=html
```

### Test Requirements

- All new code must have tests
- Tests must be deterministic (no random failures)
- Integration tests use WAV loopback (no live audio required)
- Target: ≥ 90% coverage

### Stress Testing

```bash
# Run benchmark to verify performance
echobaud benchmark --mode audible
echobaud benchmark --mode ultrasonic
```

## Code Style

### Type Hints

- All public functions must have type hints
- Use `from __future__ import annotations` for forward references
- Prefer `list[str]` over `List[str]` (Python 3.9+)

### Formatting

```bash
# Format with ruff (if configured)
ruff check --fix src/ tests/
ruff format src/ tests/
```

### Linting

```bash
# Static analysis
ruff check src/ tests/
mypy src/
```

### Conventions

- **Naming**: `snake_case` for functions/variables, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants
- **Docstrings**: Google-style for public APIs
- **Imports**: Standard library → third-party → local
- **Constants**: Define in `config.py`, import from there

### DSP Code Guidelines

- All signal processing must be deterministic
- Document mathematical derivations in comments
- Use explicit variable names (`symbol_samples` not `N`)
- Prefer NumPy vectorization over Python loops
- Validate inputs at module boundaries

## Submitting Changes

### Pull Request Process

1. **Ensure tests pass**: `pytest tests/ -v`
2. **Update documentation** if behavior changes
3. **Add tests** for new functionality
4. **Run linting**: `ruff check src/ tests/` and `mypy src/`
5. **Create PR** with clear description:
   - What changed
   - Why it changed
   - How it was tested
   - Any breaking changes

### PR Checklist

- [ ] Tests pass locally
- [ ] New code has type hints
- [ ] New code has tests
- [ ] Documentation updated
- [ ] No breaking changes (or documented)
- [ ] Commit messages follow convention
- [ ] Branch is up to date with main

### Review Process

- Maintainers will review within 1 week
- Address feedback promptly
- CI must pass before merge
- Squash commits on merge (handled by maintainer)

## Architecture Overview

### Module Structure

```
src/echobaud/
├── config.py      # All DSP parameters, profiles, constants
├── crypto.py      # AES-256-GCM + PBKDF2 key derivation
├── framing.py     # Binary packet framing, chunking, reassembly
├── fec.py         # Reed-Solomon (255,201) wrapper
├── dsp.py         # 4-FSK modulate/demod, Goertzel, chirp sync
├── audio_io.py    # sounddevice I/O, WAV read/write
├── cli.py         # argparse CLI entry point
└── __init__.py    # Package exports
```

### Key Design Principles

1. **Hardware Decoupling**: All DSP operates on `np.float32` buffers; live I/O is a thin wrapper
2. **Deterministic DSP**: No ML, no adaptive algorithms, explicit documentation
3. **Testable**: Full WAV loopback testing; no physical hardware needed for verification
4. **Minimal Dependencies**: Only `numpy`, `scipy`, `sounddevice`, `reedsolo`, `cryptography`

### Signal Pipeline

```
TEXT/FILE → ENCRYPT → FRAME + CRC32 → RS(255,201) → 4-FSK + CHIRP → AUDIO
                                                ↑
AUDIO → SYNC(CHIRP) → GOERTZEL(4-FSK) → RS DEC → DEFRAME → DECRYPT → OUTPUT
```

### Adding a New Modem Profile

1. Add profile to `config.py` (`ModemProfile` dataclass)
2. Validate tone orthogonality and Nyquist limits
3. Add profile to `PROFILES` dict
4. Test with `echobaud benchmark --mode <name>`
5. Add integration tests

## Contact

For questions, suggestions, or security concerns:

**Email**: agiprogrammer@gmail.com

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).