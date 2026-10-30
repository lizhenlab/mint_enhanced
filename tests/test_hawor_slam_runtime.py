import json
import sqlite3
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from mint.comparison.hawor_slam import prefetch_stream,read_slam_progress,write_slam_geometry


def test_prefetch_keeps_order_propagates_decoder_failure_and_stops_on_close():
    def broken():
        yield 3
        yield 8
        raise ValueError('bad source frame')
    stream=prefetch_stream(broken())
    assert [next(stream),next(stream)]==[3,8]
    with pytest.raises(ValueError,match='bad source frame'):next(stream)
    closed=threading.Event()
    def frames():
        try:yield from range(100000)
        finally:closed.set()
    stream=prefetch_stream(frames(),capacity=2)
    assert next(stream)==0
    stream.close()
    assert closed.is_set()


@pytest.mark.parametrize('disk_free,compressed',[(100*2**30,False),(1,True)])
def test_checkpoint_encoding_preserves_every_numeric_value(tmp_path,monkeypatch,disk_free,compressed):
    from mint.comparison import hawor_slam
    monkeypatch.delenv('MINT_HAWOR_SLAM_ACCELERATE',raising=False)
    monkeypatch.setattr(hawor_slam.shutil,'disk_usage',lambda _:SimpleNamespace(free=disk_free))
    arrays={'traj':np.arange(105,dtype=np.float32).reshape(15,7),
            'tstamp':np.array([0,2,9]),'disps':np.full((3,8,8),.75,np.float32)}
    path=tmp_path/'slam_geometry.npz';write_slam_geometry(path,**arrays)
    with np.load(path,allow_pickle=False) as data:
        for key,value in arrays.items():np.testing.assert_array_equal(data[key],value)
    with zipfile.ZipFile(path) as data:
        assert all(item.compress_type==(zipfile.ZIP_DEFLATED if compressed else zipfile.ZIP_STORED) for item in data.infolist())
    assert list(tmp_path.iterdir())==[path]


def test_progress_rejects_prior_attempt_and_other_run(tmp_path):
    ctx=SimpleNamespace(work=tmp_path,run_id='current')
    started=time.time()
    value={'run_id':'current','stage':'slam_global_ba','done':8,'total':19,'updated_at':started+1}
    path=tmp_path/'hawor-slam-progress.json';path.write_text(json.dumps(value))
    assert read_slam_progress(ctx,started)['done']==8
    assert not read_slam_progress(ctx,started+2)
    value['run_id']='other';path.write_text(json.dumps(value))
    assert not read_slam_progress(ctx,started)


@pytest.mark.parametrize('invalid',[None,'adapter','checkpoint','existing-motion','unfinished-file','legacy','legacy-decoder'])
def test_cross_run_slam_reuse_requires_matching_masks_adapter_and_valid_files(tmp_path,monkeypatch,invalid):
    from mint.comparison.hawor_parallel import adopt_stages
    from mint.comparison.native import publish_stage,stage_cached
    monkeypatch.delenv('MINT_NATIVE_REUSE_ROOT',raising=False)
    monkeypatch.delenv('MINT_NATIVE_REUSE_STAGE_LIMIT',raising=False)
    old=SimpleNamespace(run_id='old',work=tmp_path/'runs/old/native')
    adapter={key:'same' for key in ['contracts.py','normalizers.py','prepare.py','native.py','worker.py','native_tasks.py','read_only.py']}
    adapter['hawor_parallel.py']='current'
    identity={'interpreter':'py','sources':{'native.py':'same'},'assets':{'weights':'same'},'adapter':adapter}
    after=json.loads(json.dumps(identity))
    if invalid in ('legacy','legacy-decoder'):
        identity['adapter']['hawor_parallel.py']='6deeda2b7bdf0ed58719ecf53dfffe271f8a03561e66424dd672706b9f6742af'
    request={'algorithm':'hawor_full','sample_id':'sample','options':{'analysis_width':832,'focal':None},
             'backend':{'identity':identity}}
    with sqlite3.connect(tmp_path/'jobs.sqlite') as db:
        db.execute('CREATE TABLE runs(id,payload,status,updated)')
        db.execute('INSERT INTO runs VALUES(?,?,?,?)',('old',json.dumps(request),'completed',1))
    for stage in ['frames','detection','motion','slam_geometry']:
        path=old.work/(stage+'.data');path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(stage.encode())
        publish_stage(old,stage,[path])
    ctx=SimpleNamespace(run_id='new',directory=tmp_path/'runs/new',work=tmp_path/'runs/new/native',
        sample={'id':'sample'},options=request['options'],spec={'identity':after},metadata={})
    ctx.work.mkdir(parents=True)
    if invalid=='adapter':ctx.spec['identity']['adapter']['slam']='different'
    elif invalid=='checkpoint':(old.work/'slam_geometry.data').write_bytes(b'changed')
    elif invalid=='existing-motion':
        path=ctx.work/'own-mask';path.write_bytes(b'other masks');publish_stage(ctx,'motion',[path])
    elif invalid=='unfinished-file':(ctx.work/'motion.data').write_bytes(b'partially written')
    elif invalid=='legacy-decoder':ctx.spec['identity']['adapter']['normalizers.py']='changed'
    adopt_stages(ctx)
    valid=invalid in (None,'unfinished-file','legacy')
    assert stage_cached(ctx,'slam_geometry')==valid
    if valid:
        assert (ctx.work/'slam_geometry.data').read_bytes()==b'slam_geometry'
        assert (ctx.work/'motion.data').read_bytes()==b'motion'
        assert 'slam_geometry' in ctx.metadata['reused_native_stages_from']['stages']
