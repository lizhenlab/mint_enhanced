'use strict';

// Presentation preferences never alter inference options or saved result data.
const workspaceUI={mode:'split',geometry:'skeleton',tab:'view',sidebar:null,mobileSidebar:false,focus:false,history:[],filter:'all',noticeTimer:null};
try{const saved=JSON.parse(localStorage.getItem('mint.compare.layout.v1')||'{}');if(['split','2d','3d'].includes(saved.mode))workspaceUI.mode=saved.mode;if(['skeleton','mesh','mesh_skel'].includes(saved.geometry))workspaceUI.geometry=saved.geometry;if(typeof saved.sidebar==='boolean')workspaceUI.sidebar=saved.sidebar;}catch(_){}

function icon(name){
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('icon');svg.setAttribute('aria-hidden','true');
  const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href','#i-'+name);svg.append(use);return svg;
}
function saveLayout(){try{localStorage.setItem('mint.compare.layout.v1',JSON.stringify({mode:workspaceUI.mode,geometry:geometryMode(),sidebar:workspaceUI.sidebar}));}catch(_){} }
function sidebarIsOpen(){return !workspaceUI.focus&&(window.innerWidth<=1100?workspaceUI.mobileSidebar:(workspaceUI.sidebar??true));}
function applyLayout(){
  const open=sidebarIsOpen();$('appShell').classList.toggle('sidebar-closed',!open);document.body.classList.toggle('focus-mode',workspaceUI.focus);
  $('toggleSidebar').setAttribute('aria-expanded',String(open));$('taskSidebar').inert=!open;
  $('sidebarBackdrop').hidden=!open||window.innerWidth>1100||workspaceUI.focus;
  $('focusMode').setAttribute('aria-pressed',String(workspaceUI.focus));$('focusMode').setAttribute('aria-label',workspaceUI.focus?'退出专注查看':'专注查看');
  $('runs').dataset.viewMode=workspaceUI.mode;
  document.querySelectorAll('[data-view-mode]').forEach(button=>{if(button.tagName!=='BUTTON')return;const active=button.dataset.viewMode===workspaceUI.mode;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});
  requestAnimationFrame(()=>render(true));
}
function setViewMode(mode){workspaceUI.mode=mode;saveLayout();applyLayout();captureVideo(masterVideo(),false);}
function setWorkspaceTab(tab){
  workspaceUI.tab=tab;
  for(const id of ['resultPicker','protocolPicker','frameInspector'])$(id).open=false;
  for(const name of ['view','metrics','manage']){const active=name===tab;$('panel-'+name).hidden=!active;$('tab-'+name).setAttribute('aria-selected',String(active));$('tab-'+name).tabIndex=active?0:-1;}
  if(tab!=='view'){pause();if(typeof closeParameterPanel==='function')closeParameterPanel();}else requestAnimationFrame(()=>render(true));
  if(tab==='metrics')loadComparisonMetrics().catch(error=>notify(error.message));
}
function setFocusMode(value){workspaceUI.focus=value;applyLayout();}
function showNotice(message,error=true){
  clearTimeout(workspaceUI.noticeTimer);const host=$('notice');host.hidden=!message;host.textContent=message||'';host.style.color=error?'var(--red)':'var(--lime)';
  if(error&&$('taskDialog').open)$('formError').textContent=message;
  if(message)workspaceUI.noticeTimer=setTimeout(()=>{host.hidden=true;},error?12000:6500);
}
function comparisonState(value){
  if(!value.runs.length)return 'attention';
  if(value.runs.some(run=>!TERMINAL.has(run.status)))return 'active';
  return value.runs.some(run=>run.status!=='completed')?'attention':'completed';
}
function comparisonRecordKind(value){
  const marker=[value.title,value.comparison_key,value.origin,value.purpose].filter(Boolean).join(' '),path=value.sample_request?.source?.path||'';
  if(/验收|^(?:acceptance|validation)[:_.-]|cache[-_ ]lookup|gt[-_ ]only/i.test(marker)||/\/\.deployment\/[^\n]*\/(?:validation|acceptance)\//i.test(path))return 'system';
  if(value.runs?.length&&value.runs.every(run=>run.algorithm==='reference'))return 'reference';
  return 'regular';
}
function comparisonDisplayName(value){
  const path=value.sample_request?.source?.path||'',parts=path.split('/').filter(Boolean),file=parts.at(-1)||'未命名输入';
  const title=String(value.title||'').trim();
  if(title&&title!==file&&!/^(?:\d+:)?(?:None|null|undefined)(?::\d+)?$/i.test(title))return title;
  return /^\d+\.[^.]+$/i.test(file)&&parts.length>2?`${parts.slice(-3,-1).join(' / ')} · ${file}`:/^(?:video|input|source|clip)\.[^.]+$/i.test(file)&&parts.length>1?`${parts.at(-2)} · ${file}`:file;
}
function comparisonSourceSummary(value){
  const request=value.sample_request||{},selection=value.dataset_context?.selection,path=request.source?.path||'';
  const view=({full:'完整画面',left:'左眼',right:'右眼'})[request.view]||request.view||'完整画面';
  const episode=selection?`${selection.root?.split('/').filter(Boolean).at(-1)||'LeRobot'} · Episode ${selection.episode}${selection.video_key?' · '+selection.video_key:''}`:'';
  const name=episode||comparisonDisplayName({...value,title:''});
  const range=`源索引 ${request.start_frame??0} 起（0 起） · ${request.max_frames==null?'至片尾':'最多 '+request.max_frames+' 帧'} · ${Number(request.target_fps)>0?request.target_fps+' FPS':'保留源 FPS'}`;
  return {path,name,view,range,label:`${name} · ${view}`,description:[path,episode,view,range].filter(Boolean).join('\n')};
}
function comparisonReferenceSummary(value){
  const declared=value.runs.filter(run=>run.result?.reference_role==='user_declared_ground_truth');
  return declared.length?`声明 GT 参考：${declared.map(run=>run.label||run.algorithm).join('、')}；与其对照可计算参考误差，其他配对只表示算法差异。`:'未附声明 GT 参考；算法间数值只表示差异，不代表准确率。';
}
function comparisonSearchText(value){
  const source=comparisonSourceSummary(value);
  return [value.title,value.id,comparisonDisplayName(value),source.description,...value.runs.flatMap(run=>[run.label,run.algorithm,run.checkpoint])].filter(Boolean).join(' ').toLocaleLowerCase();
}
function preferredComparison(history,remembered){
  return history.find(value=>value.id===remembered)||history.find(value=>comparisonRecordKind(value)==='regular'&&value.runs?.some(run=>run.algorithm!=='reference'))||history[0]||null;
}
function comparisonJumpFrame(value,total){
  if(String(value).trim()===''||!Number.isFinite(Number(value))||!Number.isInteger(Number(value))||!Number.isInteger(total)||total<1)return null;
  return Math.max(0,Math.min(total-1,Number(value)-1));
}
function historyGroups(rows){
  const groups=new Map();for(const row of rows){const request=row.sample_request||{},selection=row.dataset_context?.selection,key=JSON.stringify([request.source?.path||row.id,request.view||'full',selection?.root??null,selection?.episode??null,selection?.video_key??null]);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row);}return [...groups.entries()];
}
function renderHistoryList(){
  const host=$('history'),scroll=host.scrollTop,query=$('historySearch').value.trim().toLocaleLowerCase();host.replaceChildren();
  const rows=workspaceUI.history.filter(value=>(workspaceUI.filter==='all'||comparisonState(value)===workspaceUI.filter)&&
    (query||$('historyScope')?.value==='all'||state.comparison?.id===value.id||comparisonRecordKind(value)==='regular')&&
    comparisonSearchText(value).includes(query));
  $('historyCount').textContent=`${rows.length}/${workspaceUI.history.length}`;
  for(const [key,entries]of historyGroups(rows)){
    const group=el('details',undefined,'history-group');group.open=workspaceUI.historyCollapsed?.has(key)!==true;const source=comparisonSourceSummary(entries[0]),heading=el('summary',`${source.label} · ${entries.length} 次比较`);heading.title=[source.path,source.label,'帧范围和 FPS 以各条记录为准'].filter(Boolean).join('\n');group.append(heading);group.ontoggle=()=>{workspaceUI.historyCollapsed??=new Set();if(group.open)workspaceUI.historyCollapsed.delete(key);else workspaceUI.historyCollapsed.add(key);};
    const groupItems=el('div',undefined,'history-group-items');group.append(groupItems);host.append(group);
    for(const value of entries){
    const row=el('button',undefined,'history-row');row.dataset.comparison=value.id;row.classList.toggle('active',state.comparison?.id===value.id);
    if(state.comparison?.id===value.id)row.setAttribute('aria-current','page');
    const status=comparisonState(value),done=value.runs.filter(r=>r.status==='completed').length;
    const title=el('span',comparisonDisplayName(value),'history-row-title');row.append(title);title.title=title.textContent;row.title=[comparisonDisplayName(value),comparisonSourceSummary(value).description,comparisonReferenceSummary(value),'比较 ID：'+value.id].join('\n');
    const kind=comparisonRecordKind(value);if(kind!=='regular')row.append(el('small',kind==='reference'?'参考结果记录':'系统 / 验收记录','history-record-kind'));
    const names=[...new Set(value.runs.map(r=>r.label))].join(' · '),description=el('small',names);description.title=names;row.append(description);
    const label=el('span',!value.runs.length?'尚无结果记录':status==='active'?`${done}/${value.runs.length} 完成 · 运行中`:status==='completed'?`${value.runs.length} 个结果 · 已完成`:`${done}/${value.runs.length} 完成 · 需查看`,'history-state');label.dataset.state=status;row.append(label);
    const request=comparisonSourceSummary(value),range=el('small',`${request.view} · ${request.range}`);range.title=range.textContent;row.append(range);
    row.onclick=()=>{if(window.innerWidth<=1100){workspaceUI.mobileSidebar=false;applyLayout();}openComparison(value.id).catch(e=>notify(e.message));};groupItems.append(row);
    }
  }
  if(!rows.length)host.append(el('p',workspaceUI.history.length?'没有符合筛选条件的比较记录。':'还没有比较记录，点击右上角新建。','empty-hint'));
  host.scrollTop=scroll;
}
function updateHistorySelection(){
  document.querySelectorAll('[data-comparison]').forEach(row=>{const active=row.dataset.comparison===state.comparison?.id;row.classList.toggle('active',active);if(active)row.setAttribute('aria-current','page');else row.removeAttribute('aria-current');});
}
function syncSessionSummary(value){
  const done=value.runs.filter(r=>r.status==='completed').length,status=comparisonState(value);
  $('sessionStatus').textContent=!value.runs.length?'尚无结果记录':status==='completed'?`${done} 个结果已完成`:`${done}/${value.runs.length} 完成${status==='active'?' · 运行中':' · 需查看'}`;
  $('sessionStatus').dataset.state=status;$('sessionStatus').title=comparisonReferenceSummary(value);$('sessionTitle').title=[comparisonDisplayName(value),comparisonSourceSummary(value).description,'比较 ID：'+value.id].join('\n');
  const prior=workspaceUI.history.findIndex(v=>v.id===value.id);
  if(prior>=0){const changed=workspaceUI.history[prior].runs.map(r=>r.status).join()!==value.runs.map(r=>r.status).join();workspaceUI.history[prior]=value;if(changed)renderHistoryList();}
  if(typeof refreshPlatformContext==='function')refreshPlatformContext();if(typeof updateDiagnosticReturnLinks==='function')updateDiagnosticReturnLinks();
  const sample=value.sample;
  if(typeof syncPlaybackReadiness==='function')syncPlaybackReadiness();
  const results=value.runs.filter(r=>r.result).length;$('exportVideo').disabled=!sample||!results||!!state.exporting;
  if(sample){$('frameJump').max=sample.frames;$('sampleInfo').title=comparisonSourceSummary(value).description+'\n'+comparisonReferenceSummary(value);}
}
function openTaskDialog(importMode=false){
  workspaceUI.taskDialogSession=(workspaceUI.taskDialogSession||0)+1;
  if($('platformDialog').open)$('platformDialog').close();
  pause();$('formError').textContent='';$('taskDialogTitle').textContent=importMode?'导入已有算法结果':'新建算法比较';
  if(!$('taskDialog').open)$('taskDialog').showModal();
  $('importSection').open=importMode;
  updateTaskProtocolSummary();
  clearInterval(workspaceUI.protocolTimer);workspaceUI.protocolTimer=setInterval(()=>{if(!$('taskDialog').open){clearInterval(workspaceUI.protocolTimer);return;}updateTaskProtocolSummary();},1000);
  if(!datasetForm.active)prepareSourceBrowser();if(importMode){$('importSection').scrollIntoView({block:'nearest'});$('importPath').focus();}else $(datasetForm.active?'datasetEpisode':'sourceSearch').focus({preventScroll:true});
}
function updateTaskProtocolSummary(){
  const host=$('taskProtocolSummary');if(!host)return;
  const selected=[...$('algorithmOptions').querySelectorAll('.algorithm')].filter(box=>box.querySelector('input[type=checkbox]').checked);
  const lines=[`输入：${$('source').value.trim()||'尚未选择'}${datasetForm.active?'（LeRobot Episode）':''}`,`视角：${$('view').selectedOptions[0]?.textContent||'—'} · 目标 FPS：${Number($('targetFps').value)||'保留源帧率'} · 起始源索引：${$('startFrame').value||0}（从 0 开始） · ${$('maxFrames').value?`最多 ${$('maxFrames').value} 帧`:'处理至片尾'}`];
  for(const box of selected){const options={};for(const field of box.querySelectorAll('[data-option]'))if(field.value!=='')options[field.dataset.option]=field.value;const spec=state.specs.find(value=>value.id===box.dataset.algorithm);lines.push(`${spec?.label||box.dataset.algorithm}：${runProtocol({algorithm:box.dataset.algorithm,options}).short} · GPU 上限 ${options.max_gpus||'默认'}（实际数量随阶段与负载变化）`);}
  if(!selected.length)lines.push('尚未选择推理算法；可展开下方导入已有结果。');
  lines.push(`相机标定：${$('camera').value.trim()?'显式输入':'未手工提供；依各算法原生协议'} · GT 参考与算法输入分别记录`);
  const text=lines.join('\n');if(host.textContent!==text)host.textContent=text;
}
function updateImportFormats(){
  const labels={mint_npz:'MINT prediction.npz',hawor_world_npz:'HaWoR 世界结果 NPZ',ace_pickle:'ACE 原生 PKL（results 目录内）',normalized_npz:'标准几何 NPZ'};
  const choices={reference:['normalized_npz'],mint:['mint_npz','normalized_npz'],hawor_full:['hawor_world_npz','normalized_npz'],hawor_camera:['normalized_npz'],ace_kfree:['ace_pickle','normalized_npz'],ace_kgiven:['ace_pickle','normalized_npz']};
  const select=$('importFormat'),previous=select.value;select.replaceChildren();
  for(const name of choices[$('importAlgorithm').value]||['normalized_npz']){const option=el('option',labels[name]);option.value=name;select.append(option);}
  if([...select.options].some(option=>option.value===previous))select.value=previous;
  $('importGridLabel').hidden=select.value!=='hawor_world_npz';
}
function validateTaskInput(checkAlgorithms=true){
  if(!$('source').value.trim())throw new Error('请从输入目录选择一个视频，或展开直接输入路径 / 已有样例。');
  for(const id of ['targetFps','startFrame','maxFrames'])if(!$(id).checkValidity()){$(id).reportValidity();throw new Error('请检查帧范围与 FPS，空白的最多帧数表示处理全片。');}
  for(const input of $('algorithmOptions').querySelectorAll('.algorithm'))if(checkAlgorithms&&input.querySelector('input[type=checkbox]').checked){
    for(const field of input.querySelectorAll('[data-option]'))if(!field.checkValidity()){input.querySelector('details').open=true;field.reportValidity();throw new Error('请检查所选算法的运行参数。');}
  }
}
function updatePlayControl(playing){$('play').replaceChildren(icon(playing?'pause':'play'));$('play').setAttribute('aria-label',playing?'暂停':'播放');$('play').title=playing?'暂停（空格）':'播放（空格）';}
function algorithmColor(run,index){return run.algorithm==='mint'?'#b9f45a':run.algorithm.startsWith('hawor')?'#58d5d2':run.algorithm.startsWith('ace')?'#ffb87a':COLORS[index%COLORS.length];}
function runGpuSummary(run){
  if(run.reuse)return {text:'本次未推理',title:'复用已校验结果；原运行的 GPU 用量和耗时见运行详情'};
  const live=run.progress?.gpu_scheduler;
  if(live&&!TERMINAL.has(run.status)){
    const running=Array.isArray(live.running_devices)?live.running_devices:null,eligible=Array.isArray(live.eligible_devices)?live.eligible_devices.length:'未报告';
    return {text:running?`运行 ${running.length} GPU`:'GPU 分配未报告',title:[running?.length?'当前分配：'+running.join(', '):running?'当前阶段暂无已分配的 GPU':'当前阶段未报告 GPU 分配',`可调度 ${eligible} 张 · 允许上限 ${live.max_workers??run.options?.max_gpus??'未报告'}`, '可调度不表示已分配；实际并发随阶段与负载变化'].join('\n')};
  }
  if(run.result?.parallel_stages){const values=Object.values(run.result.parallel_stages),peaks=values.map(v=>v.peak_running_workers).filter(Number.isFinite);return {text:peaks.length?`峰值 ${Math.max(0,...peaks)} GPU`:'GPU 历史记录',title:values.map(v=>`${v.gpu_devices?.length||0} 张卡，${fmt(v.wall_seconds,1)} 秒`).join('；')};}
  if(!TERMINAL.has(run.status)&&run.options?.max_gpus)return {text:`上限 ${run.options.max_gpus} GPU`,title:'允许上限；当前阶段未报告实时分配数量'};
  return {text:'',title:''};
}
function durationLabel(seconds){const value=Math.max(0,Math.floor(seconds));return value>=3600?`${Math.floor(value/3600)} 小时 ${Math.floor(value%3600/60)} 分`:value>=60?`${Math.floor(value/60)} 分 ${value%60} 秒`:`${value} 秒`;}
function runExecutionSummary(run){
  if(['completed','partial','failed','cancelled','interrupted','evicted'].includes(run.status))return '';
  const summary=run.task_summary||{},progress=run.progress||{},stage=progress.stage||summary.phase||run.status;
  const descriptions={slam_tracking:'连续 SLAM 正在逐帧跟踪，此阶段有时序依赖',slam_global_ba:'正在优化关键帧与相机轨迹',slam_trajectory:'正在补全全部源帧的相机轨迹',slam_scale:'正在恢复相机运动与米制尺度；后续还需世界动作补全',slam_geometry:'正在恢复相机运动；跟踪与全局优化按顺序执行',metric_scale:'正在估计米制尺度',world_infiller:'正在结合相机轨迹补全世界坐标手部运动',model_load:'模型冷加载，完成后更新推理计数',loading_model:'模型冷加载，完成后更新推理计数',waiting_sample:'等待共享解码完成，各算法复用同一输入',waiting_gpu:'等待显存余量与 GPU 租约，不会终止其他任务'};
  const parts=[progress.detail||progress.waiting_reason||summary.waiting_reason||descriptions[stage]].filter(Boolean);
  if(Number.isFinite(summary.phase_elapsed_seconds))parts.push(`本阶段已耗时 ${durationLabel(summary.phase_elapsed_seconds)}`);
  else if(Number.isFinite(progress.elapsed_seconds))parts.push(`已报告耗时 ${durationLabel(progress.elapsed_seconds)}`);
  if(Number.isFinite(summary.eta?.seconds)&&summary.eta.seconds>=0)parts.push(`本阶段预计还需 ${durationLabel(summary.eta.seconds)}（估算）`);
  else if(parts.length&&['slam_scale','slam_geometry','slam_global_ba','model_load'].includes(stage))parts.push('尚无可靠剩余时间估算');
  return parts.join(' · ');
}
function updateResultLayoutHint(){
  const hint=$('resultLayoutHint');if(!hint||!state.comparison)return;const count=visibleResultCards().length,host=$('runs');host.dataset.visibleCount=String(count);hint.hidden=resultView.layout!=='overview'||count<=2;
  if(!hint.hidden){const columns=comparisonGridPlan(host.clientWidth,host.clientHeight,count,'overview').columns,text=`总览 · ${count} 个结果 / ${Math.ceil(count/columns)} 行；可滚动查看。切换“基准 + 候选”可完整对照。`;if(hint.textContent!==text)hint.textContent=text;}
}

