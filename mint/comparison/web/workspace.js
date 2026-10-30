'use strict';

// Presentation preferences never alter inference options or saved result data.
const workspaceUI={mode:'split',tab:'view',sidebar:null,mobileSidebar:false,focus:false,history:[],filter:'all',noticeTimer:null};
try{const saved=JSON.parse(localStorage.getItem('mint.compare.layout.v1')||'{}');if(['split','2d','3d'].includes(saved.mode))workspaceUI.mode=saved.mode;if(typeof saved.sidebar==='boolean')workspaceUI.sidebar=saved.sidebar;}catch(_){}

function icon(name){
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('icon');svg.setAttribute('aria-hidden','true');
  const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href','#i-'+name);svg.append(use);return svg;
}
function saveLayout(){try{localStorage.setItem('mint.compare.layout.v1',JSON.stringify({mode:workspaceUI.mode,sidebar:workspaceUI.sidebar}));}catch(_){} }
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
function renderHistoryList(){
  const host=$('history'),scroll=host.scrollTop,query=$('historySearch').value.trim().toLocaleLowerCase();host.replaceChildren();
  const rows=workspaceUI.history.filter(value=>(workspaceUI.filter==='all'||comparisonState(value)===workspaceUI.filter)&&
    [value.title,value.sample_request?.source?.path,...value.runs.map(r=>r.label)].join(' ').toLocaleLowerCase().includes(query));
  $('historyCount').textContent=String(workspaceUI.history.length);
  for(const value of rows){
    const row=el('button',undefined,'history-row');row.dataset.comparison=value.id;row.classList.toggle('active',state.comparison?.id===value.id);
    if(state.comparison?.id===value.id)row.setAttribute('aria-current','page');
    const status=comparisonState(value),done=value.runs.filter(r=>r.status==='completed').length;
    const title=el('span',value.title,'history-row-title');row.append(title);row.title=value.title;
    const names=[...new Set(value.runs.map(r=>r.label))].join(' · '),description=el('small',names);description.title=names;row.append(description);
    const label=el('span',status==='active'?`${done}/${value.runs.length} 完成 · 运行中`:status==='completed'?`${value.runs.length} 个结果 · 已完成`:`${done}/${value.runs.length} 完成 · 需查看`,'history-state');label.dataset.state=status;row.append(label);
    row.onclick=()=>{if(window.innerWidth<=1100){workspaceUI.mobileSidebar=false;applyLayout();}openComparison(value.id).catch(e=>notify(e.message));};host.append(row);
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
  $('runs').style.setProperty('--columns',Math.max(1,Math.min(3,value.runs.length)));$('runs').dataset.count=value.runs.length;$('runs').dataset.many=String(value.runs.length>3);
  const prior=workspaceUI.history.findIndex(v=>v.id===value.id);
  if(prior>=0){const changed=workspaceUI.history[prior].runs.map(r=>r.status).join()!==value.runs.map(r=>r.status).join();workspaceUI.history[prior]=value;if(changed)renderHistoryList();}
  const sample=value.sample;
  for(const id of ['play','prev','next','timeline','frameJump','snapshot'])$(id).disabled=!sample;
  const results=value.runs.filter(r=>r.result).length;$('exportVideo').disabled=!sample||!results||!!state.exporting;
  if(sample){$('frameJump').max=sample.frames;$('sampleInfo').title=value.sample_request.source.path;}
}
function openTaskDialog(importMode=false){
  pause();$('formError').textContent='';$('taskDialogTitle').textContent=importMode?'导入已有算法结果':'新建算法比较';
  if(!$('taskDialog').open)$('taskDialog').showModal();
  $('importSection').open=importMode;
  if(importMode){$('importSection').scrollIntoView({block:'nearest'});$('importPath').focus();}else $('source').focus();
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
  if(!$('source').value.trim())throw new Error('请填写服务器视频路径，或从已有样例中选择。');
  for(const id of ['targetFps','startFrame','maxFrames'])if(!$(id).checkValidity()){$(id).reportValidity();throw new Error('请检查帧范围与 FPS，空白的最多帧数表示处理全片。');}
  for(const input of $('algorithmOptions').querySelectorAll('.algorithm'))if(checkAlgorithms&&input.querySelector('input[type=checkbox]').checked){
    for(const field of input.querySelectorAll('[data-option]'))if(!field.checkValidity()){input.querySelector('details').open=true;field.reportValidity();throw new Error('请检查所选算法的运行参数。');}
  }
}
function updatePlayControl(playing){$('play').replaceChildren(icon(playing?'pause':'play'));$('play').setAttribute('aria-label',playing?'暂停':'播放');$('play').title=playing?'暂停（空格）':'播放（空格）';}
function algorithmColor(run,index){return run.algorithm==='mint'?'#b9f45a':run.algorithm.startsWith('hawor')?'#58d5d2':run.algorithm.startsWith('ace')?'#ffb87a':COLORS[index%COLORS.length];}
function runGpuSummary(run){
  const live=run.progress?.gpu_scheduler;
  if(live&&!TERMINAL.has(run.status))return {text:`GPU ${live.running_devices?.length||0}/${live.max_workers}`,title:(live.running_devices||[]).join(', ')||'等待当前阶段的可用 GPU'};
  if(run.result?.parallel_stages){const values=Object.values(run.result.parallel_stages);return {text:`峰值 ${Math.max(0,...values.map(v=>v.peak_running_workers||0))} GPU`,title:values.map(v=>`${v.gpu_devices?.length||0} 张卡，${fmt(v.wall_seconds,1)} 秒`).join('；')};}
  return {text:'',title:''};
}
function initializeWorkspace(){
  applyLayout();updatePlayControl(false);
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
