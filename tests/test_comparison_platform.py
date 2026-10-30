"""Protocol, recovery, cancellation and rendering boundaries for the unified workspace."""
import gzip
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest

from mint.comparison.collections import CollectionManager, scan_videos
from mint.comparison.jobs import ComparisonManager
from mint.comparison.evaluation import aggregate_pairs, report_csv, collection_report
from mint.comparison.contracts import load_result
from tests.test_comparison import fake_backend, arrays, published, sample


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr('mint.comparison.jobs.backend_spec', fake_backend)
    monkeypatch.setattr('mint.comparison.collections.backend_spec', fake_backend)
    value = ComparisonManager(tmp_path / 'store', autostart=False)
    yield value
    value.close()


def video(tmp_path):
    path = tmp_path / 'input.mp4'; path.write_bytes(b'fixture'); return str(path)


def test_idempotent_comparison_and_shared_cancellation(manager, tmp_path):
    source = video(tmp_path)
    first = manager.create(source, [{'algorithm': 'mint'}], comparison_key='batch:0')
    again = manager.create(source, [{'algorithm': 'mint'}], comparison_key='batch:0')
    assert first['id'] == again['id'] and len(first['created_run_ids']) == 1
    with pytest.raises(ValueError, match='inputs changed'):
        manager.create(source, [{'algorithm': 'hawor_camera'}], comparison_key='batch:0')
    second = manager.create(source, [{'algorithm': 'mint'}])
    assert second['created_run_ids'] == []
    assert manager.cancel_exclusive(first['run_ids'][0], first['id']) is False
    assert manager.run(first['run_ids'][0])['status'] == 'queued'
    own = manager.create(source, [{'algorithm': 'hawor_camera'}])
    assert manager.cancel_exclusive(own['run_ids'][0], own['id']) is True
    assert manager.run(own['run_ids'][0])['status'] == 'cancelled'


def test_collection_recovery_preserves_retry_intent(manager, tmp_path):
    collection = CollectionManager(manager, autostart=False)
    value = collection.create(items=[{'source': video(tmp_path)}], selections=[{'algorithm': 'mint'}])
    collection._set(value['id'], status='running', owner_pid=999999999, owner_token='gone', items=[{'status':'running','comparison_id':'child'}])
    recovered = CollectionManager(manager, autostart=False)
    state = recovered.get(value['id'])
    assert state['status'] == 'queued' and state['items'][0]['retry'] is True
    recovered._suspend(value['id'], [{'status':'running'}, {'status':'completed'}])
    assert recovered.get(value['id'])['items'] == [{'status':'queued','retry':True}, {'status':'completed'}]
    collection.close(); recovered.close()


def test_collection_protocol_freeze_and_no_implicit_frame_cap(manager, tmp_path, monkeypatch):
    collections = CollectionManager(manager, autostart=False)
    kwargs = {'items':[{'source':video(tmp_path)}], 'selections':[{'algorithm':'mint'}]}
    value = collections.create(**kwargs)
    assert value['id'] == collections.create(**kwargs)['id'] and value['options'] == {}
    monkeypatch.setattr('mint.comparison.collections.backend_spec', lambda name, **kw: {**fake_backend(name), 'version':'changed'})
    collections._execute(value['id'])
    state = collections.get(value['id'])
    assert state['status'] == 'failed' and 'version changed' in state['error']
    collections.close()


def test_collection_cancel_does_not_touch_shared_or_preexisting_runs(manager, tmp_path):
    collection = CollectionManager(manager, autostart=False)
    source = video(tmp_path); selections=[{'algorithm':'mint'}]
    outside = manager.create(source, selections)
    value = collection.create(items=[{'source':source}], selections=selections)
    child = manager.create(source, selections, comparison_key=value['id']+':0')
    collection._set(value['id'], items=[{'status':'running','comparison_id':child['id']}])
    collection.cancel(value['id']);collection._execute(value['id'])
    assert collection.get(value['id'])['status']=='cancelled'
    assert manager.run(outside['run_ids'][0])['status']=='queued'
    collection.close()


