"""MINT-owned execution adapters for unmodified HaWoR networks and SLAM."""
from __future__ import annotations

import json
import math
import os
import shutil
import sqlite3
import sys
import time
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from mint.runtime import atomic_json,atomic_npz,file_identity


def camera_bridge(sample,options):
    w=min(sample['width'],options['analysis_width']) if options['analysis_width'] else sample['width']
    h=max(2,round(sample['height']*w/sample['width']));sx,sy=w/sample['width'],h/sample['height']
    if sample['camera']:
        K=np.array(sample['camera']['K'],float);grid=K.copy();grid[0]*=sx;grid[1]*=sy
        focal=float(np.sqrt(grid[0,0]*grid[1,1]));source=sample['camera']['source']
    elif options['focal'] is not None:
        f=options['focal'];K=np.array([[f,0,sample['width']/2],[0,f,sample['height']/2],[0,0,1.]])
        grid=K.copy();grid[0]*=sx;grid[1]*=sy;focal=f*sx
        source='User-declared sample focal; assumed centered pinhole'
    else:
        focal=600.;grid=np.array([[focal,0,w/2],[0,focal,h/2],[0,0,1.]])
        K=grid.copy();K[0]/=sx;K[1]/=sy
        source='HaWoR native 600px fallback at analysis grid; not measured calibration'
    canonical=np.array([[focal,0,w/2],[0,focal,h/2],[0,0,1.]])
    return (w,h),focal,K,canonical@np.linalg.inv(grid),source


def native_args(ctx,plan):
    root=Path(ctx.spec['root'])
    return SimpleNamespace(video_path=str(ctx.work/'input.mp4'),input_type='file',img_focal=plan['focal'],
        checkpoint=str(root/'weights/hawor/checkpoints/hawor.ckpt'),
        infiller_weight=str(root/'weights/hawor/checkpoints/infiller.pt'))


def adopt_stages(ctx):
    """Reuse only verified, input/native-version-compatible MINT-owned checkpoints."""
    from .native import publish_stage,stage_cached
    if getattr(ctx, 'request', {}).get('execution', {}).get('cache') == 'fresh':
        return
    if os.environ.get('MINT_NATIVE_REUSE_STAGES','1')=='0':return
    reuse_root=Path(os.environ.get('MINT_NATIVE_REUSE_ROOT',str(ctx.directory.parent.parent)))
    database=reuse_root/'jobs.sqlite'
    if not database.is_file():return
    db=sqlite3.connect(database)
    try: rows=db.execute("SELECT id,payload FROM runs WHERE id!=? AND status IN ('partial','completed') ORDER BY updated DESC",(ctx.run_id,)).fetchall()
    finally:db.close()
    for run_id,payload in rows:
        request=json.loads(payload)
        if request['algorithm'] not in {'hawor_full','hawor_camera'} or request['sample_id']!=ctx.sample['id']:continue
        if any(request['options'].get(k)!=ctx.options.get(k) for k in ('analysis_width','focal')):continue
        before=request['backend']['identity'];after=ctx.spec['identity']
        if before.get('interpreter')!=after.get('interpreter'):continue
        if before.get('environment')!=after.get('environment'):continue
        if any(before.get('sources',{}).get(name)!=identity for name,identity in after.get('sources',{}).items()):continue
        if not slam_adapters_compatible(before.get('adapter',{}),after.get('adapter',{})):continue
        old=reuse_root/'runs'/run_id/'native'
        copied=[]
        for name in ('frames','detection','motion','slam_geometry'):
            dependencies={'frames': [], 'detection':['weights/external/detector.pt'],
                'motion':['weights/hawor/checkpoints/hawor.ckpt'], 'slam_geometry':['weights/external/droid.pth']}[name]
            assets=after.get('assets',{})
            required=[key for key in dependencies if key in assets]
            if name=='motion':required += [key for key in assets if key.endswith('.pkl')]
            if not required and dependencies:required=list(assets)  # Strict fallback for older descriptors.
            if any(before.get('assets',{}).get(key)!=assets[key] for key in required):continue
            if name not in os.environ.get('MINT_NATIVE_REUSE_STAGE_LIMIT','frames,detection,motion,slam_geometry').split(','):continue
            if name=='slam_geometry':
                # A SLAM checkpoint is valid only with the exact motion/masks
                # adopted in this pass, and the same numerical adapter release.
                if request['algorithm']!='hawor_full' or 'motion' not in copied:continue
                if not slam_adapters_compatible(before.get('adapter',{}),after.get('adapter',{})):continue
            if stage_cached(ctx,name):continue
            try:
                state=json.loads((old/(name+'.complete.json')).read_text())
                if state['run_id']!=run_id or any(file_identity(p)!=identity for p,identity in state['files'].items()):continue
                destinations=[]
                for source in state['files']:
                    src=Path(source);relative=src.relative_to(old);dst=ctx.work/relative
                    dst.parent.mkdir(parents=True,exist_ok=True)
                    if src.resolve()!=dst.resolve():
                        temporary=dst.with_name('.'+dst.name+'.reuse')
                        temporary.unlink(missing_ok=True)
                        try:
                            if name=='frames':temporary.symlink_to(src)
                            else:shutil.copy2(src,temporary)
                            os.replace(temporary,dst)
                        finally:temporary.unlink(missing_ok=True)
                    destinations.append(dst)
                publish_stage(ctx,name,destinations);copied.append(name)
            except (OSError,ValueError,KeyError):continue
        if copied:
            ctx.metadata['reused_native_stages_from']={'run_id':run_id,'stages':copied,
                'validation':'Exact sample/native source/assets and camera options; original files unchanged'}
            return


