"""Local mutation journal and guarded undo; no model-supplied inverse commands."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from functools import wraps
import fcntl
import inspect
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import threading
import uuid

DEFAULT_PATH = Path(__file__).resolve().parent.parent / 'memory' / 'action_history.sqlite3'
LABELS = {'move_file': 'Dosya taşıma', 'rename_file': 'Dosya adı değiştirme',
          'trash_file': 'Çöp Sepeti’ne taşıma', 'add_calendar_event': 'Takvim etkinliği ekleme',
          'delete_calendar_event': 'Takvim etkinliği silme', 'add_reminder': 'Hatırlatıcı ekleme'}
OTHER_CHANGES = {'system_control': 'Ses ayarı', 'open_app': 'Uygulama açma', 'browser_control': 'Tarayıcı işlemi',
                 'play_media': 'Medya oynatma', 'shell_run': 'Terminal işlemi',
                 'save_memory': 'Hafıza kaydı', 'delete_memory': 'Hafıza kaydı silme',
                 'send_whatsapp_message': 'WhatsApp işlemi', 'save_whatsapp_contact': 'Kişi kaydı',
                 'context_control': 'Bağlamsal işlem', 'toggle_webcam': 'Kamera işlemi'}
_capture = ContextVar('jarvis_undo_capture', default=None)
KEEP_ROWS = 2000  # Her uygulama açma/medya işlemi de yazılıyor; veritabanı sınırsız büyümesin.


def _prune(db):
    db.execute('DELETE FROM actions WHERE rowid < (SELECT MIN(rowid) FROM '
               '(SELECT rowid FROM actions ORDER BY rowid DESC LIMIT ?))', (KEEP_ROWS,))


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def identity(path):
    info = Path(path).lstat()  # Track the link itself, never follow a symlink.
    return [info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode),
            getattr(info, 'st_birthtime_ns', int(getattr(info, 'st_birthtime', 0) * 1e9))]


def capture_created(kind, item):
    capture = _capture.get()
    key = 'event_id' if kind == 'event' else 'item_id'
    if capture is not None and isinstance(item, dict) and item.get(key):
        capture.update(kind=kind, expected=item)


def _case_only(source, target):
    """Büyük/küçük harfe duyarsız diskte yalnızca ad harfleri değişen aynı dosya mı?"""
    source, target = Path(source), Path(target)
    if source.parent != target.parent or source.name == target.name:
        return False
    if source.name.casefold() != target.name.casefold():
        return False
    try:
        return identity(source) == identity(target)
    except OSError:
        return False


class ActionJournal:
    def __init__(self, path=DEFAULT_PATH):
        self.path = Path(path)
        self.lock = threading.RLock()

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        os.chmod(self.path, 0o600)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('''CREATE TABLE IF NOT EXISTS actions (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, tool TEXT NOT NULL,
            label TEXT NOT NULL, state TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
            undo_json TEXT NOT NULL DEFAULT '{}', last_error TEXT NOT NULL DEFAULT '')''')
        db.commit()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def _locked(self):
        # The phone agent and desktop app can share this database.
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(str(self.path) + '.lock', 'a') as handle:
                os.chmod(handle.name, 0o600)
                fcntl.flock(handle, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def _update(self, operation_id, **fields):
        with self._connect() as db:
            db.execute('UPDATE actions SET ' + ','.join(key + '=?' for key in fields) + ' WHERE id=?',
                       [*fields.values(), operation_id])

    def execute(self, tool, function, args, kwargs):
        with self._locked():
            operation_id = uuid.uuid4().hex
            # Check storage before making a change. A failed journal cannot promise undo.
            try:
                with self._connect() as db:
                    db.execute('INSERT INTO actions(id,created_at,tool,label,state) VALUES(?,?,?,?,?)',
                               (operation_id, datetime.now().astimezone().isoformat(), tool,
                                LABELS[tool], 'running'))
                    _prune(db)
            except (OSError, sqlite3.Error) as exc:
                return _json({'status': 'error', 'message': 'İşlem geçmişi kaydedilemedi; değişiklik yapılmadı.',
                              'detail': str(exc)})
            parents = {}
            if tool in ('move_file', 'rename_file'):
                from actions.file_management import path_of
                try:
                    bound = inspect.signature(function).bind(*args, **kwargs)
                    values = bound.arguments
                    paths = values.get('source_paths', [values.get('path')])
                    if isinstance(paths, str):
                        paths = [paths]  # Tek yol metni harf harf dolaşılıp geri alma kaydı kayboluyordu.
                    parents = {str(path_of(p).parent): identity(path_of(p).parent) for p in paths}
                except (ValueError, TypeError, OSError):
                    pass  # The original tool validates its arguments and reports the problem.
            captured = {}
            token = _capture.set(captured)
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                self._update(operation_id, state='failed', summary=str(exc)[:500])
                raise
            finally:
                _capture.reset(token)
            try:
                data = json.loads(result)
            except (ValueError, TypeError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            state = 'done' if data.get('status') == 'ok' or captured else 'failed'
            if data.get('status') == 'partial':
                state = 'partial'
            if tool == 'delete_calendar_event' and str(result).startswith('Takvimden silindi:'):
                state = 'done'
            if str(result).startswith(('Takvime eklendi:', 'Animsatici eklendi:')):
                state = 'done'
            if tool == 'rename_file' and state == 'done' and not data.get('old_path'):
                state = 'no_change'
            summary = str(data.get('message') or data.get('note') or result)[:700]
            undo = captured
            try:
                moves = data.get('completed', []) if tool == 'move_file' else []
                if tool == 'rename_file' and data.get('old_path') and data.get('path'):
                    moves = [{'from': data['old_path'], 'to': data['path']}]
                if moves:
                    undo = {'kind': 'files', 'moves': [
                        {'from': item['from'], 'to': item['to'], 'identity': identity(item['to']),
                         'parent_identity': parents[str(Path(item['from']).parent)], 'undone': False}
                        for item in moves]}
                    summary = '\n'.join(f"{item['from']} → {item['to']}" for item in moves)
                elif tool == 'rename_file' and state == 'done':
                    summary = str(data.get('note', 'Ad zaten aynı.'))
                elif captured:
                    summary = str(result)
                self._update(operation_id, state=state, summary=summary[:4000], undo_json=_json(undo))
            except (OSError, KeyError, sqlite3.Error) as exc:
                # The action already happened. Keep the successful result, do not retry it.
                note = 'İşlem yapıldı fakat geri alma kaydı tamamlanamadı.'
                try:
                    self._update(operation_id, state=state, summary=summary[:4000], last_error=note)
                except (OSError, sqlite3.Error):
                    pass
                if data:
                    data['history_warning'] = note
                    return _json(data)
                return str(result) + '\n' + note
            return result

    def history(self, limit=30):
        with self._connect() as db:
            rows = db.execute('SELECT * FROM actions ORDER BY rowid DESC LIMIT ?',
                              (max(1, min(100, int(limit or 30))),)).fetchall()
        return {'status': 'ok', 'actions': [
            {'id': row['id'], 'created_at': row['created_at'], 'tool': row['tool'],
             'label': row['label'], 'state': row['state'], 'summary': row['summary'],
             'can_undo': row['state'] in ('done', 'partial') and row['undo_json'] != '{}',
             'last_error': row['last_error']} for row in rows],
            'note': 'Bu özellik açıldıktan sonraki işlemler. Yalnızca can_undo=true olanlar geri alınabilir.'}

    def record_other_change(self, tool, arguments, failed):
        if tool == 'system_control' and arguments.get('action', 'get_volume') == 'get_volume':
            return
        if tool not in OTHER_CHANGES:
            return
        if tool == 'browser_control' and arguments.get('action') == 'search':
            return
        if tool == 'context_control' and arguments.get('action') not in (
                'replace_active_code', 'rename_note', 'pause_video', 'click_button'):
            return  # Reads need no undo; Finder moves are journaled by move_file itself.
        # Never skip a newer unsupported change and silently reverse an older one.
        description = next((str(arguments[key]) for key in (
            'app_name', 'action', 'display_name', 'query', 'category') if arguments.get(key)), '')
        with self._locked(), self._connect() as db:
            db.execute('INSERT INTO actions(id,created_at,tool,label,state,summary) VALUES(?,?,?,?,?,?)',
                       (uuid.uuid4().hex, datetime.now().astimezone().isoformat(), tool,
                        OTHER_CHANGES[tool], 'failed' if failed else 'done', description[:500]))
            _prune(db)

    def undo(self, operation_id=''):
        with self._locked():
            with self._connect() as db:
                if operation_id:
                    row = db.execute('SELECT * FROM actions WHERE id=?', (operation_id,)).fetchone()
                else:
                    # Do not silently skip a newer operation that cannot be reversed.
                    row = db.execute("SELECT * FROM actions WHERE state IN ('done','partial') ORDER BY rowid DESC LIMIT 1").fetchone()
            if row is None:
                return {'status': 'no_match', 'message': 'Geri alınacak bir işlem kaydı bulunamadı.'}
            if row['state'] == 'undone':
                return {'status': 'already_done', 'message': 'Bu işlem zaten geri alındı.'}
            if row['state'] not in ('done', 'partial'):
                return {'status': 'error', 'message': 'Bu işlemin tamamlandığı doğrulanamıyor; geri alınmadı.'}
            undo = json.loads(row['undo_json'])
            if not undo:
                return {'status': 'unsupported', 'message': 'Bu işlem otomatik geri almayı desteklemiyor.',
                        'label': row['label'], 'id': row['id']}
            try:
                if undo['kind'] == 'files':
                    from actions.file_management import guard, move_one
                    pending = [item for item in reversed(undo['moves']) if not item['undone']]
                    # Validate the whole batch before touching its first file.
                    for item in pending:
                        source, target = Path(item['to']), Path(item['from'])
                        guard(source)
                        guard(target)
                        if identity(source) != item['identity']:
                            raise ValueError('Dosya kimliği değişmiş; başka bir dosyaya dokunulmadı.')
                        if identity(target.parent) != item['parent_identity']:
                            raise ValueError('Önceki klasör değişmiş; geri alma durduruldu.')
                        if os.path.lexists(target) and not _case_only(source, target):
                            raise ValueError(f'Eski konumda başka bir öğe var; üzerine yazılmadı: {target}')
                    for item in pending:
                        # Recheck immediately before the native no-overwrite move.
                        if identity(item['to']) != item['identity']:
                            raise ValueError('İşlem sırasında dosya değişti; geri alma durduruldu.')
                        if identity(Path(item['from']).parent) != item['parent_identity']:
                            raise ValueError('İşlem sırasında önceki klasör değişti; geri alma durduruldu.')
                        if _case_only(Path(item['to']), Path(item['from'])):
                            os.rename(item['to'], item['from'])  # rapor ↔ Rapor: aynı dosya
                        else:
                            move_one(Path(item['to']), Path(item['from']))
                        item['undone'] = True
                        self._update(row['id'], undo_json=_json(undo))
                elif undo['kind'] in ('event', 'reminder'):
                    from actions.calendar import _run_helper
                    mode = 'undo_created_event' if undo['kind'] == 'event' else 'reminders_undo_created'
                    ok, raw = _run_helper(mode, {'expected': undo['expected']}, timeout=25)
                    data = json.loads(raw) if ok else {}
                    if not ok or not data.get('ok'):
                        raise ValueError(data.get('detail') or raw or 'Kayıt geri alınamadı.')
                else:
                    raise ValueError('Bu işlem türü geri alınamıyor.')
                self._update(row['id'], state='undone', last_error='')
                return {'status': 'ok', 'id': row['id'], 'message': 'İşlem geri alındı.',
                        'label': row['label'], 'summary': row['summary']}
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, sqlite3.Error) as exc:
                message = str(exc) or 'İşlem geri alınamadı.'
                self._update(row['id'], last_error=message[:700])
                return {'status': 'error', 'id': row['id'], 'message': message,
                        'label': row['label'], 'note': 'Tamamlanan geri alma adımları kaydedildi; aynı adım tekrarlanmaz.'}


_journal = ActionJournal()


def tracked_action(tool):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            return _journal.execute(tool, function, args, kwargs)
        return wrapped
    return decorate


def get_action_history(limit=30):
    try:
        return _json(_journal.history(limit))
    except (OSError, ValueError, sqlite3.Error) as exc:
        return _json({'status': 'error', 'message': 'İşlem geçmişi okunamadı.', 'detail': str(exc)})


def record_other_change(tool, arguments, failed=False):
    _journal.record_other_change(tool, arguments, failed)


def undo_action(operation_id=''):
    try:
        return _json(_journal.undo(str(operation_id or '')))
    except (OSError, ValueError, sqlite3.Error) as exc:
        return _json({'status': 'error', 'message': 'İşlem geri alınamadı.', 'detail': str(exc)})