def test_scan_is_bounded_and_does_not_follow_directory_cycles(tmp_path):
    (tmp_path/'a.mp4').write_bytes(b'a');(tmp_path/'sub').mkdir();(tmp_path/'sub'/'b.MOV').write_bytes(b'b')
    (tmp_path/'sub'/'loop').symlink_to(tmp_path, target_is_directory=True)
    assert len(scan_videos(tmp_path)['videos'])==1
    result=scan_videos(tmp_path, recursive=True, limit=1)
    assert result['truncated'] and len(result['videos'])==1
    assert len(scan_videos(tmp_path, recursive=True)['videos'])==2


def test_aggregate_uses_point_counts_and_squared_rmse_with_missing_zero_distinction():
    value=aggregate_pairs([{'camera_absolute_joint_mm':0.,'paired_joints':2,'camera_ate_se3_rmse_m':1.,'world_camera_common_frames':1},
        {'camera_absolute_joint_mm':10.,'paired_joints':6,'camera_ate_se3_rmse_m':3.,'world_camera_common_frames':3}])
    assert value['camera_absolute_joint_mm']==7.5
    assert value['camera_ate_se3_rmse_m']==pytest.approx(np.sqrt(7))
    assert value['camera_wrist_mm'] is None and value['camera_wrist_mm_samples']==0
    assert report_csv({'rows':[{'label':'=SUM(A1)','algorithm':'mint'}]}).startswith('\ufeff')
    assert "'=SUM(A1)" in report_csv({'rows':[{'label':'=SUM(A1)'}]})


def test_evaluation_failed_predictions_remain_in_gt_coverage(manager, tmp_path):
    from mint.runtime import digest
    from mint.comparison.registry import normalize_options
    source=video(tmp_path);selection={'algorithm':'mint'}
    comps=[]
    for index in range(2):
        comp=manager.create(source,[selection, {'algorithm':'hawor_camera'}], start_frame=index)
        prediction,reference=comp['run_ids']
        published(manager.root/'runs'/reference/'result', sample_id=comp['sample_id'],run_id=reference,reference_role='user_declared_ground_truth')
        manager._set(reference,'completed')
        if index==0:
            published(manager.root/'runs'/prediction/'result',sample_id=comp['sample_id'],run_id=prediction,backend_version='mint-v1')
            manager._set(prediction,'completed')
        else:manager._set(prediction,'failed',error='fixture failure')
        comps.append(comp)
    collection={'id':'evaluation','status':'partial','selections':[selection], 'options':{},'backend_versions':{'mint:'+digest(selection):'mint-v1'},
        'inputs':[{'source':source} for _ in comps], 'items':[{'status':'partial','comparison_id':c['id']} for c in comps]}
    report=collection_report(manager,collection);row=report['rows'][0]
    assert row['scored_sequences']==1 and row['failed_sequences']==1
    assert row['gt_geometry_coverage']==.5 and row['gt_hand_slots']==20
    assert report['scorable_sequences']==2


def test_world_mesh_payload_transforms_vertices_without_modifying_source(tmp_path):
    from mint.comparison.results import frame_payload
    value=arrays();value['vertices_camera_m']=np.ones((5,2,778,3),np.float32)
    directory=tmp_path/'mesh';published(directory,value)
    payload=frame_payload(directory,sample(),start=0,count=1,vertices=True,origin_source_frame=10)
    assert np.allclose(np.asarray(payload['vertices_world_m'])[0,0,0],[1,1,1])
    assert np.allclose(load_result(directory)[1]['vertices_camera_m'],1.)


