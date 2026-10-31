'use strict';

// Display preferences refer to immutable runs; they never change GT roles or inference options.
const resultView={comparison:null,selected:[],reference:null,candidate:null,layout:'pair',pairSide:'candidate',expanded:null,known:[],signature:'',protocolSignature:''};
const RESULT_PREFERENCES='mint.compare.results.v1';

function defaultResultSelection(runs){
  if(!runs.length)return {selected:[],reference:null};
  const reference=runs.find(run=>run.result?.reference_role==='user_declared_ground_truth')||runs.find(run=>run.algorithm==='mint')||runs[0];
  const maximum=reference.result?.reference_role==='user_declared_ground_truth'?4:3;
  const selected=[reference.id],families=new Set([reference.algorithm.split('_')[0]]);
  for(const run of runs){const family=run.algorithm.split('_')[0];if(selected.length<maximum&&!families.has(family)){selected.push(run.id);families.add(family);}}
  for(const run of runs)if(selected.length<Math.min(maximum,runs.length)&&!selected.includes(run.id))selected.push(run.id);
  return {selected,reference:reference.id};
}
function normalizeResultSelection(runs,saved){
  const fallback=defaultResultSelection(runs),ids=new Set(runs.map(run=>run.id));
  if(!saved||!Array.isArray(saved.selected))return fallback;
  const selected=[...new Set(saved.selected.filter(id=>ids.has(id)))];
  if(!selected.length)return fallback;
  const reference=ids.has(saved.reference)?saved.reference:selected[0];
  if(!selected.includes(reference))selected.unshift(reference);
  return {selected,reference};
}
function toggleResultSelection(selection,id,checked){
  if(id===selection.reference&&!checked)return selection;
  const selected=checked?[...new Set([...selection.selected,id])]:selection.selected.filter(value=>value!==id);
  return {...selection,selected};
}
function metricValue(value,digits=2){return typeof value==='number'&&Number.isFinite(value)?value.toFixed(digits):'—';}
function metricPairScope(row){return row.scope==='error_against_declared_GT'?'参考误差':'算法差异';}
function filterMetricRows(rows,selection,scope){
  return scope==='all'?rows:rows.filter(row=>selection.selected.includes(row.first_run)&&selection.selected.includes(row.second_run)&&[row.first_run,row.second_run].includes(selection.reference));
}
function handPostprocessLabel(value){
  if(!value)return '原生后处理';
  if(value==='smooth')return 'Production · q/r/β 0.6 / 0.6 / 2';
  if(value==='smooth@0.7,0.5,0.3')return 'Viewer · q/r/β 0.7 / 0.5 / 0.3';
  if(value.startsWith('smooth@'))return '自定义 UKF · q/r/β '+value.slice(7).split(',').join(' / ');
  return ({hard:'原始硬切',blend:'线性融合'})[value]||value;
}
function runProtocol(run,sample){
  const options=run.options||{},result=run.result||{},mode=options.mode||result.native_mode;
  const strategy=result.component_sources?'相机外参组合':run.imported?'数值结果导入':({max_chunked:'最大窗分窗',chunked:'训练窗分窗',independent81:'独立 81 帧',native_tiled:'整段 VAE · 4k+1',native:'原生流程',normalized:'标准几何导入'})[mode]||mode||(run.algorithm==='reference'?'参考数据导入':run.result?'已有数值结果':'原生流程');
  const size=result.native_analysis_size||result.encoded_size||result.import_source?.image_size;
  const width=options.analysis_width??options.encode_width;
  const grid=Array.isArray(size)?size.join(' × '):width===0?'原视频尺寸':width?`宽 ${width} px`:'原生工作尺寸';
  const short=[strategy];
  if(run.algorithm==='mint')short.push(handPostprocessLabel(options.hand_mode||result.postprocess));
  else if(options.encode_width!==undefined||options.analysis_width!==undefined)short.push(grid);
  const policy=({shared:'负载感知共享',idle:'仅空闲卡',dedicated:'固定卡对照'})[options.resource_policy]||options.resource_policy||'导入数值';
  const rows=[['策略',strategy],['工作尺寸',grid]];
  if(run.reuse){short.unshift('复用结果');rows.push(['结果复用','引用已校验的原生结果；资源和耗时属于原运行，本次未执行网络']);}
  if(run.algorithm==='mint')rows.push(['手部后处理',handPostprocessLabel(options.hand_mode||result.postprocess)]);
  else if(result.postprocess)rows.push(['后处理',result.postprocess]);
  if(options.stride!==undefined)rows.push(['片段步长 / 合并',`${options.stride} / ${options.merge||'原生'}`]);
  rows.push(['相机来源',result.camera_source||'结果发布后显示'],['内参 K 来源',result.K_source||(sample?.request?.camera?'用户提供的显式标定':'由算法协议确定')],['资源策略',run.imported||run.algorithm==='reference'||result.component_sources?'已有数值处理 · 不执行模型':policy+(options.max_gpus?` · 上限 ${options.max_gpus} 卡`: '')]);
  if(result.component_sources)rows.push(['手部 / 相机组件',`${result.component_sources.hands} / ${result.component_sources.camera}`]);
  if(sample){rows.push(['输入协议',`${({full:'完整画面',left:'左眼',right:'右眼'})[sample.request?.view]||sample.request?.view||''} · ${sample.frames} 帧 · ${metricValue(sample.fps,4)} FPS`]);}
  rows.push(['实现版本',result.backend_version?.slice(0,12)||(result.component_sources?'相机组件组合':'结果发布后显示')],['结果 ID',run.id]);
  if(result.selected_checkpoint)rows.push(['检查点',result.selected_checkpoint],['模型配置',result.selected_config||'原生配置']);
  return {short:short.join(' · '),rows};
}

