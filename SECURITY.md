# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability in EchoBaud, please report it responsibly by emailing **agiprogrammer@gmail.com**.

**Please do not open a public GitHub issue for security vulnerabilities.**

### What to Include

When reporting a vulnerability, please include as much of the following as possible:

- A clear description of the vulnerability
- Steps to reproduce the issue
- Potential impact and attack scenarios
- Any proof-of-concept code (if safe to share)
- Suggested mitigation or fix (if you have one)

### Response Timeline

- **Acknowledgment**: Within 48 hours of receipt
- **Initial Assessment**: Within 1 week
- **Status Updates**: At least every 2 weeks until resolution
- **Fix Release**: Target within 30 days for critical issues, 90 days for others

## Security Features

EchoBaud implements the following security measures:

### Encryption (Optional)
- **Algorithm**: AES-256-GCM (authenticated encryption)
- **Key Derivation**: PBKDF2-HMAC-SHA256 with 100,000 iterations
- **Salt**: 16 bytes, cryptographically random per message
- **Nonce**: 12 bytes, cryptographically random per message
- **Authentication Tag**: 16 bytes, verified on decryption

### Threat Model

EchoBaud is designed for air-gapped communication. The threat model assumes:

1. **Adversary can record audio** - The acoustic channel is inherently broadcast; anyone with a microphone can capture transmissions
2. **Adversary can replay audio** - Replay attacks are possible unless application-layer protections (timestamps, nonces) are used
3. **Adversary can inject audio** - Active injection (jamming, spoofing) is possible
4. **Physical access** - If an adversary has physical access to either device, all bets are off

### What EchoBaud Does NOT Protect Against

- **Traffic analysis**: Transmission timing, frequency, and duration are observable
- **Replay attacks**: No built-in replay protection (use application-layer nonces/timestamps)
- **Denial of service**: Jamming the acoustic channel is trivial
- **Side-channel attacks**: Power consumption, EM emanations, or timing attacks on the host device
- **Compromised host**: If the sending/receiving device is compromised, encryption keys and plaintext are exposed

### Best Practices for Users

1. **Always use `--passphrase`** for sensitive data
2. **Use strong passphrases** (minimum 20 characters, high entropy)
3. **Verify the fingerprint** of received files through an authenticated channel
4. **Rotate passphrases** periodically for long-term communications
5. **Don't reuse passphrases** across different security contexts
6. **Transmit in controlled environments** when possible to reduce interception risk

## Cryptographic Implementation Notes

- Uses `cryptography` library (well-audited, maintained by the Python Cryptographic Authority)
- No custom cryptographic primitives
- Constant-time comparison for authentication tag verification (handled by AES-GCM)
- Secure random number generation via `secrets` module (OS CSPRNG)

## Known Limitations

1. **No forward secrecy** - If a passphrase is compromised, all past messages encrypted with it can be decrypted
2. **No key exchange protocol** - Passphrases must be shared out-of-band
3. **No authentication of sender** - Anyone with the passphrase can create valid ciphertexts
4. **Frame-level encryption** - Each frame is independently encrypted; traffic analysis can determine message boundaries

## Disclosure Policy

We follow responsible disclosure. When a vulnerability is confirmed:

1. We will privately notify affected users if practical
2. We will release a patch as soon as possible
3. We will publish a security advisory after the fix is available
4. We will credit the reporter (unless they request anonymity)

## Contact

For security concerns, contact: **agiprogrammer@gmail.com**

GPG key available on request.