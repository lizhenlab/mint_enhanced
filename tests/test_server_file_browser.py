"""Fresh, read-only server selection, independent of dataset/model loading."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'eval/model_effect'))
from visualization.viewer.routes import create_app


def browser(tmp_path):
    def forbidden(*a,**kw):raise AssertionError('Browsing must not register or infer videos')
    store=SimpleNamespace(root=tmp_path,default_mode='mesh_skel',video_eid=forbidden,
                          ensure_dataset_async=forbidden)
    return create_app(store).test_client()


def test_server_browser_lists_live_videos_and_keeps_directory_only_contract(tmp_path):
    (tmp_path/'子目录').mkdir();(tmp_path/'影片 & 01.MP4').write_bytes(b'video')
    (tmp_path/'clip.m4v').write_bytes(b'clip');(tmp_path/'notes.txt').write_text('not video')
    # A LeRobot layout must still be a plain filesystem listing here.
    (tmp_path/'meta').mkdir();(tmp_path/'meta/info.json').write_text('{}')
    client=browser(tmp_path)
    old=client.get('/api/dirbrowse').json
    assert 'videos' not in old and old['dirs']==['meta','子目录']
    response=client.get('/api/dirbrowse',query_string={'videos':'1','path':str(tmp_path)})
    data=response.json
    assert response.status_code==200 and response.cache_control.no_store
    assert [f['name'] for f in data['videos']]==['clip.m4v','影片 & 01.MP4']
    assert data['videos'][1]['path']==str(tmp_path/'影片 & 01.MP4')
    assert data['videos'][1]['size_bytes']==5
    (tmp_path/'new.mov').write_bytes(b'new')
    (tmp_path/'clip.m4v').unlink()
    data=client.get('/api/dirbrowse',query_string={'videos':'1'}).json
    assert [f['name'] for f in data['videos']]==['new.mov','影片 & 01.MP4']


def test_server_browser_navigation_symlinks_and_invalid_paths(tmp_path):
    child=tmp_path/'nested';child.mkdir();(tmp_path/'alias').symlink_to(child,target_is_directory=True)
    client=browser(tmp_path)
    data=client.get('/api/dirbrowse',query_string={'path':str(tmp_path/'alias'),'videos':'1'}).json
    assert data['path']==str(child) and data['parent']==str(tmp_path)
    for path in [tmp_path/'missing',tmp_path/'not-a-dir.mp4']:
        if path.suffix:path.write_bytes(b'x')
        response=client.get('/api/dirbrowse',query_string={'path':str(path),'videos':'1'})
        assert response.status_code==400 and response.json['error']


def test_server_browser_unreadable_directory_returns_error(tmp_path,monkeypatch):
    client=browser(tmp_path)
    import visualization.viewer.routes as routes
    def denied(*a,**kw):raise PermissionError('not readable')
    monkeypatch.setattr(routes.os,'scandir',denied)
    response=client.get('/api/dirbrowse',query_string={'path':str(tmp_path),'videos':'1'})
    assert response.status_code==400 and '目录不可读取' in response.json['error']
