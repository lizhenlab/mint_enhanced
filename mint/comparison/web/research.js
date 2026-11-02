'use strict';

// Shared registry, quality and workflow controls all use the standard result IDs.
const researchUI={offset:0,quality:null,qualityOffset:0,accepted:new Set(),workflow:null,workflowTimer:null};
function researchField(id,label,value='',type='text'){
  const input=el(type==='textarea'?'textarea':'input');input.id=id;if(type!=='textarea')input.type=type;input.value=value;
  if(type==='textarea')input.rows=3;input.spellcheck=false;const wrapper=inputLabel(label,input);wrapper.setAttribute('for',id);return wrapper;
}
function researchButton(label,fn){const button=el('button',label);button.onclick=async()=>{if(button.dataset.busy==='true')return;button.dataset.busy='true';button.disabled=true;try{await fn();}catch(error){hubError(error);}finally{button.dataset.busy='false';button.disabled=button.dataset.locked==='true';}};return button;}
function researchOutput(host,value){$(host).textContent=typeof value==='string'?value:JSON.stringify(value,null,2);}
function researchSelect(id,label,choices){const field=selectInput(choices,choices[0]?.[0]);field.id=id;const wrapper=inputLabel(label,field);wrapper.setAttribute('for',id);return wrapper;}
function researchSection(host,title,description){const section=el('section',undefined,'panel');section.append(el('h3',title),el('p',description,'hint'));host.append(section);return section;}
function researchDetails(value){const dialog=$('logDialog');$('logText').textContent=JSON.stringify(value,null,2);dialog.showModal();}
async function loadRegistryChoices(kind,id,{filter=()=>true,empty='请选择'}={}){
  const select=$(id),serial=Number(select.dataset.requestSerial||0)+1;select.dataset.requestSerial=String(serial);
  const value=await api(`api/comparison/registry/${kind}?limit=200`);if(String(serial)!==select.dataset.requestSerial)return null;const old=select.value;select.replaceChildren();const blank=el('option',empty);blank.value='';select.append(blank);
  for(const row of value.records.filter(filter)){const option=el('option',(row.name||row.id.slice(0,12))+' · '+row.id.slice(0,8));option.value=row.id;if(row.availability==='missing_or_changed'){option.disabled=true;option.textContent+=' · 文件缺失或变化';}select.append(option);}if(old&&!Array.from(select.options).some(option=>option.value===old)){const missing=el('option','此前选择已不可用 · '+old.slice(0,12));missing.value=old;missing.disabled=true;select.append(missing);}if(old)select.value=old;return value;
}
async function refreshArtifactCatalog(){
  researchUI.catalogSerial=(researchUI.catalogSerial||0)+1;
  const kind=$('registryKind').value,query=new URLSearchParams({query:$('registryQuery').value,offset:researchUI.offset,limit:30});
  const context=catalogNavigation(),current=()=>catalogNavigationCurrent(context),host=$('artifactRows');host.replaceChildren(el('p','正在读取目录…','hint'));host.setAttribute('aria-busy','true');$('registryPage').textContent='正在读取…';$('registryPrevious').disabled=true;$('registryNext').disabled=true;
  let value;try{value=await api(`api/comparison/registry/${kind}?${query}`);}catch(error){if(!current())return null;host.replaceChildren(el('p','目录读取失败，请重试：'+(error.message||String(error)),'hint'),researchButton('重试读取',refreshArtifactCatalog));host.setAttribute('aria-busy','false');$('registryPage').textContent='读取失败，请重试';return null;}
  if(!current())return null;host.replaceChildren();
  const table=el('table'),head=el('tr');for(const text of ['产物 / 来源','类型与状态','操作'])head.append(el('th',text));table.append(head);
  for(const row of value.records){const tr=el('tr'),name=el('td'),actions=el('td');name.append(el('strong',row.name||row.id.slice(0,12)),el('small',row.source?.path||row.path||row.root||row.run_id||row.id));
    tr.append(name,el('td',[row.type||row.kind,row.frames?`${row.frames} 帧`:'',row.availability==='missing_or_changed'?'文件缺失或变化':row.status||row.origin||''].filter(Boolean).join(' · ')));
    actions.append(researchButton('来源详情',()=>researchDetails(row)));
    if(row.run_id)actions.append(researchButton('打开结果',()=>openCatalogResult(row)));
    if(row.importable&&row.availability!=='missing_or_changed')actions.append(researchButton('导入已有数值',()=>importCatalogArtifact(row)));
    if(row.availability!=='missing_or_changed'){
      if(row.kind==='model')actions.append(researchButton('用于 MINT 比较',()=>useRegisteredModel(row)));
      if(row.kind==='dataset'){
        if(row.native)actions.append(researchButton('用此样本配置比较',()=>useNativeDataset(row)));
        if(row.training_ready)actions.append(researchButton('用此版本配置训练',()=>useTrainingDataset(row)));
        if(row.status==='reviewed_manifest')actions.append(researchButton('配置复核数据导出',()=>useReviewedDataset(row)));
      }
      if(row.kind==='protocol')actions.append(researchButton('配置离线评测',()=>useSavedProtocol(row)),researchButton('配置固定集回归',()=>useSavedProtocol(row,'regression')));
      if(row.kind==='evaluation'){
        if(row.protocol_id)actions.append(researchButton('使用所属协议',()=>useEvaluationProtocol(row)));
        if(row.rows?.some(result=>result.origin==='native'&&result.model))actions.append(researchButton('配置模型发布',()=>useEvaluationReport(row)));
      }
    }
    if(row.kind==='review'&&row.decision==='accepted'){const check=el('input');check.type='checkbox';check.checked=researchUI.accepted.has(row.id);check.onchange=()=>{if(check.checked)researchUI.accepted.add(row.id);else researchUI.accepted.delete(row.id);};actions.append(inputLabel('纳入数据版本',check));}
    tr.append(actions);table.append(tr);
  }
  host.append(table);host.setAttribute('aria-busy','false');$('registryPage').textContent=`${value.total} 项 · ${value.total?value.offset+1:0}–${Math.min(value.total,value.offset+value.records.length)}`;$('registryPrevious').disabled=value.offset===0;$('registryNext').disabled=value.offset+value.records.length>=value.total;
}
function catalogNavigation(){return JSON.stringify([platform.serial,researchUI.catalogSerial,$('registryKind').value,$('registryQuery').value]);}
function catalogNavigationCurrent(context){return $('platformDialog').open&&platform.pane==='catalog'&&context===catalogNavigation();}
async function openCatalogResult(row){
  const context=catalogNavigation(),parents=await api(`api/comparison/runs/${row.run_id}/parent`);if(!catalogNavigationCurrent(context))return null;
  if(!parents.comparisons.length)throw new Error('此结果尚未附加到比较');const target=parents.comparisons[0];closePlatformWorkspace();const navigation=platform.serial;await openComparison(target);
  if(navigation===platform.serial&&!$('platformDialog').open&&state.comparison?.id===target)inspectResult(row.run_id);return target;
}
async function importCatalogArtifact(row){
  const context=catalogNavigation(),comparison=await api(`api/comparison/registry/artifacts/${row.id}/import`,{});
  if(!catalogNavigationCurrent(context)){researchSubmissionNotice('已有数值已导入，当前页面已保留；可从比较记录查看。');return comparison;}
  closePlatformWorkspace();await openComparison(comparison.id);return comparison;
}
function useRegisteredModel(model,configuration=model.config){
  if(model.availability==='missing_or_changed')throw new Error('此模型文件缺失或已变化，请重新登记');
  closePlatformWorkspace();openTaskDialog();const box=$('algorithmOptions').querySelector('[data-algorithm=mint]');
  box.querySelector('input[type=checkbox]').checked=true;const checkpoint=box.querySelector('[data-selection=checkpoint]');checkpoint.value=model.path;box.querySelector('[data-selection=config]').value=configuration?.path||'';
  const details=checkpoint.closest('details');if(details)details.open=true;invalidateComparisonPreflight();if(typeof updateTaskProtocolSummary==='function')updateTaskProtocolSummary();
}
function chooseRegistryRecord(id,row){
  const select=$(id);let option=Array.from(select.options).find(value=>value.value===row.id);
  if(option?.disabled&&option.textContent.startsWith('此前选择已不可用 · ')&&row.availability!=='missing_or_changed'){option.remove();option=null;}
  if(!option){option=el('option',(row.name||row.id.slice(0,12))+' · '+row.id.slice(0,8));option.value=row.id;select.append(option);}
  if(row.availability==='missing_or_changed'||option.disabled)throw new Error('所选产物已不可用，请刷新目录');select.value=row.id;
}
async function useSavedProtocol(protocol,view='protocol'){
  await openPlatform('research');showResearchView(view);const navigation=platform.serial,id=view==='regression'?'suiteProtocol':'offlineProtocol',selected=$(id).value;
  await loadRegistryChoices('protocol',id);if(navigation!==platform.serial||platform.pane!=='research'||!$('platformDialog').open||nextUI.researchView!==view||selected!==$(id).value)return null;
  chooseRegistryRecord(id,protocol);const splits=[...new Set((protocol.entries||[]).map(entry=>entry.split))];if(splits.length===1&&['validation','test'].includes(splits[0]))$(view==='regression'?'suiteSplit':'offlineSplit').value=splits[0];
  if(view!=='regression'){
    const included=!!state.comparison?.sample_id&&(protocol.entries||[]).some(entry=>entry.split===$('offlineSplit').value&&entry.sample_id===state.comparison.sample_id);
    researchOutput('fixedEvaluationStatus','已选择固定协议：'+(protocol.name||protocol.id)+'。'+(included?'离线评测仅处理当前比较已有的预测；评测协议全部输入请使用固定集回归。':'当前比较不属于此协议划分；请先打开协议对应输入，或使用固定集回归处理全部输入。'));
  }
  revealPlatformTarget($(id));return protocol;
}
async function useEvaluationProtocol(report){
  const context=catalogNavigation(),protocol=await api('api/comparison/registry/record/'+report.protocol_id);if(!catalogNavigationCurrent(context))return null;return useSavedProtocol(protocol);
}
async function useEvaluationReport(report){
  await openPlatform('research');showResearchView('models');const navigation=platform.serial,selected=$('modelEvaluation').value;
  await loadRegistryChoices('evaluation','modelEvaluation');if(navigation!==platform.serial||platform.pane!=='research'||!$('platformDialog').open||nextUI.researchView!=='models'||selected!==$('modelEvaluation').value)return null;
  chooseRegistryRecord('modelEvaluation',report);updateModelActions();$('modelActionSummary').textContent='已选择评测报告 '+(report.name||report.id)+'；请选择匹配的模型版本并核对发布门槛。';revealPlatformTarget($('modelEvaluation'));return report;
}
async function publishReviewedDataset(){
  const context=catalogNavigation(),value=await api('api/comparison/registry/dataset',{name:$('datasetVersionName').value,review_ids:[...researchUI.accepted]});
  if(!catalogNavigationCurrent(context)){researchSubmissionNotice('复核清单已保存，当前页面已保留；可从数据版本目录配置导出。');return value;}
  const host=$('datasetVersionStatus');host.replaceChildren(el('span',`已保存 ${value.entries.length} 个片段 · ${value.id}。此清单尚未转换为训练格式。`),researchButton('用此清单配置复核数据导出',()=>useReviewedDataset(value)));return value;
}
function initializeCatalog(host){
  const bar=el('div',undefined,'hub-filters');bar.append(researchSelect('registryKind','产物类别',[['artifact','结果与已保存预测'],['protocol','固定评测协议'],['evaluation','离线评测报告'],['review','人工复核记录'],['dataset','数据版本'],['model','模型版本'],['experiment','加速 / 融合实验'],['deployment','多机配置']]),researchField('registryQuery','搜索名称、来源或标识'),researchButton('刷新目录',()=>{researchUI.offset=0;return refreshArtifactCatalog();}));host.append(bar);
  const rows=el('div',undefined,'hub-table');rows.id='artifactRows';host.append(rows);const page=el('div',undefined,'button-row'),info=el('span');info.id='registryPage';const prev=researchButton('上一页',()=>{researchUI.offset=Math.max(0,researchUI.offset-30);return refreshArtifactCatalog();});prev.id='registryPrevious';const next=researchButton('下一页',()=>{researchUI.offset+=30;return refreshArtifactCatalog();});next.id='registryNext';page.append(prev,info,next);host.append(page);
  $('registryKind').onchange=()=>{researchUI.offset=0;refreshArtifactCatalog().catch(hubError);};
  const version=researchSection(host,'发布复核数据版本','在“人工复核记录”中勾选已接受的记录，再发布不可变筛选清单。预测标签不会自动变为 GT。');version.append(researchField('datasetVersionName','数据版本名称','reviewed-camera-v1'),researchButton('发布已选复核清单',publishReviewedDataset));const status=el('p',undefined,'hint');status.id='datasetVersionStatus';version.append(status);
  const offline=el('details',undefined,'hub-secondary');offline.append(el('summary','登记离线预测文件'));const form=el('div',undefined,'composer-fields');form.append(researchField('offlineName','名称'),researchField('offlineSource','原始服务器视频路径'),researchSelect('offlineAlgorithm','原生格式所属算法',[['mint','MINT'],['hawor_full','HaWoR Full'],['ace_kfree','ACE K-free'],['reference','Reference GT']]),researchField('offlinePath','已有数值文件绝对路径'),researchSelect('offlineFormat','数值格式',[['mint_npz','MINT NPZ'],['hawor_world_npz','HaWoR world NPZ'],['ace_pickle','ACE native pickle'],['normalized_npz','标准 NPZ']]),researchField('offlineMap','样本位置帧映射 JSON','[0,1,2]'),researchField('offlineProvenance','来源 / 坐标 / 单位声明'),researchButton('登记文件与映射',async()=>{await api('api/comparison/registry/artifact',{name:$('offlineName').value||undefined,source:$('offlineSource').value,algorithm:$('offlineAlgorithm').value,import_entry:{path:$('offlinePath').value,format:$('offlineFormat').value,frame_map:JSON.parse($('offlineMap').value),declared_source:$('offlineProvenance').value}});await refreshArtifactCatalog();}));offline.append(form);host.append(offline);
}
function qualityComparisonData(){
  const chosen=$('qualityComparison')?.value,loaded=researchUI.qualityComparison;
  if(loaded&&loaded.id===chosen)return loaded;
  const current=typeof state==='undefined'?null:state.comparison;return !researchUI.qualitySelectionExplicit&&current&&(!chosen||chosen===current.id)?current:null;
}
function qualityContextSignature(){const value=qualityComparisonData();return JSON.stringify([value?.id,value?.sample_id,$('qualityRun').value,$('qualityReference').value]);}
function qualityAnalysisSignature(){return JSON.stringify([qualityContextSignature(),$('qualityThreshold').value,$('qualityAcceleration').value]);}
function qualityDeclaredGT(run){return run?.result?.reference_role==='user_declared_ground_truth';}
function qualityLock(id,locked){const button=$(id);if(!button)return;button.dataset.locked=String(locked);button.disabled=locked||button.dataset.busy==='true';}
function invalidateQualityAnalysis({annotation=false}={}){
  researchUI.qualityRequestSerial=(researchUI.qualityRequestSerial||0)+1;researchUI.qualityPageSerial=(researchUI.qualityPageSerial||0)+1;
  researchUI.quality=null;researchUI.qualitySignature=null;researchUI.qualityOffset=0;
  for(const id of ['qualityIssues','qualitySummaryCards','breakdownRows','breakdownAdvancedRows','jointRows'])$(id)?.replaceChildren();
  $('qualityCanvas').hidden=true;$('qualityDownload').hidden=true;$('qualitySummary').textContent='选择结果后，点击“查找问题片段”。分析使用已有几何结果。';
  $('qualityEmpty').hidden=false;$('qualityPrevious').disabled=true;$('qualityNext').disabled=true;
  if(typeof nextUI!=='undefined'){nextUI.breakdown=null;nextUI.breakdownSerial=(nextUI.breakdownSerial||0)+1;}
  if($('breakdownSummary'))$('breakdownSummary').textContent='选择参考或对照后，可查看更详细的差异。';
  if(annotation){$('reviewStatus').textContent='';$('reviewComment').value='';$('reviewStart').value='1';$('reviewEnd').value='1';if(typeof invalidateAnnotationContext==='function')invalidateAnnotationContext();}
  updateQualityActions();
}
function updateQualityActions(){
  const comparison=qualityComparisonData(),runs=(comparison?.runs||[]).filter(run=>run.result),prediction=runs.find(run=>run.id===$('qualityRun').value&&!qualityDeclaredGT(run)),reference=runs.find(run=>run.id===$('qualityReference').value),ready=!!prediction&&!researchUI.qualitySourceLoading;
  qualityLock('qualityLoadComparison',!$('qualityComparison').value||!!researchUI.qualitySourceLoading);qualityLock('qualityAnalyze',!ready||!!reference&&reference.id===prediction.id);
  qualityLock('qualityBreakdownAnalyze',!ready||!reference||reference.id===prediction.id);qualityLock('qualityBreakdownDownload',!(typeof nextUI!=='undefined'&&nextUI.breakdown));
  qualityLock('qualitySaveReview',!researchUI.quality||researchUI.qualitySignature!==qualityAnalysisSignature());
  qualityLock('qualityViewComparison',!comparison);qualityLock('annotationReadFrame',!ready);qualityLock('annotationReadPlaying',!ready||state.comparison?.sample_id!==comparison?.sample_id);
  const role=reference?(qualityDeclaredGT(reference)?'参考：声明的 GT':'对照：另一份算法结果'):'未选参考：仅检查缺失、存在性和运动线索';
  $('qualityContextSummary').textContent=comparison?`${comparison.title||comparison.id} · ${prediction?prediction.label:'暂无可用预测'} · ${role}`:$('qualityComparison').value?'尚未读取所选比较，请点击“读取”。':'请选择一组已有比较，或先运行推理。';
  if(typeof updateEvaluationNavigation==='function')updateEvaluationNavigation();
}
function qualityRunChoices({preferredRun=null,preserveAnnotation=false}={}){
  const comparison=qualityComparisonData(),runs=(comparison?.runs||[]).filter(run=>run.result),prediction=$('qualityRun'),oldPrediction=preferredRun||prediction.value,oldReference=$('qualityReference').value;
  prediction.replaceChildren();const empty=el('option','请选择预测结果');empty.value='';prediction.append(empty);
  for(const run of runs.filter(run=>!qualityDeclaredGT(run))){const option=el('option',run.label);option.value=run.id;prediction.append(option);}
  prediction.value=runs.some(run=>run.id===oldPrediction&&!qualityDeclaredGT(run))?oldPrediction:runs.find(run=>!qualityDeclaredGT(run))?.id||'';
  const reference=$('qualityReference');reference.replaceChildren();const none=el('option','不使用参考');none.value='';reference.append(none);
  for(const run of runs.filter(run=>run.id!==prediction.value)){const option=el('option',(qualityDeclaredGT(run)?'GT · ':'算法对照 · ')+run.label);option.value=run.id;reference.append(option);}
  reference.value=researchUI.qualityReferenceExplicit&&oldReference===''?'':runs.some(run=>run.id===oldReference&&run.id!==prediction.value)?oldReference:runs.find(run=>qualityDeclaredGT(run)&&run.id!==prediction.value)?.id||'';
  for(const [id,source]of [['detailRun',prediction],['detailReference',reference]]){const select=$(id);if(!select)continue;select.replaceChildren();for(const option of source.options){const copy=el('option',option.textContent);copy.value=option.value;select.append(copy);}select.value=source.value;}
  const key=qualityContextSignature();if(key!==researchUI.qualitySelectionKey){researchUI.qualitySelectionKey=key;invalidateQualityAnalysis({annotation:!preserveAnnotation});if(!preserveAnnotation&&$('annotationFrame'))$('annotationFrame').value=String(comparison?.sample_id===state.comparison?.sample_id?(state.frame||0)+1:1);}
  updateQualityActions();
}
function setQualityComparison(comparison,{preferredRun=null,preserveAnnotation=false}={}){
  if(comparison?.id!==researchUI.qualityComparison?.id&&!preserveAnnotation)researchUI.qualityReferenceExplicit=false;
  researchUI.qualityComparison=comparison;researchUI.qualitySelectionExplicit=true;const select=$('qualityComparison');
  if(comparison&&!Array.from(select.options).some(option=>option.value===comparison.id)){const option=el('option',comparison.title||comparison.id);option.value=comparison.id;select.append(option);}
  select.value=comparison?.id||'';qualityRunChoices({preferredRun,preserveAnnotation});
}
async function refreshQualityContext(){
  const serial=researchUI.qualityHistorySerial=(researchUI.qualityHistorySerial||0)+1,selected=$('qualityComparison').value;
  const history=await api('api/comparisons?limit=200');if(serial!==researchUI.qualityHistorySerial||selected!==$('qualityComparison').value)return;
  const select=$('qualityComparison'),current=researchUI.qualityComparison||(!researchUI.qualitySelectionExplicit?state.comparison:null),rows=[...history.comparisons];if(current&&!rows.some(row=>row.id===current.id))rows.unshift(current);
  select.replaceChildren();const empty=el('option','请选择已有比较');empty.value='';select.append(empty);for(const row of rows){const option=el('option',row.title+' · '+row.id.slice(0,8));option.value=row.id;select.append(option);}
  select.value=rows.some(row=>row.id===selected)?selected:current?.id||'';if(!researchUI.qualityComparison&&current&&select.value===current.id)researchUI.qualityComparison=current;
  qualityRunChoices();
}
async function loadQualityComparison(){
  const id=$('qualityComparison').value;if(!id)throw new Error('请先选择比较');const serial=researchUI.qualityLoadSerial=(researchUI.qualityLoadSerial||0)+1;
  invalidateQualityAnalysis({annotation:true});researchUI.qualitySourceLoading=true;updateQualityActions();
  try{const value=await api('api/comparisons/'+id);if(serial!==researchUI.qualityLoadSerial||id!==$('qualityComparison').value)return null;researchUI.qualitySourceLoading=false;setQualityComparison(value);return value;}
  finally{if(serial===researchUI.qualityLoadSerial){researchUI.qualitySourceLoading=false;updateQualityActions();}}
}
function showQualityView(view){
  if(!['issues','breakdown','annotation'].includes(view))view='issues';researchUI.qualityView=view;
  for(const section of $('hub-quality').querySelectorAll('[data-quality-view]'))section.hidden=section.dataset.qualityView!==view;
  for(const button of $('hub-quality').querySelectorAll('[data-quality-tab]')){const active=button.dataset.qualityTab===view;button.classList.toggle('active',active);button.setAttribute('aria-current',active?'page':'false');}
  updateQualityActions();
}
async function analyzeQuality(){
  const comparison=qualityComparisonData(),run=$('qualityRun').value,reference=$('qualityReference').value;if(!comparison||!run)throw new Error('请先读取比较并选择预测结果');if(run===reference)throw new Error('预测和参考必须是不同结果');
  const joint=Number($('qualityThreshold').value),acceleration=Number($('qualityAcceleration').value);if(!$('qualityThreshold').value.trim()||!$('qualityAcceleration').value.trim()||!Number.isFinite(joint)||joint<=0||!Number.isFinite(acceleration)||acceleration<=0)throw new Error('候选阈值需为有限正数');
  invalidateQualityAnalysis();const signature=qualityAnalysisSignature(),serial=researchUI.qualityRequestSerial;
  const value=await api('api/comparison/quality',{run_id:run,reference_run:reference||null,joint_mm:joint,acceleration});if(serial!==researchUI.qualityRequestSerial||signature!==qualityAnalysisSignature())return null;
  researchUI.qualitySignature=signature;researchUI.qualityOffset=0;await showQuality(value);return value;
}
async function loadQualityPage(direction){
  const quality=researchUI.quality;if(!quality||researchUI.qualitySignature!==qualityAnalysisSignature())return;const id=quality.id,signature=researchUI.qualitySignature,serial=researchUI.qualityPageSerial=(researchUI.qualityPageSerial||0)+1,offset=Math.max(0,quality.offset+direction*50);
  const value=await api(`api/comparison/quality/${id}?offset=${offset}`);if(serial!==researchUI.qualityPageSerial||id!==researchUI.quality?.id||signature!==qualityAnalysisSignature())return;researchUI.qualityOffset=value.offset;await showQuality(value);
}
function drawQualityCurve(quality){
  const select=$('qualityCurveMetric'),labels={joint_difference_mm:quality.comparison_role==='declared_GT_error'?'关节参考误差 · mm':'关节差异 · mm',wrist_acceleration_m_s2:'腕加速度 · m/s²',reprojection_difference_px:'重投影差异 · px'};
  for(const option of select.options){option.textContent=labels[option.value];option.disabled=!quality.curves.some(point=>Number.isFinite(point[option.value]));}
  if(select.selectedOptions[0]?.disabled)select.value=Array.from(select.options).find(option=>!option.disabled)?.value||'wrist_acceleration_m_s2';
  const key=select.value,points=quality.curves.filter(point=>Number.isFinite(point[key])),canvas=$('qualityCanvas'),ctx=canvas.getContext('2d'),width=1000,height=190;canvas.width=width;canvas.height=height;ctx.fillStyle='#0b1713';ctx.fillRect(0,0,width,height);ctx.fillStyle='#b9f65f';
  if(!points.length){ctx.fillText('当前指标没有有效值',25,35);canvas.onclick=null;return;}const maximum=Math.max(1,...points.map(point=>point[key]));ctx.fillText(`${labels[key]} · 0–${maximum.toFixed(1)}`,25,20);ctx.strokeStyle='#b9f65f';ctx.beginPath();let drawing=false;
  for(const point of quality.curves){if(!Number.isFinite(point[key])){drawing=false;continue;}const position=point.peaks?.[key]?.frame??point.frame,x=32+position/Math.max(1,quality.frames-1)*(width-48),y=height-24-point[key]/maximum*(height-55);if(drawing)ctx.lineTo(x,y);else ctx.moveTo(x,y);drawing=true;}ctx.stroke();
  canvas.onclick=event=>{const box=canvas.getBoundingClientRect(),position=Math.max(0,Math.min(quality.frames-1,((event.clientX-box.left)/box.width*width-32)/(width-48)*(quality.frames-1))),point=points.reduce((best,row)=>Math.abs((row.peaks?.[key]?.frame??row.frame)-position)<Math.abs((best.peaks?.[key]?.frame??best.frame)-position)?row:best);jumpQualityFrame(point.peaks?.[key]?.frame??point.frame).catch(hubError);};
}
async function jumpQualityFrame(frame){
  const quality=researchUI.quality,comparison=qualityComparisonData();if(!quality||researchUI.qualitySignature!==qualityAnalysisSignature())throw new Error('请先分析当前所选结果');const id=quality.id,run=quality.run_id;
  let target=comparison?.runs.some(row=>row.id===run)?comparison.id:null;if(!target){const parents=await api(`api/comparison/runs/${run}/parent`);if(id!==researchUI.quality?.id)return;if(!parents.comparisons.length)throw new Error('此结果尚未附加到可查看的比较');target=parents.comparisons[0];}
  closePlatformWorkspace();const navigation=platform.serial;if(state.comparison?.id!==target)await openComparison(target);if(state.comparison?.id!==target||(navigation!==platform.serial&&$('platformDialog').open)||id!==researchUI.quality?.id)return;
  setWorkspaceTab('view');inspectResult(run);seek(Math.max(0,Math.min(quality.frames-1,frame)));
}
async function showQuality(quality){
  if(quality.run_id!==$('qualityRun').value||(quality.identity?.reference_run||null)!==($('qualityReference').value||null))return null;
  researchUI.quality=quality;researchUI.qualityOffset=quality.offset||0;drawQualityCurve(quality);
  const cards=$('qualitySummaryCards');cards.replaceChildren();for(const [label,value]of [['样本帧数',quality.frames],['复核条目',quality.issue_count],['缺失输出帧',quality.missing_output_frames??'—'],['参考用途',quality.comparison_role==='declared_GT_error'?'GT 参考误差':$('qualityReference').value?'模型间差异':'无参考质检']]){const card=el('div');card.append(el('small',label),el('strong',String(value)));cards.append(card);}
  $('qualitySummary').textContent='以下为复核候选，不等同于已确认错误；不同原因的区间可能重叠。'+(quality.paired_frames===0&&$('qualityReference').value?' 当前没有共同有效的手部参考帧。':'');
  $('qualityDownload').href=`api/comparison/quality/${quality.id}/download`;$('qualityDownload').hidden=false;$('qualityCanvas').hidden=false;$('qualityEmpty').hidden=quality.issue_count>0;$('qualityEmpty').textContent='当前阈值下未发现候选条目，可展开曲线或调整高级阈值。';
  const rows=$('qualityIssues');rows.replaceChildren();const labels={missing_output_frame:'缺失预测输出',no_visible_geometry:'没有有效手部几何',low_presence_prediction:'手部存在性判断偏低',joint_error_against_declared_GT:'关节参考误差偏大',joint_discrepancy:'算法关节差异偏大',motion_acceleration_candidate:'腕部加速度突变'};
  for(const issue of quality.issues){const row=el('div',undefined,'quality-issue');row.append(el('span',`${issue.start+1}–${issue.end} 帧 · ${labels[issue.reason]||issue.reason}`),researchButton('查看',()=>jumpQualityFrame(issue.start)),researchButton('复核',()=>{$('reviewStart').value=issue.start+1;$('reviewEnd').value=issue.end;$('qualityReviewDetails').open=true;$('qualityReviewDetails').scrollIntoView({block:'nearest'});}));rows.append(row);}
  $('qualityPrevious').disabled=quality.offset===0;$('qualityNext').disabled=quality.offset+quality.issues.length>=quality.issue_count;$('reviewStart').max=quality.frames;$('reviewEnd').max=quality.frames;updateQualityActions();return quality;
}
async function saveQualityReview(){
  const quality=researchUI.quality,signature=qualityAnalysisSignature();if(!quality||researchUI.qualitySignature!==signature)throw new Error('请先分析当前所选结果');
  const start=Number($('reviewStart').value),end=Number($('reviewEnd').value),reviewer=$('reviewer').value.trim();if(!Number.isInteger(start)||!Number.isInteger(end)||start<1||end<start||end>quality.frames||!reviewer)throw new Error('请填写审阅人，并选择有效的样本帧区间');
  const value=await api('api/comparison/registry/review',{quality_id:quality.id,start:start-1,end,decision:$('reviewDecision').value,reviewer,comment:$('reviewComment').value});if(value.decision==='accepted')researchUI.accepted.add(value.id);if(signature===qualityAnalysisSignature()&&quality.id===researchUI.quality?.id)researchOutput('reviewStatus','复核记录已保存。可在数据与结果目录中查看。');
}
function initializeQuality(host){
  host.classList.add('quality-workspace');const source=el('section',undefined,'quality-source-card'),fields=el('div',undefined,'quality-context-fields');
  const load=researchButton('读取',loadQualityComparison);load.id='qualityLoadComparison';fields.append(researchSelect('qualityComparison','选择已有比较',[['','请选择已有比较']]),load,researchSelect('qualityRun','预测结果',[['','请选择预测结果']]),researchSelect('qualityReference','参考 / 对照（可选）',[['','不使用参考']]));source.append(fields);const context=el('p',undefined,'quality-context-summary');context.id='qualityContextSummary';source.append(context);
  const view=researchButton('查看这组比较',async()=>{const comparison=qualityComparisonData();if(!comparison)throw new Error('请先读取比较');closePlatformWorkspace();await openComparison(comparison.id);});view.id='qualityViewComparison';source.append(view);host.append(source);
  const nav=el('nav',undefined,'quality-view-tabs');nav.setAttribute('aria-label','质检工作区');for(const [name,label]of [['issues','问题片段'],['breakdown','误差明细'],['annotation','二维修正']]){const button=el('button',label);button.dataset.qualityTab=name;button.onclick=()=>showQualityView(name);nav.append(button);}host.append(nav);
  const section=el('section',undefined,'quality-panel');section.id='qualityIssuesSection';section.dataset.qualityView='issues';const actions=el('div',undefined,'quality-actions'),analyze=researchButton('查找问题片段',analyzeQuality);analyze.id='qualityAnalyze';actions.append(analyze,el('span','使用已有几何，不重新推理。','hint'));section.append(actions);
  const thresholds=el('details',undefined,'quality-advanced');thresholds.append(el('summary','高级：候选阈值'));const thresholdFields=el('div',undefined,'composer-fields');thresholdFields.append(researchField('qualityThreshold','关节差异阈值 mm',100,'number'),researchField('qualityAcceleration','腕加速度阈值 m/s²',30,'number'));thresholds.append(thresholdFields);section.append(thresholds);
  const cards=el('div',undefined,'quality-summary-grid');cards.id='qualitySummaryCards';const summary=el('p',undefined,'hint');summary.id='qualitySummary';const empty=el('p','选择结果后，查找需要复核的片段。','quality-empty');empty.id='qualityEmpty';const issues=el('div',undefined,'quality-issues-list');issues.id='qualityIssues';issues.tabIndex=0;issues.setAttribute('aria-label','复核候选列表');section.append(cards,summary,empty,issues);
  const pages=el('div',undefined,'quality-actions');for(const [id,label,direction]of [['qualityPrevious','上一页',-1],['qualityNext','下一页',1]]){const button=researchButton(label,()=>loadQualityPage(direction));button.id=id;button.disabled=true;pages.append(button);}section.append(pages);
  const curves=el('details',undefined,'quality-curve-details');curves.append(el('summary','查看逐帧曲线'),researchSelect('qualityCurveMetric','曲线指标',[['joint_difference_mm','关节差异 · mm'],['wrist_acceleration_m_s2','腕加速度 · m/s²'],['reprojection_difference_px','重投影差异 · px']]));const canvas=el('canvas');canvas.id='qualityCanvas';canvas.hidden=true;canvas.setAttribute('aria-label','单指标逐帧曲线');const link=el('a','下载完整逐帧曲线');link.id='qualityDownload';link.hidden=true;curves.append(canvas,link);section.append(curves);
  const review=el('details',undefined,'quality-advanced');review.id='qualityReviewDetails';review.append(el('summary','保存人工复核记录'),el('p','按样本位置从 1 计数，结束帧包含在内；复核决定不会自动生成 GT。','hint'));const form=el('div',undefined,'composer-fields');form.append(researchField('reviewStart','起始样本帧',1,'number'),researchField('reviewEnd','结束样本帧',1,'number'),researchField('reviewer','审阅人'),researchSelect('reviewDecision','决定',[['accepted','接受'],['rejected','拒绝'],['needs_label','需要补标']]),researchField('reviewComment','复核备注'));const save=researchButton('保存复核记录',saveQualityReview);save.id='qualitySaveReview';const status=el('p',undefined,'hint');status.id='reviewStatus';review.append(form,save,status);section.append(review);host.append(section);
  $('qualityComparison').onchange=()=>{researchUI.qualitySelectionExplicit=true;researchUI.qualityComparison=null;researchUI.qualityLoadSerial=(researchUI.qualityLoadSerial||0)+1;researchUI.qualitySourceLoading=false;invalidateQualityAnalysis({annotation:true});qualityRunChoices();};
  $('qualityRun').onchange=()=>qualityRunChoices();$('qualityReference').onchange=()=>{researchUI.qualityReferenceExplicit=true;qualityRunChoices();};for(const id of ['qualityThreshold','qualityAcceleration'])$(id).oninput=()=>invalidateQualityAnalysis();$('qualityCurveMetric').onchange=()=>{if(researchUI.quality)drawQualityCurve(researchUI.quality);};
}
function initializeQualityWorkspace(){
  $('annotationFrame').oninput=()=>invalidateAnnotationContext();$('annotationFrame').onchange=()=>invalidateAnnotationContext();
  qualityRunChoices();showQualityView(researchUI.qualityView||'issues');
}
function configureEvaluationComposer(){
  const host=$('evaluationComposer');if(!host||host.dataset.simplified==='true')return;host.dataset.simplified='true';
  $('hub-evaluation').classList.add('evaluation-workspace');$('evaluation-add-video').hidden=true;$('evaluation-add-episode').textContent='选择有 GT 的 Episode';
  $('evaluation-draft-mode').textContent='配置新评测';$('evaluation-results-mode').textContent='查看本次评测';
  for(const [key,label]of [['inputs','1 · 选择评测样本'],['settings','2 · 选择评测算法'],['review','3 · 预检并提交']]){$('evaluation-step-nav-'+key).textContent=label;$('evaluation-heading-'+key).textContent=label;}
  $('evaluation-step-inputs').querySelector('.collection-step-heading p').textContent='每个样本必须有可用 GT；无 GT 的视频可使用批量比较。';
  $('evaluation-step-settings').querySelector('.collection-step-heading p').textContent='所选算法使用相同样本与帧范围，参考标注仅用于评分。';
  $('evaluation-step-review').querySelector('.collection-step-heading p').textContent='核对样本、参考标注与警告后，再提交后台评测。';
  $('evaluation-check').textContent='预检评测并查看概览';
  const settings=$('evaluation-step-settings'),defaults=el('p','默认评测完整片段，必须有参考标注；GT 不会自动作为算法输入内参。','evaluation-basic-note');settings.querySelector('.collection-step-heading').after(defaults);
  const advanced=el('details',undefined,'evaluation-advanced');advanced.id='evaluation-advanced-settings';advanced.append(el('summary','高级：帧范围与相机标定'));
  const fields=settings.querySelector('.collection-protocol-fields'),help=fields.nextElementSibling,options=settings.querySelector('.collection-option-fields');fields.before(advanced);advanced.append(fields);if(help?.classList.contains('hint'))advanced.append(help);advanced.append(options,$('evaluation-manual-settings'),$('evaluation-protocol-note'));
  $('evaluation-reference').closest('label').hidden=true;const algorithms=$('evaluation-algorithms');for(const box of algorithms.querySelectorAll('.algorithm'))box.querySelector('input[type=checkbox]').checked=box.dataset.algorithm==='mint';
  const explanation=settings.querySelector('.collection-algorithm-settings>summary');if(explanation)explanation.textContent='选择算法与检查点';
}
function updateEvaluationNavigation(){
  const target=platform.pane==='quality'?qualityComparisonData():state.comparison,button=document.querySelector('[data-evaluation-route=current]');if(!button)return;
  button.hidden=!(target?.runs||[]).some(run=>run.result);button.title=target?.title||'';
}
async function openEvaluationCurrentMetrics(){
  const target=platform.pane==='quality'?qualityComparisonData():state.comparison;if(!target)throw new Error('请先选择一组已有比较');closePlatformWorkspace();if(state.comparison?.id!==target.id)await openComparison(target.id);if(state.comparison?.id!==target.id)return;setWorkspaceTab('metrics');
}
function workflowParameters(){
  const kind=$('workflowKind').value;
  if(kind==='diagnostic')return {profile:$('workflowProfile').value};
  if(kind==='prepare_training')return {source:$('workflowSource').value,episodes:$('workflowEpisodes').value.split(',').filter(Boolean).map(Number),...($('workflowFrames').value?{max_frames:Number($('workflowFrames').value)}:{})};
  if(kind==='curate_dataset')return {dataset_id:$('workflowDataset').value};
  if(kind==='pipeline')return {inputs:$('workflowSource').value.split('\n').filter(Boolean),gpus:Number($('workflowGpus').value),frame_stride:1};
  return {config:$('workflowConfig').value,allow_pseudo_labels:$('workflowPseudoOptIn').checked,dataset_id:$('workflowDataset').value||undefined,init_from:$('workflowInit').value||undefined,resume_checkpoint:$('workflowResumePath').value||undefined,gpus:Number($('workflowGpus').value),...($('workflowSteps').value?{max_steps:Number($('workflowSteps').value)}:{})};
}
function updateWorkflowFields(){const kind=$('workflowKind').value;for(const row of document.querySelectorAll('[data-workflow-kinds]'))row.hidden=!row.dataset.workflowKinds.split(' ').includes(kind);invalidateWorkflowReadiness();if(kind==='pipeline')checkWorkflowReadiness().catch(hubError);refreshWorkflowDatasetChoices().catch(hubError);}
function workflowSummaryText(value){const kinds={train:'MINT 训练',prepare_training:'标注数据转换',curate_dataset:'复核数据导出',produce_pseudo:'原生伪标签生产',native_import:'公开数据接入',pipeline:'参考研究链',diagnostic:'环境诊断'},request=value.request||{},progress=value.progress||{},phase=progress.stage||progress.phase,counts=Number.isFinite(progress.total)&&progress.total>0?`${progress.done??0}/${progress.total}${progress.unit==='optimizer_steps'?' 步':progress.unit==='frames'?' 帧':''}`:'',error=value.error?String(value.error).split(/\r?\n/)[0].slice(0,240):'';return ['已提交任务：'+(request.name||kinds[request.kind]||request.kind||value.id),kinds[request.kind],statusLabel(value.status),phase?(typeof STAGES!=='undefined'?STAGES[phase]:null)||phase:'',counts,'任务 ID：'+value.id,error?'错误：'+error:'','配置草稿与此任务分别管理，修改表单不会改变已提交任务。'].filter(Boolean).join(' · ');}
async function inspectWorkflow(id,{navigate=false,route=navigate}={}){
  if(navigate&&typeof showResearchView==='function'&&platform.pane==='research'&&$('platformDialog').open)showResearchView('train',{route:false});if(route&&typeof researchObjectRoute==='function')researchObjectRoute('workflow',id,'train',{replace:true});
  const serial=researchUI.workflowSerial=(researchUI.workflowSerial||0)+1;researchUI.workflow=id;clearTimeout(researchUI.workflowTimer);
  const current=()=>serial===researchUI.workflowSerial&&id===researchUI.workflow;
  $('workflowSummary').textContent='正在读取已提交任务 '+id+'；配置草稿与任务分别管理。';
  let value;try{value=await api(`api/comparison/workflows/${id}`);}catch(error){if(current()){$('workflowSummary').textContent='任务 '+id+' 读取失败 · '+error.message+'；配置草稿未因此运行。';throw error;}return null;}if(!current())return null;
  $('workflowSummary').textContent=workflowSummaryText(value);
  researchOutput('workflowState',{status:value.status,progress:value.progress,output:value.output,error:value.error});$('workflowLog').disabled=false;$('workflowLog').onclick=async()=>{try{const log=await api(`api/comparison/workflows/${id}/log`);if(id!==researchUI.workflow||platform.pane!=='research'||!$('platformDialog').open)return;$('logText').textContent=log.text;$('logDialog').showModal();}catch(error){if(current())hubError(error);}};
  window.dispatchEvent(new CustomEvent('mint-workflow-inspected',{detail:value}));renderWorkflowNextSteps(value).catch(error=>{if(current())hubError(error);});
  $('workflowCancel').disabled=TERMINAL.has(value.status);$('workflowResume').disabled=!['failed','cancelled','interrupted'].includes(value.status);
  if(!TERMINAL.has(value.status)&&$('platformDialog').open&&platform.pane==='research')researchUI.workflowTimer=setTimeout(()=>{if(current())inspectWorkflow(id).catch(hubError);},3000);return value;
}
async function refreshResearchChoices(){
  await Promise.all([refreshWorkflowDatasetChoices(),loadRegistryChoices('model','registeredModel'),loadRegistryChoices('evaluation','modelEvaluation'),loadRegistryChoices('protocol','offlineProtocol')]);if(typeof updateModelActions==='function')updateModelActions();
}
function initializeResearch(host){
  const protocol=researchSection(host,'冻结协议与离线评测','固定输入、帧范围、GT 身份和数据切分。离线评测读取已有几何；不重复推理，不声明冷启动性能。');protocol.append(researchField('fixedProtocolName','协议名称','fixed-evaluation-v1'),researchButton('用当前比较冻结测试协议',async()=>{if(!state.comparison)throw new Error('请先打开含 GT 的比较');const value=await api('api/comparison/registry/protocol',{name:$('fixedProtocolName').value,comparisons:[state.comparison.id]});await loadRegistryChoices('protocol','offlineProtocol');$('offlineProtocol').value=value.id;researchOutput('fixedEvaluationStatus',`协议已冻结 · ${value.entries.length} 个输入 · ${value.id}`);}),researchSelect('offlineProtocol','已保存协议',[['','请选择']]),researchSelect('offlineSplit','评测划分',[['test','Test'],['validation','Validation']]),researchButton('评测当前比较的预测结果',async()=>{if(!state.comparison)throw new Error('请先打开比较');const run_ids=state.comparison.runs.filter(r=>r.result&&r.result.reference_role!=='user_declared_ground_truth').map(r=>r.id);const value=await api('api/comparison/registry/evaluation',{protocol_id:$('offlineProtocol').value,split:$('offlineSplit').value,run_ids});researchOutput('fixedEvaluationStatus',value);await loadRegistryChoices('evaluation','modelEvaluation');}));const evaluation=el('pre',undefined,'research-output');evaluation.id='fixedEvaluationStatus';protocol.append(evaluation);
  const workflow=researchSection(host,'数据生产与训练任务','先选择数据版本和任务类型，再预检并提交。训练等待安全空闲 GPU，一个训练尝试内的 GPU 数固定；参考研究链有额外依赖。');const primary=researchButton('前往推荐的 MINT 原生伪标签生产',()=>revealPlatformTarget(document.getElementById('nativeProductionSection')));workflow.append(primary);const form=el('div',undefined,'composer-fields');form.append(researchSelect('workflowKind','工作类型',[['train','MINT 训练'],['prepare_training','转换已有相机标注数据'],['curate_dataset','导出经复核的相机伪标签'],['pipeline','参考研究链 · 独立依赖（高级）'],['diagnostic','环境诊断']]),researchField('workflowName','任务名称','camera-training-validation'));
  const field=(id,label,value,type,kinds)=>{const row=researchField(id,label,value,type);row.dataset.workflowKinds=kinds;form.append(row);};
  field('workflowSource','LeRobot 根目录 / Pipeline 输入路径（每行一个）','/data1/lizhen_algo/mint/data/samples/lerobot_v3','textarea','prepare_training pipeline');field('workflowEpisodes','Episode 索引（逗号分隔）','0','text','prepare_training');field('workflowFrames','转换帧数（留空为整个 Episode）','','number','prepare_training');
  const dataset=researchSelect('workflowDataset','数据版本',[['','使用配置中的数据根目录']]);dataset.dataset.workflowKinds='train curate_dataset';form.append(dataset);
  const datasetSummary=el('p',undefined,'hint');datasetSummary.id='workflowDatasetSummary';datasetSummary.dataset.workflowKinds='train curate_dataset';datasetSummary.setAttribute('aria-live','polite');form.append(datasetSummary);
  dataset.querySelector('select').onchange=()=>{$('workflowPseudoOptIn').checked=false;updateWorkflowDatasetSummary();invalidateWorkflowReadiness();};
  field('workflowConfig','训练配置','/data1/lizhen_algo/mint/configs/training/mint_step2.yaml','text','train');field('workflowInit','初始化权重（留空用配置）','','text','train');field('workflowResumePath','续训检查点目录（恢复优化器 / 随机状态）','','text','train');field('workflowSteps','优化器步数（留空按配置完整训练）','','number','train');field('workflowGpus','固定 GPU 数（1–8）',1,'number','train pipeline');
  const profile=researchSelect('workflowProfile','诊断范围',[['train','训练'],['data','数据生产'],['inference','推理'],['full','全部']]);profile.dataset.workflowKinds='diagnostic';form.append(profile);const pseudoInput=el('input');pseudoInput.id='workflowPseudoOptIn';pseudoInput.type='checkbox';const pseudo=inputLabel('明确允许使用模型伪标签训练（保留来源，不作为实际 GT）',pseudoInput);pseudo.dataset.workflowKinds='train';form.append(pseudo);workflow.append(form);
  const workflowPreflight=researchButton('预检工作流',checkWorkflowReadiness);workflowPreflight.id='workflowPreflight';const workflowSubmit=researchButton('提交持久任务',submitWorkflow);workflowSubmit.id='workflowSubmit';workflowSubmit.dataset.locked='true';workflowSubmit.disabled=true;workflow.append(workflowPreflight,workflowSubmit);const summary=el('p','配置草稿与已提交任务分别管理；当前尚未选择已提交任务。','workflow-summary hint');summary.id='workflowSummary';summary.setAttribute('role','status');workflow.append(summary);form.addEventListener('input',invalidateWorkflowReadiness);form.addEventListener('change',invalidateWorkflowReadiness);const check=el('pre',undefined,'research-output');check.id='workflowCheck';const output=el('pre',undefined,'research-output');output.id='workflowState';workflow.append(check,output);const nextSteps=el('div',undefined,'button-row');nextSteps.id='workflowNextSteps';workflow.append(nextSteps);const actions=el('div',undefined,'button-row');for(const [id,label,fn]of [['workflowLog','查看日志',()=>{}],['workflowCancel','取消',async()=>{await api(`api/comparison/workflows/${researchUI.workflow}/cancel`,{});await inspectWorkflow(researchUI.workflow);}],['workflowResume','从完整检查点恢复',async()=>{await api(`api/comparison/workflows/${researchUI.workflow}/resume`,{});await inspectWorkflow(researchUI.workflow);}]]){const button=researchButton(label,fn);button.id=id;button.disabled=true;actions.append(button);}workflow.append(actions);$('workflowKind').onchange=updateWorkflowFields;updateWorkflowFields();
  initializeModelPanel(host);
  const experiment=el('details',undefined,'hub-secondary');experiment.append(el('summary','加速 / 融合实验与多机部署验证'));const form2=el('div',undefined,'composer-fields');form2.append(researchField('experimentBaseline','原生基线 Run ID'),researchField('experimentCandidate','候选 Run ID'),researchField('experimentTolerance','最大关节差异 mm',.01,'number'),researchButton('核对完整覆盖与数值',async()=>researchOutput('experimentStatus',await api('api/comparison/registry/experiment',{name:'Controlled experiment',baseline_run:$('experimentBaseline').value,candidate_run:$('experimentCandidate').value,tolerance_mm:Number($('experimentTolerance').value)}))),researchField('deploymentNodes','节点配置 JSON','[{"host":"127.0.0.1","gpus":8,"rank":0}]','textarea'),researchField('deploymentRoot','MINT 共享路径','/data1/lizhen_algo/mint'),researchButton('验证并保存部署配置',async()=>researchOutput('experimentStatus',await api('api/comparison/registry/deployment',{name:'Distributed profile',nodes:JSON.parse($('deploymentNodes').value),shared_root:$('deploymentRoot').value}))));form2.append(el('p','多机配置仅执行校验与保存；当前使用现有单台服务器，多机执行接口保留，跨物理服务器尚未验收。','hint'));const experimentStatus=el('pre',undefined,'research-output');experimentStatus.id='experimentStatus';experiment.append(form2,experimentStatus);host.append(experiment);
}
function lockResearchAction(id,locked){const button=$(id);if(!button)return;button.dataset.locked=String(locked);button.disabled=locked||button.dataset.busy==='true';}
function modelPromotionParameters(){return {model_id:$('registeredModel').value,evaluation_id:$('modelEvaluation').value,alias:$('modelAlias').value.trim(),max_joint_mm:Number($('modelMaxError').value),min_coverage:Number($('modelCoverage').value)};}
function updateModelActions(){
  const value=modelPromotionParameters(),alias=/^[\p{L}\p{N}_-]{1,64}$/u.test(value.alias),thresholds=$('modelMaxError').value.trim()!==''&&$('modelCoverage').value.trim()!==''&&Number.isFinite(value.max_joint_mm)&&value.max_joint_mm>0&&Number.isFinite(value.min_coverage)&&value.min_coverage>=0&&value.min_coverage<=1;
  const unavailable=id=>Array.from($(id).options).some(option=>option.value===$(id).value&&option.disabled);lockResearchAction('modelRegister',!$('modelPath').value.trim());lockResearchAction('modelPromote',!value.model_id||!value.evaluation_id||unavailable('registeredModel')||unavailable('modelEvaluation')||!alias||!thresholds);lockResearchAction('modelRollback',!alias);
  const compare=$('modelConfigureComparison');if(compare)compare.hidden=!compare.dataset.alias||compare.dataset.alias!==value.alias;
  $('modelGateSummary').textContent=thresholds?`发布门槛：关节误差 ≤ ${value.max_joint_mm} mm，覆盖率 ≥ ${(value.min_coverage*100).toFixed(1)}%`:'发布门槛：请填写有效误差与覆盖率';
}
async function registerModel(){const body={path:$('modelPath').value.trim(),name:$('modelName').value.trim()||undefined,config:$('modelConfig').value.trim()||undefined},selected=$('registeredModel').value;if(!body.path)throw new Error('请填写检查点路径');const value=await api('api/comparison/registry/model',body);await refreshResearchChoices();if($('registeredModel').value===selected&&Array.from($('registeredModel').options).some(option=>option.value===value.id))$('registeredModel').value=value.id;$('modelActionSummary').textContent='已登记检查点：'+(value.name||body.path)+'；需选择匹配的离线评测报告后发布别名。';researchOutput('modelStatus',value);updateModelActions();return value;}
function modelAliasReady(alias,message){
  $('modelActionSummary').textContent=message+'；默认权重不变，不会热加载到正在运行的模型。新比较需明确选择此别名。';
  const button=$('modelConfigureComparison');button.dataset.alias=alias;button.textContent='用别名 '+alias+' 配置新比较';button.hidden=false;
}
async function usePublishedModelAlias(alias){
  const navigation=platform.serial,signature=JSON.stringify(modelPromotionParameters()),value=await api('api/comparison/models/aliases');
  if(navigation!==platform.serial||platform.pane!=='research'||!$('platformDialog').open||nextUI.researchView!=='models'||signature!==JSON.stringify(modelPromotionParameters()))return null;
  const selected=value.aliases.find(row=>row.name===alias);if(!selected?.ready)throw new Error('此别名当前不可用；请核对已评测权重和配置文件');
  useRegisteredModel(selected.model,selected.effective_config);researchSubmissionNotice('已为新比较选择别名 '+alias+' 的已评测权重和配置；请核对输入后预检。');return selected;
}
async function promoteModel(){const parameters=modelPromotionParameters();if(!parameters.model_id||!parameters.evaluation_id)throw new Error('请选择模型版本与离线评测报告');const signature=JSON.stringify(parameters),value=await api('api/comparison/models/promote',parameters);if(signature===JSON.stringify(modelPromotionParameters())){modelAliasReady(value.alias,'别名 '+value.alias+' 已更新');researchOutput('modelStatus',value);}else researchSubmissionNotice('模型别名更新已完成，当前表单选择已保留。');return value;}
async function rollbackModel(){const alias=$('modelAlias').value.trim();if(!alias)throw new Error('请填写要回滚的模型别名');const value=await api('api/comparison/models/rollback',{alias});if(alias===$('modelAlias').value.trim()){modelAliasReady(alias,'别名 '+alias+' 已回滚到上一次通过评测的模型');researchOutput('modelStatus',value);}else researchSubmissionNotice('模型别名 '+alias+' 已回滚，当前别名输入已保留。');return value;}
function initializeModelPanel(host){
  const model=researchSection(host,'模型版本与发布门槛','选择已登记模型和匹配的原生离线报告，核对门槛后更新别名。默认权重不变，不会热加载；新比较需明确选择已评测别名。');
  const registration=el('details',undefined,'hub-secondary');registration.id='modelRegistrationDetails';registration.append(el('summary','登记新的检查点'),researchField('modelName','模型名称'),researchField('modelPath','检查点路径'),researchField('modelConfig','配置路径（可选）'));const register=researchButton('登记检查点',registerModel);register.id='modelRegister';registration.append(register);model.append(registration,researchSelect('registeredModel','模型版本',[['','请选择已登记模型']]),researchSelect('modelEvaluation','原生离线评测报告',[['','请选择匹配的评测报告']]),researchField('modelAlias','模型别名','candidate'));
  const gate=el('details',undefined,'hub-secondary'),summary=el('summary');summary.id='modelGateSummary';gate.append(summary,researchField('modelMaxError','关节误差上限 mm',100,'number'),researchField('modelCoverage','GT 几何覆盖率下限（0–1）',.95,'number'));model.append(gate);const actions=el('div',undefined,'button-row'),promote=researchButton('校验并更新别名',promoteModel),rollback=researchButton('回滚上一次已评测模型',rollbackModel);promote.id='modelPromote';rollback.id='modelRollback';actions.append(promote,rollback);model.append(actions);
  const compare=researchButton('用已评测别名配置新比较',()=>usePublishedModelAlias($('modelConfigureComparison').dataset.alias));compare.id='modelConfigureComparison';compare.hidden=true;actions.append(compare);
  const status=el('p','请选择模型、评测报告和别名；服务端会核对它们的身份、来源与发布门槛。','hint');status.id='modelActionSummary';status.setAttribute('role','status');const details=el('details',undefined,'hub-secondary'),raw=el('pre',undefined,'research-output');raw.id='modelStatus';details.append(el('summary','操作详情与来源'),raw);model.append(status,details);for(const id of ['modelName','modelPath','modelConfig','registeredModel','modelEvaluation','modelAlias','modelMaxError','modelCoverage']){$(id).oninput=updateModelActions;$(id).onchange=updateModelActions;}updateModelActions();
}
function preflightSummary(value){return `${value.frames} 帧 · ${value.width}×${value.height} · ${Number(value.fps).toFixed(3)} FPS · ${value.gt?'附加 GT':'无 GT'} · ${value.calibration?'显式标定':'算法自身内参'}\nRGB ${(value.storage.rgb_bytes/2**30).toFixed(2)} GiB${value.storage.input_cached?'（缓存可复用）':''} · 可用 ${(value.storage.disk_free_bytes/2**30).toFixed(1)} GiB\n${[...value.errors,...value.warnings].join('\n')||'预检通过'}`;}
function invalidateComparisonPreflight(){researchUI.inputPreflightSerial=(researchUI.inputPreflightSerial||0)+1;const summary=$('inputPreflight');if(summary){summary.dataset.ready='pending';summary.textContent='输入或算法设置已变化，请重新预检。';}}
async function preflightComparison(payload=null){
  const request=structuredClone(payload||(typeof comparisonDraftPayload==='function'?comparisonDraftPayload():{...inputBody(),selections:selectedAlgorithms()})),signature=typeof comparisonDraftSignature==='function'?comparisonDraftSignature(request):JSON.stringify(request),serial=researchUI.inputPreflightSerial=(researchUI.inputPreflightSerial||0)+1,navigation=platform.serial,dialogOpen=!!$('taskDialog').open;
  const example=typeof defaultExampleSubmission==='function'?defaultExampleSubmission(request):null,exampleSignature=JSON.stringify(example);
  const button=$('checkInputPreflight');researchUI.inputPreflightBusy=(researchUI.inputPreflightBusy||0)+1;if(button){button.dataset.locked='true';button.disabled=true;}$('inputPreflight').textContent='正在核对输入与算法条件，不启动推理…';$('inputPreflight').dataset.ready='pending';
  try{const value=await api('api/comparison/preflight',request);const current=typeof comparisonDraftSignature==='function'?comparisonDraftSignature():JSON.stringify({...inputBody(),selections:selectedAlgorithms()}),currentExample=typeof defaultExampleSubmission==='function'?defaultExampleSubmission(typeof comparisonDraftPayload==='function'?comparisonDraftPayload():{...inputBody(),selections:selectedAlgorithms()}):null;if(signature!==current||exampleSignature!==JSON.stringify(currentExample))throw new Error('输入或算法参数已变化，请重新预检');if(serial!==researchUI.inputPreflightSerial)return null;if(navigation===platform.serial&&dialogOpen&&$('taskDialog').open){$('inputPreflight').textContent=preflightSummary(example?{...value,gt:true}:value)+(example?'\n默认示例将附加官方 GT；提交时再次校验数据版本。':'');$('inputPreflight').dataset.ready=String(value.ready);}return value;}
  catch(error){let current,currentExample;try{current=typeof comparisonDraftSignature==='function'?comparisonDraftSignature():JSON.stringify({...inputBody(),selections:selectedAlgorithms()});currentExample=typeof defaultExampleSubmission==='function'?defaultExampleSubmission(typeof comparisonDraftPayload==='function'?comparisonDraftPayload():{...inputBody(),selections:selectedAlgorithms()}):null;}catch(_){}if(serial===researchUI.inputPreflightSerial&&signature===current&&exampleSignature===JSON.stringify(currentExample)&&navigation===platform.serial&&dialogOpen&&$('taskDialog').open){$('inputPreflight').dataset.ready='false';$('inputPreflight').textContent='预检失败 · '+error.message;}throw error;}
  finally{researchUI.inputPreflightBusy--;if(button){button.dataset.locked=String(researchUI.inputPreflightBusy>0);button.disabled=researchUI.inputPreflightBusy>0;}}
}
function initializeResearchPlatform(){
  initializeCatalog($('hub-catalog'));initializeQuality($('hub-quality'));initializeResearch($('hub-research'));
  const target=$('start'),check=researchButton('输入预检',()=>preflightComparison());check.id='checkInputPreflight';target.before(check);
  const fields=el('div',undefined,'twocol execution-fields');fields.append(researchSelect('executionPriority','任务优先级',[['interactive','交互任务'],['batch','批量任务'],['background','后台任务']]),researchSelect('executionCache','结果缓存',[['exact','相同运行复用'],['compatible','数值协议相同可复用'],['fresh','独立重跑（性能试验）']]));$('algorithmOptions').before(fields);
  const summary=el('pre',undefined,'preflight-summary');summary.id='inputPreflight';$('sourceDirectory').after(summary);
  const qualityButton=researchButton('质检与复核',()=>openPlatform('quality'));$('frameInspector').append(qualityButton);
  $('taskDialog').addEventListener('input',invalidateComparisonPreflight);$('taskDialog').addEventListener('change',invalidateComparisonPreflight);
  $('platformDialog').addEventListener('close',()=>clearTimeout(researchUI.workflowTimer));
}
document.addEventListener('DOMContentLoaded',initializeResearchPlatform);

