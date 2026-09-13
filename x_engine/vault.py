"""Encrypt credentials at rest. Windows keys are bound to the current OS user."""
import ctypes
import os
from pathlib import Path

from cryptography.fernet import Fernet


def _dpapi(data: bytes, decrypt: bool = False) -> bytes:
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buf = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    dest = Blob()
    lib = ctypes.WinDLL("crypt32", use_last_error=True)
    if decrypt:
        ok = lib.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(dest))
    else:
        ok = lib.CryptProtectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(dest))
    if not ok:
        raise RuntimeError("Cannot access the vault key with this Windows user.")
    try:
        return ctypes.string_at(dest.data, dest.size)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(dest.data)


class Vault:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ("vault.dpapi" if os.name == "nt" else "vault.key")
        if not path.exists():
            key = Fernet.generate_key()
            protected = _dpapi(key) if os.name == "nt" else key
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(protected)
        protected = path.read_bytes()
        self.cipher = Fernet(_dpapi(protected, True) if os.name == "nt" else protected)

    def seal(self, value: str) -> bytes:
        return self.cipher.encrypt(value.encode())

    def open(self, value: bytes) -> str:
        return self.cipher.decrypt(value).decode()
