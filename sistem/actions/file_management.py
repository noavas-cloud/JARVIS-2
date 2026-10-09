"""Mac dosya araçları: sınırlandırılmış arama, üzerine yazmadan taşıma, Çöp Sepeti."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import json
import os
import plistlib
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
import unicodedata

HOME = Path.home()
SKIP = {'.git','.Trash','node_modules','venv','.venv','__pycache__','Library'}
PACKAGES = ('.app','.photoslibrary','.bundle','.framework')

def reply(status, **fields):
    return json.dumps({'status':status, **fields},ensure_ascii=False)

def fold(text):
    return ''.join(c for c in unicodedata.normalize('NFKD',str(text).lower().replace('ı','i')) if not unicodedata.combining(c))

def path_of(value):
    aliases={'desktop':HOME/'Desktop','masaustu':HOME/'Desktop','downloads':HOME/'Downloads','indirilenler':HOME/'Downloads','documents':HOME/'Documents','belgeler':HOME/'Documents','home':HOME}
    raw=str(value or '').strip()
    if not raw: raise ValueError('Dosya veya klasör yolu boş olamaz.')
    if fold(raw) in aliases:return aliases[fold(raw)]
    p=Path(raw).expanduser()
    if not p.is_absolute():raise ValueError('Tam dosya yolu veya desktop/downloads/documents kullan.')
    # "…/Desktop/.." son bileşeni resolve edilmediği için korumalı klasör denetimini atlatabiliyordu.
    if p.name in ('','.','..'):raise ValueError('Yol "." veya ".." ile bitemez; tam dosya/klasör yolu ver.')
    return p.parent.resolve()/p.name  # Son bileşen sembolik bağlantıysa kendisi üzerinde işlem yap.

def date_window(value):
    value=fold(value or '').strip()
    today=datetime.now().astimezone().replace(hour=0,minute=0,second=0,microsecond=0)
    if not value or value=='any':return None
    if value in ('last_week','gecen hafta'):
        end=today-timedelta(days=today.weekday())
        return end-timedelta(days=7),end
    if value in ('last_7_days','son 7 gun'):return datetime.now().astimezone()-timedelta(days=7),datetime.now().astimezone()
    if value in ('today','bugun'):return today,today+timedelta(days=1)
    if value in ('yesterday','dun'):return today-timedelta(days=1),today
    if value in ('this_week','bu hafta'):return today-timedelta(days=today.weekday()),today+timedelta(days=1)
    if '..' in value:
        a,b=value.split('..',1)
        start=datetime.fromisoformat(a).astimezone()
        end=datetime.fromisoformat(b).astimezone()+timedelta(days=1)
        if start>=end:raise ValueError('Tarih aralığı ters olamaz.')
        return start,end
    raise ValueError('Tarih: last_week, last_7_days, today, yesterday, this_week veya YYYY-MM-DD..YYYY-MM-DD.')

def file_date(path,field):
    info=path.lstat()
    if field=='modified':return info.st_mtime,'modified'
    if field=='downloaded':
        try:
            raw=subprocess.run(['/usr/bin/xattr','-px','com.apple.metadata:kMDItemDownloadedDate',str(path)],capture_output=True,text=True,timeout=2)
            stored=plistlib.loads(bytes.fromhex(raw.stdout)) if raw.returncode==0 else []
            dates=stored if isinstance(stored,list) else [stored]
            valid=[d if d.tzinfo else d.replace(tzinfo=timezone.utc) for d in dates if isinstance(d,datetime)]
            if valid:return max(valid).timestamp(),'downloaded'
        except (OSError,ValueError,plistlib.InvalidFileException,subprocess.TimeoutExpired):pass
        try:
            r=subprocess.run(['/usr/bin/mdls','-raw','-name','kMDItemDownloadedDate',str(path)],capture_output=True,text=True,timeout=2)
            match=re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4})',r.stdout)
            if r.returncode==0 and match:return datetime.strptime(match[1],'%Y-%m-%d %H:%M:%S %z').timestamp(),'downloaded'
        except (OSError,ValueError,subprocess.TimeoutExpired):pass
    birth=getattr(info,'st_birthtime',None)
    return birth,('created_fallback' if field=='downloaded' else 'created') if birth is not None else 'date_unavailable'

def indexed_matches(root,terms):
    if not terms:return set()
    def escaped(t):return t.replace('\\','\\\\').replace('"','\\"')
    expression=' && '.join(f'(kMDItemFSName == "*{escaped(t)}*"cd || kMDItemTextContent == "*{escaped(t)}*"cd)' for t in terms)
    try:
        r=subprocess.run(['/usr/bin/mdfind','-0','-onlyin',str(root),expression],capture_output=True,timeout=3)
        return {os.fsdecode(p) for p in r.stdout.split(b'\0') if p} if r.returncode==0 else set()
    except (OSError,subprocess.TimeoutExpired):return set()

def find_file(query='',directory='',file_type='',date_range='',date_field='modified',recursive=True,limit=30):
    try:
        if date_field not in ('modified','created','downloaded'):raise ValueError('date_field: modified, created veya downloaded olmalı.')
        limit=max(1,min(int(limit),100));window=date_window(date_range)
        roots=[path_of(directory)] if directory else [HOME/'Desktop',HOME/'Downloads',HOME/'Documents']
        kind=fold(file_type).lstrip('.')
        screenshots=fold(query).strip() in ('screenshots','screenshot','ekran goruntuleri','ekran goruntusu','ekran resimleri','ekran resmi')
        terms=re.findall(r'[\w-]+',fold(query)) if not screenshots else []
        matches=[];inaccessible=0;truncated=False;scanned=0;deadline=time.monotonic()+12
        for root in roots:
            if not root.is_dir():
                if directory:raise ValueError(f'Aranacak klasör bulunamadı: {root}')
                continue
            indexed=indexed_matches(root,terms)
            def onerror(exc):
                nonlocal inaccessible
                inaccessible+=1
            for current,dirs,files in os.walk(root,followlinks=False,onerror=onerror):
                dirs[:]=[d for d in dirs if d not in SKIP and not d.startswith('.') and not d.endswith(PACKAGES)]
                for name in files+list(dirs):
                    scanned+=1
                    if scanned>25000 or time.monotonic()>deadline:truncated=True;break
                    if name.startswith('.'):continue
                    path=Path(current)/name
                    is_dir=path.is_dir() and not path.is_symlink()
                    if kind in ('folder','directory','klasor'):
                        if not is_dir:continue
                    elif kind and (is_dir or path.suffix.lower()!='.'+kind):continue
                    if screenshots:
                        if is_dir or path.suffix.lower() not in ('.png','.jpg','.jpeg','.heic','.tiff'):continue
                        if not any(p in fold(name) for p in ('screenshot','screen shot','ekran resmi','ekran goruntusu')):continue
                    elif terms and not all(t in fold(name) for t in terms) and str(path) not in indexed:continue
                    try:
                        stamp,basis=file_date(path,date_field)
                        if window and (stamp is None or not window[0].timestamp()<=stamp<window[1].timestamp()):continue
                        info=path.lstat()
                    except OSError:inaccessible+=1;continue
                    matches.append({'path':str(path),'name':name,'kind':'folder' if is_dir else 'file','size_bytes':info.st_size,
                                    'date':datetime.fromtimestamp(stamp).astimezone().isoformat() if stamp is not None else None,
                                    'date_basis':basis,'matched_by':'name' if screenshots or all(t in fold(name) for t in terms) else 'spotlight_content'})
                if truncated or not recursive:break
            if truncated:break
        matches.sort(key=lambda r:r['date'] or '',reverse=True)
        return reply('ok',matches=matches[:limit],found=len(matches),truncated=truncated or len(matches)>limit,inaccessible=inaccessible,
                     date_range=[d.isoformat() for d in window] if window else None,
                     note='last_week önceki takvim haftasıdır. created_fallback indirme tarihinin bulunmadığını, oluşturulma tarihiyle eşleştiğini belirtir; kesin indirme kanıtı değildir. Spotlight yalnızca indekslenmiş içeriği arar. Tek dosya istenmişse belirsiz eşleşmelerde kullanıcıya seçenek sun.')
    except (ValueError,TypeError,OSError) as exc:return reply('error',message=str(exc))

def _identity(path,follow=True):
    try:
        info=os.stat(path) if follow else os.lstat(path)
        return info.st_dev,info.st_ino
    except (OSError,ValueError):return None

def _same_place(path,root,follow=True):
    # macOS diskleri büyük/küçük harfe duyarsızdır: "/Users/x/desktop" == Desktop.
    if str(path).casefold()==str(root).casefold():return True
    ident=_identity(path,follow)
    return ident is not None and ident==_identity(root)

def guard(path):
    exact=[Path('/'),HOME,HOME/'Desktop',HOME/'Downloads',HOME/'Documents',Path('/Volumes'),Path('/private'),Path('/tmp'),Path('/private/tmp'),Path('/var'),Path('/private/var')]
    if any(_same_place(path,root,follow=False) for root in exact):
        raise ValueError(f'Bu ana klasör üzerinde işlem yapılmaz: {path}')
    trees=[Path('/System'),Path('/Library'),Path('/usr'),Path('/bin'),Path('/sbin'),Path('/Applications'),Path('/dev'),Path('/private/etc'),HOME/'Library',HOME/'.Trash']
    for index,part in enumerate([path,*path.parents]):
        if any(_same_place(part,root,follow=index>0) for root in trees):
            raise ValueError(f'Sistem/Çöp Sepeti konumunda işlem yapılmaz: {path}')

def sources_of(paths):
    if isinstance(paths,str):paths=[paths]
    if not isinstance(paths,list) or not 1<=len(paths)<=100:raise ValueError('1–100 kesin dosya yolu belirt.')
    sources=[path_of(p) for p in paths]
    if len(set(sources))!=len(sources):raise ValueError('Aynı kaynak birden fazla verilmiş.')
    for p in sources:
        guard(p)
        if not os.path.lexists(p):raise ValueError(f'Kaynak bulunamadı: {p}')
        if any(parent in sources for parent in p.parents):raise ValueError('Klasör ve içindeki dosya aynı toplu işleme eklenemez.')
    return sources

def move_one(source,target):
    if os.path.lexists(target):raise ValueError(f'Hedef zaten var; üzerine yazılmadı: {target}')
    script = """ObjC.import('Foundation'); function run(a) {
        var error = Ref();
        var ok = $.NSFileManager.defaultManager.moveItemAtPathToPathError(a[0], a[1], error);
        if (!ok) throw new Error(ObjC.unwrap(error[0].localizedDescription));
        return 'ok';
    }"""
    r=subprocess.run(['/usr/bin/osascript','-l','JavaScript','-e',script,str(source),str(target)],capture_output=True,text=True,timeout=60)
    if r.returncode or os.path.lexists(source) or not os.path.lexists(target):
        raise RuntimeError(f'Taşıma doğrulanamadı: {source} → {target}. {r.stderr.strip()}')

from actions.action_history import tracked_action


@tracked_action('move_file')
def move_file(source_paths,destination,create_destination=True):
    completed=[]
    try:
        sources=sources_of(source_paths);folder=path_of(destination)
        # Hedef klasör symlink ise gerçek hedefin güvenliğini de kontrol et.
        folder=folder.resolve()
        guard(folder/'__destination_check__')
        if folder.exists() and not folder.is_dir():raise ValueError('Hedef bir klasör olmalı.')
        targets=[folder/p.name for p in sources]
        if len(set(targets))!=len(targets):raise ValueError('Aynı adlı kaynaklar var; önce benzersiz adlar belirle.')
        for source,target in zip(sources,targets):
            if source==folder or source in folder.parents:raise ValueError('Klasör kendi içine taşınamaz.')
            if os.path.lexists(target):raise ValueError(f'Hedefte aynı ad var; hiçbir dosya taşınmadı: {target}')
        if not folder.exists():
            if not create_destination:raise ValueError(f'Hedef klasör yok: {folder}')
            folder.mkdir(parents=True,exist_ok=True)
        for source,target in zip(sources,targets):
            move_one(source,target);completed.append({'from':str(source),'to':str(target)})
        return reply('ok',completed=completed)
    except (ValueError,TypeError,OSError,RuntimeError,subprocess.SubprocessError) as exc:
        return reply('partial' if completed else 'error',message=str(exc),completed=completed,note='İlk hatada duruldu. Tamamlanmayan yolları kontrol et; körlemesine tekrar etme.')

@tracked_action('rename_file')
def rename_file(path,new_name):
    try:
        source=sources_of([path])[0]
        if not isinstance(new_name,str) or new_name in ('','.','..') or '/' in new_name or '\0' in new_name:
            raise ValueError('Yeni ad yalnızca dosya adı olmalı; klasör yolu içeremez.')
        target=source.with_name(new_name)
        if target==source:return reply('ok',path=str(source),note='Ad zaten aynı.')
        if new_name.casefold()==source.name.casefold() and _identity(target,False)==_identity(source,False):
            # Yalnızca harf büyüklüğü değişiyor (rapor → Rapor): aynı dosya "hedef var" sanılmasın.
            os.rename(source,target)
            if new_name not in os.listdir(source.parent):raise RuntimeError('Ad değişikliği doğrulanamadı.')
            return reply('ok',old_path=str(source),path=str(target))
        move_one(source,target)
        return reply('ok',old_path=str(source),path=str(target))
    except (ValueError,TypeError,OSError,RuntimeError,subprocess.SubprocessError) as exc:return reply('error',message=str(exc))

TRASH_SWIFT = r'''
import Foundation
do {
    guard CommandLine.arguments.count == 2 else { exit(2) }
    var result: NSURL?
    try FileManager.default.trashItem(at: URL(fileURLWithPath: CommandLine.arguments[1]), resultingItemURL: &result)
    guard let path = result?.path else { exit(3) }
    let data = try JSONSerialization.data(withJSONObject: ["path": path])
    print(String(data: data, encoding: .utf8)!)
} catch {
    FileHandle.standardError.write(Data(error.localizedDescription.utf8))
    exit(1)
}
'''
_trash_temp = None
_trash_lock = threading.Lock()

def trash_helper():
    global _trash_temp
    with _trash_lock:
        if _trash_temp is not None:
            return str(Path(_trash_temp.name)/'trash-helper')
        temp = tempfile.TemporaryDirectory(prefix='jarvis-trash-')
        folder = Path(temp.name)
        source = folder/'trash.swift'
        source.write_text(TRASH_SWIFT)
        try:
            r = subprocess.run(['/usr/bin/swiftc','-module-cache-path',str(folder/'modules'),str(source),'-o',str(folder/'trash-helper')], capture_output=True,text=True,timeout=40)
            if r.returncode: raise RuntimeError('Mac Çöp Sepeti yardımcısı hazırlanamadı: '+r.stderr[-600:])
        except BaseException:
            temp.cleanup()
            raise
        _trash_temp = temp
        return str(folder/'trash-helper')

def trash_one(path):
    # Yerel NSFileManager Trash API. Kalıcı silme veya rm alternatifi yoktur.
    r=subprocess.run([trash_helper(),str(path)],capture_output=True,text=True,timeout=15)
    if r.returncode:raise RuntimeError(f'Çöp Sepeti işlemi başarısız (kod {r.returncode}): '+r.stderr.strip())
    target=json.loads(r.stdout)['path']
    if os.path.lexists(path) or not os.path.lexists(target):raise RuntimeError('Çöp Sepeti işlemi doğrulanamadı; yolları kontrol et.')
    return target

@tracked_action('trash_file')
def trash_file(paths):
    completed=[]
    try:
        for source in sources_of(paths):
            target=trash_one(source);completed.append({'from':str(source),'trash_path':target})
        return reply('ok',completed=completed,note='Yalnızca Çöp Sepetine taşındı; kalıcı silme yapılmadı.')
    except (ValueError,TypeError,OSError,RuntimeError,KeyError,subprocess.SubprocessError) as exc:
        return reply('partial' if completed else 'error',message=str(exc),completed=completed,note='İlk hatada duruldu. Kalıcı silme yapılmadı.')