def slam_adapters_compatible(before,after):
    if before==after:return True
    # Reviewed predecessor: the new SLAM path keeps all frames, equations,
    # eight-keyframe batches, thresholds, and 7+12 BA steps. Other numerical
    # dependencies must still match exactly. Drop this migration when changing
    # those semantics; do not broaden it to arbitrary historical releases.
    legacy='6deeda2b7bdf0ed58719ecf53dfffe271f8a03561e66424dd672706b9f6742af'
    numerical=('contracts.py','normalizers.py','prepare.py','native.py','worker.py','native_tasks.py','read_only.py')
    return before.get('hawor_parallel.py')==legacy and all(
        key in before and before[key]==after.get(key) for key in numerical)


def materialize_frames(ctx,size,homography):
    from .native import stage_cached,publish_stage
    if stage_cached(ctx,'frames'):return
    import cv2
    cv2.setNumThreads(1)
    folder=ctx.work/'input/extracted_images';folder.mkdir(parents=True,exist_ok=True)
    def produce(index):
        ctx.check_cancel();frame=np.asarray(ctx.frames[index])
        if frame.shape[:2]!=(size[1],size[0]):frame=cv2.resize(frame,size,interpolation=cv2.INTER_AREA)
        frame=cv2.warpPerspective(frame,homography,size,flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
        ok,encoded=cv2.imencode('.png',cv2.cvtColor(frame,cv2.COLOR_RGB2BGR),[cv2.IMWRITE_PNG_COMPRESSION,0])
        if not ok:raise RuntimeError('Native lossless frame bridge failed')
        target=folder/f'{index:06d}.jpg';temporary=target.with_suffix('.partial')
        temporary.write_bytes(encoded.tobytes());os.replace(temporary,target)
    workers=max(1,min(8,int(os.environ.get('MINT_NATIVE_IMAGE_WORKERS','8'))))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0,ctx.sample['frames'],workers*4):
            list(pool.map(produce,range(start,min(ctx.sample['frames'],start+workers*4))))
            ctx.progress(stage='input_bridge',done=min(start+workers*4,ctx.sample['frames']),total=ctx.sample['frames'])
            if shutil.disk_usage(folder).free<16*2**30:raise OSError('Native image bridge reached disk reserve')
    publish_stage(ctx,'frames',sorted(folder.glob('*.jpg')))