// Submission stays disabled until the exact current form has passed a read-only preflight.
function researchSubmissionNotice(message){if(typeof notify==='function')notify(message,false);else if(typeof nextUIMessage==='function')nextUIMessage(message);}
async function submitWorkflow(){
  const signature=workflowSignature(),body={...JSON.parse(signature),name:$('workflowName').value},navigation=platform.serial,selected=researchUI.workflow;
  const check=await checkWorkflowReadiness();if(!check?.ready||signature!==workflowSignature())throw new Error('当前参数尚未通过预检，请按提示处理后重试');
  const value=await api('api/comparison/workflows',body);
  if(navigation===platform.serial&&platform.pane==='research'&&selected===researchUI.workflow&&signature===workflowSignature())await inspectWorkflow(value.id,{navigate:true});
  else researchSubmissionNotice('工作流已提交，当前选择已保留；可从任务中心查看后台进度。');return value;
}
function workflowSignature(){return JSON.stringify({kind:$('workflowKind').value,parameters:workflowParameters()});}
function invalidateWorkflowReadiness(){
  researchUI.readySignature=null;const button=$('workflowSubmit');if(button){button.dataset.locked='true';button.disabled=true;}
  const check=$('workflowCheck');if(check){check.dataset.ready='pending';check.textContent=$('workflowKind').value==='pipeline'?'参考研究链：需单独配置 MegaSAM / MoGe / GeoCalib 与 HaWoR 资产；正在核对或等待预检。推荐先使用下方原生后端伪标签生产。':'待预检：请确认数据版本、权重、训练配置与 GPU 数量。修改参数后需要重新预检。';}
}
async function checkWorkflowReadiness(){
  const signature=workflowSignature(),serial=researchUI.preflightSerial=(researchUI.preflightSerial||0)+1;invalidateWorkflowReadiness();$('workflowCheck').textContent='正在核对依赖和输入，仅预检，不启动计算任务…';
  try{const value=await api('api/comparison/workflows/preflight',JSON.parse(signature));if(serial!==researchUI.preflightSerial||signature!==workflowSignature())return null;researchUI.readySignature=value.ready?signature:null;$('workflowSubmit').dataset.locked=String(!value.ready);$('workflowSubmit').disabled=!value.ready||$('workflowSubmit').dataset.busy==='true';$('workflowCheck').dataset.ready=String(value.ready);researchOutput('workflowCheck',value.ready?'可提交 · 预检通过，正式执行时会再次核对输入和环境。':'暂不可提交 · '+(value.errors||[]).join('\n'));return value;}
  catch(error){if(serial===researchUI.preflightSerial&&signature===workflowSignature()){$('workflowCheck').dataset.ready='false';$('workflowCheck').textContent='暂不可提交 · '+error.message;}throw error;}
}
async function refreshWorkflowDatasetChoices(){
  const kind=$('workflowKind').value;
  const value=await loadRegistryChoices('dataset','workflowDataset',{filter:row=>kind==='curate_dataset'?row.status==='reviewed_manifest':!!row.training_ready,empty:kind==='curate_dataset'?'选择已接受复核的筛选版本':'选择已验证训练版本，或使用配置中的数据'});
  if(!value||kind!==$('workflowKind').value)return null;researchUI.workflowDatasets=value.records;updateWorkflowDatasetSummary();return value;
}
function updateWorkflowDatasetSummary(){
  const host=$('workflowDatasetSummary');if(!host)return;const id=$('workflowDataset').value,dataset=(researchUI.workflowDatasets||[]).find(row=>row.id===id);
  if(!id){host.textContent=$('workflowKind').value==='curate_dataset'?'请选择已接受复核的筛选清单；导出前仍需预检来源与官方划分。':'未选择数据版本：使用训练配置中的数据；预检会核对实际来源和监督格式。';return;}
  if(!dataset){host.textContent='所选版本的来源尚未读取，请刷新后核对。';return;}
  const pseudo=dataset.label_role==='model_generated_pseudo_labels'||!!dataset.requires_training_opt_in,native=dataset.native?.plan,split=dataset.split||native?.split;
  const role=pseudo?'模型生成伪标签，需明确允许后训练':dataset.status==='reviewed_manifest'?'人工接受的预测筛选清单，尚非训练格式':native?'官方参考标注':dataset.label_role||'标注来源见版本详情';
  const plan=dataset.provenance?.label_origin?.plan,source=native?`来源：${native.dataset} / ${native.sequence}`:dataset.parent_curation_id?'来源复核清单：'+dataset.parent_curation_id.slice(0,12):Array.isArray(plan)?`来源：${plan.length} 个原生推理结果`:dataset.entries?.length?`来源：${dataset.entries.length} 个复核片段`:null;
  host.textContent=[dataset.name||dataset.id,role,split?'划分：'+({train:'Train',val:'Validation',validation:'Validation',test:'Test'}[split]||split):'',source].filter(Boolean).join(' · ');
}
async function renderWorkflowNextSteps(value){
  const host=$('workflowNextSteps');if(!host)return;host.dataset.workflow=value.id;host.replaceChildren();
  if(value.request.kind==='train'){host.append(researchButton('查看训练曲线与检查点',()=>selectTrainingCurve(value.id)));}
  if(value.status!=='completed'||!value.output?.dataset_id)return;
  const dataset=await api('api/comparison/registry/record/'+value.output.dataset_id);if(host.dataset.workflow!==value.id)return;
  if(dataset.native)host.append(researchButton('用此数据版本创建比较',()=>useNativeDataset(dataset)));
  if(dataset.training_ready)host.append(researchButton('用此数据版本配置训练',async()=>{await useTrainingDataset(dataset);}));
  else host.append(el('span','此版本用于评测或尚未具备训练格式，未开放训练捷径。','hint'));
}
async function configureWorkflowDataset(dataset,kind){
  const serial=researchUI.datasetHandoffSerial=(researchUI.datasetHandoffSerial||0)+1;await openPlatform('research');const navigation=platform.serial;showResearchView('train');$('workflowKind').value=kind;updateWorkflowFields();const expected=$('workflowDataset').value;await refreshWorkflowDatasetChoices();if($('workflowDataset').value!==expected)return null;if(serial!==researchUI.datasetHandoffSerial||navigation!==platform.serial||platform.pane!=='research'||!$('platformDialog').open||nextUI.researchView!=='train'||$('workflowKind').value!==kind)return null;
  chooseRegistryRecord('workflowDataset',dataset);if(!(researchUI.workflowDatasets||[]).some(row=>row.id===dataset.id))researchUI.workflowDatasets=[...(researchUI.workflowDatasets||[]),dataset];updateWorkflowDatasetSummary();$('workflowPseudoOptIn').checked=false;invalidateWorkflowReadiness();revealPlatformTarget($('workflowDataset'));
  if(kind==='curate_dataset')$('workflowCheck').textContent='已选择复核清单；请先预检再提交相机伪标签导出。复核记录与二维修正不会自动成为三维或 MANO 真值。';
  else if(dataset.label_role==='model_generated_pseudo_labels'||dataset.requires_training_opt_in)$('workflowCheck').textContent='已选择模型伪标签数据；请明确勾选允许伪标签训练，再预检配置。';return dataset;
}
async function useTrainingDataset(dataset){if(!dataset.training_ready)throw new Error('此数据版本尚未具备训练格式');return configureWorkflowDataset(dataset,'train');}
async function useReviewedDataset(dataset){if(dataset.status!=='reviewed_manifest')throw new Error('请选择已接受复核的筛选清单');return configureWorkflowDataset(dataset,'curate_dataset');}