// Layout is a viewing preference; overview selections and result provenance stay intact.
function resultCandidate(runs,view){
  const ids=new Set(runs.map(run=>run.id));
  return ids.has(view.candidate)&&view.candidate!==view.reference?view.candidate:
    (view.selected||[]).find(id=>ids.has(id)&&id!==view.reference)||runs.find(run=>run.id!==view.reference)?.id||null;
}
function resultCandidateNavigation(runs,view){
  const reference=runs.some(run=>run.id===view.reference)?view.reference:runs[0]?.id;
  const eligible=runs.filter(run=>run.id!==reference),candidate=resultCandidate(runs,{...view,reference}),index=eligible.findIndex(run=>run.id===candidate);
  return {candidate,index:index+1,total:eligible.length,previous:index>0?eligible[index-1].id:null,next:index>=0&&index+1<eligible.length?eligible[index+1].id:null};
}
function resultStateLabel(run){
  if(!run)return '暂无候选';
  const label=({completed:'已完成',partial:'部分结果',failed:'失败',cancelled:'已取消',interrupted:'已中断',cancelling:'正在取消',running:'运行中',queued:'排队中',waiting_gpu:'等待 GPU',waiting_sample:'准备输入',inference_complete:'前向完成，待发布'})[run.status]||run.status||(run.result?'已有结果':'等待结果');
  return !run.result&&run.status==='completed'?label+' · 无几何结果':label;
}
function resultReferenceRole(run,pinned=false){
  const declaredGT=run?.result?.reference_role==='user_declared_ground_truth';
  return {declaredGT,badge:declaredGT?(pinned?'声明 GT · 基准':'声明 GT'):'算法基准',note:!run?'固定基准用于显示与指标配对，不改变结果的 GT 角色。':declaredGT?`${run.label}：用户声明 GT；误差以指标中标注的共同有效几何口径为准。`:`${run.label}：算法对照基准；选择基准不会将算法结果声明为 GT。`};
}
function displayResultIds(runs,view,width=1000){
  if(view.expanded&&runs.some(run=>run.id===view.expanded))return [view.expanded];
  const ids=new Set(runs.map(run=>run.id)),reference=ids.has(view.reference)?view.reference:runs[0]?.id;
  if(view.layout==='overview')return [reference,...(view.selected||[])].filter((id,index,all)=>ids.has(id)&&all.indexOf(id)===index);
  const candidate=resultCandidate(runs,{...view,reference});
  if(width<620&&candidate)return [view.pairSide==='reference'?reference:candidate];
  return [reference,candidate].filter((id,index,all)=>id&&all.indexOf(id)===index);
}
function comparisonGridPlan(width,height,count,layout='pair'){
  const overview=layout==='overview';
  let columns=overview?Math.max(1,Math.min(4,count,Math.floor(width/320)||1)):Math.max(1,Math.min(count,width<620?1:2));
  if(overview&&count===4&&columns===3)columns=2;
  return {columns,rows:Math.ceil(count/columns),scroll:overview&&count>columns,rowHeight:overview?Math.max(360,Math.floor(height)):Math.max(0,Math.floor(height))};
}
function comparisonMetricSelection(runs,view){
  // A narrow-screen pager or a focused card changes the viewport, not the pair being scored.
  const reference=runs.some(run=>run.id===view.reference)?view.reference:runs[0]?.id||null;
  return {selected:displayResultIds(runs,{...view,reference,expanded:null},Infinity),reference};
}
function currentResultSelection(){return comparisonMetricSelection(state.comparison?.runs||[],resultView);}
function syncComparisonLayout(){
  if(!state.comparison)return;
  const host=$('runs'),width=host.clientWidth||window.innerWidth,plan=comparisonGridPlan(width,host.clientHeight,visibleResultCards().length,resultView.expanded?'pair':resultView.layout);
  host.dataset.layout=resultView.expanded?'single':resultView.layout;host.style.setProperty('--fit-columns',plan.columns);host.style.setProperty('--overview-row-height',plan.rowHeight+'px');
  if($('resultLayout'))$('resultLayout').value=resultView.layout;
  if($('pairSideControl'))$('pairSideControl').hidden=resultView.layout!=='pair'||width>=620||!resultView.candidate;
  if($('pairSide'))$('pairSide').value=resultView.pairSide;
  if($('candidateControl'))$('candidateControl').hidden=resultView.layout!=='pair'||!resultView.candidate;
  if($('candidateRun'))$('candidateRun').value=resultView.candidate||'';
  const navigation=resultCandidateNavigation(state.comparison.runs,resultView),candidate=state.comparison.runs.find(run=>run.id===navigation.candidate);
  for(const [id,target]of [['previousCandidate',navigation.previous],['nextCandidate',navigation.next]]){const button=$(id);if(button){button.disabled=!target;button.title=(id==='previousCandidate'?'上一个候选':'下一个候选')+(target?'：'+state.comparison.runs.find(run=>run.id===target)?.label:'（已到边界）');}}
  const position=$('candidatePosition'),positionText=navigation.total?`${navigation.index} / ${navigation.total} · ${resultStateLabel(candidate)}`:'暂无候选';if(position&&position.textContent!==positionText)position.textContent=positionText;
  const note=$('comparisonReferenceNote'),reference=state.comparison.runs.find(run=>run.id===resultView.reference),role=resultReferenceRole(reference,true);if(note){note.dataset.role=role.declaredGT?'declared-gt':'algorithm';if(note.textContent!==role.note)note.textContent=role.note;}
}
async function changeResultCandidate(id){
  if(!state.comparison||id===resultView.reference||!state.comparison.runs.some(run=>run.id===id))return;
  resultView.candidate=id;resultView.pairSide='candidate';resultView.expanded=null;
  saveResultPreferences();applyResultVisibility();render(true);if(state.metricsRows)renderMetrics(state.metricsRows);
  const token=state.pollToken;await ensureFrame(state.frame);if(token===state.pollToken)render(true);
}
function initializeComparisonToolbar(){
  const toolbar=document.querySelector('.result-toolbar'),old=document.querySelector('.view-toolbar');
  const layout=selectInput([['pair','基准 + 候选'],['overview','结果总览']],'pair');layout.id='resultLayout';layout.setAttribute('aria-label','比较布局');
  const candidate=selectInput([],'');candidate.id='candidateRun';candidate.setAttribute('aria-describedby','candidatePosition');const candidateField=el('div',undefined,'inline-label candidate-control');candidateField.id='candidateControl';const candidateLabel=el('label','候选');candidateLabel.htmlFor='candidateRun';const candidateSwitcher=el('span',undefined,'candidate-switcher'),previous=el('button','‹'),next=el('button','›');previous.id='previousCandidate';next.id='nextCandidate';previous.type=next.type='button';previous.setAttribute('aria-label','上一个候选结果');next.setAttribute('aria-label','下一个候选结果');previous.disabled=next.disabled=true;const position=el('output',undefined,'candidate-position');position.id='candidatePosition';position.setAttribute('role','status');position.setAttribute('aria-live','polite');position.setAttribute('aria-atomic','true');candidateSwitcher.append(previous,candidate,next);candidateField.append(candidateLabel,candidateSwitcher,position);
  const side=selectInput([['candidate','候选结果'],['reference','对照基准']],'candidate');side.id='pairSide';const sideField=inputLabel('当前画面',side);sideField.id='pairSideControl';sideField.className='inline-label';sideField.hidden=true;
  const advanced=el('details',undefined,'comparison-options');advanced.id='comparisonOptions';advanced.append(el('summary','显示与诊断'));const content=el('div',undefined,'comparison-options-content');advanced.append(content);
  const picker=$('resultPicker');picker.querySelector('.hint').textContent='勾选总览中展示的结果；“基准 + 候选”模式通过候选菜单切换。导出可选择全部结果或当前可见结果。';
  content.append(picker,$('comparePair'),$('showAllResults'),old.querySelector('label[title]'),old.querySelector('label:not([title])'),old.querySelector('.view-options'),$('resetView'),$('snapshot'),$('protocolPicker'),$('frameInspector'));
  const segment=old.querySelector('.segmented');toolbar.prepend(layout);$('referenceRun').closest('label').after(candidateField,sideField);toolbar.append(segment,advanced,$('focusMode'));old.hidden=true;
  const referenceNote=el('p',undefined,'comparison-reference-note');referenceNote.id='comparisonReferenceNote';toolbar.after(referenceNote);$('referenceRun').setAttribute('aria-describedby','comparisonReferenceNote');
  $('resultPicker').querySelector('summary').title='仅影响总览选列，不改变基准和候选';
  layout.onchange=()=>{resultView.layout=layout.value;resultView.expanded=null;saveResultPreferences();applyResultVisibility();render(true);ensureFrame(state.frame).then(()=>render(true));};
  candidate.onchange=()=>changeResultCandidate(candidate.value).catch(error=>notify(error.message));
  for(const [button,direction]of [[previous,'previous'],[next,'next']])button.onclick=()=>{const target=resultCandidateNavigation(state.comparison?.runs||[],resultView)[direction];if(target)changeResultCandidate(target).catch(error=>notify(error.message));};
  side.onchange=()=>{resultView.pairSide=side.value;saveResultPreferences();applyResultVisibility();render(true);ensureFrame(state.frame).then(()=>render(true));};
  document.addEventListener('click',event=>{if(advanced.open&&!advanced.contains(event.target))advanced.open=false;});
  document.addEventListener('keydown',event=>{if(event.key==='Escape')advanced.open=false;});
  if(typeof ResizeObserver!=='undefined'){const observer=new ResizeObserver(()=>{if(state.comparison&&hostVisible())requestAnimationFrame(()=>{applyResultVisibility();render(true);});});observer.observe($('runs'));}
  function hostVisible(){return $('runs').clientWidth>0&&$('runs').clientHeight>0;}
}