def build_hand_plan(tracks,size,focal,n,block_size=256):
    from lib.eval_utils.custom_utils import interpolate_bboxes
    from lib.pipeline.tools import parse_chunks
    sides={0:[],1:[]}
    for track in tracks.values():
        valid=np.array([t['det'] for t in track])
        handedness=np.concatenate([t['det_handedness'] for t in track])[valid]
        if not len(handedness):continue
        sides[int(handedness.sum()/len(handedness)>=.5)].extend(track)
    chunks=[];tasks=[]
    for slot,track in sides.items():
        track=sorted(track,key=lambda t:t['frame']);valid=np.array([t['det'] for t in track])
        if valid.sum()<2:continue
        boxes=np.concatenate([t['det_box'] for t in track]);nonzero=np.where(np.any(boxes!=0,axis=1))[0]
        first,last=nonzero[0],nonzero[-1]
        boxes[first:last+1]=interpolate_bboxes(boxes[first:last+1]);valid[first:last+1]=True
        boxes=boxes[first:last+1];frames=np.array([t['frame'] for t in track])[valid]
        frame_chunks,box_chunks=parse_chunks(frames,boxes,min_len=1)
        for frame,box in zip(frame_chunks,box_chunks):
            chunk={'slot':slot,'frames':frame.tolist(),'blocks':[]};chunk_id=len(chunks)
            for start in range(0,len(frame),block_size):
                index=len(tasks);chunk['blocks'].append(index)
                tasks.append({'index':index,'chunk':chunk_id,'slot':slot,'frames':frame[start:start+block_size].tolist(),
                              'boxes':box[start:start+block_size].tolist(),'box_dtype':str(box.dtype)})
            chunks.append(chunk)
    return {'size':list(size),'focal':focal,'frames':n,'chunks':chunks,'tasks':tasks,
            'window_semantics':'Original independent 16-frame calls; only the final group repeats its final frame'}


def merge_hand_blocks(ctx,plan):
    import joblib
    from .native import publish_stage
    sequence=ctx.work/'input';tracks=sequence/f"tracks_0_{ctx.sample['frames']}";tracks.mkdir(parents=True,exist_ok=True)
    destination=tracks/'model_masks.npy';temporary=tracks/'model_masks.partial.npy'
    masks=np.lib.format.open_memmap(temporary,mode='w+',dtype=bool,
        shape=(ctx.sample['frames'],plan['size'][1],plan['size'][0]))
    chunks=defaultdict(list);paths=[]
    try:
        for chunk in plan['chunks']:
            values=[]
            for index in chunk['blocks']:
                path=ctx.work/'hawor-blocks'/f'{index:06d}.npz'
                with np.load(path,allow_pickle=False) as z:
                    if str(z['run_id'])!=ctx.run_id:raise ValueError('Native hand block belongs to another run')
                    frame=z['frame_indices'];masks[frame]=masks[frame]|z['masks']
                    values.append({k:z[k].copy() for k in ('root','pose','trans','betas')})
            frame=np.array(chunk['frames'],np.int64);slot=chunk['slot'];chunks[slot].append(frame)
            payload={name:np.concatenate([v[key] for v in values],axis=1).tolist()
                for name,key in [('init_root_orient','root'),('init_hand_pose','pose'),('init_trans','trans'),('init_betas','betas')]}
            path=sequence/'cam_space'/str(slot)/f'{frame[0]}_{frame[-1]}.json'
            atomic_json(path,payload);paths.append(path)
        chunks.setdefault(0,[]);chunks.setdefault(1,[])
        masks.flush();os.replace(temporary,destination)
    finally:
        masks._mmap.close();temporary.unlink(missing_ok=True)
    index=tracks/'frame_chunks_all.npy';joblib.dump(chunks,index)
    publish_stage(ctx,'motion',[destination,index,*paths])
    return chunks


