'use strict';

const datasetForm={active:false,catalog:null,offset:0};
const platform={pane:'data',selection:new Map(),catalog:null,datasetOffset:0,taskOffset:0,taskSerial:0,serial:0,timer:null,collection:null,collectionKind:null,report:null,renderJob:null,capability:null};
const HUB_LABELS={data:'数据与输入',tasks:'任务中心',batch:'多视频多算法批量',evaluation:'多算法数据集评测',tools:'高级工具'};
function datasetSelection(){
  if(!datasetForm.active)return null;
  if(!datasetForm.catalog||!$('datasetEpisode').value)throw new Error('请先读取数据集并选择 Episode');
  return {root:datasetForm.catalog.root,video_key:$('datasetCamera').value,episode:Number($('datasetEpisode').value),attach_gt:$('datasetAttachGT').checked,use_calibration:$('datasetCalibration').checked};
}
function decorateInputBody(body){const dataset=datasetSelection();return dataset?{...body,dataset}:body;}
function setDatasetMode(active){
  datasetForm.active=active;$('datasetFields').hidden=!active;$('sourceDirectory').hidden=active;
  $('chooseVideoInput').classList.toggle('active',!active);$('chooseDatasetInput').classList.toggle('active',active);
  $('view').disabled=active;if(active)$('view').value='full';$('startFrameLabel').textContent=active?'Episode 内起始帧':'起始源帧';
}
function chooseDatasetEpisode(){
  const row=datasetForm.catalog?.episodes.find(value=>value.episode_index===Number($('datasetEpisode').value));if(!row)return;
  $('source').value=row.video;$('view').value='full';$('camera').value='';$('startFrame').value=0;
  $('datasetAttachGT').disabled=!row.annotation.ready;if(!row.annotation.ready)$('datasetAttachGT').checked=false;
  $('datasetCalibration').disabled=!row.annotation.camera;if(!row.annotation.camera)$('datasetCalibration').checked=false;
  $('datasetSummary').textContent=`${datasetForm.catalog.total} 个 Episode · 当前 ${row.length} 帧 · ${datasetForm.catalog.fps} FPS · ${row.annotation.ready?(row.annotation.mesh?'GT 含 MANO 网格':'GT 含 21 点'):'GT 不可用：'+row.annotation.reason}`;
}
async function loadDatasetForm(offset=0,selection=null){
  $('datasetSummary').textContent='正在读取 Episode 元数据…';
  const root=selection?.root||$('datasetRoot').value.trim(),camera=selection?.video_key||$('datasetCamera').value;
  const query=new URLSearchParams({root,offset,limit:100});if(camera)query.set('camera',camera);if(selection)query.set('episode',selection.episode);
  const value=await api('api/comparison/dataset?'+query);datasetForm.catalog=value;datasetForm.offset=value.offset;
  $('datasetRoot').value=value.root;$('datasetCamera').replaceChildren();for(const name of value.cameras){const option=el('option',name);option.value=name;$('datasetCamera').append(option);}$('datasetCamera').value=value.camera;
  $('datasetEpisode').replaceChildren();for(const row of value.episodes){const option=el('option',`Episode ${row.episode_index} · ${row.length} 帧`);option.value=row.episode_index;$('datasetEpisode').append(option);}
  if(selection&&value.episodes.some(row=>row.episode_index===selection.episode))$('datasetEpisode').value=selection.episode;
  $('datasetPreviousPage').disabled=value.offset===0;$('datasetNextPage').disabled=value.offset+value.episodes.length>=value.total;
  chooseDatasetEpisode();setDatasetMode(true);return value;
}
function restoreDatasetForm(comparison){
  const context=comparison?.dataset_context;
  setDatasetMode(!!context);if(!context){datasetForm.catalog=null;return;}
  $('datasetRoot').value=context.selection.root;$('datasetAttachGT').checked=!!context.selection.attach_gt;$('datasetCalibration').checked=!!context.selection.use_calibration;
  loadDatasetForm(0,context.selection).then(()=>{$('startFrame').value=context.relative_start;$('maxFrames').value=context.requested_max_frames??'';$('camera').value=comparison.sample_request.camera&&!context.selection.use_calibration?JSON.stringify(comparison.sample_request.camera):'';}).catch(error=>{$('datasetSummary').textContent=error.message;});
}
function selectedAlgorithmsFrom(container){
  return [...container.querySelectorAll('.algorithm')].filter(box=>box.querySelector('input[type=checkbox]').checked).flatMap(box=>{
    const options={};for(const input of box.querySelectorAll('[data-option]'))if(input.value!=='')options[input.dataset.option]=input.value;
    const selection={algorithm:box.dataset.algorithm,options},field=box.querySelector('[data-selection=checkpoint]'),config=box.querySelector('[data-selection=config]')?.value.trim();
    const checkpoints=field?.value.split('\n').map(value=>value.trim()).filter(Boolean)||[];
    return checkpoints.length?checkpoints.map(checkpoint=>({...selection,checkpoint,...(config?{config}:{})})):[{...selection,...(config?{config}:{})}];
  });
}
function hubError(error){$('platformError').textContent=error?.message||String(error);}
function selectionSummary(){
  const values=[...platform.selection.values()],episodes=values.filter(value=>value.dataset).length;
  $('collectionSelectionSummary').textContent=`已选择 ${values.length} 项 · ${episodes} 个 Episode · ${values.length-episodes} 个视频`;
}
function addInputCheckbox(host,key,value,label,description){
  const row=el('label',undefined,'collection-entry'),check=el('input');check.type='checkbox';check.checked=platform.selection.has(key);
  check.onchange=()=>{if(check.checked)platform.selection.set(key,value);else platform.selection.delete(key);selectionSummary();};
  const copy=el('span');copy.append(el('strong',label),el('small',description));row.append(check,copy);host.append(row);
}
async function scanCollectionVideos(){
  $('scanSummary').textContent='正在扫描目录…';const query=new URLSearchParams({path:$('collectionDirectory').value.trim(),recursive:$('scanRecursive').checked?'1':'0',limit:1000});
  const value=await api('api/comparison/scan?'+query);$('collectionVideos').replaceChildren();
  platform.scanned=value.videos;for(const row of value.videos)addInputCheckbox($('collectionVideos'),'video:'+row.source,{source:row.source,name:row.name},row.name,sourceFileSize(row.size_bytes));
  $('scanSummary').textContent=`${value.videos.length} 个视频${value.truncated?' · 已到单次扫描上限，请进入子目录分批选择':''}`;
}
async function loadHubDataset(offset=0){
  $('hubDatasetSummary').textContent='正在读取元数据…';const query=new URLSearchParams({root:$('hubDatasetRoot').value.trim(),offset,limit:100});if($('hubDatasetCamera').value)query.set('camera',$('hubDatasetCamera').value);
  const value=await api('api/comparison/dataset?'+query);platform.catalog=value;platform.datasetOffset=value.offset;
  $('hubDatasetRoot').value=value.root;$('hubDatasetCamera').replaceChildren();for(const name of value.cameras){const option=el('option',name);option.value=name;$('hubDatasetCamera').append(option);}$('hubDatasetCamera').value=value.camera;
  $('hubEpisodes').replaceChildren();for(const row of value.episodes){const dataset={root:value.root,video_key:value.camera,episode:row.episode_index,attach_gt:row.annotation.ready,use_calibration:false},key=`episode:${value.root}:${value.camera}:${row.episode_index}`;
    addInputCheckbox($('hubEpisodes'),key,{source:row.video,dataset,name:`${value.root.split('/').pop()} · Episode ${row.episode_index}`,annotation:row.annotation},`Episode ${row.episode_index} · ${row.length} 帧`,row.annotation.ready?(row.annotation.mesh?'GT · MANO + 相机':'GT · 21 点 + 相机'):'可推理 · GT 不完整');
  }
  $('hubDatasetSummary').textContent=`${value.total} 个 Episode · 显示 ${value.offset+1}–${value.offset+value.episodes.length} · ${value.fps} FPS`;
  $('hubDatasetPrevious').disabled=value.offset===0;$('hubDatasetNext').disabled=value.offset+value.episodes.length>=value.total;
}
function createComposer(kind){
  const host=$(kind==='batch'?'batchComposer':'evaluationComposer');if(host.children.length)return;
  const row=el('div',undefined,'composer-summary');row.append(el('p','输入来自“数据与输入”的勾选项；默认处理完整帧范围。','hint'));const choose=el('button','选择视频 / Episode');choose.onclick=()=>switchHub('data');row.append(choose);host.append(row);
  const fields=el('div',undefined,'composer-fields');
  for(const [id,label,type,value]of [['view','视频视角','select','full'],['target_fps','目标 FPS（0=源帧率）','number',0],['start_frame','起始帧（Episode 内）','number',0],['max_frames','最多帧数（留空=全部）','number','']]){
    const input=type==='select'?selectInput([['full','完整画面'],['left','双目左眼'],['right','双目右眼']],value):el('input');input.id=kind+'-'+id;if(type!=='select'){input.type=type;input.min=id==='max_frames'?1:0;input.step=id==='target_fps'?'any':1;input.value=value;}fields.append(inputLabel(label,input));
  }
  const camera=el('textarea');camera.id=kind+'-camera';camera.rows=2;camera.placeholder='可选手工标定 JSON：K、width、height、source';fields.append(inputLabel('手工相机标定（完整原视频网格）',camera));
  const calibration=el('input');calibration.type='checkbox';calibration.id=kind+'-calibration';fields.append(inputLabel('明确使用数据集标定作为算法输入',calibration));
  const reference=el('input');reference.type='checkbox';reference.id=kind+'-reference';reference.checked=true;reference.disabled=kind==='evaluation';fields.append(inputLabel('附加数据集 GT 参考',reference));host.append(fields);
  const algorithms=el('div',undefined,'composer-algorithms');algorithms.id=kind+'-algorithms';
  host.append(algorithms);cloneComposerAlgorithms(algorithms);
  const start=el('button',kind==='evaluation'?'开始多算法数据集评测':'开始多视频多算法批量','primary');start.id=kind+'-submit';start.onclick=()=>submitCollection(kind).catch(hubError);host.append(start);
}
async function submitCollection(kind){
  const chosen=[...platform.selection.values()];if(!chosen.length)throw new Error('请先在“数据与输入”选择视频或 Episode');
  if(kind==='evaluation'&&chosen.some(value=>!value.dataset||!value.annotation?.ready))throw new Error('界面评测需要每个输入都有 LeRobot GT；普通视频可使用 API 提供明确参考标注');
  const options={view:$(kind+'-view').value,target_fps:Number($(kind+'-target_fps').value),start_frame:Number($(kind+'-start_frame').value),max_frames:$(kind+'-max_frames').value===''?null:Number($(kind+'-max_frames').value)};
  const manualCamera=$(kind+'-camera').value.trim();if(manualCamera)options.camera=JSON.parse(manualCamera);
  for(const input of $(kind==='batch'?'batchComposer':'evaluationComposer').querySelectorAll('input[type=number]'))if(!input.checkValidity()){input.reportValidity();throw new Error('请检查帧范围和 FPS');}
  const items=chosen.map(value=>({source:value.source,name:value.name,...(value.dataset?{dataset:{...value.dataset,attach_gt:kind==='evaluation'||$(kind+'-reference').checked,use_calibration:$(kind+'-calibration').checked}}:{})}));
  const selections=selectedAlgorithmsFrom($(kind+'-algorithms'));if(!selections.length)throw new Error('请至少选择一个算法');
  const button=$(kind+'-submit');button.disabled=true;
  try{const value=await api('api/comparison/collections',{kind,items,selections,options});await openCollection(value.id);}finally{button.disabled=false;}
}
async function openPlatform(tab='data'){
  pause();$('platformError').textContent='';if(!$('platformDialog').open)$('platformDialog').showModal();
  if(!$('collectionDirectory').value)$('collectionDirectory').value=sourceBrowser.path||'/data1/lizhen_dataset';
  if(!$('hubDatasetRoot').value)$('hubDatasetRoot').value=$('datasetRoot').value||'/data1/lizhen_algo/mint/data/samples/lerobot_v3';
  switchHub(tab);
}
function switchHub(tab){
  platform.pane=tab;platform.serial++;clearTimeout(platform.timer);$('platformTitle').textContent=HUB_LABELS[tab];$('platformError').textContent='';
  for(const key of Object.keys(HUB_LABELS))$('hub-'+key).hidden=key!==tab;
  for(const button of document.querySelectorAll('[data-hub-pane]'))button.classList.toggle('active',button.dataset.hubPane===tab);
  $('collectionDetails').hidden=!platform.collection||platform.collectionKind!==tab;
  if(['batch','evaluation'].includes(tab))createComposer(tab);
  if(tab==='tools')refreshToolOptions().catch(hubError);
  pollPlatform();
}
async function pollPlatform(){
  clearTimeout(platform.timer);if(!$('platformDialog').open)return;const serial=platform.serial;
  try{if(!document.hidden){if(platform.pane==='tasks')await refreshWorkspaceTasks();if(platform.collection&&platform.pane===platform.collectionKind)await refreshCollection();if(platform.renderJob&&platform.pane==='tools')await refreshRenderTool();}}
  catch(error){if(serial===platform.serial)hubError(error);}
  finally{if($('platformDialog').open&&serial===platform.serial)platform.timer=setTimeout(pollPlatform,3000);}
}
function statusLabel(status){return STAGES[status]||({waiting_gpu:'等待 GPU',evicted:'缓存已释放',absent:'尚未生成'})[status]||status;}
async function refreshWorkspaceTasks(){
  const serial=++platform.taskSerial,query=new URLSearchParams({limit:50,offset:platform.taskOffset,query:$('taskSearch').value,status:$('taskStatusFilter').value});
  const value=await api('api/comparison/workspace?'+query);if(serial!==platform.taskSerial)return;
  const scheduler=value.resource||{};$('resourceSummary').textContent=`MINT 调度器：${scheduler.running_devices?.length||0} 卡运行 · ${scheduler.queued||0} 个窗口排队 · 策略 ${scheduler.policy||'按需加载'}。其他算法和渲染的实际阶段见任务行。`;
  const table=el('table'),head=el('tr');for(const label of ['任务 / 来源','状态 / 当前阶段','完成量','操作'])head.append(el('th',label));table.append(head);
  for(const task of value.tasks){const row=el('tr'),name=el('td');name.append(el('strong',task.label),el('small',task.source||task.kind));const progress=task.progress||{},phase=progress.stage||progress.phase;row.append(name,el('td',statusLabel(task.status)+(phase?' · '+(STAGES[phase]||phase):'')),el('td',progress.total?`${progress.done||0}/${progress.total}`:'—'));const actions=el('td');
    const open=el('button','打开');open.onclick=()=>{if(task.kind==='batch'||task.kind==='evaluation')openCollection(task.id).catch(hubError);else if(task.kind==='render'){platform.renderJob=task.id;switchHub('tools');}else if(task.comparison_id){$('platformDialog').close();openComparison(task.comparison_id).then(()=>inspectResult(task.id)).catch(error=>notify(error.message));}else if(task.url)location.href=task.url;};actions.append(open);
    const terminal=TERMINAL.has(task.status)||task.status==='evicted';if(['inference','batch','evaluation','render'].includes(task.kind)&&task.status!=='completed'){
      const action=el('button',terminal?'恢复':'取消');action.onclick=async()=>{try{if(task.kind==='inference')await api(`api/comparison/runs/${task.id}/${terminal?'resume':'cancel'}`,{});else if(task.kind==='render'){if(terminal){platform.renderJob=task.id;await resumeRenderTool(task.id);}else await api(`api/comparison/tools/${task.id}/cancel`,{});}else await api(`api/comparison/collections/${task.id}/${terminal?'resume':'cancel'}`,{});await refreshWorkspaceTasks();}catch(error){hubError(error);}};actions.append(action);
    }
    if(task.error){const error=el('small',task.error,'task-error');name.append(error);}row.append(actions);table.append(row);
  }
  $('workspaceTasks').replaceChildren(table);$('taskPageInfo').textContent=`${value.total} 条 · 当前 ${value.total?value.offset+1:0}–${Math.min(value.offset+value.tasks.length,value.total)}`;$('taskPreviousPage').disabled=value.offset===0;$('taskNextPage').disabled=value.offset+value.tasks.length>=value.total;
  $('legacyResults').replaceChildren();for(const entry of value.cached_predictions){const row=el('div',undefined,'legacy-result');row.append(el('span',`${entry.source.split('/').pop()} · ${entry.frames} 帧 · ${entry.camera_mode} · ${entry.hand_mode}`));const button=el('button','导入到比较');button.onclick=async()=>{button.disabled=true;try{const comparison=await api('api/comparison/legacy-results/import',{id:entry.id});$('platformDialog').close();await openComparison(comparison.id);}catch(error){hubError(error);}finally{button.disabled=false;}};row.append(button);$('legacyResults').append(row);}
  if(!value.cached_predictions.length)$('legacyResults').textContent='当前没有原工作台内存预测；可在原工作台加载已有结果，或导入保存的 NPZ。';
}
async function openCollection(id){
  const value=await api('api/comparison/collections/'+id);platform.collection=id;platform.collectionKind=value.kind;platform.report=null;$('evaluationResults').replaceChildren();
  if(!$('platformDialog').open)$('platformDialog').showModal();switchHub(value.kind);$(value.kind+'Setup').open=false;await renderCollection(value);$('collectionDetails').scrollIntoView({block:'start'});
}
async function refreshCollection(){const id=platform.collection;if(!id)return;const value=await api('api/comparison/collections/'+id);if(id===platform.collection)await renderCollection(value);}
async function renderCollection(value){
  if(value.id!==platform.collection)return;$('collectionDetails').hidden=platform.pane!==value.kind;$('collectionTitle').textContent=value.title;$('collectionProgress').textContent=`${statusLabel(value.status)} · 已处理 ${value.done}/${value.total} · 完整完成 ${value.completed} · ${value.error||''}`;
  $('cancelCollection').disabled=TERMINAL.has(value.status);$('resumeCollection').disabled=!TERMINAL.has(value.status)||value.status==='completed';$('downloadCollectionJSON').disabled=!value.report_available;
  const table=el('table'),head=el('tr');for(const label of ['输入','状态','结果'])head.append(el('th',label));table.append(head);
  value.items.slice(0,200).forEach((item,index)=>{const row=el('tr');row.append(el('td',value.inputs[index].name||value.inputs[index].source),el('td',statusLabel(item.status)+(item.error?' · '+item.error:'')));const cell=el('td');if(item.comparison_id){const button=el('button','打开联动结果');button.onclick=()=>{$('platformDialog').close();openComparison(item.comparison_id).catch(error=>notify(error.message));};cell.append(button);}row.append(cell);table.append(row);});$('collectionChildren').replaceChildren(table);
  if(value.total>200)$('collectionChildren').append(el('p',`界面显示前 200 项；完整 ${value.total} 项见下载索引。`,'hint'));
  if(value.report_available&&(!platform.report||platform.report._collection_id!==value.id)){
    const report=await api(`api/comparison/collections/${value.id}/report`);if(platform.collection!==value.id)return;platform.report={...report,_collection_id:value.id};renderEvaluationReport(platform.report);
  }
  $('downloadCollectionCSV').hidden=value.kind!=='evaluation'||!value.report_available;$('downloadCollectionCSV').href=`api/comparison/collections/${value.id}/report?format=csv`;$('downloadCollectionCSV').download='evaluation-'+value.id.slice(0,8)+'.csv';
}
function renderEvaluationReport(report){
  const host=$('evaluationResults');host.replaceChildren();if(!report.rows)return;
  host.append(el('p',`协议 ${report.protocol.name} · 可评分 ${report.scorable_sequences}/${report.total_sequences} 个输入；覆盖率包含失败预测。`,'hint'));
  const columns=[['算法',row=>row.label],['已评分 / 失败',row=>`${row.scored_sequences} / ${row.failed_sequences}`],['GT几何覆盖率',row=>row.gt_geometry_coverage===null?'—':fmt(row.gt_geometry_coverage*100,1)+'%'],['关节误差 mm',row=>metricValue(row.camera_absolute_joint_mm)],['腕对齐 mm',row=>metricValue(row.wrist_aligned_joint_mm)],['Sim3 手部 mm',row=>metricValue(row.similarity_aligned_joint_mm)],['重投影 px',row=>metricValue(row.reprojection_difference_px)],['相机 ATE SE3 m',row=>metricValue(row.camera_ate_se3_rmse_m,4)],['相机 ATE Sim3 m',row=>metricValue(row.camera_ate_sim3_rmse_m,4)],['RPE 平移 m',row=>metricValue(row.camera_rpe_translation_rmse_m,4)],['RPE 旋转 °',row=>metricValue(row.camera_rpe_rotation_rmse_deg,3)]];
  const table=el('table'),head=el('tr');for(const [name]of columns)head.append(el('th',name));table.append(head);for(const row of report.rows){const tr=el('tr');for(const [,format]of columns)tr.append(el('td',format(row)));table.append(tr);}host.append(table);
}
async function refreshToolOptions(){
  if(!platform.capability)platform.capability=await api('api/comparison/capabilities');
  const old=$('renderRun').value;$('renderRun').replaceChildren();for(const run of state.comparison?.runs||[])if(run.result){const option=el('option',run.label+' · '+run.id.slice(0,6));option.value=run.id;$('renderRun').append(option);}if([...$('renderRun').options].some(option=>option.value===old))$('renderRun').value=old;
  const cap=platform.capability,notes=[];for(const kind of ['mujoco','retarget'])if(!cap.render[kind].ready)notes.push(kind+'：'+cap.render[kind].reason);notes.push(cap.cloud.ready?'云端评测配置已填写，需显式提交。':'云端评测尚需填写工作区、资源、镜像与存储配置。');$('toolReadiness').textContent=notes.join(' ');
  $('startRenderTool').disabled=!$('renderRun').value||!cap.render[$('renderKind').value].ready;
  $('renderStart').max=state.comparison?.sample?.frames||1;$('renderEnd').max=state.comparison?.sample?.frames||1;
}
async function startRenderTool(){
  const run=$('renderRun').value;if(!run)throw new Error('请先打开一份已有几何结果');
  for(const id of ['renderStart','renderEnd'])if(!$(id).checkValidity()){$(id).reportValidity();throw new Error('请检查工具帧范围');}
  const value=await api(`api/comparison/runs/${run}/tools`,{kind:$('renderKind').value,space:$('renderSpace').value,start:Number($('renderStart').value)-1,end:$('renderEnd').value===''?null:Number($('renderEnd').value),width:Number($('renderWidth').value),include_absent:$('showAbsent').checked,resource_policy:$('renderPolicy').value});
  platform.renderJob=value.id;await refreshRenderTool();
}
async function resumeRenderTool(id){const value=await api('api/comparison/tools/'+id);const options={};for(const key of ['kind','space','start','end','width','include_absent','resource_policy'])options[key]=value[key];const resumed=await api(`api/comparison/runs/${value.run_id}/tools`,options);platform.renderJob=resumed.id;}
async function refreshRenderTool(){
  const id=platform.renderJob;if(!id)return;const job=await api('api/comparison/tools/'+id);if(id!==platform.renderJob)return;
  const host=$('renderToolStatus');host.replaceChildren(el('p',`${statusLabel(job.status)} · ${job.kind} · ${job.space}${job.progress?.total?' · '+job.progress.done+'/'+job.progress.total:''}`));
  if(job.error)host.append(el('p',job.error,'task-error'));
  if(['queued','waiting_gpu','running'].includes(job.status)){const button=el('button','取消渲染');button.onclick=()=>api(`api/comparison/tools/${id}/cancel`,{}).then(refreshRenderTool).catch(hubError);host.append(button);}
  if(job.status==='completed'){const link=el('a','下载视频');link.href=job.url;link.download='';const mapping=el('a','帧对应与坐标说明');mapping.href=`api/comparison/tools/${id}/mapping`;host.append(link,mapping);if($('renderToolVideo').dataset.job!==id){$('renderToolVideo').src=job.url;$('renderToolVideo').dataset.job=id;}$('renderToolVideo').hidden=false;}else{$('renderToolVideo').pause();$('renderToolVideo').hidden=true;}
}
function inspectResult(id){if(!state.cards.has(id))return;resultView.expanded=id;$('inspectRun').value=id;applyResultVisibility();$('frameInspector').open=true;pause();ensureFrame(state.frame).then(()=>render(true));}
function initializePlatform(){
  for(const button of document.querySelectorAll('[data-platform-tab]'))button.onclick=()=>openPlatform(button.dataset.platformTab);
  for(const button of document.querySelectorAll('[data-hub-pane]'))button.onclick=()=>switchHub(button.dataset.hubPane);
  $('backToResults').onclick=()=>{$('platformDialog').close();if(!state.comparison&&workspaceUI.history.length)openComparison(workspaceUI.history[0].id).catch(error=>notify(error.message));else setWorkspaceTab('view');};$('closePlatform').onclick=()=>$('platformDialog').close();$('platformDialog').addEventListener('close',()=>{platform.serial++;clearTimeout(platform.timer);$('renderToolVideo').pause();});
  $('chooseVideoInput').onclick=()=>setDatasetMode(false);$('chooseDatasetInput').onclick=()=>setDatasetMode(true);
  $('loadDataset').onclick=()=>loadDatasetForm(0).catch(error=>{$('datasetSummary').textContent=error.message;});$('datasetUseDirectory').onclick=()=>{$('datasetRoot').value=sourceBrowser.path;loadDatasetForm(0).catch(error=>{$('datasetSummary').textContent=error.message;});};
  $('datasetCamera').onchange=()=>loadDatasetForm(0).catch(error=>notify(error.message));$('datasetEpisode').onchange=chooseDatasetEpisode;
  $('datasetPreviousPage').onclick=()=>loadDatasetForm(Math.max(0,datasetForm.offset-100)).catch(error=>notify(error.message));$('datasetNextPage').onclick=()=>loadDatasetForm(datasetForm.offset+100).catch(error=>notify(error.message));
  $('useCurrentDirectory').onclick=()=>{$('collectionDirectory').value=sourceBrowser.path||'/data1/lizhen_dataset';};$('scanVideos').onclick=()=>scanCollectionVideos().catch(hubError);
  $('hubLoadDataset').onclick=()=>loadHubDataset(0).catch(hubError);$('hubDatasetCamera').onchange=()=>loadHubDataset(0).catch(hubError);$('hubDatasetPrevious').onclick=()=>loadHubDataset(Math.max(0,platform.datasetOffset-100)).catch(hubError);$('hubDatasetNext').onclick=()=>loadHubDataset(platform.datasetOffset+100).catch(hubError);
  $('selectAllVideos').onclick=()=>{for(const row of platform.scanned||[])platform.selection.set('video:'+row.source,{source:row.source,name:row.name});for(const check of $('collectionVideos').querySelectorAll('input'))check.checked=true;selectionSummary();};
  $('selectAllEpisodes').onclick=()=>{for(const check of $('hubEpisodes').querySelectorAll('input'))if(!check.checked){check.checked=true;check.dispatchEvent(new Event('change'));}};
  $('clearCollectionSelection').onclick=()=>{platform.selection.clear();for(const check of document.querySelectorAll('.collection-items input'))check.checked=false;selectionSummary();};
  $('useSingleEpisode').onclick=async()=>{try{const selected=[...platform.selection.values()];if(selected.length!==1)throw new Error('单次比较请选择一个视频或 Episode');const item=selected[0];$('platformDialog').close();if(item.dataset){$('datasetRoot').value=item.dataset.root;await loadDatasetForm(0,item.dataset);}else{setDatasetMode(false);$('source').value=item.source;}$('maxFrames').value='';openTaskDialog();}catch(error){hubError(error);}};
  $('prepareBatch').onclick=()=>newCollectionForm('batch');$('prepareEvaluation').onclick=()=>newCollectionForm('evaluation');
  $('refreshWorkspaceTasks').onclick=()=>refreshWorkspaceTasks().catch(hubError);let searchTimer;$('taskSearch').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{platform.taskOffset=0;refreshWorkspaceTasks().catch(hubError);},250);};$('taskStatusFilter').onchange=()=>{platform.taskOffset=0;refreshWorkspaceTasks().catch(hubError);};$('taskPreviousPage').onclick=()=>{platform.taskOffset=Math.max(0,platform.taskOffset-50);refreshWorkspaceTasks().catch(hubError);};$('taskNextPage').onclick=()=>{platform.taskOffset+=50;refreshWorkspaceTasks().catch(hubError);};
  $('cancelCollection').onclick=()=>api(`api/comparison/collections/${platform.collection}/cancel`,{}).then(refreshCollection).catch(hubError);$('resumeCollection').onclick=()=>{platform.report=null;api(`api/comparison/collections/${platform.collection}/resume`,{}).then(refreshCollection).catch(hubError);};$('downloadCollectionJSON').onclick=async()=>{try{const value=await api(`api/comparison/collections/${platform.collection}/report`);download('collection-'+platform.collection.slice(0,8)+'.json',JSON.stringify(value,null,2));}catch(error){hubError(error);}};
  $('renderKind').onchange=()=>refreshToolOptions().catch(hubError);$('startRenderTool').onclick=()=>startRenderTool().catch(hubError);
  $('closeExportDialog').onclick=()=>$('exportDialog').close();$('exportCurrentPNG').onclick=()=>{$('exportDialog').close();$('snapshot').click();};$('exportFullReport').onclick=()=>$('downloadReport').click();
}

