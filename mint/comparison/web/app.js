'use strict';
const $=id=>document.getElementById(id);
const api=async(path,data,options={})=>{const response=await fetch(path,{method:data===undefined?'GET':'POST',headers:data===undefined?{}:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data),...(options.signal?{signal:options.signal}:{})});const value=await response.json();if(!response.ok)throw new Error(value.error||`${response.status} ${response.statusText}`);return value;};
const state={specs:[],comparison:null,cards:new Map(),cache:new Map(),frame:0,playing:false,rate:1,orbit:{yaw:-.3,pitch:-.2,zoom:1},lastRendered:-1,frameBuffer:null,loading:new Map(),pollToken:0,metricsKey:''};
const FRAME_READ_COUNT=16;
const COLORS=['#a5ee7c','#6ad5f0','#ffbd72','#d6a0ff','#f78cab','#8ee0c9'];
const TERMINAL=new Set(['completed','partial','failed','cancelled','interrupted']);
const STAGES={inference_complete:'窗口前向完成',frames:'共享图像',detection:'检测跟踪',motion:'手部运动',slam_geometry:'SLAM 与轨迹',slam_tracking:'SLAM 逐帧跟踪',slam_global_ba:'SLAM 全局优化',slam_trajectory:'全帧相机轨迹补全',slam_checkpoint:'保存 SLAM 检查点',metric_scale:'尺度恢复',parallel_inference:'多卡片段推理',metric_depth:'多卡深度预测',slam_buffer_grow:'扩容 SLAM 缓冲',queued:'排队',waiting_sample:'等待共享输入准备',preparing:'准备输入',decode:'共享 RGB 解码',preview:'生成播放代理',waiting_gpu:'等待安全 GPU 配额',launching:'启动独立工作进程',model_load:'加载模型',inference:'逐窗推理',input_bridge:'准备原生输入',detection_tracking:'检测与跟踪',hand_prediction:'手部预测',slam_scale:'SLAM 与尺度',world_infiller:'世界动作补全',camera_geometry:'相机系解码',vae_encode:'VAE 编码',mano_decode:'MANO 几何解码',shared_gpu_yield:'为共享任务让出计算时间',validate_publish:'校验并发布',running:'运行中',completed:'已完成',partial:'部分结果',failed:'失败',cancelled:'已取消',interrupted:'已中断',cancelling:'正在取消'};
const el=(tag,text,className)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;};
const fmt=(value,digits=2)=>value===null||value===undefined?'—':Number(value).toFixed(digits);
function notify(message,error=true){showNotice(message,error);}
function download(name,data,type='application/json'){const link=el('a');link.href=URL.createObjectURL(new Blob([data],{type}));link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);}
function inputLabel(text,input){const label=el('label',text);label.append(input);return label;}
function selectInput(values,value){const node=el('select');for(const [id,label] of values){const option=el('option',label);option.value=id;node.append(option);}node.value=value;return node;}
function optionField(container,name,label,values,value){const input=Array.isArray(values)?selectInput(values,value):el('input');input.dataset.option=name;if(!Array.isArray(values)){input.type='number';input.value=value??'';}container.append(inputLabel(label,input));return input;}
function buildAlgorithms(){
  const host=$('algorithmOptions');host.replaceChildren();$('importAlgorithm').replaceChildren();
  for(const spec of state.specs){
    const box=el('div',undefined,'algorithm');box.dataset.algorithm=spec.id;
    const label=el('label'),check=el('input');check.type='checkbox';check.value=spec.id;check.checked=['mint','hawor_full','ace_kfree'].includes(spec.id)&&spec.ready;check.disabled=!spec.ready||spec.import_only;
    if(spec.import_only){
      check.hidden=true;check.checked=false;check.setAttribute('aria-hidden','true');
      const status=el('div',undefined,'reference-input-state');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
      const heading=el('div',undefined,'reference-input-heading'),title=el('strong','GT 参考标注'),badge=el('span','未绑定','reference-input-badge'),description=el('p','使用带 GT 的默认示例或在下方导入参考标注。普通视频不会自动生成 GT。');
      title.dataset.referenceTitle='';badge.dataset.referenceBadge='';description.dataset.referenceDescription='';heading.append(title,badge);status.append(heading,description);box.classList.add('reference-input-card');box.append(check,status);host.append(box);
      const option=el('option',spec.label);option.value=spec.id;$('importAlgorithm').append(option);continue;
    }
    label.append(check,document.createTextNode(' '+spec.label));box.append(label,el('p',spec.ready?spec.description:'依赖未就绪：'+spec.missing.join(', '),spec.ready?'desc':'desc unavailable'));
    const details=el('details');details.append(el('summary','运行参数'));const fields=el('div');
    if(spec.parameters?.accepts_checkpoint)checkpointFields(fields);
    for(const field of spec.parameters?.fields||[]){
      const input=optionField(fields,field.name,field.label,field.choices||null,field.default);
      if(field.minimum!==undefined&&field.minimum!==null)input.min=field.minimum;
      if(field.maximum!==undefined&&field.maximum!==null)input.max=field.maximum;
      if(field.step!==undefined)input.step=field.step;
      input.dataset.parameterScope=field.scope;
    }
    details.append(fields);box.append(details);host.append(box);
    const option=el('option',spec.label);option.value=spec.id;$('importAlgorithm').append(option);
  }
  if(typeof syncReferenceInputState==='function')syncReferenceInputState();
}
function inputBody(){const camera=$('camera').value.trim();return decorateInputBody({source:$('source').value.trim(),view:$('view').value,target_fps:Number($('targetFps').value),start_frame:Number($('startFrame').value),max_frames:$('maxFrames').value===''?null:Number($('maxFrames').value),camera:camera?JSON.parse(camera):null,execution:{priority:$('executionPriority')?.value||'interactive',cache:$('executionCache')?.value||'exact'}});}
function selectedAlgorithms(){return selectedAlgorithmsFrom($('algorithmOptions'));}
function comparisonDraftPayload(){return structuredClone({...inputBody(),selections:selectedAlgorithms()});}
function comparisonDraftSignature(payload=comparisonDraftPayload()){return JSON.stringify(payload);}
function comparisonNavigation(){return [state.pollToken,state.comparison?.id??null,typeof platform==='undefined'?null:platform.serial,workspaceUI.taskDialogSession??null];}
function comparisonNavigationCurrent(context){return JSON.stringify(comparisonNavigation())===JSON.stringify(context);}
function comparisonDraftCurrent(signature,navigation,payload=comparisonDraftPayload){try{return $('taskDialog').open&&comparisonNavigationCurrent(navigation)&&signature===JSON.stringify(payload());}catch(_){return false;}}
async function submit(){
  if(state.submitting)return;let signature,navigation,posted=false,submission;
  try{
    if(typeof defaultExampleUI!=='undefined'&&defaultExampleUI.loading)throw new Error('默认示例正在读取，请稍后开始比较。');
    $('formError').textContent='';validateTaskInput();const payload=comparisonDraftPayload();if(!payload.selections.length)throw new Error('至少选择一个算法');submission=typeof defaultExampleSubmission==='function'?defaultExampleSubmission(payload):null;const routeSignature=JSON.stringify(submission),routeCurrent=()=>routeSignature===JSON.stringify(typeof defaultExampleSubmission==='function'?defaultExampleSubmission(comparisonDraftPayload()):null);signature=comparisonDraftSignature(payload);navigation=comparisonNavigation();state.submitting=true;$('start').disabled=true;
    const preflight=await preflightComparison(payload);if(!comparisonNavigationCurrent(navigation)||!$('taskDialog').open)return;
    if(!comparisonDraftCurrent(signature,navigation)||!routeCurrent())throw new Error('输入或算法参数已变化（含示例来源），请重新预检');if(!preflight?.ready)throw new Error(preflight?.errors?.join('；')||'当前输入尚未通过预检');
    posted=true;const result=await api(submission?.path||'api/comparisons',submission?.body||payload);
    if(!comparisonDraftCurrent(signature,navigation)||!routeCurrent()){notify('比较已提交，当前页面与草稿已保留；可在比较记录或任务中心查看。',false);loadHistory().catch(()=>{});return result;}
    const opening=state.pollToken+1;await openComparison(result.id);if(state.pollToken===opening&&state.comparison?.id===result.id)$('taskDialog').close();await loadHistory();return result;
  }catch(error){if(!navigation||comparisonNavigationCurrent(navigation)&&$('taskDialog').open)$('formError').textContent=error.message;else if(posted)notify('此前比较提交未确认，可在任务中心核对：'+error.message);}
  finally{state.submitting=false;$('start').disabled=false;}
}
async function refreshExisting(){if(!state.comparison)return;const comparison=state.comparison,token=state.pollToken,serial=state.existingSerial=(state.existingSerial||0)+1,old=$('existingRun').value;const data=await api('api/comparisons?limit=100');if(token!==state.pollToken||comparison.id!==state.comparison?.id||serial!==state.existingSerial)return;const select=$('existingRun');select.replaceChildren();const seen=new Set(comparison.run_ids);for(const value of data.comparisons){if(value.sample_id!==comparison.sample_id)continue;for(const run of value.runs){if(seen.has(run.id))continue;seen.add(run.id);const option=el('option',`${run.label} · ${STAGES[run.status]||run.status} · ${run.id.slice(0,8)}`);option.value=run.id;select.append(option);}}if([...select.options].some(option=>option.value===old))select.value=old;}
async function loadHistory(){const serial=workspaceUI.historySerial=(workspaceUI.historySerial||0)+1,data=await api('api/comparisons?limit=100');if(serial!==workspaceUI.historySerial)return;workspaceUI.history=data.comparisons;renderHistoryList();}
async function openComparison(id,{replace=false,history=true,keepPlatform=false}={}){
  const draftSession=workspaceUI.taskDialogSession,restoreDraft=()=>workspaceUI.taskDialogSession===draftSession&&!$('taskDialog').open;
  if(!keepPlatform&&$('platformDialog').open)$('platformDialog').close();
  $('refreshMetrics').disabled=false;
  pause();state.pollToken++;resetFrameLoading();if(typeof resetParameterPanel==='function')resetParameterPanel();resetGeometryCache();state.cache.clear();state.loading.clear();state.cards.clear();state.comparison=null;state.metricsKey='';state.metricsRows=null;state.metricsRowsKey=null;state.metricRequest=null;state.frame=0;state.lastRendered=-1;state.frameBuffer=null;
  $('workspace').setAttribute('aria-busy','true');$('sessionTitle').textContent='正在读取比较任务…';$('sessionStatus').textContent='';$('sampleInfo').textContent='';
  for(const control of ['editComparison','downloadReport','exportVideo','play','prev','next','timeline','frameJump','snapshot'])$(control).disabled=true;
  $('runs').replaceChildren();$('welcome').hidden=true;$('workspace').hidden=false;setWorkspaceTab('view');const token=state.pollToken;
  document.querySelectorAll('[data-comparison]').forEach(row=>{const active=row.dataset.comparison===id;row.classList.toggle('active',active);if(active)row.setAttribute('aria-current','page');else row.removeAttribute('aria-current');});
  await refresh(id,token);if(token!==state.pollToken)return;if(restoreDraft())restoreForm();$('workspace').setAttribute('aria-busy','false');$('editComparison').disabled=false;$('downloadReport').disabled=false;
  if(history){const query=new URLSearchParams(location.search),method=replace||query.get('id')===id?'replaceState':'pushState';if(!keepPlatform)for(const key of ['hub','section','collection','tool_job','tools_tab','workflow','study','research_tab','source_type','task_query','task_status','task_kind'])query.delete(key);query.set('id',id);window.history[method](null,'','/compare?'+query.toString());}
  try{localStorage.setItem('mint.compare.last',id);}catch(_){}
  updateHistorySelection();await refreshExisting();if(token!==state.pollToken)return;if(restoreDraft())restoreDatasetForm(state.comparison);poll(id,token);
}
function restoreForm(){const item=state.comparison?.sample_request;if(!item)return;$('source').value=item.source.path;$('examples').value=[...$('examples').options].some(option=>option.value===item.source.path)?item.source.path:'';$('view').value=item.view;$('targetFps').value=item.target_fps;$('startFrame').value=item.start_frame;$('maxFrames').value=item.max_frames??'';$('camera').value=item.camera?JSON.stringify(item.camera,null,2):'';
  for(const box of $('algorithmOptions').querySelectorAll('.algorithm')){const run=state.comparison.runs.find(r=>r.algorithm===box.dataset.algorithm);const check=box.querySelector('input[type=checkbox]');check.checked=!!run&&!check.disabled;const checkpoints=box.querySelector('[data-selection=checkpoint]'),configuration=box.querySelector('[data-selection=config]');if(checkpoints)checkpoints.value=state.comparison.runs.filter(r=>r.algorithm===box.dataset.algorithm&&r.checkpoint).map(r=>r.checkpoint).join('\n');if(configuration)configuration.value=run?.config||'';if(run)for(const field of box.querySelectorAll('[data-option]'))if(run.options[field.dataset.option]!==undefined&&run.options[field.dataset.option]!==null)field.value=field.dataset.option==='max_gpus'&&run.algorithm!=='mint'&&!run.result?.parallel_stages?8:run.options[field.dataset.option];}}