def growing_video_class(base,*,progress=lambda **kw:None,minimum_free_gib=6):
    """Grow storage at safe frame boundaries; all original values/thresholds survive."""
    class GrowingVideo(base):
        def ensure_capacity(self,needed):
            import torch
            old_capacity=self.poses.shape[0]
            if needed<=old_capacity:return
            capacity=max(old_capacity*2,needed+32)
            fields=[key for key,value in self.__dict__.items() if torch.is_tensor(value) and value.ndim and value.shape[0]==old_capacity]
            if not fields:raise RuntimeError('Unknown native DepthVideo storage layout')
            with self.get_lock():
                for key in fields:
                    old=getattr(self,key)
                    shape=(capacity,*old.shape[1:]);needed_bytes=int(np.prod(shape))*old.element_size()
                    if old.is_cuda:
                        free,_=torch.cuda.mem_get_info(old.device)
                        reclaimable=torch.cuda.memory_reserved(old.device)-torch.cuda.memory_allocated(old.device)
                        if free+reclaimable-needed_bytes<minimum_free_gib*2**30:
                            raise MemoryError(f'SLAM keyframe capacity needs {capacity} slots; protected GPU headroom is insufficient')
                    if key=='disps':value=torch.ones(shape,dtype=old.dtype,device=old.device)
                    else:value=torch.zeros(shape,dtype=old.dtype,device=old.device)
                    if key=='poses':value[:,6]=1
                    value[:old_capacity].copy_(old)
                    if old.is_shared():value.share_memory_()
                    setattr(self,key,value)
                    del old,value
                progress(stage='slam_buffer_grow',capacity=capacity,previous_capacity=old_capacity)
        def append(self,*item):
            self.ensure_capacity(self.counter.value+1);return super().append(*item)
        def __setitem__(self,index,item):
            if isinstance(index,int):needed=index+1
            elif isinstance(index,slice):needed=index.stop or self.poses.shape[0]
            else:needed=int(index.max())+1
            self.ensure_capacity(needed);return super().__setitem__(index,item)
    return GrowingVideo


def grow_factor_graph(graph,video):
    """Native FactorGraph holds its own per-keyframe damping allocation."""
    import torch
    previous=graph.damping
    capacity=video.disps.shape[0]
    if previous.shape[0]<capacity:
        damping=torch.full_like(video.disps,1e-6)
        damping[:len(previous)].copy_(previous)
        graph.damping=damping