// Checkpoint browsing is read-only. Selecting a path does not load a model.
function checkpointFields(host){
  const checkpoint=el('textarea');checkpoint.rows=2;checkpoint.dataset.selection='checkpoint';checkpoint.placeholder='留空使用默认模型；每行一个检查点绝对路径';checkpoint.spellcheck=false;
  const config=el('input');config.dataset.selection='config';config.placeholder='留空优先使用检查点配置快照';config.spellcheck=false;
  host.append(inputLabel('MINT 检查点（可多选）',checkpoint),inputLabel('可选模型配置路径',config));
  const picker=el('details',undefined,'checkpoint-browser');picker.append(el('summary','浏览服务器检查点'));host.append(picker);wireCheckpointBrowser(picker);
}
function wireCheckpointBrowser(picker){
  picker.ontoggle=()=>{if(picker.open&&!picker.querySelector('.checkpoint-browser-body'))browseCheckpoint(picker,'').catch(error=>notify(error.message));};
}
async function browseCheckpoint(picker,path){
  const value=await api('/api/ckpt/browse?'+new URLSearchParams({path}));
  let body=picker.querySelector('.checkpoint-browser-body');if(!body){body=el('div',undefined,'checkpoint-browser-body');picker.append(body);}body.replaceChildren();
  const row=el('div',undefined,'source-path-row'),input=el('input');input.value=value.path;input.setAttribute('aria-label','检查点目录');input.spellcheck=false;
  const go=el('button','前往'),up=el('button','上一级');go.onclick=()=>browseCheckpoint(picker,input.value).catch(error=>notify(error.message));up.onclick=()=>browseCheckpoint(picker,value.parent).catch(error=>notify(error.message));input.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();go.click();}};row.append(input,go,up);body.append(row);
  const append=path=>{const field=picker.closest('.algorithm').querySelector('[data-selection=checkpoint]');const paths=field.value.split('\n').map(v=>v.trim()).filter(Boolean);if(!paths.includes(path))paths.push(path);field.value=paths.join('\n');picker.closest('.algorithm').querySelector('input[type=checkbox]').checked=true;};
  if(value.selectable){const choose=el('button','添加当前检查点目录');choose.onclick=()=>append(value.path);body.append(choose);}
  const list=el('div',undefined,'checkpoint-files');for(const directory of value.dirs){const button=el('button','📁 '+directory);button.onclick=()=>browseCheckpoint(picker,value.path+'/'+directory).catch(error=>notify(error.message));list.append(button);}for(const file of value.files){const button=el('button','＋ '+file);button.onclick=()=>append(value.path+'/'+file);list.append(button);}body.append(list);
  if(value.truncated)body.append(el('p','目录超过 2000 项，请定位更小目录。','hint'));
}
function cloneComposerAlgorithms(host){
  if(!$('algorithmOptions').querySelector('.algorithm')){host.dataset.waiting='true';host.textContent='正在读取算法…';return;}
  host.replaceChildren();delete host.dataset.waiting;
  for(const box of $('algorithmOptions').children)if(box.dataset.algorithm!=='reference'){
    const clone=box.cloneNode(true),from=box.querySelectorAll('input,textarea,select'),to=clone.querySelectorAll('input,textarea,select');
    from.forEach((field,index)=>{to[index].value=field.value;if(field.type==='checkbox')to[index].checked=field.checked;});
    for(const picker of clone.querySelectorAll('.checkpoint-browser')){picker.querySelector('.checkpoint-browser-body')?.remove();picker.open=false;wireCheckpointBrowser(picker);}host.append(clone);
  }
}
function refreshComposerAlgorithms(){for(const kind of ['batch','evaluation']){const host=$(kind+'-algorithms');if(host?.dataset.waiting)cloneComposerAlgorithms(host);}}
async function runDiagnostic(profile){
  const buttons=[...document.querySelectorAll('[data-diagnostic]')];for(const button of buttons)button.disabled=true;$('diagnosticStatus').textContent='正在独立进程检查…';$('diagnosticResults').replaceChildren();
  try{const value=await api('api/comparison/diagnostics',{profile}),table=el('table');
    for(const check of value.checks){const row=el('tr');row.append(el('td',check.ok?'✓ 通过':check.required?'✗ 缺失':'○ 可选'),el('td',check.name),el('td',check.detail));table.append(row);}
    $('diagnosticResults').append(table);$('diagnosticStatus').textContent=value.ready?'必需项已就绪':'仍有必需项缺失';
  }catch(error){hubError(error);$('diagnosticStatus').textContent='检查失败';}finally{for(const button of buttons)button.disabled=false;}
}
for(const button of document.querySelectorAll('[data-diagnostic]'))button.onclick=()=>runDiagnostic(button.dataset.diagnostic);

function newCollectionForm(kind){platform.collection=null;platform.collectionKind=null;platform.report=null;$(kind+'Setup').open=true;switchHub(kind);}
