"""Deterministic Turkish commands. File mutations require an exact, unique target."""
import json
from pathlib import Path
import re
from actions.open_app import APP_ALIASES
from actions.file_management import find_file, fold
from actions.context_control import active_context

FOLDERS = {'masaustu': 'desktop', 'masaustunde': 'desktop', 'masaustundeki': 'desktop',
           'belgeler': 'documents', 'belgelerdeki': 'documents', 'belgelerde': 'documents',
           'indirilenler': 'downloads', 'indirilenlerdeki': 'downloads', 'indirilenlerde': 'downloads'}
DESTINATIONS = {'masaustune': 'desktop', 'belgelere': 'documents', 'indirilenlere': 'downloads'}


def normalized_with_spans(raw):
    result, offsets = [], []
    for index, char in enumerate(raw):
        text = fold(char).replace('’', "'")
        for part in text:
            result.append(part)
            offsets.append(index)
    return ''.join(result), offsets


def number(text):
    words = fold(text).replace('yuzde', '').strip().split()
    if len(words) == 1 and words[0].isdigit():
        return int(words[0])
    units = {'sifir': 0, 'bir': 1, 'iki': 2, 'uc': 3, 'dort': 4, 'bes': 5,
             'alti': 6, 'yedi': 7, 'sekiz': 8, 'dokuz': 9}
    tens = {'on': 10, 'yirmi': 20, 'otuz': 30, 'kirk': 40, 'elli': 50,
            'altmis': 60, 'yetmis': 70, 'seksen': 80, 'doksan': 90, 'yuz': 100}
    if len(words) == 1:
        return {**units, **tens}.get(words[0])
    if len(words) == 2 and words[0] in tens and words[1] in units:
        return tens[words[0]] + units[words[1]]
    return None


def clean_name(text):
    text = re.sub(r'\s+nokta\s+', '.', text.strip(), flags=re.I)
    return text.strip(' "“”').rstrip("'’")


def parse_command(raw):
    raw = str(raw).strip().rstrip('.!?')
    raw = re.sub(r'^(?:hey\s+)?jarvis[\s,:]+', '', raw, flags=re.I)
    raw = re.sub(r'[\s,]+jarvis$', '', raw, flags=re.I)
    text, offsets = normalized_with_spans(raw)
    def original(match, group):
        start, end = match.span(group)
        return raw[offsets[start]:offsets[end - 1] + 1].strip() if end > start else ''
    if text in ('merhaba', 'selam', 'beni duyuyor musun'):
        return {'message': 'Buradayım. İnternetsiz temel komutları kullanabilirsin.'}
    if text in ('son islemi geri al', 'geri al', 'az once yaptigini geri al'):
        return {'tool': 'undo_action', 'args': {}}
    if text in ('islem gecmisini goster', 'islem gecmisi'):
        return {'tool': 'get_action_history', 'args': {}}
    if text in ('yarim kalan islere devam et', 'kaldigin yerden devam et', 'gorevlere devam et'):
        return {'tool': 'resume_tasks', 'args': {}}
    if text in ('gorevler ne durumda', 'indirme ne durumda', 'yarim kalan isleri goster'):
        return {'tool': 'get_parallel_tasks', 'args': {}}
    if text in ('sesi kapat', 'sessize al', 'bilgisayari sessize al'):
        return {'tool': 'system_control', 'args': {'action': 'mute'}}
    if text in ('sesi ac', 'sessizden cikar'):
        return {'tool': 'system_control', 'args': {'action': 'unmute'}}
    if re.fullmatch(r'sesi (?:biraz )?(?:azalt|kis|dusur)', text):
        return {'tool': 'system_control', 'args': {'action': 'adjust_volume', 'value': -10}}
    if re.fullmatch(r'sesi (?:biraz )?(?:artir|arttir|yukselt)', text):
        return {'tool': 'system_control', 'args': {'action': 'adjust_volume', 'value': 10}}
    match = re.fullmatch(r'sesi (.+?) (?:yap|ayarla|getir)', text)
    if match and (value := number(match[1])) is not None and 0 <= value <= 100:
        return {'tool': 'system_control', 'args': {'action': 'set_volume', 'value': value}}
    if text in ('ses kac', 'ses seviyesi kac'):
        return {'tool': 'system_control', 'args': {'action': 'get_volume'}}
    info = {'pil durumu': 'battery', 'sarj ne kadar': 'battery', 'saat kac': 'time',
            'sistem bilgisi': 'all', 'disk durumu': 'disk', 'ram durumu': 'ram'}
    if text in info:
        return {'tool': 'sys_info', 'args': {'query': info[text]}}

    # Local Turkish ASR can fuse an app name and the short verb: "Safariac".
    # Only exact known aliases qualify; never split arbitrary filenames or sentences.
    for alias, target in APP_ALIASES.items():
        if text in (fold(alias) + 'ac', fold(alias) + 'baslat'):
            return {'tool': 'open_app', 'args': {'app_name': target}}

    # Full-utterance grammar avoids executing a stray verb in a longer sentence.
    match = re.fullmatch(r'(.+?) (?:uygulamasini )?(?:ac|baslat)', text)
    if match:
        value = match[1]
        app = None
        for alias, target in APP_ALIASES.items():
            if value in (fold(alias), *[fold(alias) + suffix for suffix in ("'yi", "'i", "'yu", "'u", 'yi', 'i', 'yu', 'u', 'ni')]):
                app = target
                break
        if app:
            return {'tool': 'open_app', 'args': {'app_name': app}}
        return {'message': 'Bu uygulama adını net eşleştiremedim. Örneğin Safari aç veya Hesap makinesi aç diyebilirsin.'}

    match = re.fullmatch(r'(.+?) adini (.+?) (?:yap|olarak degistir|degistir)', text)
    if match:
        return {'file_action': 'rename', 'source': original(match, 1), 'new_name': clean_name(original(match, 2))}
    match = re.fullmatch(r'(.+?) (masaustune|belgelere|indirilenlere) tasi', text)
    if match:
        return {'file_action': 'move', 'source': original(match, 1), 'destination': DESTINATIONS[match[2]]}
    match = re.fullmatch(r'(.+?) (?:cope at|cop sepetine tasi)', text)
    if match:
        return {'file_action': 'trash', 'source': original(match, 1)}
    match = re.fullmatch(r'(.+?) bul', text)
    if match:
        query, directory = source_query(original(match, 1))
        if not query:
            return {'message': 'Arayacağım dosyanın adını söylemelisin.'}
        return {'tool': 'find_file', 'args': {'query': query, 'directory': directory, 'limit': 8}}
    return {'message': 'İnternetsiz moddayım. Uygulama açabilir, sesi ayarlayabilir ve dosya bulup taşıyabilir ya da adını değiştirebilirim. Web araştırması ve serbest sohbet için bağlantı gerekli.'}