async function poll(id,token){while(token===state.pollToken){await new Promise(resolve=>setTimeout(resolve,1000));if(token!==state.pollToken)return;try{await refresh(id,token);if(state.comparison.runs.every(r=>TERMINAL.has(r.status)))return;}catch(error){notify(error.message);}}}
async function refresh(id,token){if(token!==state.pollToken)return;const serial=state.refreshSerial=(state.refreshSerial||0)+1,value=await api(`api/comparisons/${id}`);if(token!==state.pollToken||serial!==state.refreshSerial)return;state.comparison=value;const revision=comparisonResultRevision(value);if(revision!==state.metricsKey){state.metricsKey=revision;state.metricsRows=null;state.metricsRowsKey=null;state.metricRequest=null;$('refreshMetrics').disabled=false;$('metrics').textContent='打开此页后计算共同有效帧的数值。';if(workspaceUI.tab==='metrics')loadComparisonMetrics().catch(error=>notify(error.message));}if(typeof syncToolsComparison==='function')syncToolsComparison();syncSessionSummary(value);$('sessionTitle').textContent=comparisonDisplayName(value);const sample=value.sample;$('sampleInfo').textContent=sample?`${sample.frames} 帧 · ${sample.width}×${sample.height} · ${fmt(sample.fps,4)} FPS · ${{full:'完整画面',left:'左眼',right:'右眼'}[sample.request.view]||sample.request.view} · 源索引 ${sample.source_frame_indices[0]}…${sample.source_frame_indices.at(-1)}（从 0 开始）`:'共享输入准备中';if(sample){$('timeline').max=sample.frames-1;}
  for(const [index,run] of value.runs.entries()){let card=state.cards.get(run.id);if(!card){card=createCard(run,index);state.cards.set(run.id,card);$('runs').append(card.node);}updateCard(card,run,sample);}
  syncResultTools(value);updateCombination();render(true);await ensureFrame(state.frame);if(token!==state.pollToken||serial!==state.refreshSerial)return;render(true);

}
function openRunDetails(card){
  const dialog=$('runDetailsDialog'),body=$('runDetailsBody');if(!dialog||!body)return;
  pause();$('runDetailsTitle').textContent=card.run.label+' · 详情与来源';
  const declared=card.run.result?.reference_role==='user_declared_ground_truth';
  body.replaceChildren(el('p',declared?'此结果为用户声明的 GT；平台保留原始来源声明。':'此结果保留自身的算法与来源；设为对照基准不会将其声明为 GT。','explanation'));
  const source=state.comparison?.sample_request?.source?.path;if(source)body.append(el('p','比较输入：'+source,'run-details-source'));
  for(const child of card.meta.children)body.append(child.cloneNode(true));
  if(!dialog.open)dialog.showModal();
}
async function performRunAction(runId,action,button){
  if(button.dataset.busy==='true'||!state.comparison)return;const comparisonId=state.comparison.id,token=state.pollToken,current=()=>token===state.pollToken&&state.comparison?.id===comparisonId;button.dataset.busy='true';button.disabled=true;
  try{await api(`api/comparison/runs/${runId}/${action}`,{});if(!current())return;await refresh(comparisonId,token);if(current()&&action==='resume')poll(comparisonId,token);}
  catch(error){if(current())notify(error.message);}
  finally{button.dataset.busy='false';if(current()){const card=state.cards.get(runId);if(card)updateCard(card,card.run,state.comparison.sample);}}
}
function createCard(run,index){
  const node=el('section',undefined,'run-card'),color=algorithmColor(run,index);node.style.setProperty('--run-color',color);node.setAttribute('aria-label',run.label+' 结果');node.setAttribute('role','listitem');
  const head=el('div',undefined,'run-head'),titleGroup=el('div',undefined,'run-title'),title=el('h2',run.label),status=el('div',undefined,'run-state'),gpu=el('span',undefined,'run-gpu'),reference=el('span','基准','reference-badge');reference.hidden=true;titleGroup.append(title,status);head.append(titleGroup,reference,gpu);
  const execution=el('p',undefined,'run-execution-summary');execution.hidden=true;titleGroup.append(execution);
  const protocol=el('p',undefined,'run-protocol');
  const visuals=el('div',undefined,'visual-grid'),videoPane=el('div',undefined,'media-pane video-pane'),videoBox=el('div',undefined,'video-box'),video=el('video');video.muted=true;video.playsInline=true;video.preload='metadata';
  const overlay=el('canvas');overlay.setAttribute('aria-label',run.label+' 原视频二维叠加');
  if(index===0)videoBox.append(video);videoBox.append(overlay);videoPane.append(videoBox,el('span','2D · 原视频叠加','media-label'));
  const scene=el('div',undefined,'media-pane scene-pane'),label=el('span','3D · 相机系 · 米','canvas-label'),canvas=el('canvas',undefined,'canvas3d');canvas.setAttribute('aria-label',run.label+' 三维手部，可拖动旋转和滚轮缩放');scene.append(canvas,label);visuals.append(videoPane,scene);
  const numbers=el('div',undefined,'current-values'),summary=el('div',undefined,'run-summary'),coverage=el('span','等待结果','coverage'),origin=el('span',run.imported?'导入结果':'原生推理','run-origin');summary.append(coverage,origin);
  const meta=el('div',undefined,'run-meta');meta.hidden=true;meta.id='run-meta-'+run.id;
  const actions=el('div',undefined,'run-bottom'),cancel=el('button','取消'),resume=el('button','恢复'),log=el('button','查看日志'),geometry=el('a','下载几何'),enlarge=el('button','单列放大'),details=el('button','详情与来源','details-button');geometry.href=`api/comparison/runs/${run.id}/download`;geometry.download='';details.setAttribute('aria-haspopup','dialog');details.setAttribute('aria-controls','runDetailsDialog');
  const actionMenu=el('details',undefined,'run-action-menu'),menuSummary=el('summary','更多'),menuContent=el('div',undefined,'run-action-menu-content');menuSummary.setAttribute('aria-label',run.label+' 更多操作');actionMenu.append(menuSummary,menuContent);menuContent.append(log,geometry,details);
  actionMenu.addEventListener('toggle',()=>{if(actionMenu.open)for(const other of document.querySelectorAll('.run-action-menu[open]'))if(other!==actionMenu)other.open=false;});
  enlarge.setAttribute('aria-label',run.label+' 单列放大或返回对比');enlarge.onclick=async()=>{resultView.expanded=resultView.expanded===run.id?null:run.id;applyResultVisibility();render(true);const token=state.pollToken;await ensureFrame(state.frame);if(token===state.pollToken)render(true);};
  cancel.onclick=()=>performRunAction(run.id,'cancel',cancel);
  resume.onclick=()=>performRunAction(run.id,'resume',resume);
  log.onclick=async()=>{actionMenu.open=false;const token=state.pollToken;try{const value=await api(`api/comparison/runs/${run.id}/log`);if(token!==state.pollToken)return;$('logTitle').textContent=run.label+' · 运行日志';$('logText').textContent=value.text;$('logDialog').showModal();}catch(e){if(token===state.pollToken)notify(e.message);}};
  details.onclick=()=>{actionMenu.open=false;openRunDetails({run:state.cards.get(run.id)?.run||run,meta});};geometry.onclick=()=>{actionMenu.open=false;};
  const inspect=el('button','逐帧分析');inspect.onclick=event=>{event.stopPropagation();inspectResult(run.id);};actions.append(inspect,enlarge,cancel,resume,actionMenu);node.append(head,protocol,visuals,numbers,summary,actions,meta);
  let drag=null;canvas.addEventListener('pointerdown',event=>{drag=[event.clientX,event.clientY];canvas.setPointerCapture(event.pointerId);});canvas.addEventListener('pointermove',event=>{if(!drag)return;state.orbit.yaw+=(event.clientX-drag[0])*.008;state.orbit.pitch=Math.max(-1.4,Math.min(1.4,state.orbit.pitch+(event.clientY-drag[1])*.008));drag=[event.clientX,event.clientY];render(true);});
  for(const event of ['pointerup','pointercancel','lostpointercapture'])canvas.addEventListener(event,()=>drag=null);
  canvas.addEventListener('wheel',event=>{event.preventDefault();state.orbit.zoom=Math.max(.2,Math.min(8,state.orbit.zoom*Math.exp(-event.deltaY*.001)));render(true);},{passive:false});
  if(index===0){const token=state.pollToken;video.addEventListener('loadedmetadata',()=>{if(token===state.pollToken&&state.comparison?.sample&&state.frame>0)video.currentTime=state.frame/state.comparison.sample.fps+.0001;});video.addEventListener('loadeddata',()=>captureVideo(video,false));video.addEventListener('seeked',()=>captureVideo(video,false));video.addEventListener('ended',()=>{if(token===state.pollToken)pause();});
    if(video.requestVideoFrameCallback){const onFrame=(_now,metadata)=>{if(token!==state.pollToken)return;if(workspaceUI.mode!=='3d')captureVideo(video,true,metadata.mediaTime);video.requestVideoFrameCallback(onFrame);};video.requestVideoFrameCallback(onFrame);}
    for(const event of ['loadedmetadata','loadeddata','canplay','waiting','stalled','error','emptied','seeked'])video.addEventListener(event,()=>{if(token===state.pollToken){if(event==='error'&&state.playing)pause();else syncPlaybackReadiness();}});}
  return {node,title,status,gpu,reference,protocol,execution,enlarge,inspect,video,videoBox,overlay,canvas,label,numbers,meta,cancel,resume,geometry,coverage,origin,color,run,trajectory:null,trajectoryKey:''};
}
function updateCard(card,run,sample){
  const revision=resultRevision(run);if(card.resultRevision!==revision){card.resultRevision=revision;card.trajectory=null;card.trajectoryKey='';card.trajectoryRequest=null;}
  card.run=run;card.node.dataset.status=run.status;const progress=run.progress||{};
  const protocol=runProtocol(run,sample);card.protocol.textContent=protocol.short;card.protocol.title=protocol.rows.map(([label,value])=>`${label}：${value}`).join('\n');
  card.status.textContent=`${STAGES[run.status]||run.status}${!TERMINAL.has(run.status)?' · '+(STAGES[progress.stage]||progress.stage||'等待调度'):''}${!TERMINAL.has(run.status)&&progress.total?` ${progress.done||0}/${progress.total}`:''}`;
  card.status.title=run.error||card.status.textContent;const gpu=runGpuSummary(run);card.gpu.textContent=gpu.text;card.gpu.title=gpu.title;
  const execution=runExecutionSummary(run);card.execution.textContent=execution;card.execution.hidden=!execution;
  card.cancel.disabled=TERMINAL.has(run.status)||card.cancel.dataset.busy==='true';card.resume.disabled=card.resume.dataset.busy==='true';card.cancel.hidden=TERMINAL.has(run.status);card.resume.hidden=!TERMINAL.has(run.status)||run.status==='completed';card.geometry.hidden=!run.result;
  card.inspect.disabled=!run.result;card.inspect.title=run.result?'查看当前结果的逐帧数值':'结果发布后可逐帧分析';
  card.coverage.textContent=run.result?`${run.result.frames}/${sample?.frames||run.result.frames} 帧`:sample?`${sample.frames} 帧 · 等待结果`:'准备共享输入';
  card.origin.textContent=run.reuse?'复用原生结果':run.imported?'已有结果导入':run.result?.component_sources?'相机组合':run.result?.reused_native_stages_from?'阶段复用':run.algorithm==='mint'&&run.result?.native_timings?.forward_worker_seconds===0?'窗口结果复用':'原生推理';card.origin.title=run.reuse?'本次未运行网络；所示 GPU 与耗时属于原运行':run.result?.reused_native_stages_from?'已复用：'+run.result.reused_native_stages_from.stages.map(name=>STAGES[name]||name).join('、'):card.origin.textContent==='窗口结果复用'?'窗口未执行新的 GPU 前向；耗时包含读取、拼接和后处理，不代表冷启动耗时':'';
  if(sample&&!card.video.src&&card===state.cards.values().next().value){card.video.src=`comparison-media/${sample.id}.mp4`;card.video.load();}
  const signature=revision+':'+(run.error||'');if(signature===card.metaSignature)return;card.metaSignature=signature;
  card.meta.replaceChildren();if(run.result){const r=run.result;for(const [label,value] of [['相机',r.camera_source||('T_world_camera'in r.arrays?'原生世界相机':'无世界相机')],['K',r.K_source||'标准结果内参'],['覆盖',`${r.frames}/${sample?.frames||r.frames} 帧`],['策略',r.native_mode||run.options.mode],[r.native_analysis_size||r.encoded_size||r.import_source?.image_size?'原生工作网格':'展示网格',(r.native_analysis_size||r.encoded_size||r.import_source?.image_size||[sample?.width,sample?.height]).join('×')],['版本',r.backend_version?.slice(0,12)||'组件组合'],['后处理',r.postprocess||'原生'],['工作进程耗时',fmt(r.timings?.worker_end_to_end_seconds,2)+' 秒'],['主进程 RSS 峰值',fmt(r.peak_rss_gib)+' GiB']]){card.meta.append(el('div',`${label}：${value}`));}if(r.parallel_stages)for(const [stage,v]of Object.entries(r.parallel_stages)){card.meta.append(el('div',`${stage}：${v.gpu_devices.length} 卡 · 峰值并发 ${v.peak_running_workers} · ${fmt(v.wall_seconds,1)} 秒 · ${v.completed_tasks}/${v.total_tasks} 任务`));}if(r.slam_runtime)card.meta.append(el('div',`SLAM：跟踪 ${fmt(r.slam_runtime.track_compute_seconds,1)} 秒 · 全局优化 ${fmt(r.slam_runtime.global_ba_seconds,1)} 秒 · 全帧轨迹 ${fmt(r.slam_runtime.trajectory_seconds,1)} 秒 · 保存 ${fmt(r.slam_runtime.checkpoint_seconds,1)} 秒`));if(card.origin.textContent==='窗口结果复用')card.meta.append(el('div','本次复用已有窗口预测，耗时不代表从零运行完整模型。'));if(r.reused_native_stages_from)card.meta.append(el('div','复用已有阶段：'+r.reused_native_stages_from.stages.map(name=>STAGES[name]||name).join('、')+'；耗时不代表所有阶段从零执行。'));if(sample?.preparation_timings)card.meta.append(el('div',`共享输入准备：${fmt(sample.preparation_timings.preparation_seconds,1)} 秒（各算法共用）`));if(r.stage_error)card.meta.append(el('div','部分失败：'+r.stage_error.error,'run-error'));if(run.imported)card.meta.append(el('div','已有结果导入；所示耗时不含原始模型推理'));if(r.component_sources)card.meta.append(el('div','组件组合：只替换相机外参'));}
  if(run.error)card.meta.append(el('div',run.error,'run-error'));
}
function currentResultRead(card,revision,token,comparisonId,origin){return token===state.pollToken&&state.comparison?.id===comparisonId&&(state.comparison.common_world_origin_source_frame??null)===(origin??null)&&state.cards.get(card.run.id)===card&&resultRevision(card.run)===revision;}
async function ensureFrame(frame){
  const comparison=state.comparison;if(!comparison?.sample)return;
  const token=state.pollToken,start=Math.floor(frame/FRAME_READ_COUNT)*FRAME_READ_COUNT,origin=comparison.common_world_origin_source_frame??null;
  scheduleVisibleFrames(frame);const cards=frameReadCards().filter(card=>card.run.result);
  const basic=Promise.all(cards.map(card=>loadFrameBlock(card,start)));
  // Optional detail readers are independent; a slow mesh or trajectory cannot
  // prevent already available joint values from reaching the screen.
  ensureFrameDetails(frame,cards,comparison,token,origin).catch(error=>{if(token===state.pollToken&&error.name!=='AbortError')notify(error.message);});
  await basic;
  if(frame===state.frame&&token===state.pollToken&&comparison.id===state.comparison?.id){ensureMeshFrame(frame).catch(error=>{if(token===state.pollToken&&error.name!=='AbortError')notify(error.message);});if(typeof ensureParameterPreviousFrame==='function')ensureParameterPreviousFrame(frame).catch(error=>{if(token===state.pollToken&&error.name!=='AbortError')notify(error.message);});}
}
async function ensureFrameDetails(frame,cards,comparison,token,origin){
  if(frame!==state.frame||token!==state.pollToken)return;
  await Promise.all(cards.map(async card=>{
    const revision=resultRevision(card.run),current=()=>currentResultRead(card,revision,token,comparison.id,origin);
    if(!current())return;
    const trajectoryKey=resultFrameKey(card.run,'trajectory',origin);
    if(($('showTrails').checked||$('frameInspector').open||(typeof parameterPanelIsOpen==='function'&&parameterPanelIsOpen()))&&card.trajectoryKey!==trajectoryKey){
      const query=origin!==null&&'T_world_camera'in card.run.result.arrays?`?origin=${origin}`:'';
      if(card.trajectoryRequest?.key!==trajectoryKey){
        if(card.trajectoryFailure?.key===trajectoryKey&&Date.now()<card.trajectoryFailure.retryAt)return;
        const promise=frameReadAPI(`api/comparison/runs/${card.run.id}/trajectory${query}`).then(value=>{if(current()&&card.trajectoryRequest?.key===trajectoryKey){card.trajectory=value;card.trajectoryKey=trajectoryKey;card.trajectoryFailure=null;requestFrameRender();}});card.trajectoryRequest={key:trajectoryKey,promise};
      }
      const pending=card.trajectoryRequest;
      try{await pending.promise;}catch(error){if(card.trajectoryRequest===pending){card.trajectoryRequest=null;card.trajectoryFailure={key:trajectoryKey,retryAt:Date.now()+5000};}if(current()&&error.name!=='AbortError')notify('轨迹读取失败：'+error.message);}
    }
  }));
}
function dataFor(card){if(!card?.run||!state.comparison)return null;const start=Math.floor(state.frame/FRAME_READ_COUNT)*FRAME_READ_COUNT,key=resultFrameKey(card.run,start,state.comparison.common_world_origin_source_frame);const payload=state.cache.get(key);return payload?{payload,index:state.frame-start}:null;}
function resizeCanvas(canvas,width,height){if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height;}return canvas.getContext('2d');}
function project2D(point,K){if(!point||!point.every(Number.isFinite)||point[2]<=.001)return null;return [(K[0][0]*point[0]+K[0][1]*point[1])/point[2]+K[0][2],(K[1][0]*point[0]+K[1][1]*point[1])/point[2]+K[1][2]];}
function visible(data,index,slot){return data.geometry_valid?.[index]?.[slot]&&($('showAbsent').checked||data.presence===null||data.presence?.[index]?.[slot]);}
function drawOverlay(card,data){
  const sample=state.comparison.sample,ctx=resizeCanvas(card.overlay,...sample.proxy_pixel_grid);
  ctx.clearRect(0,0,card.overlay.width,card.overlay.height);if(state.frameBuffer)ctx.drawImage(state.frameBuffer,0,0,card.overlay.width,card.overlay.height);if(!data)return;
  const meshDrawn=drawMesh2D(card,data),grid=sample.proxy_content_grid||[sample.width,sample.height],project=point=>{const p=project2D(point,data.payload.K[data.index]);return p?[p[0]*grid[0]/sample.width,p[1]*grid[1]/sample.height]:null;};
  drawBaseline(card,ctx,(point,K)=>{const p=project2D(point,K);return p?[p[0]*grid[0]/sample.width,p[1]*grid[1]/sample.height]:null;});
  if(!(geometryMode()==='mesh'&&meshDrawn)){
    const {payload:p,index:i}=data;
    for(let slot=0;slot<2;slot++){
      if(!visible(p,i,slot))continue;const points=p.joints_camera_m[i][slot].map(project);
      ctx.strokeStyle=handColor(slot);ctx.fillStyle=handColor(slot);ctx.lineWidth=Math.max(1.5,card.overlay.width/450);
      for(const [a,b]of p.connections){if(!points[a]||!points[b])continue;ctx.beginPath();ctx.moveTo(...points[a]);ctx.lineTo(...points[b]);ctx.stroke();}
      for(const point of points){if(!point)continue;ctx.beginPath();ctx.arc(...point,Math.max(2,card.overlay.width/350),0,Math.PI*2);ctx.fill();}
    }
  }
  if(typeof drawParameterJoint==='function')drawParameterJoint(card,data,ctx,project,{ratio:Math.max(1,card.overlay.width/700)});
}
function sceneProject(point,center,scale,w,h){const x=point[0]-center[0],y=point[1]-center[1],z=point[2]-center[2],{yaw,pitch}=state.orbit;const u=Math.cos(yaw)*x+Math.sin(yaw)*z,v=-Math.sin(yaw)*x+Math.cos(yaw)*z;return [w/2+u*scale,h/2+(Math.cos(pitch)*y-Math.sin(pitch)*v)*scale];}
function draw3D(card,data,bounds){const ratio=window.devicePixelRatio||1,w=Math.round(card.canvas.clientWidth*ratio),h=Math.round(card.canvas.clientHeight*ratio);if(!w||!h)return;const ctx=resizeCanvas(card.canvas,w,h);ctx.clearRect(0,0,w,h);ctx.font=`${11*ratio}px sans-serif`;ctx.textAlign='left';const world=$('space').value==='world';card.label.textContent=world?'3D · 世界系 · 米 · 首帧对齐':'3D · 相机系 · 米';if(!data||(world&&(!data.payload.joints_world_m||state.comparison.common_world_origin_source_frame===null))){const loading=!data&&!!card.run.result;ctx.fillStyle='#9fb4b5';ctx.textAlign='center';ctx.fillText(loading?'正在加载当前帧…':!data?'等待有效几何结果':'此结果暂无可用世界相机',w/2,h/2,w-28*ratio);ctx.font=`${9*ratio}px sans-serif`;ctx.fillText(loading?'':!data?'结果发布后自动显示':'可切换相机系查看手部',w/2,h/2+20*ratio,w-28*ratio);return;}
  const {payload:p,index:i}=data,joints=world?p.joints_world_m[i]:p.joints_camera_m[i];const center=bounds.center,scale=Math.min(w,h)*.65/bounds.span*state.orbit.zoom;const proj=point=>sceneProject(point,center,scale,w,h);
  ctx.lineWidth=ratio;for(let axis=0;axis<3;axis++){const from=center.slice(),to=center.slice();to[axis]+=.1;ctx.strokeStyle=['#fa7979','#8ed782','#79a9ef'][axis];ctx.beginPath();ctx.moveTo(...proj(from));ctx.lineTo(...proj(to));ctx.stroke();ctx.fillStyle=ctx.strokeStyle;ctx.fillText(['X','Y','Z'][axis],...proj(to));}
  if($('showTrails').checked&&card.trajectory){const wrists=world?card.trajectory.world_wrists_m:card.trajectory.camera_wrists_m;for(const [values,color]of [[wrists?.map((v,i)=>!$('showAbsent').checked&&card.trajectory.presence&&!card.trajectory.presence[i][0]?null:v[0]),card.color],[wrists?.map((v,i)=>!$('showAbsent').checked&&card.trajectory.presence&&!card.trajectory.presence[i][1]?null:v[1]),'#ffdf80'],[world?card.trajectory.camera_world_m:null,'#89a6c2']]){if(!values)continue;ctx.strokeStyle=color+'88';ctx.lineWidth=ratio;ctx.beginPath();let open=false;for(const point of values){if(!point||point.some(x=>x===null)){open=false;continue;}const xy=proj(point);if(open)ctx.lineTo(...xy);else ctx.moveTo(...xy);open=true;}ctx.stroke();}}
  const meshDrawn=drawMesh3D(card,data,proj);drawBaseline(card,ctx,proj,world);if(geometryMode()==='mesh'&&meshDrawn){if(typeof drawParameterJoint==='function')drawParameterJoint(card,data,ctx,proj,{world,ratio});return;}
  for(let slot=0;slot<2;slot++){if(!visible(p,i,slot)||!joints?.[slot])continue;const points=joints[slot];ctx.strokeStyle=handColor(slot);ctx.fillStyle=ctx.strokeStyle;ctx.lineWidth=2*ratio;for(const [a,b]of p.connections){if(points[a]?.some(x=>x===null)||points[b]?.some(x=>x===null))continue;ctx.beginPath();ctx.moveTo(...proj(points[a]));ctx.lineTo(...proj(points[b]));ctx.stroke();}for(const point of points){if(!point||point.some(x=>x===null))continue;ctx.beginPath();ctx.arc(...proj(point),2.2*ratio,0,Math.PI*2);ctx.fill();}}
  if(typeof drawParameterJoint==='function')drawParameterJoint(card,data,ctx,proj,{world,ratio});
}
function render(force=false){updateResultLayoutHint();syncPlaybackReadiness();if(!state.comparison?.sample){if(typeof updateParameterPanel==='function')updateParameterPanel();return;}scheduleVisibleFrames(state.frame);if(!force&&state.lastRendered===state.frame)return;state.lastRendered=state.frame;const sample=state.comparison.sample;$('timeline').value=state.frame;if(document.activeElement!==$('frameJump'))$('frameJump').value=state.frame+1;$('timeLabel').textContent=`第 ${state.frame+1}/${sample.frames} 帧 · 源索引 ${sample.source_frame_indices[state.frame]}（0 起） · ${fmt(sample.timestamps_s[state.frame],3)}s`;
  const all=[];for(const card of visibleResultCards()){const d=dataFor(card),points=($('space').value==='world'?d?.payload.joints_world_m:d?.payload.joints_camera_m)?.[d?.index];if(points)for(const [slot,hand] of points.entries()){if(!visible(d.payload,d.index,slot))continue;for(const point of hand||[])if(point?.every(Number.isFinite))all.push(point);}if($('space').value==='world'&&$('showTrails').checked&&card.trajectory?.camera_world_m)for(const point of card.trajectory.camera_world_m)if(point?.every(Number.isFinite))all.push(point);}
  const min=[0,0,0],max=[0,0,0];if(all.length)for(let axis=0;axis<3;axis++){min[axis]=Math.min(...all.map(p=>p[axis]));max[axis]=Math.max(...all.map(p=>p[axis]));}const bounds={center:min.map((v,i)=>(v+max[i])/2),span:Math.max(.3,...min.map((v,i)=>max[i]-v))};
  for(const card of visibleResultCards()){const data=dataFor(card);if(workspaceUI.mode!=='3d')drawOverlay(card,data);if(workspaceUI.mode!=='2d')draw3D(card,data,bounds);if(!data){delete card.numbers.dataset.parameterSummary;const failure=frameReadFailure(card),key=JSON.stringify([state.frame,!!card.run.result,failure?.message]);if(card.numbers.dataset.frameReadState!==key){card.numbers.dataset.frameReadState=key;const label=el('span',failure?'当前帧读取失败：'+failure.message:card.run.result?'当前帧数据加载中…':'等待几何结果');card.numbers.replaceChildren(label);if(failure){const retry=el('button','重试');retry.type='button';retry.onclick=()=>retryFrameRead(card);card.numbers.append(retry);}}}else{delete card.numbers.dataset.frameReadState;if(typeof renderWristSummary==='function')renderWristSummary(card,data);}}

  updateFrameInspector();if(typeof updateParameterPanel==='function')updateParameterPanel();
}
// Media readiness is separate from geometry: frame inspection stays usable without video.
function comparisonPlaybackState(sample,video,frame){
  const count=Number(sample?.frames),hasSample=Number.isInteger(count)&&count>0;
  const validFrame=hasSample?Math.max(0,Math.min(count-1,Number.isFinite(frame)?Math.round(frame):0)):0;
  const ready=hasSample&&!!video&&video.readyState>=2&&!video.error&&!video.seeking;
  return {ready,previous:hasSample&&validFrame>0,next:hasSample&&validFrame<count-1,seek:hasSample,
    note:!hasSample?'等待共享输入准备…':video?.error?'预览不可用 · 仍可逐帧查看三维结果':
      video?.seeking?'正在定位视频帧…':!ready?'视频预览准备中 · 可逐帧查看三维结果':'← → 逐帧 · 空格播放 · F 专注'};
}
function syncPlaybackReadiness(){
  const value=comparisonPlaybackState(state.comparison?.sample,masterVideo(),state.frame);
  const flags={play:!value.ready&&!state.playing,prev:!value.previous,next:!value.next,timeline:!value.seek,frameJump:!value.seek,snapshot:!value.seek||state.cards.size===0};
  for(const [id,disabled]of Object.entries(flags)){const node=$(id);if(node)node.disabled=disabled;}
  const label=$('playbackStatus');if(label){if(label.textContent!==value.note)label.textContent=value.note;label.title=value.note;label.dataset.state=masterVideo()?.error?'error':!value.seek?'waiting':!value.ready?'loading':'ready';}
  const playButton=$('play');if(playButton)playButton.title=state.playing?'暂停（空格）':value.ready?'播放（空格）':value.note;
}
function comparisonShortcutAllowed(event){
  return !event.defaultPrevented&&!event.isComposing&&!event.altKey&&!event.metaKey&&!event.ctrlKey&&
    !event.target?.closest?.('input,textarea,select,button,a,summary,video,[contenteditable],[role="tab"],[role="slider"]');
}
function masterVideo(){return state.cards.values().next().value?.video;}
function captureVideo(video,advance=false,mediaTime=video?.currentTime){
  const sample=state.comparison?.sample;if(!video||!sample||video!==masterVideo()||video.readyState<2||video.seeking)return;
  if(advance&&state.playing){const next=Math.max(0,Math.min(sample.frames-1,Math.round(mediaTime*sample.fps)));if(next===state.frame&&state.frameBuffer)return;state.frame=next;}
  if(!state.frameBuffer)state.frameBuffer=el('canvas');const ctx=resizeCanvas(state.frameBuffer,...sample.proxy_pixel_grid);ctx.drawImage(video,0,0,state.frameBuffer.width,state.frameBuffer.height);
  ensureFrame(state.frame).then(()=>render(true));render(true);
}
function pause(){state.playIntent=(state.playIntent||0)+1;state.playing=false;updatePlayControl(false);for(const card of state.cards.values())card.video.pause();syncPlaybackReadiness();}
async function seek(frame){
  if(!state.comparison?.sample||!Number.isFinite(frame))return;const token=state.pollToken;
  pause();state.frame=Math.max(0,Math.min(state.comparison.sample.frames-1,Math.round(frame)));const video=masterVideo();
  if(video?.readyState>=1&&!video.error){const target=state.frame/state.comparison.sample.fps+.0001;if(Math.abs(video.currentTime-target)>.00005)video.currentTime=target;else captureVideo(video,false);}
  syncPlaybackReadiness();await ensureFrame(state.frame);if(token===state.pollToken)render(true);
}
async function play(){
  if(state.playing){pause();return;}const video=masterVideo(),token=state.pollToken;
  if(!comparisonPlaybackState(state.comparison?.sample,video,state.frame).ready){syncPlaybackReadiness();return;}
  const intent=state.playIntent=(state.playIntent||0)+1;
  try{video.playbackRate=state.rate;state.playing=true;updatePlayControl(true);await video.play();if(token!==state.pollToken||video!==masterVideo())video.pause();else if(!state.playing)video.pause();}
  catch(error){if(token!==state.pollToken||video!==masterVideo()||intent!==state.playIntent)return;if(!state.playing&&error.name==='AbortError')return;pause();notify('视频暂不可播放：'+error.message);}
  if(token===state.pollToken)syncPlaybackReadiness();
}
function tick(){const video=masterVideo();if(state.playing&&video&&(!video.requestVideoFrameCallback||workspaceUI.mode==='3d'||video.closest('.run-card')?.hidden))captureVideo(video,true);requestAnimationFrame(tick);}
function updateCombination(){for(const [id,worldOnly]of [['handRun',false],['cameraRun',true]]){const select=$(id),old=select.value;select.replaceChildren();for(const run of state.comparison.runs){if(!run.result||(worldOnly&&!('T_world_camera'in run.result.arrays)))continue;const option=el('option',run.label+' · '+run.id.slice(0,6));option.value=run.id;select.append(option);}if([...select.options].some(o=>o.value===old))select.value=old;}}
async function loadComparisonMetrics(){
  if(!state.comparison)return;const comparison=state.comparison,token=state.pollToken,key=comparison.id+':'+comparisonResultRevision(comparison),current=()=>token===state.pollToken&&state.comparison?.id===comparison.id&&key===comparison.id+':'+comparisonResultRevision(state.comparison);
  if(state.metricsRows&&state.metricsRowsKey===key){renderMetrics(state.metricsRows);return;}
  if(comparison.runs.filter(r=>r.result).length<2){renderMetrics([]);return;}
  if(state.metricRequest?.key===key)return state.metricRequest.promise;
  $('metrics').textContent='正在计算共同有效帧与覆盖情况…';$('refreshMetrics').disabled=true;
  const promise=api(`api/comparisons/${comparison.id}/metrics`).then(value=>{if(current()&&state.metricRequest?.promise===promise){state.metricsRows=value.metrics;state.metricsRowsKey=key;renderMetrics(value.metrics);}}).catch(error=>{if(current()&&state.metricRequest?.promise===promise){$('metrics').textContent='指标暂不可用，可重新打开此页重试。';throw error;}}).finally(()=>{if(state.metricRequest?.promise===promise){state.metricRequest=null;if(current())$('refreshMetrics').disabled=false;}});
  state.metricRequest={key,promise};return promise;
}
$('start').onclick=submit;$('refreshHistory').onclick=()=>loadHistory().catch(e=>notify(e.message));$('source').oninput=()=>{$('examples').value=[...$('examples').options].some(option=>option.value===$('source').value.trim())?$('source').value.trim():'';};$('examples').onchange=()=>{if($('examples').value){$('source').value=$('examples').value;$('view').value=$('source').value.includes('stereo_720')?'left':'full';}};$('play').onclick=play;$('prev').onclick=()=>seek(state.frame-1);$('next').onclick=()=>seek(state.frame+1);$('timeline').oninput=()=>seek(Number($('timeline').value));$('speed').onchange=()=>{state.rate=Number($('speed').value);for(const card of state.cards.values())card.video.playbackRate=state.rate;};for(const id of ['space','showAbsent'])$(id).onchange=()=>render(true);$('showTrails').onchange=()=>{render(true);if($('showTrails').checked)ensureFrame(state.frame).then(()=>render(true));};$('resetView').onclick=()=>{state.orbit={yaw:-.3,pitch:-.2,zoom:1};render(true);};$('closeLog').onclick=()=>$('logDialog').close();
$('refreshExisting').onclick=()=>refreshExisting().catch(e=>notify(e.message));
$('attachExisting').onclick=async()=>{const button=$('attachExisting');if(button.dataset.busy==='true')return;const comparison=state.comparison,token=state.pollToken,run=$('existingRun').value;try{if(!comparison||!run)throw new Error('没有可加入的同输入结果');button.dataset.busy='true';button.disabled=true;await api(`api/comparisons/${comparison.id}/attach`,{run_id:run});if(token!==state.pollToken||state.comparison?.id!==comparison.id)return;await refresh(comparison.id,token);if(token===state.pollToken)await refreshExisting();}catch(error){if(token===state.pollToken)notify(error.message);}finally{button.dataset.busy='false';button.disabled=false;}};
$('combine').onclick=async()=>{const button=$('combine');if(button.dataset.busy==='true')return;const comparison=state.comparison,token=state.pollToken,hand=$('handRun').value,camera=$('cameraRun').value;try{if(!comparison||!hand||!camera)throw new Error('先选择有完整结果的手部和世界相机');button.dataset.busy='true';button.disabled=true;await api(`api/comparisons/${comparison.id}/combine`,{hand_run:hand,camera_run:camera});if(token===state.pollToken&&state.comparison?.id===comparison.id)await refresh(comparison.id,token);}catch(error){if(token===state.pollToken)notify(error.message);}finally{button.dataset.busy='false';button.disabled=false;}};
function importDraftPayload(){return structuredClone({...inputBody(),selections:[{algorithm:$('importAlgorithm').value}],import_entry:{path:$('importPath').value.trim(),format:$('importFormat').value,frame_map:JSON.parse($('importMap').value),declared_source:$('importSource').value,image_size:$('importGrid').value.trim()?JSON.parse($('importGrid').value):undefined}});}
async function importExistingResult(){
  const button=$('import');if(button.dataset.busy==='true')return;let navigation,posted=false;
  try{validateTaskInput(false);if(!$('importPath').value.trim())throw new Error('请填写服务器结果路径。');const payload=importDraftPayload(),signature=JSON.stringify(payload),original=state.comparison?{id:state.comparison.id,sample_id:state.comparison.sample_id}:null;navigation=comparisonNavigation();button.dataset.busy='true';button.disabled=true;
    posted=true;const value=await api('api/comparison/import',payload);let id=value.id;
    if(!comparisonDraftCurrent(signature,navigation,importDraftPayload)){notify('结果已导入，当前页面与草稿已保留；可在比较记录中查看。',false);loadHistory().catch(()=>{});return value;}
    if(original?.sample_id===value.sample_id){id=original.id;await api(`api/comparisons/${id}/attach`,{run_id:value.run_ids[0]});if(!comparisonDraftCurrent(signature,navigation,importDraftPayload)){notify('结果已加入原比较，当前页面已保留。',false);loadHistory().catch(()=>{});return value;}}
    const opening=state.pollToken+1;await openComparison(id);if(state.pollToken===opening&&state.comparison?.id===id)$('taskDialog').close();await loadHistory();return value;
  }catch(error){if(!navigation||comparisonNavigationCurrent(navigation))notify(error.message);else if(posted)notify('此前结果导入未确认，可在比较记录中核对：'+error.message);}
  finally{button.dataset.busy='false';button.disabled=false;}
}
$('import').onclick=importExistingResult;
$('downloadReport').onclick=async()=>{try{const comparison=state.comparison;const metrics=await api(`api/comparisons/${comparison.id}/metrics`);download('comparison-'+comparison.id.slice(0,8)+'.json',JSON.stringify({comparison,...metrics},null,2));}catch(error){notify(error.message);}};
$('exportVideo').onclick=()=>{$('exportGeometry').value=geometryMode();$('exportBaseline').checked=$('showBaseline').checked;$('exportDialog').showModal();};
$('confirmVideoExport').onclick=exportSelectedVideo;
async function exportSelectedVideo(){
  const comparison=state.comparison;if(!comparison||state.exporting)return;const token=state.pollToken,revision=comparisonResultRevision(comparison),attempt={comparison:comparison.id,token},current=()=>state.pollToken===token&&state.comparison?.id===comparison.id&&comparisonResultRevision(state.comparison)===revision;
  const options={include_absent:$('showAbsent').checked,geometry:$('exportGeometry').value,...($('exportScope').value==='visible'?{run_ids:visibleResultCards().filter(card=>card.run.result).map(card=>card.run.id)}:{}),...($('exportBaseline').checked&&resultView.reference?{baseline_run:resultView.reference}:{})};
  state.exportAttempt=attempt;state.exporting=true;$('exportDialog').close();$('exportVideo').disabled=true;
  try{notify('正在后台生成同步对比视频…',false);let result=await api(`api/comparisons/${comparison.id}/export`,options);
    if(!current()){notify('先前比较的导出任务已提交，可从任务中心查看；当前比较已保留。',false);return result;}
    while(!['completed','failed','evicted','cancelled','interrupted','absent'].includes(result.status)){
      await new Promise(resolve=>setTimeout(resolve,1200));if(!current())return result;result=await api(`api/comparison/exports/${result.id}`);if(!current())return result;notify(`正在生成同步视频：${result.done||0}/${result.total||'…'} 帧`,false);
    }
    if(result.status!=='completed')throw new Error(result.error||'导出已失效');if(!current())return result;const link=el('a');link.href=result.url;link.download='';link.click();notify('同步对比视频已生成。',false);return result;
  }catch(error){if(current())notify(error.message);}
  finally{if(state.exportAttempt===attempt){state.exportAttempt=null;state.exporting=false;if(typeof syncPlaybackReadiness==='function')syncPlaybackReadiness();$('exportVideo').disabled=!state.comparison?.sample||!state.comparison.runs.some(run=>run.result);}}
}
$('snapshot').onclick=async()=>{
  if(!state.comparison?.sample)return;pause();const token=state.pollToken,selectedFrame=state.frame;await ensureFrame(selectedFrame);if(token!==state.pollToken||selectedFrame!==state.frame)return;await ensureMeshFrame(selectedFrame);if(token!==state.pollToken||selectedFrame!==state.frame)return;
  if(workspaceUI.mode!=='3d'&&masterVideo()?.seeking){notify('当前画面正在定位，请稍后再保存。',false);return;}captureVideo(masterVideo(),false);render(true);
  const frame=state.frame,cards=visibleResultCards(),w=640,mode=workspaceUI.mode,h=mode==='split'?760:500,canvas=el('canvas');canvas.width=w*cards.length;canvas.height=h;const ctx=canvas.getContext('2d');ctx.fillStyle='#07100f';ctx.fillRect(0,0,canvas.width,canvas.height);
  cards.forEach((card,i)=>{ctx.fillStyle=card.color;ctx.font='20px sans-serif';ctx.fillText(card.run.label,i*w+15,28);
    if(mode!=='3d'){const targetH=Math.min(mode==='split'?300:430,w*state.comparison.sample.height/state.comparison.sample.width),targetW=targetH*state.comparison.sample.width/state.comparison.sample.height;ctx.drawImage(card.overlay,i*w+(w-targetW)/2,40,targetW,targetH);}
    if(mode!=='2d'){const areaH=mode==='split'?360:430,scale=Math.min(w/card.canvas.width,areaH/card.canvas.height),sceneW=card.canvas.width*scale,sceneH=card.canvas.height*scale;ctx.drawImage(card.canvas,i*w+(w-sceneW)/2,(mode==='split'?370:45)+(areaH-sceneH)/2,sceneW,sceneH);}});
  canvas.toBlob(blob=>{if(!blob)return;const link=el('a');link.href=URL.createObjectURL(blob);link.download=`compare-frame-${frame+1}.png`;link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);});
};
window.addEventListener('resize',()=>render(true));
document.addEventListener('keydown',event=>{
  if(!comparisonShortcutAllowed(event)||document.querySelector('dialog[open]')||!state.comparison||workspaceUI.tab!=='view')return;
  if(event.key==='ArrowRight'){event.preventDefault();seek(state.frame+1);}if(event.key==='ArrowLeft'){event.preventDefault();seek(state.frame-1);}if(event.code==='Space'){event.preventDefault();play();}if(event.key.toLowerCase()==='f'){event.preventDefault();setFocusMode(!workspaceUI.focus);}
});
document.addEventListener('click',event=>{for(const menu of document.querySelectorAll('.run-action-menu[open]'))if(!menu.contains(event.target))menu.open=false;});
document.addEventListener('keydown',event=>{if(event.key==='Escape')for(const menu of document.querySelectorAll('.run-action-menu[open]'))menu.open=false;});
if($('closeRunDetails'))$('closeRunDetails').onclick=()=>$('runDetailsDialog').close();
initializeWorkspace();
if(typeof initializeDefaultExample==='function')initializeDefaultExample();
(async()=>{try{
  const legacyDiagnostic=legacyDiagnosticHashURL(location.pathname,location.search,location.hash);if(legacyDiagnostic){location.replace(legacyDiagnostic);return;}
  let startupToken=state.pollToken;const startupCurrent=()=>startupToken===state.pollToken;
  // History is useful context, but explicit links must not wait for the full catalog.
  const historyReady=loadHistory().catch(error=>notify(error.message));
  const explicitRoute=query=>['id','run','hub','collection','tool_job','workflow','study'].some(key=>query.get(key));
  const [algorithms,examples]=await Promise.all([api('api/algorithms'),api('api/comparison/examples')]);state.specs=algorithms.algorithms;buildAlgorithms();refreshComposerAlgorithms();updateImportFormats();
  const available=state.specs.filter(s=>!s.import_only);$('dependencyStatus').textContent=`${available.filter(s=>s.ready).length}/${available.length} 变体就绪`;
  for(const example of examples.examples){const option=el('option',example.label);option.value=example.path;$('examples').append(option);}
  if(examples.examples.length&&!$('source').value.trim()){$('source').value=examples.examples[0].path;$('examples').value=examples.examples[0].path;}
  let query=new URLSearchParams(location.search);
  if(!explicitRoute(query)&&!$('platformDialog').open&&!$('taskDialog').open){await historyReady;query=new URLSearchParams(location.search);}
  if($('taskDialog').open||!startupCurrent())return;
  // A module link must become visible before loading its optional comparison context.
  const initialHub=query.get('hub');
  if(initialHub&&HUB_LABELS[initialHub]&&(!$('platformDialog').open||platform.pane!==initialHub))await openPlatform(initialHub);
  if(!startupCurrent())return;
  query=new URLSearchParams(location.search);
  let id=query.get('id');
  if(query.get('run')&&!id){const requestedRun=query.get('run'),parents=await api('api/comparison/runs/'+encodeURIComponent(requestedRun)+'/parent');if(!startupCurrent())return;query=new URLSearchParams(location.search);id=query.get('id')||(query.get('run')===requestedRun?parents.comparisons[0]:null);}
  if(!id&&!explicitRoute(query)&&!$('platformDialog').open){
    let remembered;try{remembered=localStorage.getItem('mint.compare.last');}catch(_){}
    id=preferredComparison(workspaceUI.history,remembered)?.id;
  }
  // A navigation made while the request is pending retains its pane and route.
  if(!startupCurrent())return;
  if(id&&state.comparison?.id!==id){const opening=openComparison(id,{replace:true,keepPlatform:true});startupToken=state.pollToken;await opening;}
  query=new URLSearchParams(location.search);if($('taskDialog').open||!startupCurrent())return;
  const hub=query.get('hub');
  if(!$('platformDialog').open&&!hub){if(query.get('run'))inspectResult(query.get('run'));if(query.get('frame'))seek(Math.max(0,Number(query.get('frame'))-1));}
  if(query.get('collection')){if(platform.collection!==query.get('collection'))await openCollection(query.get('collection'));}
  else if(query.get('tool_job')){if(!$('platformDialog').open||platform.pane!=='tools')await openPlatform('tools');await inspectRenderJob(query.get('tool_job'));}
  else if(hub&&HUB_LABELS[hub]&&(!$('platformDialog').open||platform.pane!==hub))await openPlatform(hub);
if(typeof updateDiagnosticReturnLinks==='function')updateDiagnosticReturnLinks();
}catch(error){notify(error.message);}})();tick();