function visibleResultCards(){
  const visibleIds=displayResultIds(state.comparison?.runs||[],resultView,$('runs').clientWidth||window.innerWidth);
  const cards=[...state.cards.values()].filter(card=>visibleIds.includes(card.run.id));
  return cards.sort((a,b)=>Number(b.run.id===resultView.reference)-Number(a.run.id===resultView.reference));
}
function saveResultPreferences(){
  try{const saved=JSON.parse(localStorage.getItem(RESULT_PREFERENCES)||'{}');saved[state.comparison.id]={selected:resultView.selected,reference:resultView.reference,candidate:resultView.candidate,layout:resultView.layout,pairSide:resultView.pairSide};const entries=Object.entries(saved).slice(-40);localStorage.setItem(RESULT_PREFERENCES,JSON.stringify(Object.fromEntries(entries)));}catch(_){}
}
function applyResultVisibility(){
  const visible=visibleResultCards(),visibleIds=new Set(visible.map(card=>card.run.id)),host=$('runs');
  for(const card of state.cards.values()){
    card.node.hidden=!visibleIds.has(card.run.id);
    card.node.classList.toggle('is-reference',card.run.id===resultView.reference);
    const pinned=card.run.id===resultView.reference,role=resultReferenceRole(card.run,pinned);card.reference.hidden=!pinned&&!role.declaredGT;card.reference.textContent=role.badge;card.reference.title=role.note;
    card.enlarge.textContent=resultView.expanded===card.run.id?'返回对比':'单列放大';
  }
  // Reparenting a card may interrupt its video decoder. CSS order pins the reference without moving media.
  for(const [index,card]of visible.entries()){card.node.style.order=index;card.node.setAttribute('aria-posinset',String(index+1));card.node.setAttribute('aria-setsize',String(visible.length));}
  host.style.setProperty('--columns',Math.max(1,Math.min(3,visible.length)));host.dataset.count=visible.length;host.dataset.many=String(visible.length>3);
  $('resultSelectionCount').textContent=`总览选列 ${resultView.selected.length}/${state.comparison.runs.length}`;
  for(const check of $('resultSelectionList').querySelectorAll('input')){check.checked=resultView.selected.includes(check.value);check.disabled=check.value===resultView.reference;}
  $('referenceRun').value=resultView.reference||'';const candidateSelect=$('candidateRun'),eligible=(state.comparison.runs||[]).filter(run=>run.id!==resultView.reference),candidateSignature=JSON.stringify(eligible.map(run=>[run.id,run.label,run.status]));if(candidateSelect&&candidateSelect.dataset.signature!==candidateSignature){candidateSelect.dataset.signature=candidateSignature;candidateSelect.replaceChildren();for(const run of eligible){const option=el('option',run.label+' · '+(STAGES[run.status]||run.status||''));option.value=run.id;candidateSelect.append(option);}}syncComparisonLayout();
}
async function changeResultSelection(selection){
  if(!state.comparison)return;
  resultView.expanded=null;
  Object.assign(resultView,normalizeResultSelection(state.comparison.runs,selection));resultView.candidate=resultCandidate(state.comparison.runs,resultView);
  saveResultPreferences();applyResultVisibility();render(true);
  if(state.metricsRows)renderMetrics(state.metricsRows);
  const token=state.pollToken;await ensureFrame(state.frame);if(token===state.pollToken)render(true);
}
function syncResultTools(value){
  if(resultView.comparison!==value.id){
    let saved;try{saved=JSON.parse(localStorage.getItem(RESULT_PREFERENCES)||'{}')[value.id];}catch(_){}
    Object.assign(resultView,normalizeResultSelection(value.runs,saved),{comparison:value.id,candidate:saved?.candidate||null,layout:saved?.layout==='overview'?'overview':'pair',pairSide:saved?.pairSide==='reference'?'reference':'candidate',expanded:null,known:value.run_ids.slice(),signature:'',protocolSignature:''});
  }else{
    const added=value.run_ids.filter(id=>!resultView.known.includes(id));
    Object.assign(resultView,normalizeResultSelection(value.runs,{selected:[...resultView.selected,...added],reference:resultView.reference}));resultView.known=value.run_ids.slice();
  }
  resultView.candidate=resultCandidate(value.runs,resultView);
  const signature=JSON.stringify(value.runs.map(run=>[run.id,run.label,!!run.result,run.result?.reference_role]));
  if(resultView.signature!==signature){
    const inspected=$('inspectRun').value;
    resultView.signature=signature;$('resultSelectionList').replaceChildren();$('referenceRun').replaceChildren();$('inspectRun').replaceChildren();
    for(const run of value.runs){
      const label=el('label'),check=el('input');check.type='checkbox';check.value=run.id;check.setAttribute('aria-label','显示 '+run.label+' '+run.id.slice(0,6));
      check.onchange=()=>changeResultSelection(toggleResultSelection(resultView,run.id,check.checked)).catch(error=>notify(error.message));
      const name=el('span',run.label),id=el('small',run.id.slice(0,6));label.append(check,name,id);
      if(run.result?.reference_role==='user_declared_ground_truth')label.append(el('small','声明 GT','role-tag'));
      $('resultSelectionList').append(label);const option=el('option',run.label+(run.result?.reference_role==='user_declared_ground_truth'?' · 声明 GT':' · 算法结果')+' · '+run.id.slice(0,6));option.value=run.id;$('referenceRun').append(option);
      if(run.result){const inspect=el('option',run.label+' · '+run.id.slice(0,6));inspect.value=run.id;$('inspectRun').append(inspect);}
    }
    if([...$('inspectRun').options].some(option=>option.value===inspected))$('inspectRun').value=inspected;
  }
  const protocols=value.runs.map(run=>({run,...runProtocol(run,value.sample)})),protocolSignature=JSON.stringify(protocols.map(value=>[value.run.id,value.short,value.rows,!!value.run.result]));
  if(resultView.protocolSignature!==protocolSignature){
    resultView.protocolSignature=protocolSignature;$('protocolList').replaceChildren();
    for(const item of protocols){
      const block=el('section',undefined,'protocol-item');block.append(el('h3',item.run.label));const list=el('dl');
      for(const [label,value]of item.rows)list.append(el('dt',label),el('dd',value));block.append(list);
      if(item.run.result){const link=el('a','查看完整来源清单');link.href=`api/comparison/runs/${encodeURIComponent(item.run.id)}/manifest`;link.target='_blank';link.rel='noopener';block.append(link);}
      $('protocolList').append(block);
    }
  }
  applyResultVisibility();
}
function initializeResultTools(){
  initializeComparisonToolbar();
  $('referenceRun').onchange=()=>changeResultSelection({selected:[...resultView.selected,$('referenceRun').value],reference:$('referenceRun').value}).catch(error=>notify(error.message));
  $('showAllResults').onclick=()=>{resultView.layout='overview';changeResultSelection({selected:state.comparison.runs.map(run=>run.id),reference:resultView.reference}).catch(error=>notify(error.message));};
  $('comparePair').onclick=()=>{resultView.layout='pair';resultView.expanded=null;saveResultPreferences();applyResultVisibility();render(true);ensureFrame(state.frame).then(()=>render(true));};
  $('metricsScope').onchange=()=>{if(state.metricsRows)renderMetrics(state.metricsRows);};
  $('manageProtocol').onclick=event=>{event.stopPropagation();setWorkspaceTab('view');$('comparisonOptions').open=true;$('protocolPicker').open=true;};
  $('manageReport').onclick=()=>$('downloadReport').click();
  $('refreshMetrics').onclick=()=>{state.metricsRows=null;state.metricRequest=null;loadComparisonMetrics().catch(error=>notify(error.message));};
  document.addEventListener('click',event=>{for(const id of ['resultPicker','protocolPicker','frameInspector']){const picker=$(id);if(picker.open&&!picker.contains(event.target))picker.open=false;}});
  document.addEventListener('keydown',event=>{if(event.key==='Escape')for(const id of ['resultPicker','protocolPicker','frameInspector'])$(id).open=false;});
}

