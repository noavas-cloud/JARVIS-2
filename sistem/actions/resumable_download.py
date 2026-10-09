"""Checkpointed HTTPS transfers with validated ranges and idempotent publication."""
from email.message import Message
import os
from pathlib import Path
import re
import socket
import time
import uuid
from urllib.parse import urljoin, urlsplit
import requests
from actions.local_state import LocalState

# Bunlar geçicidir: parça ve kayıt korunur, bağlantı gelince devam edilir.
NETWORK_ERRORS = (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                  requests.exceptions.ChunkedEncodingError, socket.gaierror)
_TEMP_RE = r'\.jarvis-[a-f0-9]{32}\.part'


def discard_transfer(checkpoint_path):
    """Vazgeçilen indirmenin gizli .part parçasını ve kaydını temizler (en fazla 2 GiB yer kaplıyordu)."""
    if not checkpoint_path:
        return
    store = LocalState(checkpoint_path)
    try:
        state = store.read({})
    except (OSError, ValueError):
        state = {}
    temp, folder = str(state.get('temp') or ''), state.get('folder')
    if folder and re.fullmatch(_TEMP_RE, temp) and not state.get('published'):
        try:
            (Path(folder) / temp).unlink(missing_ok=True)
        except OSError:
            pass
    try:
        store.path.unlink(missing_ok=True)
    except OSError:
        pass


