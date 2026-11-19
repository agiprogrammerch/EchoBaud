"""Tests for crypto module."""

import pytest

from echobaud.crypto import (
    encrypt,
    decrypt,
    derive_key,
    create_crypto_context,
    WrongPassphraseError,
    TamperedCiphertextError,
    CRYPTO_HEADER_SIZE,
    SALT_SIZE,
    NONCE_SIZE,
    TAG_SIZE,
    KEY_SIZE,
)


def test_derive_key():
    """Test key derivation is deterministic with same salt."""
    passphrase = "test-passphrase"
    salt = b"0" * 16
    
    key1 = derive_key(passphrase, salt)
    key2 = derive_key(passphrase, salt)
    
    assert key1 == key2
    assert len(key1) == KEY_SIZE


def test_derive_key_different_salt():
    """Test different salts produce different keys."""
    passphrase = "test-passphrase"
    salt1 = b"0" * 16
    salt2 = b"1" * 16
    
    key1 = derive_key(passphrase, salt1)
    key2 = derive_key(passphrase, salt2)
    
    assert key1 != key2


def test_encrypt_decrypt_roundtrip():
    """Test encryption/decryption round-trip."""
    passphrase = "my-secret-passphrase-123"
    plaintext = b"Hello, EchoBaud! " * 50  # ~900 bytes
    
    ciphertext = encrypt(plaintext, passphrase)
    
    # Check structure: salt + nonce + ciphertext + tag
    assert len(ciphertext) == CRYPTO_HEADER_SIZE + len(plaintext) + TAG_SIZE
    assert ciphertext[:SALT_SIZE] != b"\x00" * SALT_SIZE  # Random salt
    assert ciphertext[SALT_SIZE:SALT_SIZE+NONCE_SIZE] != b"\x00" * NONCE_SIZE  # Random nonce
    
    decrypted = decrypt(ciphertext, passphrase)
    assert decrypted == plaintext


def test_encrypt_empty():
    """Test encryption of empty payload."""
    passphrase = "test"
    plaintext = b""
    
    ciphertext = encrypt(plaintext, passphrase)
    decrypted = decrypt(ciphertext, passphrase)
    
    assert decrypted == plaintext


def test_wrong_passphrase():
    """Test decryption fails with wrong passphrase."""
    passphrase = "correct-passphrase"
    wrong = "wrong-passphrase"
    plaintext = b"Secret data"
    
    ciphertext = encrypt(plaintext, passphrase)
    
    with pytest.raises(TamperedCiphertextError):
        decrypt(ciphertext, wrong)


def test_tampered_ciphertext():
    """Test decryption fails with tampered ciphertext."""
    passphrase = "test-passphrase"
    plaintext = b"Important data"
    
    ciphertext = bytearray(encrypt(plaintext, passphrase))
    # Flip a bit in the ciphertext (after header)
    ciphertext[CRYPTO_HEADER_SIZE] ^= 0xFF
    
    with pytest.raises(TamperedCiphertextError):
        decrypt(bytes(ciphertext), passphrase)


def test_tampered_tag():
    """Test decryption fails with tampered tag."""
    passphrase = "test-passphrase"
    plaintext = b"Important data"
    
    ciphertext = bytearray(encrypt(plaintext, passphrase))
    # Flip a bit in the tag (last 16 bytes)
    ciphertext[-1] ^= 0xFF
    
    with pytest.raises(TamperedCiphertextError):
        decrypt(bytes(ciphertext), passphrase)


def test_short_ciphertext():
    """Test decryption fails with too-short ciphertext."""
    passphrase = "test"
    
    with pytest.raises(ValueError):
        decrypt(b"too short", passphrase)


def test_crypto_context():
    """Test CryptoContext high-level API."""
    # Without passphrase (passthrough)
    ctx = create_crypto_context(None)
    data = b"plaintext"
    assert ctx.encrypt(data) == data
    assert ctx.decrypt(data) == data
    
    # With passphrase
    ctx = create_crypto_context("secret")
    plaintext = b"encrypted data"
    ciphertext = ctx.encrypt(plaintext)
    assert ciphertext != plaintext
    assert ctx.decrypt(ciphertext) == plaintext


def test_crypto_context_wrong_passphrase():
    """Test CryptoContext raises WrongPassphraseError on wrong passphrase."""
    ctx = create_crypto_context("correct")
    ciphertext = ctx.encrypt(b"data")
    
    ctx_wrong = create_crypto_context("wrong")
    with pytest.raises(WrongPassphraseError):
        ctx_wrong.decrypt(ciphertext)


def test_large_payload():
    """Test encryption of large payload (multi-block)."""
    passphrase = "test"
    plaintext = b"X" * 10000  # 10 KB
    
    ciphertext = encrypt(plaintext, passphrase)
    decrypted = decrypt(ciphertext, passphrase)
    
    assert decrypted == plaintext


def test_unicode_passphrase():
    """Test passphrase with unicode characters."""
    passphrase = "pässwörd-🔐-中文"
    plaintext = b"data"
    
    ciphertext = encrypt(plaintext, passphrase)
    decrypted = decrypt(ciphertext, passphrase)
    
    assert decrypted == plaintext