def source_query(source):
    source = source.strip(' "“”')
    parts = source.split(maxsplit=1)
    directory = FOLDERS.get(fold(parts[0]), '') if parts else ''
    if directory:
        source = parts[1] if len(parts) > 1 else ''
    source = re.sub(r'\s+(?:dosyas[ıi]n[ıi]n|dosyan[ıi]n|dosyas[ıi]n[ıi]|dosyay[ıi]|dosya|klasörünün|klasorunun|klasörünü|klasorunu|belgesini)$', '', source, flags=re.I)
    source = re.sub(r"['’](?:yi|yı|yu|yü|i|ı|u|ü)$", '', source, flags=re.I)
    return clean_name(source), directory


def resolve_file_command(intent):
    source, directory = source_query(intent['source'])
    if fold(source) in ('bunu', 'bunun', 'secili', 'secili dosya', 'secili dosyayi', 'secili dosyanin'):
        context = active_context(include_history=False)
        paths = context.get('selected_paths', [])
        if context.get('bundle_id') != 'com.apple.finder' or len(paths) != 1:
            return {'message': 'Finder’da tek bir dosya veya klasör seçmelisin.'}
        path = Path(paths[0])
    else:
        if not source:
            return {'message': 'Dosyanın adını veya Finder’da seçili tek dosyayı belirtmelisin.'}
        data = json.loads(find_file(query=source, directory=directory, limit=100))
        matches = [Path(item['path']) for item in data.get('matches', [])
                   if fold(Path(item['path']).name) == fold(source)
                   or (not Path(source).suffix and fold(Path(item['path']).stem) == fold(source))]
        if len(matches) != 1 or data.get('truncated'):
            return {'message': 'Dosyayı tek bir kesin eşleşmeyle bulamadım. Klasörünü ve tam adını söyle veya Finder’da tek dosya seç.'}
        path = matches[0]
    if intent['file_action'] == 'rename':
        name = intent['new_name']
        if not Path(name).suffix and path.is_file():
            name += path.suffix
        return {'tool': 'rename_file', 'args': {'path': str(path), 'new_name': name}}
    if intent['file_action'] == 'move':
        return {'tool': 'move_file', 'args': {'source_paths': [str(path)], 'destination': intent['destination']}}
    return {'tool': 'trash_file', 'args': {'paths': [str(path)]}}


def spoken_result(result):
    try:
        data = json.loads(result)
    except (ValueError, TypeError):
        return str(result)[:700]
    if not isinstance(data, dict):
        return str(result)[:700]
    if data.get('spoken_summary'):
        return data['spoken_summary'][:900]
    if data.get('message'):
        return str(data['message'])[:700]
    if 'matches' in data:
        names = [Path(item['path']).name for item in data['matches'][:4]]
        return 'Bulduğum dosyalar: ' + ', '.join(names) if names else 'Eşleşen dosya bulunamadı.'
    if 'old_path' in data:
        return 'Dosyanın adı ' + Path(data['path']).name + ' olarak değiştirildi.'
    if 'completed' in data:
        return f"{len(data['completed'])} dosya işlemi tamamlandı."
    if 'actions' in data:
        return 'İşlem geçmişini açtım.'
    return 'İşlem tamamlandı.' if data.get('status') == 'ok' else 'İşlem tamamlanamadı.'