def test_render_inputs_camera_is_display_only_and_preserves_absence(tmp_path):
    from mint.comparison.tool_worker import render_inputs
    value=arrays(world=False);value['geometry_valid'][:,0]=False;value['joints_camera_m'][:,0]=np.nan
    value['presence']=np.ones((5,2),bool);value['presence'][1,1]=False
    directory=tmp_path/'result';published(directory,value)
    manifest=tmp_path/'sample.json';manifest.write_text(json.dumps(sample()))
    request={'result_dir':str(directory),'sample_manifest':str(manifest),'start':0,'end':3,'space':'camera','include_absent':False}
    world,cameras,kept,K,_,ids=render_inputs(request)
    assert np.isfinite(world['left']['joints']).all() and not kept[:,0].any()
    assert not kept[1,1] and np.allclose(cameras,np.eye(4)) and list(ids)==[10,11,12]
    assert np.isnan(load_result(directory)[1]['joints_camera_m'][:,0]).all()


def test_json_compression_respects_accept_encoding_and_never_changes_media(manager, tmp_path, monkeypatch):
    from flask import Flask
    from mint.comparison.api import comparison_blueprint
    monkeypatch.setattr('mint.comparison.api.ComparisonManager',lambda:manager)
    comp=manager.create(video(tmp_path),[{'algorithm':'mint'}]);run=comp['run_ids'][0]
    published(manager.root/'runs'/run/'result',sample_id=comp['sample_id'],run_id=run);manager._set(run,'completed')
    manifest=manager.root/'inputs'/comp['sample_id']/'sample.json'
    manifest.parent.mkdir(parents=True);manifest.write_text(json.dumps(sample()))
    app=Flask(__name__);app.register_blueprint(comparison_blueprint());client=app.test_client()
    path='/api/comparison/runs/'+run+'/frames?count=5'
    plain=client.get(path,headers={'Accept-Encoding':'gzip;q=0'})
    compressed=client.get(path,headers={'Accept-Encoding':'gzip'})
    assert plain.status_code==compressed.status_code==200
    assert 'Content-Encoding' not in plain.headers and compressed.headers['Content-Encoding']=='gzip'
    assert json.loads(gzip.decompress(compressed.data))==plain.json
    assert len(compressed.data)<len(plain.data)/3

@pytest.fixture
def annotated_episode(tmp_path, monkeypatch):
    import cv2
    import pyarrow as pa
    import pyarrow.parquet as pq
    from mint.comparison import datasets
    root=tmp_path/'dataset';(root/'meta').mkdir(parents=True)
    info={'fps':30,'hand_frame':'camera','features':{'observation.images.ego':{'dtype':'video','shape':[64,128,3]}}}
    (root/'meta/info.json').write_text(json.dumps(info))
    source=root/'input.mp4';writer=cv2.VideoWriter(str(source),cv2.VideoWriter_fourcc(*'mp4v'),30,(128,64))
    for index in range(10):writer.write(np.full((64,128,3),index*15,np.uint8))
    writer.release()
    K=np.array([[100.,0.,61.],[0,102.,29.],[0,0,1.]])
    values={'cam_trans':[[0.,0.,0.]]*10,'cam_quat':[[0.,0.,0.,1.]]*10,'cam_fov':[[1.,1.]]*10,
        'left_kpt21':[[0.,0.,1.]*21]*10,'right_kpt21':[[.1,0.,1.]*21]*10,
        'hand_kept':[[True,True]]*10,'intrinsics':[K.reshape(-1).tolist()]*10}
    values['hand_kept'][4]=[False,True]
    parquet=root/'labels.parquet';pq.write_table(pa.table(values),parquet)
    ep={'episode_index':2,'length':4,'start':3,'video_start':3,'video':str(source),'parquet':str(parquet),'dataset_root':str(root),'hand_frame':'camera'}
    monkeypatch.setattr(datasets,'episodes',lambda *args:(root,info,['observation.images.ego'],'observation.images.ego',[ep]))
    return root,ep,K


