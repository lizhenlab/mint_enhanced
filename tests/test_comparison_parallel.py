"""Execution isolation, native-capacity and full-input regression coverage."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


def test_external_sources_are_read_only_even_through_symlinks_and_dir_fd(tmp_path):
    protected=tmp_path/'native';protected.mkdir();(protected/'source.py').write_text('original')
    output=tmp_path/'mint';output.mkdir();(output/'alias').symlink_to(protected,target_is_directory=True)
    code='''
import os,sys
from pathlib import Path
from mint.comparison.read_only import protect_native_roots
native,output=map(Path,sys.argv[1:])
protect_native_roots([native])
assert (native/'source.py').read_text()=='original'
native.mkdir(exist_ok=True)
fd=os.open(native,os.O_RDONLY)
actions=[lambda:(native/'source.py').write_text('changed'),
 lambda:os.unlink('source.py',dir_fd=fd),
 lambda:os.open('source.py',os.O_WRONLY,dir_fd=fd),
 lambda:(output/'alias/source.py').write_text('changed'),
 lambda:os.rename(native/'source.py',output/'moved.py'),
 lambda:os.utime('source.py',dir_fd=fd),
 lambda:os.mkdir('new',dir_fd=fd)]
for action in actions:
 try:action()
 except PermissionError:pass
 else:raise AssertionError('Native mutation was allowed')
os.close(fd)
(output/'result.json').write_text('success')
'''
    subprocess.run([sys.executable,'-c',code,str(protected),str(output)],check=True)
    assert (protected/'source.py').read_text()=='original'
    assert (output/'result.json').read_text()=='success'


def test_growing_native_storage_preserves_prefix_and_original_initialization():
    import torch
    from multiprocessing import Value
    from mint.comparison.hawor_parallel import growing_video_class
    class Original:
        def __init__(self):
            self.counter=Value('i',3);self.poses=torch.zeros(4,7);self.poses[:,6]=1
            self.images=torch.arange(4*3*2*2,dtype=torch.uint8).reshape(4,3,2,2)
            self.disps=torch.ones(4,2,2);self.other=torch.zeros(2)
        def get_lock(self):return self.counter.get_lock()
    events=[];video=growing_video_class(Original,progress=lambda **kw:events.append(kw))()
    prefix=video.images.clone();video.ensure_capacity(5)
    assert torch.equal(video.images[:4],prefix)
    assert torch.all(video.images[4:]==0) and torch.all(video.disps[4:]==1)
    assert torch.all(video.poses[:,6]==1) and video.counter.value==3
    assert video.other.shape==(2,) and video.images.dtype==torch.uint8
    assert events[0]['previous_capacity']==4 and events[0]['capacity']>=5
    previous=video.images.data_ptr();video.ensure_capacity(6)
    assert video.images.data_ptr()==previous


def test_native_factor_graph_damping_grows_with_keyframe_storage():
    import torch
    from mint.comparison.hawor_parallel import grow_factor_graph
    graph=SimpleNamespace(damping=torch.full((512,2,3),.25))
    video=SimpleNamespace(disps=torch.ones(1024,2,3))
    grow_factor_graph(graph,video)
    assert torch.all(graph.damping[:512]==.25)
    assert torch.all(graph.damping[512:]==1e-6)
    # The previously failing native index must be valid and writable.
    graph.damping[torch.tensor([511,512,700])]=.5


def test_native_frame_cooperation_uses_fresh_async_telemetry_and_refreshes_stale_state(tmp_path,monkeypatch):
    import torch
    from mint.comparison.worker import WorkerContext
    from mint.inference import gpu_scheduler
    path=tmp_path/'gpu.json';monkeypatch.setenv('MINT_NATIVE_GPU_STATE',str(path))
    monkeypatch.setattr(torch.cuda,'synchronize',lambda:None)
    calls=[]
    def probe(**kw):
        calls.append(kw)
        return lambda:{'uuid':gpu_scheduler.GpuSample('uuid',30000,40000,0,0,(),0)}
    monkeypatch.setattr(gpu_scheduler,'NvidiaSmiProbe',probe)
    ctx=WorkerContext.__new__(WorkerContext);ctx.options={'resource_policy':'shared'}
    ctx.request={'gpu_uuids':['uuid']};ctx.timings={};ctx._last_probe=0.;ctx._foreign=True
    ctx.check_cancel=lambda:None;ctx.progress=lambda **kw:None
    record={'updated_at':time.time(),'gpus':[{'uuid':'uuid','foreign_pids':[123],'foreign_utilization':0}]}
    path.write_text(json.dumps(record));ctx.cooperate(time.perf_counter())
    assert not calls and not ctx._foreign
    record['updated_at']-=3600;path.write_text(json.dumps(record));ctx._last_probe=0.
    ctx.cooperate(time.perf_counter())
    assert len(calls)==1 and not ctx._foreign


def test_external_gpu_pool_cap_applies_to_all_physical_devices_and_cleans_up(tmp_path,monkeypatch):
    from mint.comparison import native_pool as module
    from mint.inference.gpu_scheduler import GpuSample,IdleGpuScheduler
    class Lease:
        def __init__(self,uuid):self.path=tmp_path/uuid;self.file=None
        def acquire(self):self.file=True;return True
        def release(self):self.file=None
    monkeypatch.setattr(module,'GpuLease',Lease)
    monkeypatch.setattr(module.NativeMemoryLease,'update',lambda *a:True)
    samples={f'uuid-{i}':GpuSample(f'uuid-{i}',40000,40000,0,0,() if i>=3 else (999999,),0) for i in range(5)}
    monkeypatch.setattr(module,'NvidiaSmiProbe',lambda **kw:lambda:samples)
    monkeypatch.setattr(module,'IdleGpuScheduler',lambda *a,**kw:IdleGpuScheduler(*a,**{**kw,'poll_seconds':.02}))
    active=set();peak=[0];devices=set();lock=threading.Lock()
    class Process:
        def __init__(self,pool,device):
            self.pool,self.device=pool,device
            with lock:active.add(device);peak[0]=max(peak[0],len(active))
        def call(self,item):
            with lock:devices.add(self.device)
            time.sleep(.03)
            with self.pool.lock:self.pool.completed_devices.add(self.device)
            return item
        def close(self):
            with lock:active.discard(self.device)
    monkeypatch.setattr(module,'NativeProcess',Process)
    ctx=SimpleNamespace(options={'max_gpus':2,'resource_policy':'idle'},spec={'min_free_mib':1000},metadata={},work=tmp_path,
        progress=lambda **kw:None,cancelled=lambda:False)
    with module.NativePool(ctx,'ace') as pool:assert pool.run(list(range(12)))==list(range(12))
    assert peak[0]==2 and not active
    assert devices=={'gpu:3','gpu:4'} # A limit of two must not restrict selection to GPU 0/1.


@pytest.mark.parametrize('algorithm',['ace_kfree','ace_kgiven','hawor_camera','hawor_full','mint'])
def test_all_native_algorithms_accept_eight_gpu_cap(algorithm):
    from mint.comparison.registry import normalize_options
    assert normalize_options(algorithm)['max_gpus']==8
    assert normalize_options(algorithm,{'max_gpus':3})['max_gpus']==3


@pytest.mark.parametrize('target_fps',[0,10,17])
def test_parallel_input_preserves_source_pixels_frame_indices_and_pts(tmp_path,monkeypatch,target_fps):
    import cv2
    from decord import VideoReader
    from mint.comparison.contracts import source_request
    from mint.comparison.prepare import prepare_sample
    from mint.inference.frame_store import select_view,sample_indices
    monkeypatch.setenv('MINT_CACHE_MIN_FREE_GIB','0')
    source=tmp_path/'input.mp4'
    writer=cv2.VideoWriter(str(source),cv2.VideoWriter_fourcc(*'mp4v'),30,(96,48))
    for i in range(270):
        frame=np.zeros((48,96,3),np.uint8);frame[:,:48]=i%255;frame[:,48:]=(i+73)%255;writer.write(frame)
    writer.release()
    request=source_request(source,view='right',target_fps=target_fps,start_frame=5)
    record=prepare_sample(request,tmp_path/'inputs'/request['id'])
    reader=VideoReader(str(source),num_threads=2)
    indices,_=sample_indices(len(reader),reader.get_avg_fps(),target_fps);indices=indices[indices>=5]
    decoded=select_view(reader.get_batch(indices.tolist()).asnumpy(),'right')
    np.testing.assert_array_equal(np.load(record['frames_path']),decoded)
    np.testing.assert_array_equal(record['source_frame_indices'],indices)
    np.testing.assert_array_equal(record['timestamps_s'],np.asarray(reader.get_frame_timestamp(indices.tolist()))[:,0])
    assert record['preparation_timings']['preparation_seconds']>0
