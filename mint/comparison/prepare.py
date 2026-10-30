"""Prepare one immutable, shared frame timeline without algorithm-specific sampling."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack, contextmanager
from pathlib import Path

import numpy as np

from mint.runtime import atomic_json, file_identity
from mint.inference.cache_budget import CacheBudget, file_lock
from mint.inference.frame_store import sample_indices, select_view
from mint.visualization.encoding import atomic_video_file


@contextmanager
def preparation_lock(path, cancel, progress):
    from mint.inference.base import InferenceCancelled
    while True:
        stack = ExitStack()
        try:
            stack.enter_context(file_lock(path, blocking=False))
        except BlockingIOError:
            stack.close()
            if cancel():
                raise InferenceCancelled('Cancelled while waiting for shared sample preparation')
            progress(stage='waiting_sample', detail='Another selected algorithm is preparing this exact input')
            time.sleep(.1)
        else:
            break
    with stack:
        if cancel():
            raise InferenceCancelled('Sample preparation cancelled')
        yield


def decode_rgb(source, indices, view, destination, source_shape, progress, cancel, on_chunk=lambda start,stop:None):
    """Independent decoders write disjoint full-resolution RGB ranges."""
    from decord import VideoReader
    from mint.inference.base import InferenceCancelled
    workers=max(1,min(8,int(os.environ.get('MINT_COMPARISON_DECODE_WORKERS','4')),len(indices)//128 or 1))
    chunk=max(1,min(96,64*2**20//int(np.prod(source_shape))))
    count=0;last_report=0.;lock=threading.Lock();failed=threading.Event()
    def decode(bounds):
        nonlocal count,last_report
        start,end=bounds
        reader=VideoReader(source,num_threads=2)
        mapped=np.load(destination,mmap_mode='r+',allow_pickle=False)
        try:
            for first in range(start,end,chunk):
                if cancel() or failed.is_set():raise InferenceCancelled('Sample preparation cancelled')
                last=min(end,first+chunk)
                mapped[first:last]=select_view(reader.get_batch(indices[first:last].tolist()).asnumpy(),view)
                on_chunk(first,last)
                with lock:
                    count+=last-first
                    now=time.monotonic()
                    if now-last_report>.25 or count==len(indices):
                        last_report=now;progress(stage='decode',done=count,total=len(indices),decode_workers=workers)
        except BaseException:
            failed.set();raise
        finally:mapped._mmap.close()
    bounds=np.linspace(0,len(indices),workers+1,dtype=int)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(decode,(int(a),int(b))) for a,b in zip(bounds[:-1],bounds[1:])]
        for future in as_completed(futures):future.result()
    # One durable writeback after all disjoint writers finish, instead of whole-file
    # synchronous flushes in every decode chunk.
    mapped=np.load(destination,mmap_mode='r+',allow_pickle=False)
    try:mapped.flush()
    finally:mapped._mmap.close()
    return workers


def render_proxy(frames_path,fps,size,destination,wait_ready,cancel,budget):
    """Encode completed RGB cache frames once, while other ranges still decode."""
    from mint.inference.base import InferenceCancelled
    frames=np.load(frames_path,mmap_mode='r',allow_pickle=False)
    n,height,width,_=frames.shape
    log=destination.with_suffix('.ffmpeg.log')
    try:
        with log.open('wb') as output:
            child=subprocess.Popen(['ffmpeg','-nostdin','-y','-v','error','-f','rawvideo','-pix_fmt','rgb24',
                '-s',f'{width}x{height}','-r',str(fps),'-i','pipe:0','-an','-filter_threads','2',
                '-vf',f'scale={size[0]}:{size[1]}','-c:v','libx264','-preset','veryfast','-crf','23',
                '-threads','4','-g',str(max(12,round(fps*2))),'-sc_threshold','0',
                '-pix_fmt','yuv420p','-movflags','+faststart',str(destination)],
                stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=output)
            try:
                for index,frame in enumerate(frames):
                    wait_ready(index)
                    if cancel():raise InferenceCancelled('Sample preview cancelled')
                    child.stdin.write(memoryview(frame).cast('B'))
                    if index%96==0 and shutil.disk_usage(destination.parent).free<budget.minimum_free:
                        raise OSError('Preview reached disk reserve')
                child.stdin.close()
                if child.wait(timeout=120):raise RuntimeError('Preview encoding failed: '+log.read_text(errors='replace')[-1500:])
            finally:
                try:
                    if not child.stdin.closed:child.stdin.close()
                except (BrokenPipeError,OSError):pass
                if child.poll() is None:
                    child.terminate()
                    try:child.wait(timeout=3)
                    except subprocess.TimeoutExpired:child.kill();child.wait()
    finally:
        frames._mmap.close();log.unlink(missing_ok=True)
    return destination


def prepare_sample(request, directory, progress=lambda **kw: None, cancel=lambda: False):
    from decord import VideoReader
    from mint.inference.base import InferenceCancelled
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    source = request['source']['path']
    if file_identity(source) != request['source']:
        raise ValueError('Input video changed; submit a new sample')
    with preparation_lock(directory / '.prepare.lock', cancel, progress):
        manifest = directory / 'sample.json'
        if manifest.exists():
            record = json.loads(manifest.read_text())
            if (record['request'] == request and file_identity(record['frames_path']) == record.get('frames_identity')
                    and file_identity(record['video_path']) == record.get('video_identity')):
                return record
        reader = VideoReader(source, num_threads=4)
        source_fps = float(reader.get_avg_fps())
        indices, fps = sample_indices(len(reader), source_fps, request['target_fps'])
        indices = indices[indices >= request['start_frame']]
        if request['max_frames'] is not None:
            indices = indices[:request['max_frames']]
        if not len(indices):
            raise ValueError('The requested time range is empty')
        timestamps = np.asarray(reader.get_frame_timestamp(indices.tolist()), dtype=np.float64)[:, 0]
        if not np.isfinite(timestamps).all() or np.any(np.diff(timestamps) <= 0):
            raise ValueError('Decoder did not provide a strictly increasing source PTS timeline')
        probe = reader.get_batch([int(indices[0])]).asnumpy()
        source_h, source_w = probe.shape[1:3]
        probe = select_view(probe, request['view'])
        height, width = probe.shape[1:3]
        shape = (len(indices), height, width, 3)
        frames_path = directory / 'frames.npy'
        budget = CacheBudget(directory.parent, pattern='*/frames.npy', pin_parent=True,
                             max_gib=float(os.environ.get('MINT_COMPARISON_INPUT_MAX_GIB', '256')))
        with file_lock(directory / '.active.pin', shared=True), budget.reserve(frames_path, int(np.prod(shape)) + 4096):
            temporary = directory / f'frames.partial-{os.getpid()}.npy'
            frames = np.lib.format.open_memmap(temporary, mode='w+', dtype=np.uint8, shape=shape)
            frames._mmap.close()
            from .storage import PreviewBudget
            preview_budget = PreviewBudget(directory.parent)
            scale=min(1.,1280/width)
            proxy_size=(max(2,2*round(width*scale/2)),max(2,2*round(height*scale/2)))
            stopped=threading.Event()
            cancelled=lambda:cancel() or stopped.is_set()
            timings={};started=time.perf_counter()
            ready=np.zeros(len(indices),bool);condition=threading.Condition()
            def on_chunk(start,stop):
                with condition:ready[start:stop]=True;condition.notify_all()
            def wait_ready(index):
                with condition:
                    while not ready[index]:
                        if cancelled():raise InferenceCancelled('Sample preview cancelled')
                        condition.wait(.1)
            def preview():
                tick=time.perf_counter()
                with preparation_lock(directory.parent / '.proxy-encode.lock',cancelled,progress):
                    with preview_budget.reserve(directory/'input.mp4',max(2**20,len(indices)*proxy_size[0]*proxy_size[1]//32)):
                        def render(path):
                            render_proxy(temporary,fps,proxy_size,path,wait_ready,cancelled,preview_budget)
                            used=sum(p.stat().st_size for p in preview_budget._files() if p!=directory/'input.mp4')
                            if used+path.stat().st_size>preview_budget.maximum:raise OSError('Comparison preview quota exceeded')
                        path=atomic_video_file(directory/'input.mp4',render,len(indices))
                timings['preview_seconds']=time.perf_counter()-tick
                return path
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    def guarded_preview():
                        try:return preview()
                        except BaseException:stopped.set();raise
                    future=pool.submit(guarded_preview)
                    try:
                        tick=time.perf_counter()
                        workers=decode_rgb(source,indices,request['view'],temporary,(source_h,source_w,3),progress,cancelled,on_chunk)
                        timings['decode_seconds']=time.perf_counter()-tick
                        proxy=future.result()
                    except BaseException:
                        stopped.set()
                        if future.done() and future.exception() is not None:raise future.exception()
                        raise
                if file_identity(source) != request['source']:
                    raise ValueError('Source changed during preparation')
                os.replace(temporary, frames_path)
                timings['preparation_seconds']=time.perf_counter()-started
                camera = request['camera']
                if camera:
                    K = np.asarray(camera['K'], np.float64).copy()
                    K[0] *= source_w / camera['width']
                    K[1] *= source_h / camera['height']
                    if request['view'] == 'right':
                        K[0, 2] -= source_w // 2
                    camera = {'K': K.tolist(), 'source': camera['source'], 'model': 'pinhole', 'grid': [width, height]}
                record = {'schema': 1, 'id': request['id'], 'request': request, 'source': request['source'],
                    'source_frames': len(reader), 'source_fps': source_fps, 'frames': len(indices), 'fps': fps,
                    'source_frame_indices': indices.tolist(), 'timestamps_s': timestamps.tolist(),
                    'time_domain': 'source_video_pts_seconds', 'width': width, 'height': height,
                    'frames_path': str(frames_path), 'video_path': str(proxy), 'camera': camera,
                    'frames_identity': file_identity(frames_path), 'video_identity': file_identity(proxy),
                    'proxy_pixel_grid': list(proxy_size), 'proxy_content_grid': list(proxy_size),
                    'preparation_timings': timings, 'decode_workers': workers,
                    'input_policy': 'Identical decoded RGB frames for all workers; native resize and camera remapping are declared by each algorithm'}
                atomic_json(manifest, record)
                return record
            finally:
                stopped.set()
                temporary.unlink(missing_ok=True)
