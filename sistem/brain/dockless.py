"""İkinci Beyin penceresini Dock'ta ayrı bir simge göstermeden açar.

Pencere ayrı bir Python sürecidir; Tk açılırken kendini "Dock'ta görünen uygulama" yaptığı için Dock'ta
"Python" adlı ikinci bir simge çıkıyordu. Burada Tk penceresi oluşur oluşmaz macOS'a bu sürecin yardımcı
(accessory) bir uygulama olduğu söylenir: pencere açılır, odaklanır, klavye ve kamera çalışır ama Dock'ta
ve Cmd+Tab listesinde ayrı simge olmaz. brain.window'a dokunulmaz; bu yalnızca onu saran başlatıcıdır.

Çalıştırma: python -m brain.dockless [--focus "sorgu"] [--ipc]   (brain.window ile aynı argümanlar)
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
import tkinter

NS_APPLICATION_ACTIVATION_POLICY_ACCESSORY = 1


def hide_from_dock() -> bool:
    """[NSApp setActivationPolicy:Accessory]; başarısızsa pencere yine açılır (yalnız Dock simgesi kalır)."""
    if sys.platform != "darwin":
        return False
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        # objc_msgSend, arm64'te her imza için doğru türle çağrılmalı.
        send_id = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
        send_long = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(
            ("objc_msgSend", objc))
        app = send_id(objc.objc_getClass(b"NSApplication"), objc.sel_registerName(b"sharedApplication"))
        if not app:
            return False
        return bool(send_long(app, objc.sel_registerName(b"setActivationPolicy:"),
                              NS_APPLICATION_ACTIVATION_POLICY_ACCESSORY))
    except Exception:
        return False


def _patch_tk() -> None:
    original = tkinter.Tk.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        hide_from_dock()

    tkinter.Tk.__init__ = init


def main(argv=None) -> int:
    _patch_tk()
    from brain import window
    return window.main(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    sys.exit(main())