def stage_runner(kind,ctx):
    from .native import _hawor_numpy_compat
    _hawor_numpy_compat()
    plan=json.loads((ctx.work/'hawor-plan.json').read_text());args=native_args(ctx,plan)
    sequence=ctx.work/'input';n=ctx.sample['frames']
    if kind=='hawor_detect':
        def detect(item):
            import lib.pipeline.tools as tools
            original_yolo=tools.YOLO
            class CooperativeYOLO(original_yolo):
                def track(self,*a,**kw):
                    tick=time.perf_counter();result=super().track(*a,**kw)
                    ctx.cooperate(tick);return result
            tools.YOLO=CooperativeYOLO
            from scripts.scripts_test_video.detect_track_video import detect_track_video
            begin,end,seq,files=detect_track_video(args)
            if begin!=0 or end!=n or len(files)!=n:raise ValueError('Native detector changed sample coverage')
            return Path(seq)/f'tracks_0_{n}/model_tracks.npy'
        return detect
    if kind=='hawor_slam':
        def slam(item):
            import torch
            import lib.pipeline.masked_droid_slam as native
            import droid
            from .hawor_slam import SlamProgress,install_runtime,write_slam_geometry
            report=SlamProgress(ctx)
            files=[str(sequence/'extracted_images'/f'{i:06d}.jpg') for i in range(n)]
            masks=torch.from_numpy(np.load(sequence/f'tracks_0_{n}/model_masks.npy',allow_pickle=False))
            events=[];base=droid.DepthVideo
            droid.DepthVideo=growing_video_class(base,progress=lambda **kw:events.append(kw))
            original_droid=native.Droid
            class GrowingDroid(original_droid):
                def track(self,*a,**kw):
                    # Frontend also writes the next pose directly after append.
                    self.video.ensure_capacity(self.video.counter.value+64)
                    grow_factor_graph(self.frontend.graph,self.video)
                    tick=time.perf_counter();value=super().track(*a,**kw)
                    ctx.cooperate(tick);report.track_seconds+=time.perf_counter()-tick
                    report('slam_tracking',int(a[0])+1,n,keyframes=self.video.counter.value)
                    return value
                def terminate(self,*a,**kw):
                    self.video.ensure_capacity(self.video.counter.value+64)
                    original_backend=self.backend
                    def backend(steps=12):
                        report('slam_global_ba',report.ba_steps,19,pass_steps=steps,force=True)
                        before=report.ba_steps
                        original_ba=self.video.ba
                        def ba(*args,**kwargs):
                            value=original_ba(*args,**kwargs)
                            report.ba_steps+=1
                            report('slam_global_ba',report.ba_steps,19,pass_steps=steps,force=True)
                            return value
                        self.video.ba=ba;started=time.perf_counter()
                        try:value=original_backend(steps)
                        finally:self.video.ba=original_ba
                        if report.ba_steps==before:report.ba_steps+=steps
                        report.ba_seconds+=time.perf_counter()-started
                        report('slam_global_ba',report.ba_steps,19,pass_steps=steps,force=True)
                        return value
                    self.backend=backend
                    original_filler=self.traj_filler
                    def fill(stream):
                        started=time.perf_counter()
                        def tracked_stream():
                            for t,image,intrinsics in stream:
                                report('slam_trajectory',int(t),n,keyframes=self.video.counter.value)
                                yield t,image,intrinsics
                        value=original_filler(tracked_stream())
                        report.fill_seconds=time.perf_counter()-started
                        report('slam_trajectory',n,n,force=True)
                        return value
                    self.traj_filler=fill
                    return super().terminate(*a,**kw)
            native.Droid=GrowingDroid;restore=install_runtime(native,droid,report)
            try:
                calib=np.array(native.est_calib(files));calib[:2]=plan['focal']
                model,traj=native.run_slam(files,masks=masks,calib=calib)
            finally:
                restore();native.Droid=original_droid;droid.DepthVideo=base
            count=model.video.counter.value
            path=ctx.work/'slam_geometry.npz'
            report('slam_checkpoint',0,1,keyframes=count,force=True)
            started=time.perf_counter()
            encoding=write_slam_geometry(path,traj=np.asarray(traj),tstamp=model.video.tstamp[:count].cpu().int().numpy(),
                disps=model.video.disps_up[:count].cpu().numpy(),calib=calib)
            atomic_json(ctx.work/'slam-capacity.json',{'events':events,'keyframes':count,'final_capacity':model.video.poses.shape[0]})
            atomic_json(ctx.work/'slam-runtime.json',{'track_compute_seconds':report.track_seconds,
                'global_ba_seconds':report.ba_seconds,'trajectory_seconds':report.fill_seconds,
                'checkpoint_seconds':time.perf_counter()-started,'checkpoint_encoding':encoding,
                'solver':'Unmodified native DROID-SLAM','frames':n,'keyframes':count,
                'native_ba_iterations':[7,12],'native_keyframe_batch':8,
                'tracking_image_prefetch':4 if os.environ.get('MINT_HAWOR_SLAM_ACCELERATE','1')!='0' else 0})
            report('slam_checkpoint',1,1,force=True)
            return path
        return slam
    if kind=='hawor_depth':
        sys.path.insert(0,str(Path(ctx.spec['root'])/'thirdparty/Metric3D'))
        from metric import Metric3D
        import cv2
        model=Metric3D('thirdparty/Metric3D/weights/metric_depth_vit_large_800k.pth')
        with np.load(ctx.work/'slam_geometry.npz',allow_pickle=False) as z:calib=z['calib'].copy()
        from lib.pipeline.masked_droid_slam import get_dimention
        H,W=get_dimention([str(sequence/'extracted_images/000000.jpg')])
        def depth(item):
            for frame in item['frames']:
                ctx.check_cancel();tick=time.perf_counter()
                value=model(str(sequence/'extracted_images'/f'{frame:06d}.jpg'),calib)
                value=cv2.resize(value,(W,H))
                atomic_npz(ctx.work/'metric-depth'/f'{frame:06d}.npz',depth=value,run_id=np.asarray(ctx.run_id))
                ctx.cooperate(tick)
            return ctx.work/'metric-depth'/f"{item['frames'][-1]:06d}.npz"
        return depth
    if kind=='hawor_infiller':
        def infill(item):
            import joblib,torch
            from scripts.scripts_test_video.hawor_video import hawor_infiller
            from lib.eval_utils.custom_utils import load_slam_cam
            chunks=joblib.load(sequence/f'tracks_0_{n}/frame_chunks_all.npy')
            values=hawor_infiller(args,0,n,chunks)
            slam=sequence/'SLAM'/f'hawor_slam_w_scale_0_{n}.npz'
            _,_,R,t=load_slam_cam(str(slam))
            def cpu(x):return x.detach().cpu().numpy() if torch.is_tensor(x) else np.asarray(x)
            path=ctx.work/'hawor_world_native.npz'
            atomic_npz(path,**dict(zip(('translation','root_rotation','hand_pose','shape','valid'),map(cpu,values))),
                camera_rotation_c2w=cpu(R),camera_translation_c2w=cpu(t),hand_order=np.asarray(['left','right']),img_focal=np.asarray(plan['focal']))
            return path
        return infill
    raise ValueError('Unknown native task kind '+kind)


