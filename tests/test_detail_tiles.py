from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
from PIL import Image
from geotrace.detail_tiles import DetailTiles
from test_sheet_seams import maps


def drain(cache):
    for _ in range(200):
        cache.pump()
        if not cache.futures and all(k in cache.cache for k,_ in cache.plan):return
        time.sleep(.005)
    raise AssertionError(cache.errors)


def test_pan_reuses_tiles_and_zoom_keeps_previous_detail(tmp_path,monkeypatch):
    monkeypatch.setattr('geotrace.detail_tiles.TILE',32)
    calls=[]
    def load(doc,bounds,path):
        calls.append(bounds);Path(path).parent.mkdir(parents=True,exist_ok=True)
        Image.new('RGBA',(32,32),(20,80,120,255)).save(path);return Path(path).stat().st_size
    doc=maps();r=doc.canvas(160)
    with ThreadPoolExecutor(max_workers=2) as pool:
        cache=DetailTiles(pool,tmp_path,loader=load);cache.reset(doc,r)
        cache.request((1010,990,1050,1020),80,60);drain(cache)
        before={row['key']:row['url'] for row in cache.rows()};count=len(calls)
        assert before and all(Path(row['path']).is_file() for row in cache.cache.values())
        cache.request((1010.2,990,1050.2,1020),80,60);drain(cache)
        assert len(calls)==count
        assert before=={row['key']:row['url'] for row in cache.rows()}
        from shapely.geometry import box
        visible={':'.join(map(str,key)) for key,row in cache.cache.items() if box(*row['bounds']).intersects(box(1010,990,1050,1020))}
        cache.minimum=.1;cache.request((1010,990,1050,1020),160,120)
        assert visible.issubset({row['key'] for row in cache.rows()})
        drain(cache);assert len(cache.cache)<=192
        # Polygon style edits do not invalidate the source mosaic.
        assert cache.reset(doc.clone(),r) is False
        doc.sheets[0].visible=False
        assert cache.reset(doc,r) is True and not cache.rows()


def test_lru_is_bounded_and_real_tile_render_preserves_alpha(tmp_path,monkeypatch):
    from geotrace.detail_tiles import render_tile
    doc=maps();target=tmp_path/'real.png';size=render_tile(doc,(1000,980,1064,1044),target)
    image=Image.open(target);assert image.size==(512,512) and size>0
    assert image.getpixel((0,0))[3]==0 and image.getpixel((20,100))[3]>0
    monkeypatch.setattr('geotrace.detail_tiles.TILE',32)
    def load(doc,bounds,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True);Path(path).write_bytes(b'x'*100);return 100
    with ThreadPoolExecutor(max_workers=2) as pool:
        cache=DetailTiles(pool,tmp_path/'bounded',budget=400,max_tiles=4,loader=load);cache.reset(doc,doc.canvas(160))
        cache.request((1010,990,1050,1020),80,60)
        for _ in range(30):cache.pump();time.sleep(.003)
        assert cache.bytes<=400 and len(cache.cache)<=4


def test_current_zoom_tiles_are_not_hidden_by_many_old_fine_fragments(tmp_path):
    from shapely.geometry import box
    doc=maps();r=doc.canvas(160)
    with ThreadPoolExecutor(max_workers=1) as pool:
        cache=DetailTiles(pool,tmp_path);cache.reset(doc,r)
        cache.view=((1000,960,1080,1040),box(1000,960,1080,1040),0)
        for i in range(140):
            cache.cache[(cache.generation,2,i,0)]=dict(path=str(tmp_path/f'{i}.png'),bounds=(1000,960,1080,1040),level=2,size=1)
        key=(cache.generation,0,0,0)
        cache.cache[key]=dict(path=str(tmp_path/'current.png'),bounds=(1000,960,1080,1040),level=0,size=1)
        rows=cache.rows()
        assert len(rows)<=128 and any(row['url'].endswith('/current.png') for row in rows)
