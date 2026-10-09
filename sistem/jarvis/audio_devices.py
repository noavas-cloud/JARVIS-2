"""macOS'un varsayılan ses giriş/çıkış aygıtını okur (CoreAudio, ctypes; ek paket gerekmez).

PortAudio (PyAudio) aygıt listesini ve varsayılan aygıtı yalnız başlarken okur: AirPods takılınca ya da kulaklık
çıkarılınca JARVIS eski aygıtta kalıyordu. Core bu modülle saniyede bir varsayılan aygıtlara bakar; değişince
mikrofonu ve hoparlörü kapatıp yeniden açar (bkz. live.Core._watch_audio_devices).
"""

from __future__ import annotations

import ctypes
import ctypes.util

_SYSTEM_OBJECT = 1


def _fourcc(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


class _Address(ctypes.Structure):
    _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("element", ctypes.c_uint32)]


_ca = _cf = None
try:
    _ca = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))
    _cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
    _ca.AudioObjectGetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address), ctypes.c_uint32,
                                               ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
    _ca.AudioObjectGetPropertyData.restype = ctypes.c_int32
    _cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    _cf.CFStringGetCString.restype = ctypes.c_bool
    _cf.CFRelease.argtypes = [ctypes.c_void_p]
except (OSError, TypeError, AttributeError):
    _ca = _cf = None


def _device(selector: str) -> int | None:
    if _ca is None:
        return None
    addr = _Address(_fourcc(selector), _fourcc("glob"), 0)
    value, size = ctypes.c_uint32(0), ctypes.c_uint32(4)
    err = _ca.AudioObjectGetPropertyData(_SYSTEM_OBJECT, ctypes.byref(addr), 0, None, ctypes.byref(size),
                                         ctypes.byref(value))
    return int(value.value) if err == 0 and value.value else None


def default_devices() -> tuple[int | None, int | None]:
    """(varsayılan giriş, varsayılan çıkış) aygıt kimlikleri; okunamazsa None."""
    return _device("dIn "), _device("dOut")


def device_name(device_id: int | None) -> str:
    if _ca is None or not device_id:
        return ""
    addr = _Address(_fourcc("lnam"), _fourcc("glob"), 0)
    ref, size = ctypes.c_void_p(0), ctypes.c_uint32(ctypes.sizeof(ctypes.c_void_p))
    if _ca.AudioObjectGetPropertyData(device_id, ctypes.byref(addr), 0, None, ctypes.byref(size),
                                      ctypes.byref(ref)) != 0 or not ref.value:
        return ""
    try:
        buf = ctypes.create_string_buffer(256)
        ok = _cf.CFStringGetCString(ref, buf, len(buf), 0x08000100)       # kCFStringEncodingUTF8
        return buf.value.decode("utf-8", "replace") if ok else ""
    finally:
        _cf.CFRelease(ref)