function initializeWorkspace(){
  applyLayout();updatePlayControl(false);initializeSourceBrowser();initializeResultTools();initializeGeometryTools();initializePlatform();
  $('toggleSidebar').onclick=()=>{if(window.innerWidth<=1100)workspaceUI.mobileSidebar=!workspaceUI.mobileSidebar;else workspaceUI.sidebar=!sidebarIsOpen();saveLayout();applyLayout();};
  $('sidebarBackdrop').onclick=()=>{workspaceUI.mobileSidebar=false;applyLayout();};
  $('focusMode').onclick=()=>setFocusMode(!workspaceUI.focus);
  for(const button of document.querySelectorAll('button[data-view-mode]'))button.onclick=()=>setViewMode(button.dataset.viewMode);
  const tabs=[...document.querySelectorAll('[data-workspace-tab]')];
  for(const [index,button]of tabs.entries()){
    button.onclick=()=>setWorkspaceTab(button.dataset.workspaceTab);
    button.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();event.stopPropagation();const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].click();tabs[next].focus();};
  }
  $('historySearch').oninput=renderHistoryList;
  const layoutHint=el('p',undefined,'result-layout-hint hint');layoutHint.id='resultLayoutHint';layoutHint.hidden=true;layoutHint.setAttribute('role','status');$('runs').before(layoutHint);
  const protocolDetails=el('details',undefined,'task-protocol-summary');protocolDetails.open=true;protocolDetails.append(el('summary','提交协议概览'));const protocolSummary=el('pre',undefined,'hint');protocolSummary.id='taskProtocolSummary';protocolDetails.append(protocolSummary);$('taskDialog').querySelector('.task-config-grid').after(protocolDetails);$('taskDialog').addEventListener('input',updateTaskProtocolSummary);$('taskDialog').addEventListener('change',updateTaskProtocolSummary);
  const historyScope=selectInput([['regular','常规比较（搜索包含系统记录）'],['all','全部记录（含参考与验收）']],'regular');historyScope.id='historyScope';historyScope.setAttribute('aria-label','比较记录范围');historyScope.onchange=renderHistoryList;$('historySearch').closest('label').after(historyScope);
  for(const button of document.querySelectorAll('[data-history-filter]'))button.onclick=()=>{workspaceUI.filter=button.dataset.historyFilter;for(const other of document.querySelectorAll('[data-history-filter]')){const selected=other===button;other.classList.toggle('active',selected);other.setAttribute('aria-pressed',String(selected));}renderHistoryList();};
  for(const id of ['newComparison','welcomeNew','editComparison'])$(id).onclick=()=>openTaskDialog();
  for(const id of ['openImport','manageImport'])$(id).onclick=()=>openTaskDialog(true);
  $('closeTask').onclick=()=>$('taskDialog').close();$('taskDialog').addEventListener('close',()=>{workspaceUI.taskDialogSession=(workspaceUI.taskDialogSession||0)+1;});
  $('importAlgorithm').onchange=updateImportFormats;$('importFormat').onchange=()=>{$('importGridLabel').hidden=$('importFormat').value!=='hawor_world_npz';};
  const jumpToFrame=()=>{const field=$('frameJump'),frame=comparisonJumpFrame(field.value,state.comparison?.sample?.frames);if(frame===null){if(field.value.trim())field.reportValidity();return;}field.value=String(frame+1);seek(frame);};
  $('frameJump').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();jumpToFrame();}};
  $('frameJump').onchange=jumpToFrame;
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!document.querySelector('dialog[open]')&&workspaceUI.focus)setFocusMode(false);});
  window.addEventListener('resize',applyLayout);
}
if(typeof module!=='undefined'&&module.exports)module.exports={comparisonRecordKind,comparisonDisplayName,comparisonSourceSummary,comparisonReferenceSummary,comparisonSearchText,preferredComparison,comparisonJumpFrame,historyGroups,runGpuSummary,runExecutionSummary,durationLabel};
