"""Version discovery and verified installer downloads; never embed account tokens."""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

import certifi

DEFAULT_FEED='https://api.github.com/repos/TheJakSin/GeoTrace/releases/latest'
RELEASES='https://github.com/TheJakSin/GeoTrace/releases/latest'
MAX_INSTALLER=3*1024**3


class UpdateError(ValueError):pass
class DownloadCancelled(UpdateError):pass


def version_tuple(value):
    match=re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)',str(value))
    if not match:raise UpdateError('Некорректный номер версии в канале обновлений.')
    return tuple(map(int,match.groups()))


def _url(url,local=False):
    p=urllib.parse.urlparse(url)
    if p.username or p.password or not p.hostname:raise UpdateError('Некорректный адрес обновления.')
    if p.scheme!='https' and not (local and p.scheme=='http' and p.hostname in ('127.0.0.1','localhost')):
        raise UpdateError('Канал обновлений должен использовать HTTPS.')
    return url


class _Redirect(urllib.request.HTTPRedirectHandler):
    def __init__(self,local):self.local=local
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        _url(newurl,self.local)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def _open(url,local=False):
    _url(url,local)
    opener=urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())),_Redirect(local))
    request=urllib.request.Request(url,headers={'User-Agent':'GeoTrace-Updater','Accept':'application/vnd.github+json' if 'api.github.com/' in url else '*/*'})
    return opener.open(request,timeout=12)


def installer_suffix(system=None,machine=None):
    system=system or sys.platform;machine=(machine or platform.machine()).lower()
    if system=='win32' and machine in ('amd64','x86_64','x64'):return '-Windows-x64-Setup.exe'
    if system=='darwin':
        if machine in ('arm64','aarch64'):return '-macOS-arm64.pkg'
        if machine in ('x86_64','amd64'):return '-macOS-x86_64.pkg'
    raise UpdateError('Для этой системы нет готового установщика.')


def check_updates(current,feed=None,system=None,machine=None,local=False):
    feed=feed or os.environ.get('GEOTRACE_UPDATE_FEED',DEFAULT_FEED)
    try:
        with _open(feed,local) as response:
            raw=response.read(2_000_001)
        if len(raw)>2_000_000:raise UpdateError('Слишком большой ответ канала обновлений.')
        release=json.loads(raw)
        if not isinstance(release,dict):raise UpdateError('Некорректный ответ канала обновлений.')
        if release.get('draft') or release.get('prerelease'):raise UpdateError('Канал не содержит готового стабильного выпуска.')
        version=release.get('tag_name',release.get('version',''))
        newer=version_tuple(version)>version_tuple(current)
        if not newer:return dict(state='current',message='Новых стабильных версий нет.',version=version.lstrip('v'),releaseUrl=RELEASES)
        suffix=installer_suffix(system,machine);assets=release.get('assets',[])
        if not isinstance(assets,list) or not all(isinstance(a,dict) for a in assets):raise UpdateError('Некорректный список установщиков.')
        asset=next((a for a in assets if re.fullmatch(r'GeoTrace-'+re.escape(version.lstrip('v'))+r'-build\d+'+re.escape(suffix),a.get('name',''))),None)
        if asset is None:raise UpdateError('Новая версия найдена, установщик для этой системы ещё не опубликован.')
        url=_url(asset.get('browser_download_url',''),local)
        digest=asset.get('digest','')
        if digest.startswith('sha256:'):digest=digest[7:]
        if not re.fullmatch('[a-fA-F0-9]{64}',digest):
            sums=next((a for a in assets if a.get('name')=='SHA256SUMS.txt'),None)
            if sums is None:raise UpdateError('Для установщика не опубликована контрольная сумма.')
            with _open(sums['browser_download_url'],local) as response:raw=response.read(1_000_001)
            if len(raw)>1_000_000:raise UpdateError('Некорректный список контрольных сумм.')
            digest=next((line[:64] for line in raw.decode('utf-8').splitlines() if line[64:].strip()==asset['name']),'')
        size=asset.get('size',0)
        if not re.fullmatch('[a-fA-F0-9]{64}',digest) or type(size) is not int or not 0<size<=MAX_INSTALLER:
            raise UpdateError('Не удалось проверить сведения об установщике.')
        return dict(state='available',message='Доступна версия '+version.lstrip('v'),version=version.lstrip('v'),
            notes=str(release.get('body',''))[:24000],name=asset['name'],size=size,sha256=digest.lower(),url=url,releaseUrl=RELEASES)
    except urllib.error.HTTPError as exc:
        if exc.code in (401,403,404):raise UpdateError('Канал выпусков пока недоступен приложению. Возможно, выпуски закрыты или ещё не опубликованы. Можно открыть страницу загрузки вручную.') from exc
        raise UpdateError('Сервис обновлений ответил с ошибкой. Повторите позже.') from exc
    except (OSError,ValueError,KeyError,TypeError) as exc:
        if isinstance(exc,UpdateError):raise
        raise UpdateError('Не удалось проверить обновления. Проверьте подключение к интернету.') from exc


def download_update(release,folder,progress=None,cancel=None,local=False):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    name=release['name']
    if Path(name).name!=name or not re.fullmatch(r'GeoTrace-\d+\.\d+\.\d+-build\d+-(Windows-x64-Setup\.exe|macOS-(arm64|x86_64)\.pkg)',name):
        raise UpdateError('Некорректное имя установщика.')
    path=folder/name;partial=folder/(name+'.partial');digest=hashlib.sha256();received=0
    try:
        if path.is_file() and path.stat().st_size==release['size']:
            with path.open('rb') as f:cached=hashlib.file_digest(f,'sha256').hexdigest()
            if cached==release['sha256']:return dict(release,path=str(path))
        with _open(release['url'],local) as response, partial.open('wb') as stream:
            while True:
                if cancel is not None and cancel.is_set():raise DownloadCancelled('Загрузка отменена.')
                block=response.read(1024*1024)
                if not block:break
                received+=len(block)
                if received>release['size'] or received>MAX_INSTALLER:raise UpdateError('Размер установщика отличается от опубликованного.')
                stream.write(block);digest.update(block)
                if progress:progress(received,release['size'])
            stream.flush();os.fsync(stream.fileno())
        if received!=release['size'] or digest.hexdigest()!=release['sha256']:
            raise UpdateError('Контрольная сумма установщика не совпала. Файл не будет запущен.')
        os.replace(partial,path)
        return dict(release,path=str(path))
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def launch_installer(release):
    path=Path(release['path'])
    with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if digest!=release['sha256']:raise UpdateError('Загруженный файл изменился. Повторите загрузку.')
    if sys.platform=='win32':os.startfile(str(path))
    elif sys.platform=='darwin':subprocess.Popen(['open',str(path)],close_fds=True)
    else:raise UpdateError('Установка поддерживается в Windows и macOS.')
