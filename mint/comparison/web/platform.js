'use strict';

const datasetForm={active:false,catalog:null,offset:0};
const platform={pane:'data',selection:new Map(),catalog:null,datasetOffset:0,taskOffset:0,taskSerial:0,serial:0,timer:null,collection:null,collectionKind:null,report:null,renderJob:null,capability:null};
const HUB_LABELS={data:'数据接入',tasks:'任务与恢复',batch:'批量比较',evaluation:'数据集评测',tools:'专项工具',catalog:'数据与结果目录',quality:'逐帧质检与人工复核',research:'训练与模型'};
const HUB_GROUPS={data:'data',catalog:'data',batch:'data',evaluation:'evaluation',quality:'evaluation',research:'research',tasks:'tasks',tools:'tools'};
const HUB_CONTEXT={data:'选择输入来源，再选择视频、Episode 或已准备的数据版本。',catalog:'检索不可变结果、数据版本、模型和协议。比较记录保存一组对照，任务中心管理实际执行过程。',batch:'同一批输入、统一协议，逐个保存比较结果；可取消或恢复未完成的子任务。',evaluation:'当前比较看算法差异；数据集评测统计误差和覆盖；检查点回归验证模型变化；公开基准使用独立协议。',quality:'逐帧检查、区间复核和二维点修正。二维修正不会自动变成三维或 MANO 真值。',research:'准备数据 → 训练 → 检查点回归 → 模型发布。数据、模型和协议均保留独立版本。',tasks:'查看执行阶段、等待原因、资源与日志。一个比较可能包含多个算法任务。',tools:'专项分析与高级调试；各工具的可用状态和配置要求单独显示。'};
const platformNavigation={scroll:new Map(),restoring:false};
function platformRoute(tab,replace=false){
  const url=new URL(location.href);if(tab){if(url.searchParams.get('hub')!==tab)url.searchParams.delete('section');if(tab!==platform.collectionKind)url.searchParams.delete('collection');if(tab!=='tools')url.searchParams.delete('tool_job');if(tab!=='research')url.searchParams.delete('study');if(tab!=='research')url.searchParams.delete('research_tab');if(tab!=='data')url.searchParams.delete('source_type');url.searchParams.set('hub',tab);}else{for(const key of ['hub','section','collection','tool_job','study','research_tab','source_type'])url.searchParams.delete(key);}
  if(url.href!==location.href)window.history[replace?'replaceState':'pushState']({mintHub:tab||'compare'},'',url.pathname+url.search+url.hash);
}
function syncPlatformNavigation(tab){
  const group=HUB_GROUPS[tab];document.querySelector('#appMasthead h1').textContent=({data:'数据',evaluation:'评测与质检',research:'训练与模型',tasks:'任务',tools:'工具'})[group]||'多算法比较';document.body.classList.toggle('platform-page-open',!!tab);$('appShell').hidden=!!tab;$('appShell').inert=!!tab;
  for(const button of document.querySelectorAll('[data-platform-tab]')){const active=button.dataset.platformTab===group;button.classList.toggle('active',active);if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');}
  $('backToResults').classList.toggle('active',!tab);if(!tab)$('backToResults').setAttribute('aria-current','page');else $('backToResults').removeAttribute('aria-current');
  for(const button of document.querySelectorAll('[data-hub-pane]')){button.hidden=HUB_GROUPS[button.dataset.hubPane]!==group;const active=button.dataset.hubPane===tab;button.classList.toggle('active',active);button.setAttribute('aria-current',active?'page':'false');}
  document.querySelector('.platform-tabs').hidden=['research','tasks','tools','evaluation'].includes(group);
  $('evaluationRoutes').hidden=group!=='evaluation';for(const button of document.querySelectorAll('[data-evaluation-route]')){const active=button.dataset.evaluationRoute===(tab==='quality'?'quality':'dataset');button.classList.toggle('active',active);button.setAttribute('aria-current',active?'page':'false');}$('moduleContext').textContent=HUB_CONTEXT[tab]||'';syncPlatformPrimaryAction(tab);refreshPlatformContext();
}
function closePlatformWorkspace({history=true}={}){
  platformNavigation.scroll.set(platform.pane,document.querySelector('.platform-body').scrollTop);
  if($('platformDialog').open)$('platformDialog').close();syncPlatformNavigation(null);if(history&&!platformNavigation.restoring)platformRoute(null);
}
function focusHubSection(tab,title){
  openPlatform(tab);const url=new URL(location.href);url.searchParams.set('section',title);window.history.replaceState(null,'',url.pathname+url.search);
  requestAnimationFrame(()=>{const section=findHubSection(tab,title);if(section){revealPlatformTarget(section);section.setAttribute('tabindex','-1');section.focus({preventScroll:true});}});
}

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
  $('collectionSelectionSummary').textContent=`批量列表：已选择 ${values.length} 项 · ${episodes} 个 Episode · ${values.length-episodes} 个视频`;refreshPlatformContext();
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
  try{button.textContent=`正在预检 ${items.length} 个输入…`;const check=await api('api/comparison/collections/preflight',{kind,items,selections,options});if(!check.ready)throw new Error(check.errors.slice(0,20).join('；'));const value=await api('api/comparison/collections',{kind,items,selections,options});await openCollection(value.id);}finally{button.disabled=false;button.textContent=kind==='evaluation'?'开始多算法数据集评测':'开始多视频多算法批量';}
}
async function openPlatform(tab='data'){
  pause();$('platformError').textContent='';if(!$('platformDialog').open)$('platformDialog').show();
  if(!$('collectionDirectory').value)$('collectionDirectory').value=sourceBrowser.path||'/data1/lizhen_dataset';
  if(!$('hubDatasetRoot').value)$('hubDatasetRoot').value=$('datasetRoot').value||'/data1/lizhen_algo/mint/data/samples/lerobot_v3';
  switchHub(tab);
}
function switchHub(tab){
  if(!HUB_LABELS[tab])return;
  const body=document.querySelector('.platform-body');platformNavigation.scroll.set(platform.pane,body.scrollTop);
  platform.pane=tab;platform.serial++;clearTimeout(platform.timer);$('platformTitle').textContent=HUB_LABELS[tab];$('platformError').textContent='';
  for(const key of Object.keys(HUB_LABELS))$('hub-'+key).hidden=key!==tab;
  syncPlatformNavigation(tab);if(!platformNavigation.restoring)platformRoute(tab);requestAnimationFrame(()=>{if(platform.pane===tab)body.scrollTop=platformNavigation.scroll.get(tab)||0;});
  $('collectionDetails').hidden=!platform.collection||platform.collectionKind!==tab;
  if(['batch','evaluation'].includes(tab))createComposer(tab);
  if(tab==='tools')refreshToolOptions().catch(hubError);
  if(tab==='catalog')refreshArtifactCatalog().catch(hubError);
  if(tab==='quality')refreshQualityContext().catch(hubError);
  if(tab==='research')refreshResearchChoices().catch(hubError);
  window.dispatchEvent(new CustomEvent('mint-platform-open',{detail:tab}));refreshPlatformContext();
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
  renderTaskResourceSummary(value);
  const table=el('table'),head=el('tr');for(const label of ['任务 / 来源','状态 / 当前阶段','完成量','操作'])head.append(el('th',label));table.append(head);
  for(const task of value.tasks){const row=el('tr'),name=el('td');name.append(el('strong',task.label),el('small',task.source||task.kind));const progress=task.progress||{},phase=progress.stage||progress.phase;row.append(name,el('td',statusLabel(task.status)+(phase?' · '+(STAGES[phase]||phase):'')),el('td',progress.total?`${progress.done||0}/${progress.total}${progress.unit==='frames'?' 帧':progress.unit==='optimizer_steps'?' 步':''}`:'—'));const actions=el('td');
    if(task.waiting_reason)name.append(el('small',task.waiting_reason,'waiting-reason'));if(task.eta)name.append(el('small',`当前阶段预计剩余 ${Math.ceil(task.eta.seconds)} 秒 · 估算`));
    const history=researchButton('阶段记录',async()=>researchDetails(await api(`api/comparison/tasks/${task.kind}/${task.id}`)));actions.append(history);
    const open=el('button','打开');open.onclick=()=>{if(task.kind==='batch'||task.kind==='evaluation')openCollection(task.id).catch(hubError);else if(task.kind==='render'){platform.renderJob=task.id;switchHub('tools');}else if(task.kind==='workflow'){switchHub('research');inspectWorkflow(task.id).catch(hubError);}else if(task.kind==='study'){nextUI.study=task.id;openPlatform('research').then(()=>showResearchView('regression')).catch(hubError);}else if(task.comparison_id){$('platformDialog').close();openComparison(task.comparison_id).then(()=>inspectResult(task.id)).catch(error=>notify(error.message));}else if(task.url)location.href=task.url;};open.disabled=!(['batch','evaluation','render','workflow','study'].includes(task.kind)||task.comparison_id||task.url);actions.append(open);
    const terminal=TERMINAL.has(task.status)||task.status==='evicted';if(['inference','batch','evaluation','render','export','workflow','study'].includes(task.kind)&&task.status!=='completed'){
      const action=el('button',terminal?'恢复':'取消');action.onclick=async()=>{try{if(task.kind==='inference')await api(`api/comparison/runs/${task.id}/${terminal?'resume':'cancel'}`,{});else if(task.kind==='render'){if(terminal){platform.renderJob=task.id;await resumeRenderTool(task.id);}else await api(`api/comparison/tools/${task.id}/cancel`,{});}else if(task.kind==='export')await api(`api/comparison/exports/${task.id}/${terminal?'resume':'cancel'}`,{});else if(task.kind==='study')await api(`api/comparison/studies/${task.id}/${terminal?'resume':'cancel'}`,{});else if(task.kind==='workflow')await api(`api/comparison/workflows/${task.id}/${terminal?'resume':'cancel'}`,{});else await api(`api/comparison/collections/${task.id}/${terminal?'resume':'cancel'}`,{});await refreshWorkspaceTasks();}catch(error){hubError(error);}};actions.append(action);
    }
    if(task.error){const error=el('small',task.error,'task-error');name.append(error);}row.append(actions);table.append(row);
  }
  $('workspaceTasks').replaceChildren(table);$('taskPageInfo').textContent=`${value.total} 条 · 当前 ${value.total?value.offset+1:0}–${Math.min(value.offset+value.tasks.length,value.total)}`;$('taskPreviousPage').disabled=value.offset===0;$('taskNextPage').disabled=value.offset+value.tasks.length>=value.total;
  $('legacyResults').replaceChildren();for(const entry of value.cached_predictions){const row=el('div',undefined,'legacy-result');row.append(el('span',`${entry.source.split('/').pop()} · ${entry.frames} 帧 · ${entry.camera_mode} · ${entry.hand_mode}`));const button=el('button','导入到比较');button.onclick=async()=>{button.disabled=true;try{const comparison=await api('api/comparison/legacy-results/import',{id:entry.id});$('platformDialog').close();await openComparison(comparison.id);}catch(error){hubError(error);}finally{button.disabled=false;}};row.append(button);$('legacyResults').append(row);}
  if(!value.cached_predictions.length)$('legacyResults').textContent='当前没有诊断页的内存预测；可在 MINT 单算法诊断中加载已有结果，或导入保存的 NPZ。';
}
async function openCollection(id){
  const value=await api('api/comparison/collections/'+id);platform.collection=id;platform.collectionKind=value.kind;platform.report=null;$('evaluationResults').replaceChildren();
  if(!$('platformDialog').open)$('platformDialog').show();switchHub(value.kind);$(value.kind+'Setup').open=false;const url=new URL(location.href);url.searchParams.set('collection',id);window.history.replaceState(null,'',url.pathname+url.search);await renderCollection(value);$('collectionDetails').scrollIntoView({block:'start'});
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
  platform.capability=await api('api/comparison/capabilities');
  const old=$('renderRun').value;$('renderRun').replaceChildren();for(const run of state.comparison?.runs||[])if(run.result){const option=el('option',run.label+' · '+run.id.slice(0,6));option.value=run.id;$('renderRun').append(option);}if([...$('renderRun').options].some(option=>option.value===old))$('renderRun').value=old;
  const cap=platform.capability,host=$('toolReadiness');host.replaceChildren();host.classList.add('tool-readiness-grid');
  const add=(label,ready,reason)=>{const card=el('div',undefined,'tool-readiness-card'),badge=el('span',ready?'可用':'需配置 / 待实现','readiness-badge');badge.dataset.ready=String(!!ready);card.append(el('strong',label),badge,el('p',reason||'按需显式提交，不会自动运行。','hint'));host.append(card);};
  for(const [kind,label]of [['mujoco','MuJoCo 几何展示'],['retarget','Wuji Hand 运动学重定向']])add(label,cap.render?.[kind]?.ready,cap.render?.[kind]?.ready?'使用当前已有几何；重定向不代表接触或动力学验证。':cap.render?.[kind]?.reason);
  add('数据专项分析',cap.data_analysis,cap.data_analysis?'视频质量、FOV 和多样性分析已连接。':'数据分析后端未连接。');add('MINT 单算法诊断',cap.legacy,cap.legacy?'MINT 诊断后端已连接；诊断结果可显式加入统一比较。':'MINT 诊断后端未连接。');
  add('Aliyun 云端评测',cap.cloud?.ready,cap.cloud?.ready?'配置已填写；仍需在公开基准工具中预检并显式提交。':cap.cloud?.reason||'请配置工作区、资源、镜像与存储。');
  const bench=cap.benchmark;if(bench){const supported=(bench.datasets||[]).filter(row=>row.implemented).map(row=>row.name),missing=(bench.datasets||[]).filter(row=>!row.implemented).map(row=>row.name),missingHeads=(bench.heads||[]).filter(row=>!row.implemented).map(row=>row.name);const card=el('div',undefined,'tool-readiness-card benchmark-readiness');card.append(el('strong','公开基准 · 适配器与当前模型能力分别检查'),el('p','已有数据集适配器：'+(supported.join('、')||'无')+'。','hint'),el('p','待实现适配器：'+(missing.join('、')||'无')+'；待实现指标：'+(missingHeads.join('、')||'无')+'。','hint'),el('p','当前 MINT 配置输出：'+(bench.model_capability||[]).join('、')+'。适配器已实现仍需匹配数据标注和模型输出；当前模型未启用的深度等指标不可直接评测。','hint'));host.append(card);}
  for(const link of document.querySelectorAll('#hub-tools .tool-links a')){const tool=new URL(link.href,location.href).searchParams.get('tool'),ready=tool?.startsWith('analysis-')?cap.data_analysis:cap.legacy;link.dataset.ready=String(!!ready);link.setAttribute('aria-disabled',String(!ready));link.title=ready?'打开工具；不会自动提交计算':'后端未连接，请先检查环境';link.onclick=ready?null:event=>{event.preventDefault();hubError(new Error('该工具后端未连接，请先检查环境。'));};}

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
function inspectResult(id){if(!state.cards.has(id))return;resultView.expanded=id;$('inspectRun').value=id;applyResultVisibility();if($('comparisonOptions'))$('comparisonOptions').open=true;$('frameInspector').open=true;pause();ensureFrame(state.frame).then(()=>render(true));}
function initializePlatform(){
  for(const button of document.querySelectorAll('[data-platform-tab]'))button.onclick=()=>openPlatform(button.dataset.platformTab);
  for(const button of document.querySelectorAll('[data-hub-pane]'))button.onclick=()=>switchHub(button.dataset.hubPane);
  $('backToResults').onclick=()=>{closePlatformWorkspace();if(!state.comparison&&workspaceUI.history.length)openComparison(workspaceUI.history[0].id).catch(error=>notify(error.message));else setWorkspaceTab('view');};$('closePlatform').onclick=()=>closePlatformWorkspace();$('platformDialog').addEventListener('close',()=>{if($('platformDialog').open)return;platform.serial++;clearTimeout(platform.timer);$('renderToolVideo').pause();syncPlatformNavigation(null);if(!platformNavigation.restoring)platformRoute(null,true);});
  $('newComparison').addEventListener('click',interceptPlatformPrimaryAction,{capture:true});
  $('chooseNativeInput').onclick=()=>{$('taskDialog').close();focusHubSection('data','开源数据集原生接入');};
  $('chooseExistingInput').onclick=()=>{$('importSection').open=true;$('importSection').scrollIntoView({block:'nearest',behavior:'smooth'});$('importPath').focus({preventScroll:true});};
  for(const button of document.querySelectorAll('[data-evaluation-route]'))button.onclick=()=>{const route=button.dataset.evaluationRoute;if(route==='current'){if(!state.comparison){hubError(new Error('请先打开一组比较，再查看当前比较指标。'));return;}closePlatformWorkspace();setWorkspaceTab('metrics');}else if(route==='dataset')switchHub('evaluation');else if(route==='quality')switchHub('quality');else focusHubSection('research','固定集回归与性能对照');};
  window.addEventListener('popstate',()=>{platformNavigation.restoring=true;try{const query=new URLSearchParams(location.search),tab=query.get('hub');if(tab&&HUB_LABELS[tab]){openPlatform(tab);}else closePlatformWorkspace({history:false});}finally{platformNavigation.restoring=false;}});
  window.addEventListener('mint-platform-open',()=>{const section=new URLSearchParams(location.search).get('section');if(section)requestAnimationFrame(()=>{const target=findHubSection(platform.pane,section);if(target)revealPlatformTarget(target);});});
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
  const aliases=el('details',undefined,'checkpoint-browser');aliases.dataset.modelAliases='true';aliases.append(el('summary','选择已评测模型别名'));host.append(aliases);wireModelAliasBrowser(aliases);
  const picker=el('details',undefined,'checkpoint-browser');picker.append(el('summary','浏览服务器检查点'));host.append(picker);wireCheckpointBrowser(picker);
}
function wireCheckpointBrowser(picker){
  picker.ontoggle=()=>{if(picker.open&&!picker.querySelector('.checkpoint-browser-body'))browseCheckpoint(picker,'').catch(error=>notify(error.message));};
}
function wireModelAliasBrowser(picker){
  picker.ontoggle=async()=>{if(!picker.open)return;try{
    const value=await api('api/comparison/models/aliases');picker.querySelector('.alias-options')?.remove();const options=el('div',undefined,'alias-options');
    for(const alias of value.aliases){const button=el('button',`${alias.name} · ${alias.model.name}${alias.ready?'':' · 文件变化'}`);button.disabled=!alias.ready;
      button.onclick=()=>{const box=picker.closest('.algorithm');box.querySelector('[data-selection=checkpoint]').value=alias.model.path;box.querySelector('[data-selection=config]').value=alias.effective_config?.path||alias.model.config?.path||'';box.querySelector('input[type=checkbox]').checked=true;};options.append(button);}
    if(!value.aliases.length)options.append(el('p','尚无已通过门槛的模型别名。可在数据与训练中登记、评测后发布。','hint'));picker.append(options);
  }catch(error){notify(error.message);}};
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
    if(host.dataset.onlyMint==='true')clone.querySelector('input[type=checkbox]').checked=clone.dataset.algorithm==='mint';
    for(const picker of clone.querySelectorAll('.checkpoint-browser')){picker.querySelector('.checkpoint-browser-body')?.remove();picker.querySelector('.alias-options')?.remove();picker.open=false;if(picker.dataset.modelAliases)wireModelAliasBrowser(picker);else wireCheckpointBrowser(picker);}host.append(clone);
  }
}
function refreshComposerAlgorithms(){for(const id of ['batch-algorithms','evaluation-algorithms','nativeAlgorithms','studyAlgorithms']){const host=$(id);if(host?.dataset.waiting)cloneComposerAlgorithms(host);}}
async function runDiagnostic(profile){
  const buttons=[...document.querySelectorAll('[data-diagnostic]')];for(const button of buttons)button.disabled=true;$('diagnosticStatus').textContent='正在独立进程检查…';$('diagnosticResults').replaceChildren();
  try{const value=await api('api/comparison/diagnostics',{profile}),table=el('table');
    for(const check of value.checks){const row=el('tr');row.append(el('td',check.ok?'✓ 通过':check.required?'✗ 缺失':'○ 可选'),el('td',check.name),el('td',check.detail));table.append(row);}
    $('diagnosticResults').append(table);$('diagnosticStatus').textContent=value.ready?'必需项已就绪':value.environment_ready&&!value.inputs_ready?'环境就绪；默认生产配方的数据 / 初始化仍需配置，可在数据与训练中选择已转换版本。':'仍有必需环境或资产缺失';
  }catch(error){hubError(error);$('diagnosticStatus').textContent='检查失败';}finally{for(const button of buttons)button.disabled=false;}
}
for(const button of document.querySelectorAll('[data-diagnostic]'))button.onclick=()=>runDiagnostic(button.dataset.diagnostic);

