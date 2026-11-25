from contextlib import contextmanager
import hashlib,json,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

import pytest
from geotrace.updates import check_updates,download_update,launch_installer,UpdateError,DownloadCancelled,installer_suffix


@contextmanager
def feed():
    payload=b'GeoTrace verified installer\x00'*100
    data={'reads':0,'corrupt':False,'release':{}}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path=='/release':body=json.dumps(data['release']).encode()
            else:
                data['reads']+=1
                body=payload if not data['corrupt'] else payload[:-1]+b'X'
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    assets=[]
    for suffix in ('Windows-x64-Setup.exe','macOS-arm64.pkg','macOS-x86_64.pkg'):
        assets.append(dict(name='GeoTrace-1.2.0-build25-'+suffix,browser_download_url=base+'/installer',size=len(payload),digest='sha256:'+hashlib.sha256(payload).hexdigest()))
    data['release']=dict(tag_name='v1.2.0',draft=False,prerelease=False,body='Изменения новой версии',assets=assets)
    try:yield base+'/release',data,payload
    finally:server.shutdown();server.server_close();thread.join()


def test_release_version_architecture_and_unavailable_channel():
    with feed() as (url,data,_):
        update=check_updates('1.1.0',url,'win32','AMD64',local=True)
        assert update['state']=='available' and update['name'].endswith('Windows-x64-Setup.exe')
        assert check_updates('1.2.0',url,local=True)['state']=='current'
        assert check_updates('1.3.0',url,local=True)['state']=='current'
        assert check_updates('1.1.0',url,'darwin','arm64',local=True)['name'].endswith('arm64.pkg')
        data['release']['prerelease']=True
        with pytest.raises(UpdateError):check_updates('1.1.0',url,local=True)
        with pytest.raises(UpdateError):check_updates('1.1.0',url)
    assert installer_suffix('darwin','x86_64')=='-macOS-x86_64.pkg'


def test_verified_download_cache_and_digest_rechecked_before_launch(tmp_path,monkeypatch):
    with feed() as (url,data,payload):
        update=check_updates('1.1.0',url,'darwin','arm64',local=True)
        progress=[];ready=download_update(update,tmp_path,lambda a,b:progress.append((a,b)),local=True)
        path=Path(ready['path']);assert path.read_bytes()==payload and progress[-1]==(len(payload),len(payload))
        reads=data['reads'];download_update(update,tmp_path,local=True);assert data['reads']==reads
        calls=[];monkeypatch.setattr('geotrace.updates.subprocess.Popen',lambda args,**kw:calls.append(args))
        monkeypatch.setattr('geotrace.updates.sys.platform','darwin')
        launch_installer(ready);assert calls==[['open',str(path)]]
        path.write_bytes(b'tampered')
        with pytest.raises(UpdateError,match='изменился'):launch_installer(ready)
        assert len(calls)==1


def test_wrong_digest_cancel_and_unsafe_name_leave_no_installer(tmp_path):
    with feed() as (url,data,_):
        update=check_updates('1.1.0',url,'win32','AMD64',local=True);data['corrupt']=True
        with pytest.raises(UpdateError,match='сумма'):download_update(update,tmp_path,local=True)
        assert not list(tmp_path.iterdir())
        data['corrupt']=False;cancel=threading.Event();cancel.set()
        with pytest.raises(DownloadCancelled):download_update(update,tmp_path,cancel=cancel,local=True)
        assert not list(tmp_path.iterdir())
        with pytest.raises(UpdateError):download_update(dict(update,name='../other.exe'),tmp_path,local=True)