def test_episode_fps_selection_stays_inside_boundaries(annotated_episode):
    from mint.comparison.datasets import resolve_dataset_input
    root,ep,K=annotated_episode
    resolved,context=resolve_dataset_input({'root':str(root),'episode':2,'attach_gt':True},target_fps=15)
    assert resolved['start_frame']==4 and resolved['max_frames']==2
    assert resolved['camera'] is None and context['reference_only']
    with pytest.raises(ValueError,match='起始帧超出'):
        resolve_dataset_input({'root':str(root),'episode':2},start_frame=4)
    with pytest.raises(ValueError,match='完整画面'):
        resolve_dataset_input({'root':str(root),'episode':2},view='left')


def test_reference_uses_annotated_K_masks_and_no_rgb_decode(annotated_episode, monkeypatch):
    from mint.comparison.datasets import resolve_dataset_input, reference_arrays
    from mint.comparison.contracts import validate_geometry
    root,ep,K=annotated_episode
    resolved,context=resolve_dataset_input({'root':str(root),'episode':2,'attach_gt':True},target_fps=15)
    monkeypatch.setattr('decord.VideoReader',lambda *args,**kw: (_ for _ in ()).throw(AssertionError('GT must not decode RGB')))
    sample_value={'width':128,'height':64,'source_frame_indices':[4,6],'timestamps_s':[4/30,6/30]}
    result,metadata=reference_arrays(sample_value,context)
    assert validate_geometry(result)==2 and not result['geometry_valid'][0,0]
    assert np.isnan(result['joints_camera_m'][0,0]).all() and np.allclose(result['K'],K)
    assert metadata['reference_role']=='user_declared_ground_truth'
    info_path=root/'meta/info.json';info_path.write_text(info_path.read_text()+' ')
    with pytest.raises(ValueError,match='changed'):
        reference_arrays(sample_value,context)


def test_catalog_locates_selected_episode_on_later_page(annotated_episode, monkeypatch):
    from mint.comparison import datasets
    root,ep,K=annotated_episode;info=json.loads((root/'meta/info.json').read_text())
    records=[{**ep,'episode_index':i} for i in range(215)]
    monkeypatch.setattr(datasets,'episodes',lambda *args:(root,info,['observation.images.ego'],'observation.images.ego',records))
    result=datasets.catalog(root,limit=100,episode=203)
    assert result['offset']==200 and result['episodes'][3]['episode_index']==203
    info.pop('hand_frame');(root/'meta/info.json').write_text(json.dumps(info))
    assert not datasets.annotation_capability(ep)['ready']


def test_cached_workspace_bridge_is_idempotent_and_does_not_infer(manager, tmp_path):
    from mint.comparison.workspace import WorkspaceServices
    source=video(tmp_path);prediction={'hand':np.ones((5,218),np.float32),'pose_enc':np.zeros((5,9),np.float32)}
    legacy=SimpleNamespace(_pred={(1,'chunked','hard'):prediction},_items={1:{'kind':'video','path':source,'video_view':'full','hand_frame':'world'}},
        _raw={1:{'source_indices':np.arange(10,15),'fps':30}},predictor=SimpleNamespace(_cache_model_identity='model-v1'),ckpt_path='/original/model',
        _validate_input_version=lambda eid:None)
    service=WorkspaceServices(manager,None,None,legacy)
    selected=service.cached_predictions()[0]
    assert selected['identity']['hand_frame']=='world'
    first=service.import_prediction(selected['id']);again=service.import_prediction(selected['id'])
    assert first['id']==again['id'] and first['run_ids']==again['run_ids']
    request=json.loads(manager.row(first['run_ids'][0])['payload'])
    assert request['import']['hand_frame']=='world' and request['import']['format']=='mint_npz'
    assert all(manager.run(run)['status']=='queued' for run in first['run_ids'])
    prediction['hand'][0,0]=9
    changed=service.import_prediction(selected['id'])
    assert changed['id']!=first['id']