function newCollectionForm(kind){platform.collection=null;platform.collectionKind=null;platform.report=null;$(kind+'Setup').open=true;switchHub(kind);}


// Module views preserve their original controls; switching never creates jobs or resets drafts.
function platformPrimaryAction(tab){return ({data:{label:'选择输入',kind:'data'},catalog:{label:'准备数据',kind:'data'},batch:{label:'配置批量',kind:'batch'},evaluation:{label:'新建评测',kind:'evaluation'},quality:{label:'选择比较',kind:'quality'},research:{label:'新建训练',kind:'train'}})[tab]||null;}
function syncPlatformPrimaryAction(tab){const button=$('newComparison'),action=platformPrimaryAction(tab);button.hidden=!!tab&&!action;const label=tab?action?.label:'新建比较';if(label){button.replaceChildren(icon('plus'),document.createTextNode(label));button.setAttribute('aria-label',label);button.title=tab?'配置当前模块，不会立即提交计算任务':'配置算法与输入';}}
function interceptPlatformPrimaryAction(event){if(!$('platformDialog').open)return false;event.preventDefault();event.stopImmediatePropagation();activatePlatformPrimaryAction();return true;}
function activatePlatformPrimaryAction(){
  const kind=platformPrimaryAction(platform.pane)?.kind;
  if(kind==='train'){showResearchView('train');$('workflowKind').value='train';updateWorkflowFields();revealPlatformTarget($('workflowKind'));$('workflowName').focus({preventScroll:true});}
  else if(kind==='data'){openPlatform('data');requestAnimationFrame(()=>{$('dataSourceTabs')?.focus();});}
  else if(kind==='evaluation'){switchHub('evaluation');$('evaluationSetup').open=true;revealPlatformTarget($('evaluationComposer'));}
  else if(kind==='batch'){$('batchSetup').open=true;revealPlatformTarget($('batchComposer'));}
  else if(kind==='quality'){revealPlatformTarget($('qualityComparison'));$('qualityComparison').focus({preventScroll:true});}
}
function findHubSection(tab,title){
  const ids={'开源数据集原生接入':'nativeDataPanel','固定集回归与性能对照':'suiteProtocol','训练曲线与完整检查点':'trainingCurve','原生后端伪标签生产':'nativeProductionSection','模型版本与发布门槛':'modelName','数据生产与训练任务':'workflowKind','冻结协议与离线评测':'offlineProtocol'};
  const node=ids[title]?$(ids[title]):null;return node?.closest('section')||[...$('hub-'+tab).querySelectorAll('section')].find(row=>row.querySelector('h3')?.textContent.includes(title));
}
function revealPlatformTarget(target,{scroll=true}={}){
  if(!target)return;
  if(target.closest('#hub-research')&&typeof revealResearchTarget==='function')revealResearchTarget(target);
  if(target.closest('#hub-data')&&typeof showDataSource==='function'){const region=target.closest('[data-data-source]');if(region)showDataSource(region.dataset.dataSource);}
  if(scroll)target.scrollIntoView({block:'start',behavior:'auto'});
}
function platformContextText(comparison,pane,selectionCount=0){
  if(['batch','evaluation'].includes(pane))return `批量输入集合：${selectionCount} 项；与当前打开的比较分别管理。`;
  if(!['quality','tools','research'].includes(pane))return '';
  if(!comparison)return pane==='research'?'尚未打开比较；训练使用所选数据版本，回归和生产按各自来源选择。':'尚未打开比较；需要已有结果的操作请先选择一组比较。';
  const request=comparison.sample_request||{},path=request.source?.path||'',sample=comparison.sample;
  const frames=sample?`${sample.frames} 帧 · 源索引 ${sample.source_frame_indices?.[0]??request.start_frame??0}…${sample.source_frame_indices?.at(-1)??'—'}`:`源索引 ${request.start_frame??0} 起`;
  return `当前比较：${comparison.title||path.split('/').pop()||comparison.id} · ${frames} · ${(comparison.runs||[]).filter(r=>r.result).length} 个已有结果。${pane==='research'?'训练数据、回归协议和生产来源仍以各自选择为准。':''}`;
}
function refreshPlatformContext(){
  let host=$('platformCurrentContext');if(!host){const parent=$('moduleContext');if(!parent)return;host=el('div',undefined,'platform-current-context');host.id='platformCurrentContext';host.setAttribute('role','status');parent.after(host);}
  const text=platformContextText(state.comparison,platform.pane,platform.selection.size);host.hidden=!$('platformDialog').open||!text;host.title=state.comparison?.sample_request?.source?.path||'';host.replaceChildren();if(!text)return;host.append(el('span',text));
  if(['quality','tools','research'].includes(platform.pane)){const change=el('button',state.comparison?'更换比较':'选择比较','text-button');change.onclick=()=>{closePlatformWorkspace();if(window.innerWidth<=1100){workspaceUI.mobileSidebar=true;applyLayout();}$('historySearch').focus();};host.append(change);}
}
function renderTaskResourceSummary(value){
  const host=$('resourceSummary'),scheduler=value.resource||{},resources=value.resources||{},gpus=resources.gpus||[];
  let summary=$('taskResourcePrimary'),details=$('taskResourceDetails'),body=$('taskResourceBody');
  if(!summary){summary=el('p',undefined,'task-resource-primary');summary.id='taskResourcePrimary';details=el('details');details.id='taskResourceDetails';details.append(el('summary','主机与 GPU 资源详情'));body=el('p',undefined,'hint');body.id='taskResourceBody';details.append(body);host.replaceChildren(summary,details);}
  summary.textContent=`MINT 当前使用 ${scheduler.running_devices?.length||0} 张 GPU · ${scheduler.queued||0} 个推理窗口排队 · 当前筛选 ${value.total} 条任务`;
  body.textContent=`物理 GPU ${gpus.length} 张 · 显存空余 ${(gpus.reduce((sum,gpu)=>sum+(gpu.free_mib||0),0)/1024).toFixed(1)} GiB · CPU ${resources.cpu_count||'—'} 核 / 1 分钟负载 ${Number(resources.load_average?.[0]||0).toFixed(1)} · 主机可用内存 ${((resources.memory?.MemAvailable||0)/2**30).toFixed(1)} GiB · 磁盘余量 ${((resources.disk?.free_bytes||0)/2**30).toFixed(1)} GiB。资源数值包含其他用户任务。`;
}


// HTTP redirects cannot see fragments from old diagnostic bookmarks.
function legacyDiagnosticHashURL(pathname,search,hash){
  const match=String(pathname||'').match(/^((?:\/[^/]+)*)\/compare\/?$/);if(!match||!['#diagnostic-input','#diagnostic-results','#diagnostic-tools'].includes(hash))return null;
  const query=new URLSearchParams(search||'');if(['id','run','hub','collection','tool_job','workflow','study'].some(key=>query.has(key)))return null;
  return match[1]+'/diagnostics'+(search||'')+hash;
}
