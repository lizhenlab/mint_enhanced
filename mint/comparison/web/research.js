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
  for(const row of value.records.filter(filter)){const option=el('option',(row.name||row.id.slice(0,12))+' · '+row.id.slice(0,8));option.value=row.id;select.append(option);}if([...select.options].some(o=>o.value===old))select.value=old;return value;
}
async function refreshArtifactCatalog(){
  const serial=researchUI.catalogSerial=(researchUI.catalogSerial||0)+1;
  const kind=$('registryKind').value,query=new URLSearchParams({query:$('registryQuery').value,offset:researchUI.offset,limit:30});
  const value=await api(`api/comparison/registry/${kind}?${query}`);if(serial!==researchUI.catalogSerial||kind!==$('registryKind').value||query.get('query')!==$('registryQuery').value)return;const host=$('artifactRows');host.replaceChildren();
  const table=el('table'),head=el('tr');for(const text of ['产物 / 来源','类型与状态','操作'])head.append(el('th',text));table.append(head);
  for(const row of value.records){const tr=el('tr'),name=el('td'),actions=el('td');name.append(el('strong',row.name||row.id.slice(0,12)),el('small',row.source?.path||row.path||row.root||row.run_id||row.id));
    tr.append(name,el('td',[row.type||row.kind,row.frames?`${row.frames} 帧`:'',row.availability==='missing_or_changed'?'文件缺失或变化':row.status||row.origin||''].filter(Boolean).join(' · ')));
    actions.append(researchButton('来源详情',()=>researchDetails(row)));
    if(row.run_id)actions.append(researchButton('打开结果',async()=>{const parents=await api(`api/comparison/runs/${row.run_id}/parent`);if(!parents.comparisons.length)throw new Error('此结果尚未附加到比较');$('platformDialog').close();await openComparison(parents.comparisons[0]);inspectResult(row.run_id);}));
    if(row.importable&&row.availability!=='missing_or_changed')actions.append(researchButton('导入已有数值',async()=>{const comp=await api(`api/comparison/registry/artifacts/${row.id}/import`,{});$('platformDialog').close();await openComparison(comp.id);}));
    if(row.kind==='model')actions.append(researchButton('用于 MINT 比较',()=>useRegisteredModel(row)));
    if(row.kind==='review'&&row.decision==='accepted'){const check=el('input');check.type='checkbox';check.checked=researchUI.accepted.has(row.id);check.onchange=()=>{if(check.checked)researchUI.accepted.add(row.id);else researchUI.accepted.delete(row.id);};actions.append(inputLabel('纳入数据版本',check));}
    tr.append(actions);table.append(tr);
  }
  host.append(table);$('registryPage').textContent=`${value.total} 项 · ${value.total?value.offset+1:0}–${Math.min(value.total,value.offset+value.records.length)}`;$('registryPrevious').disabled=value.offset===0;$('registryNext').disabled=value.offset+value.records.length>=value.total;
}
function useRegisteredModel(model){$('platformDialog').close();openTaskDialog();const box=$('algorithmOptions').querySelector('[data-algorithm=mint]');box.querySelector('input[type=checkbox]').checked=true;box.querySelector('[data-selection=checkpoint]').value=model.path;box.querySelector('[data-selection=config]').value=model.config?.path||'';}
function initializeCatalog(host){
  const bar=el('div',undefined,'hub-filters');bar.append(researchSelect('registryKind','产物类别',[['artifact','结果与已保存预测'],['protocol','固定评测协议'],['evaluation','离线评测报告'],['review','人工复核记录'],['dataset','数据版本'],['model','模型版本'],['experiment','加速 / 融合实验'],['deployment','多机配置']]),researchField('registryQuery','搜索名称、来源或标识'),researchButton('刷新目录',()=>{researchUI.offset=0;return refreshArtifactCatalog();}));host.append(bar);
  const rows=el('div',undefined,'hub-table');rows.id='artifactRows';host.append(rows);const page=el('div',undefined,'button-row'),info=el('span');info.id='registryPage';const prev=researchButton('上一页',()=>{researchUI.offset=Math.max(0,researchUI.offset-30);return refreshArtifactCatalog();});prev.id='registryPrevious';const next=researchButton('下一页',()=>{researchUI.offset+=30;return refreshArtifactCatalog();});next.id='registryNext';page.append(prev,info,next);host.append(page);
  $('registryKind').onchange=()=>{researchUI.offset=0;refreshArtifactCatalog().catch(hubError);};
  const version=researchSection(host,'发布复核数据版本','在“人工复核记录”中勾选已接受的记录，再发布不可变筛选清单。预测标签不会自动变为 GT。');version.append(researchField('datasetVersionName','数据版本名称','reviewed-camera-v1'),researchButton('发布已选复核清单',async()=>{const value=await api('api/comparison/registry/dataset',{name:$('datasetVersionName').value,review_ids:[...researchUI.accepted]});researchOutput('datasetVersionStatus',`已保存 ${value.entries.length} 个片段 · ${value.id}。可在数据与训练中导出明确标记的相机伪标签。`);}));const status=el('p',undefined,'hint');status.id='datasetVersionStatus';version.append(status);
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
async function inspectWorkflow(id,{navigate=false}={}){
  if(navigate&&typeof showResearchView==='function'&&platform.pane==='research'&&$('platformDialog').open)showResearchView('train');
  const serial=researchUI.workflowSerial=(researchUI.workflowSerial||0)+1;researchUI.workflow=id;clearTimeout(researchUI.workflowTimer);
  const current=()=>serial===researchUI.workflowSerial&&id===researchUI.workflow;
  let value;try{value=await api(`api/comparison/workflows/${id}`);}catch(error){if(current())throw error;return null;}if(!current())return null;
  researchOutput('workflowState',{status:value.status,progress:value.progress,output:value.output,error:value.error});$('workflowLog').disabled=false;$('workflowLog').onclick=async()=>{try{const log=await api(`api/comparison/workflows/${id}/log`);if(id!==researchUI.workflow||platform.pane!=='research'||!$('platformDialog').open)return;$('logText').textContent=log.text;$('logDialog').showModal();}catch(error){if(current())hubError(error);}};
  window.dispatchEvent(new CustomEvent('mint-workflow-inspected',{detail:value}));renderWorkflowNextSteps(value).catch(error=>{if(current())hubError(error);});
  $('workflowCancel').disabled=TERMINAL.has(value.status);$('workflowResume').disabled=!['failed','cancelled','interrupted'].includes(value.status);
  if(!TERMINAL.has(value.status)&&$('platformDialog').open&&platform.pane==='research')researchUI.workflowTimer=setTimeout(()=>{if(current())inspectWorkflow(id).catch(hubError);},3000);return value;
}
async function refreshResearchChoices(){
  await Promise.all([refreshWorkflowDatasetChoices(),loadRegistryChoices('model','registeredModel'),loadRegistryChoices('evaluation','modelEvaluation'),loadRegistryChoices('protocol','offlineProtocol')]);
}
function initializeResearch(host){
  const protocol=researchSection(host,'冻结协议与离线评测','固定输入、帧范围、GT 身份和数据切分。离线评测读取已有几何；不重复推理，不声明冷启动性能。');protocol.append(researchField('fixedProtocolName','协议名称','fixed-evaluation-v1'),researchButton('用当前比较冻结测试协议',async()=>{if(!state.comparison)throw new Error('请先打开含 GT 的比较');const value=await api('api/comparison/registry/protocol',{name:$('fixedProtocolName').value,comparisons:[state.comparison.id]});await loadRegistryChoices('protocol','offlineProtocol');$('offlineProtocol').value=value.id;researchOutput('fixedEvaluationStatus',`协议已冻结 · ${value.entries.length} 个输入 · ${value.id}`);}),researchSelect('offlineProtocol','已保存协议',[['','请选择']]),researchButton('评测当前比较的预测结果',async()=>{if(!state.comparison)throw new Error('请先打开比较');const run_ids=state.comparison.runs.filter(r=>r.algorithm!=='reference'&&!r.result?.reference_role).map(r=>r.id);const value=await api('api/comparison/registry/evaluation',{protocol_id:$('offlineProtocol').value,run_ids});researchOutput('fixedEvaluationStatus',value);await loadRegistryChoices('evaluation','modelEvaluation');}));const evaluation=el('pre',undefined,'research-output');evaluation.id='fixedEvaluationStatus';protocol.append(evaluation);
  const workflow=researchSection(host,'数据生产与训练任务','先选择数据版本和任务类型，再预检并提交。训练等待安全空闲 GPU，一个训练尝试内的 GPU 数固定；参考研究链有额外依赖。');const primary=researchButton('前往推荐的 MINT 原生伪标签生产',()=>revealPlatformTarget(document.getElementById('nativeProductionSection')));workflow.append(primary);const form=el('div',undefined,'composer-fields');form.append(researchSelect('workflowKind','工作类型',[['train','MINT 训练'],['prepare_training','转换已有相机标注数据'],['curate_dataset','导出经复核的相机伪标签'],['pipeline','参考研究链 · 独立依赖（高级）'],['diagnostic','环境诊断']]),researchField('workflowName','任务名称','camera-training-validation'));
  const field=(id,label,value,type,kinds)=>{const row=researchField(id,label,value,type);row.dataset.workflowKinds=kinds;form.append(row);};
  field('workflowSource','LeRobot 根目录 / Pipeline 输入路径（每行一个）','/data1/lizhen_algo/mint/data/samples/lerobot_v3','textarea','prepare_training pipeline');field('workflowEpisodes','Episode 索引（逗号分隔）','0','text','prepare_training');field('workflowFrames','转换帧数（留空为整个 Episode）','','number','prepare_training');
  const dataset=researchSelect('workflowDataset','数据版本',[['','使用配置中的数据根目录']]);dataset.dataset.workflowKinds='train curate_dataset';form.append(dataset);
  field('workflowConfig','训练配置','/data1/lizhen_algo/mint/configs/training/mint_step2.yaml','text','train');field('workflowInit','初始化权重（留空用配置）','','text','train');field('workflowResumePath','续训检查点目录（恢复优化器 / 随机状态）','','text','train');field('workflowSteps','优化器步数（留空按配置完整训练）','','number','train');field('workflowGpus','固定 GPU 数（1–8）',1,'number','train pipeline');
  const profile=researchSelect('workflowProfile','诊断范围',[['train','训练'],['data','数据生产'],['inference','推理'],['full','全部']]);profile.dataset.workflowKinds='diagnostic';form.append(profile);const pseudoInput=el('input');pseudoInput.id='workflowPseudoOptIn';pseudoInput.type='checkbox';const pseudo=inputLabel('明确允许使用模型伪标签训练（保留来源，不作为实际 GT）',pseudoInput);pseudo.dataset.workflowKinds='train';form.append(pseudo);workflow.append(form);
  const workflowPreflight=researchButton('预检工作流',checkWorkflowReadiness);workflowPreflight.id='workflowPreflight';const workflowSubmit=researchButton('提交持久任务',async()=>{const check=await checkWorkflowReadiness();if(!check?.ready)throw new Error('当前工作流尚未通过预检，请按提示处理后重试');const value=await api('api/comparison/workflows',{kind:$('workflowKind').value,parameters:workflowParameters(),name:$('workflowName').value});await inspectWorkflow(value.id);});workflowSubmit.id='workflowSubmit';workflowSubmit.dataset.locked='true';workflowSubmit.disabled=true;workflow.append(workflowPreflight,workflowSubmit);form.addEventListener('input',invalidateWorkflowReadiness);form.addEventListener('change',invalidateWorkflowReadiness);const check=el('pre',undefined,'research-output');check.id='workflowCheck';const output=el('pre',undefined,'research-output');output.id='workflowState';workflow.append(check,output);const nextSteps=el('div',undefined,'button-row');nextSteps.id='workflowNextSteps';workflow.append(nextSteps);const actions=el('div',undefined,'button-row');for(const [id,label,fn]of [['workflowLog','查看日志',()=>{}],['workflowCancel','取消',async()=>{await api(`api/comparison/workflows/${researchUI.workflow}/cancel`,{});await inspectWorkflow(researchUI.workflow);}],['workflowResume','从完整检查点恢复',async()=>{await api(`api/comparison/workflows/${researchUI.workflow}/resume`,{});await inspectWorkflow(researchUI.workflow);}]]){const button=researchButton(label,fn);button.id=id;button.disabled=true;actions.append(button);}workflow.append(actions);$('workflowKind').onchange=updateWorkflowFields;updateWorkflowFields();
  const model=researchSection(host,'模型版本与发布门槛','登记权重后，用冻结协议运行原生推理评测。通过误差和覆盖门槛才能更新可选别名；不会替换正在运行的模型或默认权重文件。');model.append(researchField('modelName','模型名称'),researchField('modelPath','检查点路径'),researchField('modelConfig','配置路径（可选）'),researchButton('登记检查点',async()=>{await api('api/comparison/registry/model',{path:$('modelPath').value,name:$('modelName').value||undefined,config:$('modelConfig').value||undefined});await refreshResearchChoices();}),researchSelect('registeredModel','模型版本',[['','请选择']]),researchSelect('modelEvaluation','原生离线评测报告',[['','请选择']]),researchField('modelAlias','模型别名','candidate'),researchField('modelMaxError','关节误差上限 mm',100,'number'),researchField('modelCoverage','GT 几何覆盖率下限（0–1）',.95,'number'),researchButton('校验并更新别名',async()=>{const value=await api('api/comparison/models/promote',{model_id:$('registeredModel').value,evaluation_id:$('modelEvaluation').value,alias:$('modelAlias').value,max_joint_mm:Number($('modelMaxError').value),min_coverage:Number($('modelCoverage').value)});researchOutput('modelStatus',value);}),researchButton('回滚上一次已评测模型',async()=>researchOutput('modelStatus',await api('api/comparison/models/rollback',{alias:$('modelAlias').value}))));const modelStatus=el('pre',undefined,'research-output');modelStatus.id='modelStatus';model.append(modelStatus);
  const experiment=el('details',undefined,'hub-secondary');experiment.append(el('summary','加速 / 融合实验与多机部署验证'));const form2=el('div',undefined,'composer-fields');form2.append(researchField('experimentBaseline','原生基线 Run ID'),researchField('experimentCandidate','候选 Run ID'),researchField('experimentTolerance','最大关节差异 mm',.01,'number'),researchButton('核对完整覆盖与数值',async()=>researchOutput('experimentStatus',await api('api/comparison/registry/experiment',{name:'Controlled experiment',baseline_run:$('experimentBaseline').value,candidate_run:$('experimentCandidate').value,tolerance_mm:Number($('experimentTolerance').value)}))),researchField('deploymentNodes','节点配置 JSON','[{"host":"127.0.0.1","gpus":8,"rank":0}]','textarea'),researchField('deploymentRoot','MINT 共享路径','/data1/lizhen_algo/mint'),researchButton('验证并保存部署配置',async()=>researchOutput('experimentStatus',await api('api/comparison/registry/deployment',{name:'Distributed profile',nodes:JSON.parse($('deploymentNodes').value),shared_root:$('deploymentRoot').value}))));form2.append(el('p','多机配置仅执行校验与保存；当前使用现有单台服务器，多机执行接口保留，跨物理服务器尚未验收。','hint'));const experimentStatus=el('pre',undefined,'research-output');experimentStatus.id='experimentStatus';experiment.append(form2,experimentStatus);host.append(experiment);
}
function preflightSummary(value){return `${value.frames} 帧 · ${value.width}×${value.height} · ${Number(value.fps).toFixed(3)} FPS · ${value.gt?'附加 GT':'无 GT'} · ${value.calibration?'显式标定':'算法自身内参'}\nRGB ${(value.storage.rgb_bytes/2**30).toFixed(2)} GiB${value.storage.input_cached?'（缓存可复用）':''} · 可用 ${(value.storage.disk_free_bytes/2**30).toFixed(1)} GiB\n${[...value.errors,...value.warnings].join('\n')||'预检通过'}`;}
async function preflightComparison(){const value=await api('api/comparison/preflight',{...inputBody(),selections:selectedAlgorithms()});$('inputPreflight').textContent=preflightSummary(value);$('inputPreflight').dataset.ready=String(value.ready);return value;}
function initializeResearchPlatform(){
  initializeCatalog($('hub-catalog'));initializeQuality($('hub-quality'));initializeResearch($('hub-research'));
  const target=$('start'),check=researchButton('输入预检',preflightComparison);check.id='checkInputPreflight';target.before(check);
  const fields=el('div',undefined,'twocol execution-fields');fields.append(researchSelect('executionPriority','任务优先级',[['interactive','交互任务'],['batch','批量任务'],['background','后台任务']]),researchSelect('executionCache','结果缓存',[['exact','相同运行复用'],['compatible','数值协议相同可复用'],['fresh','独立重跑（性能试验）']]));$('algorithmOptions').before(fields);
  const summary=el('pre',undefined,'preflight-summary');summary.id='inputPreflight';$('sourceDirectory').after(summary);
  const qualityButton=researchButton('质检与复核',()=>openPlatform('quality'));$('frameInspector').append(qualityButton);
  $('platformDialog').addEventListener('close',()=>clearTimeout(researchUI.workflowTimer));
  const params=new URLSearchParams(location.search);if(params.has('workflow'))openPlatform('research').then(()=>inspectWorkflow(params.get('workflow'))).catch(hubError);
}
document.addEventListener('DOMContentLoaded',initializeResearchPlatform);

// Submission stays disabled until the exact current form has passed a read-only preflight.
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
  return loadRegistryChoices('dataset','workflowDataset',{filter:row=>kind==='curate_dataset'?row.status==='reviewed_manifest':!!row.training_ready,empty:kind==='curate_dataset'?'选择已接受复核的筛选版本':'选择已验证训练版本，或使用配置中的数据'});
}
async function renderWorkflowNextSteps(value){
  const host=$('workflowNextSteps');if(!host)return;host.dataset.workflow=value.id;host.replaceChildren();
  host.append(el('span',`${value.request.name||value.request.kind} · ${statusLabel(value.status)}${value.error?' · '+value.error:''}`,'hint'));
  if(value.request.kind==='train'){host.append(researchButton('查看训练曲线与检查点',()=>{nextUI.curve=value.id;$('curveWorkflow').value=value.id;revealPlatformTarget(document.getElementById('trainingCurve')?.closest('section'));return refreshCurves();}));}
  if(value.status!=='completed'||!value.output?.dataset_id)return;
  const dataset=await api('api/comparison/registry/record/'+value.output.dataset_id);if(host.dataset.workflow!==value.id)return;
  if(dataset.native)host.append(researchButton('用此数据版本创建比较',async()=>{await openPlatform('data');await refreshNativeVersions();$('nativeVersion').value=dataset.id;revealPlatformTarget($('nativeVersion'));}));
  if(dataset.training_ready)host.append(researchButton('用此数据版本配置训练',async()=>{await useTrainingDataset(dataset);}));
  else host.append(el('span','此版本用于评测或尚未具备训练格式，未开放训练捷径。','hint'));
}
async function useTrainingDataset(dataset){
  await openPlatform('research');$('workflowKind').value='train';updateWorkflowFields();await refreshWorkflowDatasetChoices();$('workflowDataset').value=dataset.id;$('workflowPseudoOptIn').checked=false;invalidateWorkflowReadiness();revealPlatformTarget($('workflowDataset'));if(dataset.label_role==='model_generated_pseudo_labels'||dataset.requires_training_opt_in)$('workflowCheck').textContent='已选择模型伪标签数据；请明确勾选允许伪标签训练，再预检配置。';
}