def test_imported_world_hand_geometry_is_converted_by_its_own_camera(tmp_path, monkeypatch):
    from mint.comparison import native
    from mint.runtime import file_identity
    path=tmp_path/'prediction.npz';np.savez(path,hand=np.ones((5,218)),pose_enc=np.zeros((5,9)))
    decoded=arrays();decoded['vertices_camera_m']=np.ones((5,2,778,3),np.float32)
    monkeypatch.setattr(native,'mint_geometry',lambda *args:decoded)
    ctx=SimpleNamespace(request={'import':{'path':str(path),'identity':file_identity(path),'format':'mint_npz','frame_map':list(range(5)),'hand_frame':'world'}},sample=sample(),metadata={})
    result=native.import_native(ctx)
    assert np.allclose(result['joints_camera_m'][0,...,0],-4.)
    assert np.allclose(result['vertices_camera_m'][0,...,0],-4.)
    assert 'Explicit world-frame' in ctx.metadata['hand_coordinate_conversion']


def test_services_register_recovery_without_starting_gpu_work_during_app_construction(manager, monkeypatch):
    from flask import Flask
    from mint.comparison.api import comparison_blueprint
    monkeypatch.setattr('mint.comparison.api.ComparisonManager',lambda:manager)
    app=Flask(__name__);app.register_blueprint(comparison_blueprint())
    assert callable(app.extensions['mint_comparison_start'])
    assert manager._threads=={}


def test_diagnostic_timeout_returns_structured_error(manager, monkeypatch):
    import subprocess
    from flask import Flask
    from mint.comparison.api import comparison_blueprint
    app=Flask(__name__);app.register_blueprint(comparison_blueprint())
    monkeypatch.setattr(subprocess,'run',mock.Mock(side_effect=subprocess.TimeoutExpired('doctor',90)))
    response=app.test_client().post('/api/comparison/diagnostics',json={'profile':'train'})
    assert response.status_code==504 and 'no inference or training' in response.json['error']


def test_task_hub_can_index_collections_beyond_public_history_page(manager, tmp_path):
    collection=CollectionManager(manager,autostart=False)
    value=collection.create(items=[{'source':video(tmp_path)}],selections=[{'algorithm':'mint'}])
    with manager.db() as db:
        template=db.execute('SELECT * FROM collections WHERE id=?',(value['id'],)).fetchone()
        for index in range(205):
            key=f'{index:064x}';payload=json.loads(template['payload']);payload['id']=key
            db.execute('INSERT OR IGNORE INTO collections(id,payload,status,items,created,updated) VALUES(?,?,?,?,?,?)',
                (key,json.dumps(payload),'queued',template['items'],index,index))
    assert len(collection.history(200))==200 and len(collection.history(limit=None))==206
    collection.close()


def test_render_scheduler_can_see_old_jobs_beyond_recent_100(manager):
    from mint.comparison.tools import RenderTools
    tools=RenderTools(manager,autostart=False)
    for index in range(105):
        folder=tools.root/f'{index:064x}';folder.mkdir()
        (folder/'request.json').write_text(json.dumps({'id':folder.name,'created':index}))
        (folder/'status.json').write_text(json.dumps({'status':'queued' if index==0 else 'failed'}))
    assert len(tools.history())==100
    assert len(tools.history(limit=None))==105
    assert any(job['status']=='queued' for job in tools.history(limit=None))
    tools.close()


def test_task_hub_links_completed_exports_and_marks_released_video_cache(manager, monkeypatch):
    from mint.comparison.workspace import WorkspaceServices
    monkeypatch.setattr('mint.inference.checkpoints.list_tasks',lambda limit=None:[])
    empty=SimpleNamespace(history=lambda limit=None:[])
    service=WorkspaceServices(manager,empty,empty)
    folder=manager.root/'exports';folder.mkdir();key='a'*64
    (folder/(key+'.json')).write_text(json.dumps({'id':key,'status':'completed','done':81,'total':81}))
    movie=folder/(key+'.mp4');movie.write_bytes(b'fixture')
    task=service.tasks()['tasks'][0]
    assert task['url']=='/api/comparison/exports/'+key+'/download' and task['status']=='completed'
    movie.unlink()
    assert service.tasks(status='active')['total']==0
    task=service.tasks(status='attention')['tasks'][0]
    assert task['status']=='evicted' and task['url'] is None