def run_hawor_parallel(ctx):
    from .native import _hawor_numpy_compat,stage_cached,publish_stage
    from .native_pool import NativePool
    from .normalizers import hawor_camera_geometry,hawor_world_geometry
    _hawor_numpy_compat()
    import joblib
    sample=ctx.sample;n=sample['frames'];size,focal,K,H,source=camera_bridge(sample,ctx.options)
    sequence=ctx.work/'input';tracks=sequence/f'tracks_0_{n}'
    adopt_stages(ctx)
    started=time.perf_counter();materialize_frames(ctx,size,H)
    ctx.timings['input_bridge_seconds']=time.perf_counter()-started
    atomic_json(ctx.work/'hawor-plan.json',{'size':size,'focal':focal})
    if not stage_cached(ctx,'detection'):
        for name in ('model_boxes.npy','model_tracks.npy'):(tracks/name).unlink(missing_ok=True)
        with NativePool(ctx,'hawor_detect',maximum=1,min_free_mib=8000) as pool:pool.run([{'index':0}],stage='detection_tracking')
        publish_stage(ctx,'detection',[tracks/'model_boxes.npy',tracks/'model_tracks.npy'])
    if not stage_cached(ctx,'motion'):
        raw=np.load(tracks/'model_tracks.npy',allow_pickle=True).item()
        plan=build_hand_plan(raw,size,focal,n);atomic_json(ctx.work/'hawor-plan.json',plan)
        missing=[]
        for task in plan['tasks']:
            path=ctx.work/'hawor-blocks'/f"{task['index']:06d}.npz"
            try:
                with np.load(path,allow_pickle=False) as z:
                    if str(z['run_id'])!=ctx.run_id or not np.array_equal(z['frame_indices'],task['frames']):raise ValueError('Block identity changed')
            except (OSError,ValueError,KeyError):missing.append({'index':task['index']})
        if missing:
            with NativePool(ctx,'hawor_hand') as pool:pool.run(missing,stage='hand_prediction')
        chunks=merge_hand_blocks(ctx,plan)
    else:chunks=joblib.load(tracks/'frame_chunks_all.npy')
    ctx.progress(stage='camera_geometry')
    arrays=hawor_camera_geometry(sequence,chunks,sample,K)
    ctx.metadata.update(K_source=source,native_analysis_size=list(size),input_camera_remap=H.tolist(),
        camera_source='unavailable: camera-only stage',stage_coverage={'camera':True,'world':False},
        input_encoding='Lossless PNG, parallel CPU materialization',
        tracked_semantics='Original globally tracked/interpolated boxes; native 16-frame temporal groups',
        numerical_path='Original native weights/forward/SLAM; MINT-owned parallel execution and adaptive capacity')
    if ctx.request['algorithm']=='hawor_camera':return arrays
    try:
        if not stage_cached(ctx,'slam_geometry'):
            with NativePool(ctx,'hawor_slam',maximum=1,min_free_mib=28000) as pool:pool.run([{'index':0}],stage='slam_scale')
            publish_stage(ctx,'slam_geometry',[ctx.work/'slam_geometry.npz',ctx.work/'slam-capacity.json',ctx.work/'slam-runtime.json'])
        if (ctx.work/'slam-runtime.json').is_file():
            ctx.metadata['slam_runtime']=json.loads((ctx.work/'slam-runtime.json').read_text())
        with np.load(ctx.work/'slam_geometry.npz',allow_pickle=False) as z:
            stamps=z['tstamp'].copy();disps=z['disps'].copy();traj=z['traj'].copy();calib=z['calib'].copy()
        missing=[]
        for stamp in stamps:
            path=ctx.work/'metric-depth'/f'{stamp:06d}.npz'
            try:
                with np.load(path,allow_pickle=False) as z:
                    if str(z['run_id'])!=ctx.run_id:raise ValueError('Depth identity changed')
            except (OSError,ValueError,KeyError):missing.append(int(stamp))
        if missing:
            with NativePool(ctx,'hawor_depth',min_free_mib=12000) as pool:
                pool.run([{'frames':missing[i:i+16]} for i in range(0,len(missing),16)],stage='metric_depth')
        from lib.pipeline.est_scale import est_scale_hybrid
        masks=np.load(tracks/'model_masks.npy',mmap_mode='r',allow_pickle=False)
        scales=[];low,high=.4,.7
        for index,stamp in enumerate(stamps):
            ctx.check_cancel()
            ctx.progress(stage='metric_scale',done=index,total=len(stamps))
            with np.load(ctx.work/'metric-depth'/f'{stamp:06d}.npz',allow_pickle=False) as z:depth=z['depth'].copy()
            scale=est_scale_hybrid(1/disps[index],depth,sigma=.5,msk=masks[stamp].astype(np.uint8),near_thresh=low,far_thresh=high)
            for attempt in range(64):
                if not math.isnan(scale):break
                low-=.1;high+=.1
                scale=est_scale_hybrid(1/disps[index],depth,sigma=.5,msk=masks[stamp].astype(np.uint8),near_thresh=low,far_thresh=high)
            if not np.isfinite(scale) or scale<=0:raise ValueError('Native metric scale did not converge to a positive finite value')
            scales.append(scale)
        masks._mmap.close()
        slam=sequence/'SLAM'/f'hawor_slam_w_scale_0_{n}.npz'
        atomic_npz(slam,tstamp=stamps,disps=disps,traj=traj,img_focal=np.asarray(focal),img_center=calib[-2:],scale=np.asarray(np.median(scales)))
        if not stage_cached(ctx,'world'):
            with NativePool(ctx,'hawor_infiller',maximum=1,min_free_mib=8000) as pool:pool.run([{'index':0}],stage='world_infiller')
            publish_stage(ctx,'world',[ctx.work/'hawor_world_native.npz'])
        with np.load(ctx.work/'hawor_world_native.npz',allow_pickle=False) as z:native={key:z[key].copy() for key in z.files}
        result=hawor_world_geometry(native,sample,K,observed=arrays['tracked'])
        ctx.metadata.update(camera_source='HaWoR native masked DROID-SLAM + Metric3D scale',
            stage_coverage={'camera':True,'world':True},postprocess='Native infiller; filled slots explicit',
            slam_capacity=json.loads((ctx.work/'slam-capacity.json').read_text()))
        return result
    except Exception as exc:
        traceback.print_exc()
        ctx.check_cancel();ctx.metadata.update(partial=True,stage_error={'stage':'slam_or_infiller','error':str(exc)})
        return arrays