const METRIC_GROUPS=[
  {title:'覆盖与有效性',note:'A、B 对应每行标注的结果；手槽为每帧左/右手的有效几何，存在性不用于静默剔除困难预测。',columns:[
    ['共同帧',row=>metricValue(row.common_time_frames,0)],['有效手槽 A / B',row=>`${metricValue(row.available_hand_slots_first,0)} / ${metricValue(row.available_hand_slots_second,0)}`],['共同手槽',row=>metricValue(row.paired_hand_slots,0)],['共同关节',row=>metricValue(row.paired_joints,0)],['A 缺失、B 有效',row=>metricValue(row.missing_first_given_second,0)]]},
  {title:'手部几何',note:'原始相机系、腕对齐、逐手逐帧 Sim3 结果分别报告；投影使用各自 K，仅统计正深度的共同有效关节。',columns:[
    ['相机系关节 · mm',row=>metricValue(row.camera_absolute_joint_mm)],['腕位置 · mm',row=>metricValue(row.camera_wrist_mm)],['腕对齐 · mm',row=>metricValue(row.wrist_aligned_joint_mm)],['Sim3 对齐 · mm',row=>metricValue(row.similarity_aligned_joint_mm)],['重投影 · px',row=>metricValue(row.reprojection_difference_px)]]},
  {title:'时间稳定性',note:'比较相邻共同有效样本的腕部速度，使用原始时间间隔；结果表示两份预测的速度差异。',columns:[
    ['腕速度差异 · m/s',row=>metricValue(row.camera_wrist_velocity_difference_m_s,4)]]},
  {title:'相机轨迹',note:'需两份结果均有有效世界相机。首帧对齐保留尺度差；SE3、允许拟合尺度的 Sim3 和 RPE 分开报告。— 表示不具备计算条件。',columns:[
    ['共同相机帧',row=>metricValue(row.world_camera_common_frames,0)],['首帧对齐 RMSE · m',row=>metricValue(row.first_camera_translation_rmse_m,4)],['ATE SE3 · m',row=>metricValue(row.camera_ate_se3_rmse_m,4)],['ATE Sim3 · m',row=>metricValue(row.camera_ate_sim3_rmse_m,4)],['拟合尺度 A→B',row=>metricValue(row.fitted_camera_scale,4)],['RPE 平移 · m',row=>metricValue(row.camera_rpe_translation_rmse_m,4)],['RPE 旋转 · °',row=>metricValue(row.camera_rpe_rotation_rmse_deg,3)]]}
];
function renderMetrics(rows){
  const host=$('metrics');host.replaceChildren();
  const filtered=filterMetricRows(rows,currentResultSelection(),$('metricsScope').value),names=new Map(state.comparison.runs.map(run=>[run.id,run.label]));
  const reference=names.get(resultView.reference)||'尚未选择';
  $('metricsScopeNote').textContent=filtered.some(row=>row.scope==='error_against_declared_GT')?'每组配对分别注明参考误差或算法差异。GT 为用户声明的参考来源；固定对照基准不会改变 GT 角色。':'当前显示算法之间的差异。没有声明的 GT，不据此生成精度排名；固定基准仅用于选列和数值对照。';
  const overview=el('div',undefined,'metric-overview');
  for(const [label,value]of [['当前配对',filtered.length],['已发布结果',state.comparison.runs.filter(run=>run.result).length],['对照基准',reference]]){const tile=el('div');tile.append(el('span',label),el('strong',String(value)));overview.append(tile);}host.append(overview);
  if(!filtered.length){host.append(el('p',rows.length?'当前选择不足两份有效结果。可显示更多结果，或切换“全部结果 · 两两比较”。':'至少两份有效结果完成后可比较。','empty-hint'));return;}
  for(const group of METRIC_GROUPS){
    const section=el('section',undefined,'metric-group');section.append(el('h3',group.title),el('p',group.note,'hint'));const wrap=el('div',undefined,'metric-table-scroll'),table=el('table'),thead=el('thead'),head=el('tr');head.append(el('th','结果配对 / 数值口径'));
    for(const [label]of group.columns)head.append(el('th',label));thead.append(head);table.append(thead);const body=el('tbody');
    for(const row of filtered){const tr=el('tr'),pair=el('td',undefined,'metric-pair');
      for(const [tag,id]of [['A',row.first_run],['B',row.second_run]])pair.append(el('div',`${tag} · ${names.get(id)||id} · ${id.slice(0,6)}${id===resultView.reference?' · 基准':''}`));
      pair.append(el('span',metricPairScope(row),'metric-role'));tr.append(pair);for(const [,format]of group.columns)tr.append(el('td',format(row)));body.append(tr);
    }
    table.append(body);wrap.append(table);section.append(wrap);host.append(section);
  }
}

if(typeof module!=='undefined')module.exports={defaultResultSelection,normalizeResultSelection,toggleResultSelection,metricValue,metricPairScope,filterMetricRows,handPostprocessLabel,runProtocol,resultCandidate,resultCandidateNavigation,resultStateLabel,resultReferenceRole,displayResultIds,comparisonGridPlan,comparisonMetricSelection};
