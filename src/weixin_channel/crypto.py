"""Crypto helpers for Weixin CDN media."""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


AES_BLOCK_BITS = 128
AES_BLOCK_BYTES = 16


def aes_ecb_padded_size(plaintext_size: int) -> int:
    """Compute AES/PKCS7 ciphertext size for a plaintext length."""
    return ((plaintext_size // AES_BLOCK_BYTES) + 1) * AES_BLOCK_BYTES


def encrypt_aes_128_ecb(plaintext: bytes, key: bytes) -> bytes:
    if len(key) != 16:
        raise ValueError("AES-128 key must be exactly 16 bytes")
    padder = padding.PKCS7(AES_BLOCK_BITS).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def decrypt_aes_128_ecb(ciphertext: bytes, key: bytes) -> bytes:
    if len(key) != 16:
        raise ValueError("AES-128 key must be exactly 16 bytes")
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(AES_BLOCK_BITS).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def parse_cdn_aes_key(value: str) -> bytes:
    """Parse CDNMedia.aes_key.

    Observed encodings:
    - base64(raw 16 bytes)
    - base64(32 ASCII hex chars)
    """
    decoded = base64.b64decode(value)
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        try:
            text = decoded.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("invalid CDN aes_key encoding") from exc
        if all(ch in "0123456789abcdefABCDEF" for ch in text):
            return bytes.fromhex(text)
    raise ValueError(f"invalid CDN aes_key length: {len(decoded)}")


def encode_hex_key_for_cdn(hex_key: str) -> str:
    """Encode a hex key the same way openclaw-weixin sends it in CDNMedia.aes_key."""
    return base64.b64encode(hex_key.encode("ascii")).decode("ascii")
