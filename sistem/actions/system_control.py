"""Bounded local output-volume controls; no arbitrary AppleScript input."""
import json
import re
import subprocess


def _script(script):
    result = subprocess.run(['/usr/bin/osascript', '-e', script], capture_output=True,
                            text=True, timeout=5)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Ses ayarı yapılamadı.')
    return result.stdout.strip()


FIXED_OUTPUT = ('Bu ses çıkışının düzeyi macOS üzerinden ayarlanamıyor (ör. HDMI/dijital çıkış); '
                'sesi bağlı cihazdan değiştir.')


def volume_state():
    raw = _script('get volume settings')
    volume = re.search(r'output volume:(\d+|missing value)', raw)
    muted = re.search(r'output muted:(true|false|missing value)', raw)
    if not volume or not muted:
        raise RuntimeError('Mevcut ses düzeyi okunamadı.')
    # HDMI/DisplayPort gibi çıkışlarda macOS "missing value" döndürür; bu bir hata değil.
    return {'volume': None if volume[1] == 'missing value' else int(volume[1]),
            'muted': None if muted[1] == 'missing value' else muted[1] == 'true'}


def system_control(action='get_volume', value=None):
    try:
        before = volume_state()
        if action == 'get_volume':
            if before['volume'] is None:
                return json.dumps({'status': 'ok', **before, 'message': FIXED_OUTPUT}, ensure_ascii=False)
            return json.dumps({'status': 'ok', **before,
                               'message': 'Ses kapalı.' if before['muted'] else f"Ses yüzde {before['volume']}."},
                              ensure_ascii=False)
        if before['volume'] is None or before['muted'] is None:
            raise RuntimeError(FIXED_OUTPUT)
        if action in ('mute', 'unmute'):
            wanted = action == 'mute'
            _script('set volume output muted ' + ('true' if wanted else 'false'))
        elif action in ('set_volume', 'adjust_volume'):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not -100 <= value <= 100:
                raise ValueError('Ses değeri 0–100, göreli değişiklik -100–100 aralığında olmalı.')
            if action == 'set_volume' and value < 0:
                raise ValueError('Ses değeri negatif olamaz.')
            target = max(0, min(100, round(value + (before['volume'] if action == 'adjust_volume' else 0))))
            _script(f'set volume output volume {target}')
            if target > 0:
                _script('set volume output muted false')
        else:
            raise ValueError('Bu sistem komutu desteklenmiyor.')
        after = volume_state()
        if action in ('mute', 'unmute') and after['muted'] != wanted:
            raise RuntimeError('Sessize alma durumu doğrulanamadı.')
        # macOS düzeyi cihaz adımlarına yuvarlayabilir (33 → 32/34); ±2 fark başarısızlık değildir.
        if action in ('set_volume', 'adjust_volume') and (after['volume'] is None or abs(after['volume'] - target) > 2):
            raise RuntimeError('Ses düzeyi değişikliği doğrulanamadı.')
        message = 'Ses kapatıldı.' if after['muted'] else f"Ses yüzde {after['volume']}."
        return json.dumps({'status': 'ok', **after, 'message': message}, ensure_ascii=False)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return json.dumps({'status': 'error', 'message': str(exc)}, ensure_ascii=False)
