"""
Terminal komutu çalıştırma — macOS bash
"""

import os
import shlex
import signal
import subprocess


# Tehlikeli komutları engelle
BLOCKED = [
    "rm -rf /",
    "sudo rm -rf",
    "mkfs",
    "dd if=",
    ":(){:|:&};:",
    "shutdown",
    "reboot",
    "halt",
    "diskutil erase",
    "diskutil apfs deletecontainer",
    ">:",
]


# Kalıcı silme / üzerine yazma / yetki değiştirme yapan komutlar (boru hattının HERHANGİ bir
# yerinde, tam yolla ya da xargs/env/nohup gibi sarmalayıcılarla çağrılsa da engellenir).
DESTRUCTIVE = {
    "rm", "rmdir", "unlink", "srm", "shred", "mv", "cp", "ln", "install", "rsync", "ditto",
    "chmod", "chown", "chgrp", "chflags", "xattr", "sudo", "su", "doas", "dd", "truncate",
    "shutdown", "reboot", "halt", "kill", "killall", "pkill", "launchctl", "defaults", "crontab",
    "tmutil", "csrutil", "nvram", "pmset", "scutil", "networksetup", "security", "spctl",
}
WRAPPERS = {"command", "builtin", "exec", "nohup", "time", "nice", "env", "caffeinate", "stdbuf", "timeout",
            "xargs", "then", "do", "else", "if", "while", "until", "!", "{"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
INTERPRETERS = {"python", "python3", "perl", "ruby", "node", "osascript", "swift", "php", "lua", "tclsh"}
SAFE_DISKUTIL = {"list", "info", "activity", "verifyvolume", "verifydisk", "apfs"}
SAFE_REDIRECT = {"/dev/null", "/dev/stdout", "/dev/stderr"}
SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "`", "|&", ";;", "$"}


def _segments(line: str) -> list[list[str]]:
    lex = shlex.shlex(line, posix=True, punctuation_chars="();<>|&`")
    lex.whitespace_split = True
    seg: list[str] = []
    out = [seg]
    for tok in lex:
        if tok in SEPARATORS:
            seg = []
            out.append(seg)
        else:
            seg.append(tok)
    return [s for s in out if s]


def _command_word(tokens: list[str]) -> tuple[str, int]:
    """Segmentteki gerçek komutun adı (sarmalayıcılar ve VAR=değer atlanır) ve konumu."""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        base = os.path.basename(tok).lower()
        if "=" in tok and not tok.startswith(("-", "=")) and tok.split("=", 1)[0].replace("_", "").isalnum():
            i += 1
            continue
        if base in WRAPPERS or (tok.startswith("-") and i > 0):
            i += 1
            if base == "timeout" and i < len(tokens) and not tokens[i].startswith("-"):
                i += 1   # süre değeri
            continue
        return base, i
    return "", len(tokens)


def _unsafe_reason(command: str, depth: int = 0) -> str | None:
    if depth > 3:
        return "iç içe kabuk komutu"
    lowered = command.lower()
    for blocked in BLOCKED:
        if blocked in lowered:
            return blocked
    if "do shell script" in lowered:
        return "do shell script"
    for line in command.splitlines() or [command]:
        try:
            segments = _segments(line)
        except ValueError:
            return "komut ayrıştırılamadı (kapanmamış tırnak)"
        for k, seg in enumerate(segments):
            for j, tok in enumerate(seg):
                if tok in (">", ">|", "&>", "&>>") or (tok.endswith(">") and tok[:-1].isdigit()):
                    target = seg[j + 1] if j + 1 < len(seg) else ""
                    if tok != "&>>" and target not in SAFE_REDIRECT and not target.startswith("&"):
                        return "dosyanın üzerine yazan yönlendirme (>)"
            name, at = _command_word(seg)
            if not name:
                continue
            args = seg[at + 1:]
            if name in DESTRUCTIVE or name.startswith(("mkfs", "newfs", "fdisk")):
                return name
            # xargs sonrası gerçek komut da denetlenir (ör. "ls | xargs rm")
            if any(os.path.basename(t).lower() in DESTRUCTIVE for t in seg[:at]
                   if os.path.basename(t).lower() not in WRAPPERS):
                return name
            if name == "find" and any(a in ("-delete", "-exec", "-execdir", "-ok", "-okdir") for a in args):
                return "find -delete/-exec"
            if name == "diskutil" and (not args or args[0].lower() not in SAFE_DISKUTIL
                                       or (args[0].lower() == "apfs" and any("delete" in a.lower() or "erase" in a.lower() for a in args))):
                return "diskutil"
            if name in SHELLS:
                if "-c" in args:
                    inner = args[args.index("-c") + 1] if args.index("-c") + 1 < len(args) else ""
                    reason = _unsafe_reason(inner, depth + 1)
                    if reason:
                        return reason
                elif k > 0 or not args:
                    return f"komutu {name} kabuğuna aktarma"
            if name in INTERPRETERS or name.startswith("python"):
                if name == "osascript" and "-e" not in args:
                    return "osascript"
                if any(a in ("-c", "-e") for a in args) and name != "osascript":
                    return f"{name} ile satır içi betik"
                if k > 0 and not args:
                    return f"komutu {name} yorumlayıcısına aktarma"
            if name in ("eval", "source", "."):
                return name
    return None


def _run(command: str, timeout: int) -> tuple[int | None, str]:
    """Komutu kendi süreç grubunda çalıştırır; zaman aşımında tüm alt süreçler de öldürülür."""
    proc = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        out, _ = proc.communicate()
        return None, (out or b"").decode("utf-8", errors="replace")
    return proc.returncode, (out or b"").decode("utf-8", errors="replace")


def shell_run(command: str, timeout: int = 30) -> str:
    if not command or not command.strip():
        return "Komut belirtilmedi."

    reason = _unsafe_reason(command)
    if reason:
        return (
            f"Güvenlik: Bu komut çalıştırılmadı ({reason}). Dosya silme/taşıma/üzerine yazma veya yetki "
            "değiştirme shell_run ile yapılmaz; find_file, move_file, rename_file, trash_file kullan."
        )

    try:
        code, output = _run(command, timeout)
        output = output.strip()
        if code is None:
            return f"Komut zaman aşımına uğradı ({timeout}s)." + (f"\n{output[:400]}" if output else "")
        if not output:
            return "Komut başarıyla çalıştı (çıktı yok)."
        # Çok uzun çıktıları kırp
        if len(output) > 800:
            output = output[:800] + "\n... (çıktı kısaltıldı)"
        return output
    except Exception as e:
        return f"Hata: {e}"
