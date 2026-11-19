"""
EchoBaud Cryptography Module

Provides optional end-to-end encryption using AES-256-GCM with PBKDF2-HMAC-SHA256
key derivation. When a passphrase is provided, all payloads are encrypted before
framing and decrypted after deframing.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Final

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.exceptions import InvalidTag


# =============================================================================
# Constants
# =============================================================================

# PBKDF2 parameters
PBKDF2_ITERATIONS: Final[int] = 100_000  # OWASP 2024 recommendation for PBKDF2-SHA256
SALT_SIZE: Final[int] = 16  # bytes (128 bits)
NONCE_SIZE: Final[int] = 12  # bytes (96 bits) - GCM standard
KEY_SIZE: Final[int] = 32  # bytes (256 bits) - AES-256
TAG_SIZE: Final[int] = 16  # bytes (128 bits) - GCM tag

# Combined header: salt (16) + nonce (12) = 28 bytes prepended to ciphertext
CRYPTO_HEADER_SIZE: Final[int] = SALT_SIZE + NONCE_SIZE


# =============================================================================
# Exceptions
# =============================================================================

class CryptoError(Exception):
    """Base exception for cryptographic operations."""
    pass


class WrongPassphraseError(CryptoError):
    """Raised when decryption fails due to incorrect passphrase."""
    pass


class TamperedCiphertextError(CryptoError):
    """Raised when ciphertext integrity check fails (wrong tag)."""
    pass


# =============================================================================
# Key Derivation
# =============================================================================

def derive_key(passphrase: str, salt: bytes) -> bytes:
    """
    Derive AES-256 key from passphrase using PBKDF2-HMAC-SHA256.
    
    Args:
        passphrase: User-provided passphrase (UTF-8 encoded)
        salt: Random 16-byte salt
        
    Returns:
        32-byte AES-256 key
    """
    if len(salt) != SALT_SIZE:
        raise ValueError(f"Salt must be {SALT_SIZE} bytes, got {len(salt)}")
    
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


# =============================================================================
# Encryption / Decryption
# =============================================================================

def encrypt(plaintext: bytes, passphrase: str) -> bytes:
    """
    Encrypt plaintext with AES-256-GCM using passphrase-derived key.
    
    Output format: [SALT:16] [NONCE:12] [CIPHERTEXT] [TAG:16]
    The tag is appended by AESGCM.encrypt() automatically.
    
    Args:
        plaintext: Data to encrypt
        passphrase: Encryption passphrase
        
    Returns:
        Ciphertext with prepended salt and nonce
    """
    # Generate random salt and nonce
    salt = secrets.token_bytes(SALT_SIZE)
    nonce = secrets.token_bytes(NONCE_SIZE)
    
    # Derive key
    key = derive_key(passphrase, salt)
    
    # Encrypt
    aesgcm = AESGCM(key)
    ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext, None)  # No AAD
    
    # Prepend salt and nonce
    return salt + nonce + ciphertext_with_tag


def decrypt(ciphertext: bytes, passphrase: str) -> bytes:
    """
    Decrypt ciphertext with AES-256-GCM using passphrase-derived key.
    
    Expected format: [SALT:16] [NONCE:12] [CIPHERTEXT] [TAG:16]
    
    Args:
        ciphertext: Encrypted data with salt/nonce header
        passphrase: Decryption passphrase
        
    Returns:
        Decrypted plaintext
        
    Raises:
        WrongPassphraseError: If passphrase is incorrect
        TamperedCiphertextError: If ciphertext is corrupted/tampered
        ValueError: If ciphertext format is invalid
    """
    if len(ciphertext) < CRYPTO_HEADER_SIZE + TAG_SIZE:
        raise ValueError(f"Ciphertext too short: {len(ciphertext)} bytes (min {CRYPTO_HEADER_SIZE + TAG_SIZE})")
    
    # Extract salt, nonce, and ciphertext+tag
    salt = ciphertext[:SALT_SIZE]
    nonce = ciphertext[SALT_SIZE:SALT_SIZE + NONCE_SIZE]
    ciphertext_with_tag = ciphertext[SALT_SIZE + NONCE_SIZE:]
    
    # Derive key
    key = derive_key(passphrase, salt)
    
    # Decrypt
    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
        return plaintext
    except InvalidTag:
        # InvalidTag can mean wrong key OR tampered ciphertext
        # We can't distinguish, so we raise a generic crypto error
        # but we provide a more specific exception hierarchy
        raise TamperedCiphertextError(
            "Decryption failed: invalid authentication tag. "
            "Wrong passphrase or corrupted/tampered ciphertext."
        ) from None


# =============================================================================
# High-Level API
# =============================================================================

@dataclass(frozen=True, slots=True)
class CryptoContext:
    """Holds encryption state for a session."""
    passphrase: str | None
    
    def encrypt(self, data: bytes) -> bytes:
        """Encrypt data if passphrase is set, otherwise return as-is."""
        if self.passphrase is None:
            return data
        return encrypt(data, self.passphrase)
    
    def decrypt(self, data: bytes) -> bytes:
        """Decrypt data if passphrase is set, otherwise return as-is."""
        if self.passphrase is None:
            return data
        try:
            return decrypt(data, self.passphrase)
        except TamperedCiphertextError as e:
            # Re-raise as WrongPassphraseError for cleaner CLI handling
            raise WrongPassphraseError(
                "Decryption failed. Wrong passphrase or corrupted data."
            ) from e


def create_crypto_context(passphrase: str | None) -> CryptoContext:
    """Factory for CryptoContext."""
    return CryptoContext(passphrase=passphrase)


# =============================================================================
# Testing Helpers
# =============================================================================

def _test_roundtrip() -> None:
    """Internal test for encryption round-trip."""
    passphrase = "test-passphrase-123"
    plaintext = b"Hello, EchoBaud! " * 100  # 1800 bytes
    
    # Encrypt
    ciphertext = encrypt(plaintext, passphrase)
    assert len(ciphertext) == CRYPTO_HEADER_SIZE + len(plaintext) + TAG_SIZE
    
    # Decrypt with correct passphrase
    decrypted = decrypt(ciphertext, passphrase)
    assert decrypted == plaintext
    
    # Wrong passphrase should fail
    try:
        decrypt(ciphertext, "wrong-passphrase")
        assert False, "Should have raised"
    except TamperedCiphertextError:
        pass
    
    # Tampered ciphertext should fail
    tampered = bytearray(ciphertext)
    tampered[CRYPTO_HEADER_SIZE] ^= 0xFF  # Flip a bit in ciphertext
    try:
        decrypt(bytes(tampered), passphrase)
        assert False, "Should have raised"
    except TamperedCiphertextError:
        pass
    
    print("Crypto round-trip tests passed.")


if __name__ == "__main__":
    _test_roundtrip()