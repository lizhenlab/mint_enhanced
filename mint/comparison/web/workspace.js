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
  if(tab!=='view')pause();else requestAnimationFrame(()=>render(true));
  if(tab==='metrics')loadComparisonMetrics().catch(error=>notify(error.message));
}
function setFocusMode(value){workspaceUI.focus=value;applyLayout();}
function showNotice(message,error=true){
  clearTimeout(workspaceUI.noticeTimer);const host=$('notice');host.hidden=!message;host.textContent=message||'';host.style.color=error?'var(--red)':'var(--lime)';
  if(error&&$('taskDialog').open)$('formError').textContent=message;
  if(message)workspaceUI.noticeTimer=setTimeout(()=>{host.hidden=true;},error?12000:6500);
}
function comparisonState(value){
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
function historyGroups(rows){
  const groups=new Map();for(const row of rows){const request=row.sample_request||{},key=`${request.source?.path||row.id}|${request.view||'full'}`;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row);}return [...groups.entries()];
}
function renderHistoryList(){
  const host=$('history'),scroll=host.scrollTop,query=$('historySearch').value.trim().toLocaleLowerCase();host.replaceChildren();
  const rows=workspaceUI.history.filter(value=>(workspaceUI.filter==='all'||comparisonState(value)===workspaceUI.filter)&&
    (query||$('historyScope')?.value==='all'||state.comparison?.id===value.id||comparisonRecordKind(value)==='regular')&&
    [value.title,value.id,value.sample_request?.source?.path,...value.runs.map(r=>r.label)].join(' ').toLocaleLowerCase().includes(query));
  $('historyCount').textContent=`${rows.length}/${workspaceUI.history.length}`;
  for(const [key,entries]of historyGroups(rows)){
    const group=el('details',undefined,'history-group');group.open=workspaceUI.historyCollapsed?.has(key)!==true;const heading=el('summary',`${comparisonDisplayName({...entries[0],title:''})} · ${entries.length} 次比较`);heading.title=entries[0].sample_request?.source?.path||'';group.append(heading);group.ontoggle=()=>{workspaceUI.historyCollapsed??=new Set();if(group.open)workspaceUI.historyCollapsed.delete(key);else workspaceUI.historyCollapsed.add(key);};
    const groupItems=el('div',undefined,'history-group-items');group.append(groupItems);host.append(group);
    for(const value of entries){
    const row=el('button',undefined,'history-row');row.dataset.comparison=value.id;row.classList.toggle('active',state.comparison?.id===value.id);
    if(state.comparison?.id===value.id)row.setAttribute('aria-current','page');
    const status=comparisonState(value),done=value.runs.filter(r=>r.status==='completed').length;
    const title=el('span',comparisonDisplayName(value),'history-row-title');row.append(title);row.title=[value.title,value.id].join(' · ');
    const kind=comparisonRecordKind(value);if(kind!=='regular')row.append(el('small',kind==='reference'?'参考结果记录':'系统 / 验收记录','history-record-kind'));
    const names=[...new Set(value.runs.map(r=>r.label))].join(' · '),description=el('small',names);description.title=names;row.append(description);
    const label=el('span',status==='active'?`${done}/${value.runs.length} 完成 · 运行中`:status==='completed'?`${value.runs.length} 个结果 · 已完成`:`${done}/${value.runs.length} 完成 · 需查看`,'history-state');label.dataset.state=status;row.append(label);
    const request=value.sample_request||{},range=el('small',`${{full:'完整画面',left:'左眼',right:'右眼'}[request.view]||''} · 源索引 ${request.start_frame??0} 起 · ${request.max_frames==null?'全片':request.max_frames+' 帧'}`);row.append(range);
    row.onclick=()=>{if(window.innerWidth<=1100){workspaceUI.mobileSidebar=false;applyLayout();}openComparison(value.id).catch(e=>notify(e.message));};groupItems.append(row);
    }
  }
  if(!rows.length)host.append(el('p',workspaceUI.history.length?'没有符合筛选条件的任务。':'还没有比较任务，点击右上角新建。','empty-hint'));
  host.scrollTop=scroll;
}
function updateHistorySelection(){
  document.querySelectorAll('[data-comparison]').forEach(row=>{const active=row.dataset.comparison===state.comparison?.id;row.classList.toggle('active',active);if(active)row.setAttribute('aria-current','page');else row.removeAttribute('aria-current');});
}
function syncSessionSummary(value){
  const done=value.runs.filter(r=>r.status==='completed').length,status=comparisonState(value);
  $('sessionStatus').textContent=status==='completed'?`${done} 个结果已完成`:`${done}/${value.runs.length} 完成${status==='active'?' · 运行中':' · 需查看'}`;
  $('sessionStatus').dataset.state=status;$('sessionTitle').title=value.title;
  const prior=workspaceUI.history.findIndex(v=>v.id===value.id);
  if(prior>=0){const changed=workspaceUI.history[prior].runs.map(r=>r.status).join()!==value.runs.map(r=>r.status).join();workspaceUI.history[prior]=value;if(changed)renderHistoryList();}
  if(typeof refreshPlatformContext==='function')refreshPlatformContext();
  const sample=value.sample;
  for(const id of ['play','prev','next','timeline','frameJump','snapshot'])$(id).disabled=!sample;
  const results=value.runs.filter(r=>r.result).length;$('exportVideo').disabled=!sample||!results||!!state.exporting;
  if(sample){$('frameJump').max=sample.frames;$('sampleInfo').title=value.sample_request.source.path;}
}
function openTaskDialog(importMode=false){
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
  const live=run.progress?.gpu_scheduler;
  if(live&&!TERMINAL.has(run.status))return {text:`GPU ${live.running_devices?.length||0}/${live.max_workers??run.options?.max_gpus??'—'}`,title:(live.running_devices||[]).length?'当前物理 GPU：'+live.running_devices.join(', '):'当前阶段暂无已分配的 GPU；分母是允许上限'};
  if(run.result?.parallel_stages){const values=Object.values(run.result.parallel_stages);return {text:`峰值 ${Math.max(0,...values.map(v=>v.peak_running_workers||0))} GPU`,title:values.map(v=>`${v.gpu_devices?.length||0} 张卡，${fmt(v.wall_seconds,1)} 秒`).join('；')};}
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
  $('closeTask').onclick=()=>$('taskDialog').close();
  $('importAlgorithm').onchange=updateImportFormats;$('importFormat').onchange=()=>{$('importGridLabel').hidden=$('importFormat').value!=='hawor_world_npz';};
  $('frameJump').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();if($('frameJump').checkValidity())seek(Number($('frameJump').value)-1);else $('frameJump').reportValidity();}};
  $('frameJump').onchange=()=>{if($('frameJump').checkValidity())seek(Number($('frameJump').value)-1);};
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!document.querySelector('dialog[open]')&&workspaceUI.focus)setFocusMode(false);});
  window.addEventListener('resize',applyLayout);
}
if(typeof module!=='undefined'&&module.exports)module.exports={comparisonRecordKind,comparisonDisplayName,historyGroups,runExecutionSummary,durationLabel};