def download_file(url, progress, folder, checkpoint_path=None):
    # Lazy import avoids a circular module dependency.
    from actions.parallel_tasks import _check_url, _safe_name, _available_path, MAX_DOWNLOAD_BYTES, CHUNK_BYTES
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise OSError('İndirilenler klasörü açılamadı.')
    store = LocalState(checkpoint_path) if checkpoint_path else None
    state = store.read({}) if store else {}
    if state and (state.get('source') != url or state.get('folder') != str(folder)):
        raise ValueError('İndirme kaydı bu dosyayla eşleşmiyor.')
    if not state:
        state = {'source': url, 'folder': str(folder), 'temp': f'.jarvis-{uuid.uuid4().hex}.part'}
    if not re.fullmatch(_TEMP_RE, state['temp']):
        raise ValueError('Geçersiz geçici dosya kaydı.')
    temporary = folder / state['temp']
    def save():
        if store:
            store.write(state)
    def identity(path):
        stat = path.lstat()
        return [stat.st_dev, stat.st_ino, stat.st_size]
    def result(destination):
        return {'status': 'done', 'result': destination.name + ' indirildi.',
                'path': str(destination), 'bytes': state['bytes']}
    if state.get('published'):
        destination = folder / _safe_name(state['published'])
        if destination.exists() and not destination.is_symlink() and identity(destination) == state.get('identity'):
            return result(destination)
        raise ValueError('Önceki indirmenin hedefi değişmiş; tekrar indirmedim.')
    if temporary.is_symlink():
        raise ValueError('Geçici indirme dosyası bir bağlantıya dönüşmüş.')
    if temporary.exists() and state.get('temp_identity') != identity(temporary)[:2]:
        raise ValueError('Geçici indirme dosyasının kimliği değişmiş.')
    def publish():
        # Persist intended destination before link: a crash immediately after link
        # is recognized using inode identity, never creates another download copy.
        destination = folder / _safe_name(state.get('planned') or state['filename'])
        if state.get('planned') and destination.exists() and not destination.is_symlink() and os.path.samefile(temporary, destination):
            pass
        else:
            for _ in range(1000):
                destination = _available_path(folder, state['filename'])
                state['planned'] = destination.name
                save()
                try:
                    os.link(temporary, destination, follow_symlinks=False)
                    break
                except FileExistsError:
                    continue
            else:
                raise OSError('Dosya için boş ad bulunamadı.')
        state['published'] = destination.name
        state['identity'] = identity(destination)
        save()
        temporary.unlink(missing_ok=True)
        progress(state['bytes'], state.get('total'))
        return result(destination)
    if state.get('complete'):
        if not temporary.exists() or temporary.stat().st_size != state.get('bytes'):
            raise ValueError('Tamamlanmış geçici dosya bulunamadı veya değişmiş.')
        return publish()

    response = None
    session = requests.Session()
    save()
    try:
        request_url = _check_url(url)
        offset = temporary.stat().st_size if temporary.exists() else 0
        validator = state.get('validator')
        # Without a validator we cannot prove that a remote file has not changed.
        if not validator:
            offset = 0
        headers = {'Accept-Encoding': 'identity'}
        if offset:
            headers.update({'Range': f'bytes={offset}-', 'If-Range': validator})
        for _ in range(6):
            response = session.get(request_url, stream=True, timeout=(5, 15), allow_redirects=False, headers=headers)
            if response.status_code in (301, 302, 303, 307, 308):
                target = response.headers.get('Location')
                response.close()
                if not target:
                    raise ValueError('İndirme yönlendirmesi eksik.')
                request_url = _check_url(urljoin(request_url, target))
                continue
            break
        else:
            raise ValueError('Çok fazla indirme yönlendirmesi var.')
        if response.status_code == 416 and offset:
            response.close()
            headers = {'Accept-Encoding': 'identity'}
            response = session.get(request_url, stream=True, timeout=(5, 15), allow_redirects=False, headers=headers)
            offset = 0
        response.raise_for_status()
        if response.status_code not in (200, 206):
            raise ValueError('Beklenmeyen indirme yanıtı.')
        if 'text/html' in response.headers.get('Content-Type', '').lower():
            raise ValueError('Bu bağlantı dosya yerine web sayfası döndürdü.')
        etag = response.headers.get('ETag', '')
        current_validator = (etag if etag and not etag.startswith('W/') else response.headers.get('Last-Modified'))
        declared = response.headers.get('Content-Length', '')
        total = int(declared) if declared.isdecimal() else None
        if response.status_code == 206:
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
            if not offset or not match or int(match[1]) != offset or int(match[2]) != int(match[3]) - 1:
                raise ValueError('Sunucu geçersiz devam aralığı döndürdü; dosyalar birleştirilmedi.')
            if current_validator and current_validator != validator:
                raise ValueError('Sunucudaki dosya değişmiş; parçalar birleştirilmedi.')
            if total is not None and total != int(match[3]) - offset:
                raise ValueError('İndirme aralığının uzunluğu uyuşmuyor.')
            total = int(match[3])
        else:
            offset = 0  # Server ignored Range or If-Range detected a new revision.
        if total is not None and total > MAX_DOWNLOAD_BYTES:
            raise ValueError('Dosya 2 GiB indirme sınırını aşıyor.')
        disposition = Message()
        disposition['content-disposition'] = response.headers.get('Content-Disposition', '')
        filename = _safe_name(disposition.get_filename() or urlsplit(request_url).path.rstrip('/').split('/')[-1])
        state.update(filename=filename, validator=current_validator, total=total, complete=False)
        flags = os.O_WRONLY | os.O_NOFOLLOW
        flags |= os.O_APPEND if offset else os.O_TRUNC
        if not temporary.exists():
            flags |= os.O_CREAT | os.O_EXCL
        with os.fdopen(os.open(temporary, flags, 0o600), 'ab' if offset else 'wb') as output:
            state['temp_identity'] = identity(temporary)[:2]
            save()
            received, last_emit = offset, 0.0
            for chunk in response.iter_content(CHUNK_BYTES):
                if not chunk:
                    continue
                received += len(chunk)
                if received > MAX_DOWNLOAD_BYTES:
                    raise ValueError('Dosya 2 GiB indirme sınırını aştı.')
                output.write(chunk)
                if time.monotonic() - last_emit >= .5:
                    output.flush()
                    progress(received, total)
                    last_emit = time.monotonic()
            output.flush()
            os.fsync(output.fileno())
        if total is not None and received != total:
            raise requests.exceptions.ConnectionError('İndirme eksik; bağlantı gelince devam edilecek.')
        state.update(complete=True, bytes=received)
        save()
        return publish()
    except NETWORK_ERRORS:
        raise
    except Exception:
        # Kalıcı hata (HTML sayfa, 404, boyut sınırı, değişmiş dosya, disk dolu): yarım parça
        # İndirilenler'de gizli olarak kalmasın; bir sonraki deneme temiz başlasın.
        if not state.get('published'):
            temporary.unlink(missing_ok=True)
        if store:
            try:
                store.path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    finally:
        if response is not None:
            response.close()
        session.close()
        if not store:
            temporary.unlink(missing_ok=True)
