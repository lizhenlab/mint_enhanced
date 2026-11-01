'use strict';

// Shared inputs, result IDs and task ownership connect these tools to the workspace.
const nextUI={catalog:[],nativeJob:null,study:null,curve:null,timer:null,annotation:null,image:null,undo:[],dirty:false};
const nextField=(...args)=>researchField(...args);
function nextText(id,tag='p'){const node=el(tag);node.id=id;return node;}
function nextCheck(id,label,checked=false){const input=el('input');input.type='checkbox';input.id=id;input.checked=checked;return inputLabel(label,input);}
function nextOptions(id,rows,empty='请选择'){const select=$(id),old=select.value;select.replaceChildren();for(const [value,label] of [['',empty],...rows]){const option=el('option',label);option.value=value;select.append(option);}if([...select.options].some(o=>o.value===old))select.value=old;}
function nextTable(host,headers,rows){host.replaceChildren();const table=el('table'),head=el('tr');for(const value of headers)head.append(el('th',value));table.append(head);for(const values of rows){const tr=el('tr');for(const value of values){const td=el('td');td.append(value instanceof Node?value:document.createTextNode(String(value??'—')));tr.append(td);}table.append(tr);}host.append(table);}
function nextChoices(id){const host=$(id);if(!host.querySelector('.algorithm')||host.dataset.waiting)cloneComposerAlgorithms(host);return selectedAlgorithmsFrom(host);}
function nextAlgorithms(id,onlyMint=false){const details=el('details');details.append(el('summary','算法、检查点与运行参数'));const host=el('div',undefined,'composer-algorithms');host.id=id;host.dataset.onlyMint=String(onlyMint);details.append(host);cloneComposerAlgorithms(host);if(onlyMint)for(const box of host.querySelectorAll('.algorithm'))box.querySelector('input[type=checkbox]').checked=box.dataset.algorithm==='mint';return details;}

function nativeButton(id,label,action,primary=false){
  const button=researchButton(label,action);button.id=id;if(primary)button.classList.add('primary');return button;
}
function nativeWide(field){field.classList.add('native-wide');return field;}
function nativeLock(id,locked){const button=$(id);if(!button)return;button.dataset.locked=String(locked);button.disabled=locked||button.dataset.busy==='true';}
function nativeDatasetRow(){return $('nativeDataset').value===''?null:nextUI.catalog[Number($('nativeDataset').value)];}
function nativeSelectedVersion(){return (nextUI.nativeVersions||[]).find(row=>row.id===$('nativeVersion').value);}
function nativePanel(){
  const host=$('hub-data'),section=researchSection(host,'公开数据集导入与参考评测','读取官方格式的视频与标注，整理成可重复使用的标准样本，再运行算法比较。数据准备不会启动推理或训练。');
  host.prepend(section);section.id='nativeDataPanel';section.classList.add('native-data-panel');section.dataset.dataSource='native';
  const nav=el('nav',undefined,'native-step-nav');nav.setAttribute('aria-label','公开数据集操作步骤');
  const steps=el('div',undefined,'native-steps');
  for(const [key,label,description]of [['source','1 · 选择原始数据','选择数据集、官方划分和带参考标注的序列。'],['prepare','2 · 准备标准样本','校验所选帧段，保存视频、参考标注与原始帧映射。'],['compare','3 · 运行比较','核对实际样本与内参条件，再选择算法。']]){
    const button=el('button',label);button.id='nativeStepNav-'+key;button.setAttribute('aria-controls','nativeStep-'+key);button.onclick=()=>showNativeStep(key,{focus:true});nav.append(button);
    const step=el('section',undefined,'native-step');step.id='nativeStep-'+key;step.setAttribute('aria-labelledby','nativeHeading-'+key);
    const heading=el('div',undefined,'native-step-heading'),title=el('h3',label);title.id='nativeHeading-'+key;title.tabIndex=-1;heading.append(title,el('p',description));step.append(heading);steps.append(step);
  }
  section.append(nav,steps);
  const source=$('nativeStep-source'),fields=el('div',undefined,'native-fields');
  fields.append(researchSelect('nativeDataset','选择已支持的数据集',[]),nativeButton('nativeReadCatalog','读取 / 刷新数据集清单',loadNativeCatalog),researchSelect('nativeSplit','官方划分',[['val','Validation'],['test','Test'],['train','Train']]),researchSelect('nativeGroup','任务 / 分组',[]),nativeWide(researchSelect('nativeSequence','选择序列',[])));
  source.append(fields);
  const directory=nextText('nativeDirectory');directory.className='native-note';directory.dataset.i18nIgnore='';source.append(directory);
  const pages=el('div',undefined,'native-actions');pages.id='nativeSequencePages';pages.append(nativeButton('nativePrevious','上一页',()=>loadNativeSequences($('nativeGroup').value,Math.max(0,Number($('nativeSequence').dataset.offset||0)-50))),nativeButton('nativeNext','下一页',()=>loadNativeSequences($('nativeGroup').value,Number($('nativeSequence').dataset.offset||0)+50)));source.append(pages);
  const inventory=nextText('nativeInventory');inventory.className='native-status';inventory.setAttribute('role','status');source.append(inventory);
  const note=nextText('nativeSequenceHint');note.className='native-note';note.setAttribute('role','status');source.append(note);
  const catalog=el('details',undefined,'native-catalog-details');catalog.append(el('summary','高级：目录与支持范围'),el('p','当前适配 ARCTIC、EgoDex、DexYCB。清单与格式存在性检查不代表全部文件完整，所选帧段会在准备前进一步校验。','native-note'));
  const paths=el('div',undefined,'native-fields');paths.append(nextField('nativeCatalogRoot','服务器数据集清单目录','/dataset'),nextField('nativeRoot','所选数据集目录'),nativeButton('nativeBrowse','刷新序列',()=>loadNativeSequences('')));catalog.append(paths,el('p','清单目录用于读取库存；所选数据集目录用于读取当前序列。修改目录后请刷新。','native-note'),nextText('nativeCatalogStatus'));
  const table=nextText('nativeCatalogRows','div');table.className='native-catalog-table';catalog.append(table);source.append(catalog);
  const sourceActions=el('div',undefined,'native-actions');sourceActions.append(nativeButton('nativeToPrepare','下一步：准备样本',()=>showNativeStep('prepare',{focus:true}),true),nativeButton('nativeExisting','使用已准备的样本',()=>showNativeStep('compare',{focus:true})),nativeButton('nativeVideoOnly','只推理已有视频',()=>{showDataSource('video');document.querySelector('.platform-body').scrollTop=0;}));source.append(sourceActions);
  const prepare=$('nativeStep-prepare'),draft=nextText('nativeSourceSummary','div');draft.className='native-sample-summary';prepare.append(draft);
  const range=el('div',undefined,'native-fields');range.append(nextField('nativeStart','起始标注帧（从 0 起）',0,'number'),nextField('nativeMaximum','最多帧数（留空处理全部）','','number'));prepare.append(range,el('p','保留原始帧号与时间映射。原始数据只读；ARCTIC 图像去畸变时同步更新内参。','native-note'));
  const training=el('details',undefined,'native-training');training.append(el('summary','高级：同时导出训练格式'),nextCheck('nativeTraining','为所选样本生成训练格式'),nextText('nativeTrainingHint'));prepare.append(training);
  const prepareActions=el('div',undefined,'native-actions');prepareActions.append(nativeButton('nativePrepare','校验并准备样本',prepareNative,true),nativeButton('nativeBackSource','返回选择原始数据',()=>showNativeStep('source',{focus:true})),nativeButton('nativePrepared','查看已准备样本',()=>showNativeStep('compare',{focus:true})));prepare.append(prepareActions);
  const progress=nextText('nativeProgress');progress.className='native-status';progress.setAttribute('role','status');prepare.append(progress,el('p','生成独立数据版本，方便复用和追溯；更改序列或帧范围后需重新准备。后台任务可在任务中心查看。','native-note'));
  const compare=$('nativeStep-compare'),versions=el('div',undefined,'native-fields');versions.append(nativeWide(researchSelect('nativeVersion','本次比较使用的标准样本',[])),nativeButton('nativeRefreshVersions','刷新样本列表',refreshNativeVersions));compare.append(versions);
  const warning=nextText('nativeVersionWarning');warning.className='native-version-warning';warning.setAttribute('role','status');warning.hidden=true;compare.append(warning);
  const summary=nextText('nativeSampleSummary','div');summary.className='native-sample-summary';summary.setAttribute('aria-live','polite');compare.append(summary);
  compare.append(researchSelect('nativeCalibration','算法输入的内参条件',[['unknown','不提供已知内参'],['known','提供数据集内参给支持的算法']]));
  const protocol=nextText('nativeProtocolNote','div');protocol.className='native-protocol-note';protocol.setAttribute('role','status');compare.append(protocol,nextAlgorithms('nativeAlgorithms'));
  const compareActions=el('div',undefined,'native-actions');compareActions.append(nativeButton('nativeCompare','开始比较并附加参考标注',compareNative,true),nativeButton('nativeBackPrepare','返回准备样本',()=>showNativeStep('prepare',{focus:true})));compare.append(compareActions);
  const useTraining=el('details',undefined,'native-training');useTraining.append(el('summary','高级：使用此样本配置训练'),el('p','仅支持已导出训练格式的官方 Train 样本；此操作进入配置，不会直接启动训练。','native-note'),nativeButton('nativeUseTraining','用此样本配置训练',async()=>{const row=nativeSelectedVersion();if(!row?.training_ready)throw new Error('请选择已导出训练格式的官方 Train 样本');await useTrainingDataset(row);}));compare.append(useTraining);
  $('nativeDataset').onchange=()=>{
    invalidateNativeDraft(true);const row=nativeDatasetRow();nextOptions('nativeGroup',[]);nextOptions('nativeSequence',[]);
    $('nativeRoot').value=row?.path||'';for(const option of $('nativeSplit').options)option.disabled=!row?.splits.includes(option.value);
    if(row&&!row.splits.includes($('nativeSplit').value))$('nativeSplit').value=row.splits.includes('val')?'val':row.splits[0];
    $('nativeTraining').checked=false;updateNativeDraft();if(row)loadNativeSequences('').catch(hubError);
  };
  $('nativeGroup').onchange=()=>{invalidateNativeDraft(true);loadNativeSequences($('nativeGroup').value).catch(hubError);};
  $('nativeSplit').onchange=()=>{invalidateNativeDraft(true);$('nativeTraining').checked=false;updateNativeDraft();loadNativeSequences('').catch(hubError);};
  $('nativeRoot').oninput=()=>{invalidateNativeDraft(true);updateNativeDraft();};
  $('nativeSequence').onchange=()=>{invalidateNativeDraft();updateNativeDraft();};
  for(const id of ['nativeStart','nativeMaximum','nativeTraining'])$(id).oninput=()=>{invalidateNativeDraft();updateNativeDraft();};
  $('nativeVersion').onchange=()=>{nextUI.nativeVersionWarning='';nextUI.nativeVersionChoice=(nextUI.nativeVersionChoice||0)+1;updateNativeSample();};
  $('nativeCalibration').onchange=updateNativeProtocol;$('nativeAlgorithms').onchange=updateNativeProtocol;
  $('nativeCatalogRoot').oninput=()=>{nextUI.nativeCatalogSerial=(nextUI.nativeCatalogSerial||0)+1;};
  nextOptions('nativeDataset',[],'请先读取目录清单');nextOptions('nativeSequence',[]);nextOptions('nativeVersion',[],'请选择已准备样本');
  $('nativeSequence').disabled=true;$('nativeGroup').closest('label').hidden=true;$('nativeSequencePages').hidden=true;
  showNativeStep('source');updateNativeDraft();updateNativeSample();
}
function showNativeStep(key,{focus=false}={}){
  if(!['source','prepare','compare'].includes(key))key='source';nextUI.nativeStep=key;
  for(const name of ['source','prepare','compare']){const active=name===key;$('nativeStep-'+name).hidden=!active;const button=$('nativeStepNav-'+name);button.classList.toggle('active',active);button.setAttribute('aria-current',active?'step':'false');}
  updateNativeDraft();updateNativeSample();if(focus)$('nativeHeading-'+key).focus();
}
function nativeSequenceContext(){return JSON.stringify([$('nativeDataset').value,$('nativeRoot').value.trim(),$('nativeSplit').value,$('nativeGroup').value]);}
function nativeDraftFingerprint(){return JSON.stringify([nativeSequenceContext(),$('nativeSequence').value,$('nativeStart').value,$('nativeMaximum').value,$('nativeTraining').checked]);}
function invalidateNativeDraft(sequence=false){
  if($('nativeVersion').value)nextUI.nativeVersionWarning='原始数据或帧范围已变化，已清除此前的样本选择。请重新准备，或明确选择一个已准备样本。';
  $('nativeVersion').value='';nextUI.nativeVersionChoice=(nextUI.nativeVersionChoice||0)+1;
  if(sequence){nextUI.nativeSequenceSerial=(nextUI.nativeSequenceSerial||0)+1;nextUI.nativeSequenceReady=null;$('nativeSequence').disabled=true;}
  updateNativeSample();
}
function nativeImportParameters(){
  const row=nativeDatasetRow();if(!row||!$('nativeSequence').value||nextUI.nativeSequenceReady!==nativeSequenceContext())throw new Error('请先浏览当前数据集、目录与划分，再选择原生序列');
  const sequence=(nextUI.nativeSequences||[]).find(value=>value.sequence===$('nativeSequence').value);
  if(sequence&&(sequence.reference_available===false||sequence.has_mano===false))throw new Error(sequence.reference_issue||'此序列缺少参考标注，不能用于参考评测');
  const start=$('nativeStart').value.trim(),maximum=$('nativeMaximum').value.trim();
  if(!start||!Number.isInteger(Number(start))||Number(start)<0||(maximum&&(!Number.isInteger(Number(maximum))||Number(maximum)<1)))throw new Error('起始标注帧需为非负整数，最多帧数需为正整数或留空');
  return {dataset:row.adapter,root:$('nativeRoot').value.trim(),sequence:$('nativeSequence').value,split:$('nativeSplit').value,start_frame:Number(start),max_frames:maximum?Number(maximum):null,training:$('nativeTraining').checked};
}
function nativeSummary(host,values){
  host.replaceChildren();const list=el('dl');for(const [label,value,literal]of values){const detail=el('dd',String(value??'—'));if(literal)detail.dataset.i18nIgnore='';list.append(el('dt',label),detail);}host.append(list);
}
function updateNativeDraft(){
  const row=nativeDatasetRow(),training=!!row?.training_export&&$('nativeSplit').value==='train';$('nativeTraining').disabled=!training;
  $('nativeDirectory').textContent=row?$('nativeRoot').value.trim():'';
  $('nativeTrainingHint').textContent=!row?'请先选择数据集。':row.adapter==='dexycb'?'当前 DexYCB 适配器只用于参考评测，不导出训练格式。':!training?'当前划分仅用于比较和评测，训练导出需要官方 Train。':row.adapter==='egodex'?'EgoDex 仅提供相机监督，不生成 MANO 真值。':'ARCTIC 导出相机与 MANO 监督。';
  let parameters,error;try{parameters=nativeImportParameters();}catch(value){error=value.message;}
  nativeLock('nativeToPrepare',!parameters);nativeLock('nativePrepare',!parameters||!!nextUI.nativePreparing||!!nextUI.nativeJob);
  nativeLock('nativeBrowse',!row||!$('nativeRoot').value.trim());
  if(parameters)nativeSummary($('nativeSourceSummary'),[['数据集',row.dataset||row.adapter],['官方划分',parameters.split],['序列',parameters.sequence,true],['所选帧段',`${parameters.start_frame} 起 · ${parameters.max_frames===null?'全部剩余帧':parameters.max_frames+' 帧'}`]]);
  else $('nativeSourceSummary').textContent=error||'请先选择原始数据。';
}
function updateNativeProtocol(){
  const known=$('nativeCalibration').value==='known',host=$('nativeProtocolNote');host.replaceChildren();
  host.append(el('strong',known?'当前协议：提供已知内参':'当前协议：不提供已知内参'),el('p','参考标注始终用于评测；该选项只控制是否向推理算法提供相机内参。相同视频与时间轴不代表各算法使用相同类型的额外信息。'));
  const table=el('table');for(const [label,value]of [['MINT','保持模型自身 FoV 预测'],['HaWoR · Full / Camera',known?'使用数据集内参并按原生相机模型重映射':'使用算法配置焦距或原生默认值'],['ACE · K-free','保持模型自身内参预测'],['ACE · K-given',known?'使用数据集内参':'需要提供已知内参，当前条件下不可运行']]){const tr=el('tr');tr.append(el('th',label),el('td',value));table.append(tr);}host.append(table);
  const choices=[...$('nativeAlgorithms').querySelectorAll('.algorithm')].filter(box=>box.querySelector('input[type=checkbox]')?.checked).map(box=>box.dataset.algorithm);
  const blocked=!known&&choices.includes('ace_kgiven');if(blocked)host.append(el('p','请取消 ACE · K-given，或切换为提供数据集内参。','native-version-warning'));
  nativeLock('nativeCompare',!nativeSelectedVersion()||blocked||!!nextUI.nativeComparing);
}
function updateNativeSample(){
  const row=nativeSelectedVersion(),warning=$('nativeVersionWarning');warning.textContent=nextUI.nativeVersionWarning||'';warning.hidden=!warning.textContent;
  if(row?.native){const n=row.native,plan=n.plan,ids=n.native_frame_indices||[];nativeSummary($('nativeSampleSummary'),[['本次实际运行',row.name||row.id],['数据集 / 划分',`${plan.dataset} / ${plan.split}`],['序列',plan.sequence,true],['帧数 / 原始帧号',`${n.frames} 帧 · ${ids.length?ids[0]+'–'+ids.at(-1):'见版本映射'}`],['参考类型',n.details.label_role],['标准视频',n.source,true],['样本版本',row.id,true]]);}
  else $('nativeSampleSummary').textContent='尚未选择比较样本。可复用已有样本，或先完成步骤 1、2；比较将只运行此处明确选中的版本。';
  updateNativeProtocol();updateNativeTrainingAction();
}
async function loadNativeCatalog(){
  const serial=nextUI.nativeCatalogSerial=(nextUI.nativeCatalogSerial||0)+1,root=$('nativeCatalogRoot').value.trim();
  invalidateNativeDraft(true);nextUI.catalog=[];nextOptions('nativeDataset',[]);nextOptions('nativeGroup',[]);nextOptions('nativeSequence',[]);$('nativeRoot').value='';updateNativeDraft();
  $('nativeInventory').textContent='正在读取目录清单与支持范围…';
  const result=await api('api/comparison/native/catalog?'+new URLSearchParams({root}));if(serial!==nextUI.nativeCatalogSerial||root!==$('nativeCatalogRoot').value.trim())return;
  nextUI.catalog=result.datasets;nextOptions('nativeDataset',result.datasets.flatMap((row,index)=>row.native_import?[[String(index),row.dataset+' · '+row.path]]:[]),'请选择数据集');
  $('nativeInventory').textContent=`清单 ${result.datasets.length} 项；${result.datasets.filter(row=>row.native_import).length} 个目录已有适配器。请选择数据集，再浏览序列。`;
  $('nativeCatalogStatus').textContent=(result.inventory_source==='saved_inventory'?'来源：已保存的服务器库存清单。':'来源：服务器当前目录。')+(result.verification_note||'只检查目录与基本格式存在性，完整性以所选样本准备结果为准。');
  nextTable($('nativeCatalogRows'),['数据集 / 目录','当前接入状态'],result.datasets.map(row=>[row.dataset+' · '+row.path,row.native_import?(row.format_ready===false?'格式待核对：'+row.format_issue:'已有适配器 · 待校验所选样本'):(row.available?'需要适配器或可用工作目录':'目录不存在')]));
  updateNativeDraft();await refreshNativeVersions();
}
async function loadNativeSequences(group='',offset=0){
  const row=nativeDatasetRow();if(!row)throw new Error('请先读取数据集清单并选择适配器');
  const serial=nextUI.nativeSequenceSerial=(nextUI.nativeSequenceSerial||0)+1;nextUI.nativeSequenceReady=null;nextUI.nativeSequences=[];nextOptions('nativeSequence',[]);$('nativeSequence').disabled=true;if(!group)nextOptions('nativeGroup',[],'请选择任务 / 分组');
  const context=nativeSequenceContext(),query=new URLSearchParams({dataset:row.adapter,root:$('nativeRoot').value.trim(),split:$('nativeSplit').value,group,offset,limit:50}),current=()=>serial===nextUI.nativeSequenceSerial&&context===nativeSequenceContext();
  $('nativeInventory').textContent='正在读取当前目录与划分的序列…';updateNativeDraft();
  try{
    const result=await api('api/comparison/native/sequences?'+query);if(!current())return;
    if(!group)nextOptions('nativeGroup',result.groups.map(value=>[value,value]),'请选择任务 / 分组');
    $('nativeGroup').closest?.('label')?.classList.toggle('native-wide',true);if($('nativeGroup').closest?.('label'))$('nativeGroup').closest('label').hidden=!result.groups.length&&!group;
    nextUI.nativeSequences=result.sequences;
    nextOptions('nativeSequence',result.sequences.map(value=>[value.sequence,value.name+((value.reference_available===false||value.has_mano===false)?' · 参考不可用':'')]));
    for(const option of $('nativeSequence').options){const value=result.sequences.find(item=>item.sequence===option.value);option.disabled=!!value&&(value.reference_available===false||value.has_mano===false);if(option.disabled)option.title=value.reference_issue||'缺少参考标注';}
    if([...$('nativeSequence').options].some(option=>option.value===$('nativeSequence').value&&option.disabled))$('nativeSequence').value='';
    $('nativeSequence').disabled=!result.sequences.length;$('nativeSequence').dataset.offset=String(result.offset);nextUI.nativeSequenceReady=nativeSequenceContext();
    const missing=result.sequences.filter(value=>value.reference_available===false||value.has_mano===false);
    $('nativeInventory').textContent=result.groups.length&&!group?`请选择任务 / 分组，共 ${result.groups.length} 项。`:`${result.total} 个序列 · 当前 ${result.sequences.length?result.offset+1:0}–${result.offset+result.sequences.length} · 本页 ${missing.length} 项参考不可用`;
    $('nativeSequenceHint').textContent=missing.length?`${missing[0].reference_issue||'部分序列缺少参考标注'}。这些序列已禁用；如仅推理已有视频，请切换“服务器视频”。`:'列表只做基本文件检查，所选帧段仍需校验；原始图片序列需先生成视频才能从视频入口推理。';
    $('nativeSequencePages').hidden=result.total<=50;nativeLock('nativePrevious',result.offset===0);nativeLock('nativeNext',result.offset+result.sequences.length>=result.total);updateNativeDraft();
  }catch(error){if(current()){$('nativeInventory').textContent='序列读取失败，请检查目录、划分后重新浏览。';updateNativeDraft();throw error;}}
}
async function prepareNative(){
  if(nextUI.nativePreparing||nextUI.nativeJob)return;
  const parameters=nativeImportParameters(),signature=JSON.stringify(parameters),fingerprint=nativeDraftFingerprint(),choice=nextUI.nativeVersionChoice||0,context=nativeSequenceContext();
  nextUI.nativePreparing=true;updateNativeDraft();
  try{
    const check=await api('api/comparison/workflows/preflight',{kind:'native_import',parameters});if(!check.ready)throw new Error(check.errors.join('；'));
    if(context!==nativeSequenceContext()||signature!==JSON.stringify(nativeImportParameters()))throw new Error('序列或参数已变化，请核对后重新预检');
    const row=nativeDatasetRow(),job=await api('api/comparison/workflows',{kind:'native_import',parameters,name:row.dataset+' · '+parameters.sequence+' · '+parameters.split});
    nextUI.nativeJob=job.id;nextUI.nativeJobDraft={fingerprint,choice};$('nativeProgress').textContent='样本准备已提交，尚未开始算法推理；关闭面板后继续运行。';await pollNext();
  }finally{nextUI.nativePreparing=false;updateNativeDraft();}
}
async function refreshNativeVersions(){
  const value=await loadRegistryChoices('dataset','nativeVersion',{filter:row=>!!row.native,empty:'请选择已准备样本'});if(!value)return;
  nextUI.nativeVersions=value.records.filter(row=>!!row.native);updateNativeSample();return value;
}
async function pollNativePreparation(){
  const job=nextUI.nativeJob;if(!job)return;const value=await api('api/comparison/workflows/'+job);if(job!==nextUI.nativeJob)return;
  $('nativeProgress').textContent=`${statusLabel(value.status)} · ${value.progress.stage||''}${value.progress.done?' · '+value.progress.done:''}${value.error?' · '+value.error:''}`;
  if(!TERMINAL.has(value.status))return;
  const draft=nextUI.nativeJobDraft;
  if(value.output?.dataset_id){
    const refreshed=await refreshNativeVersions();if(!refreshed||job!==nextUI.nativeJob)return;
    if(draft&&draft.fingerprint===nativeDraftFingerprint()&&draft.choice===(nextUI.nativeVersionChoice||0)){
      $('nativeVersion').value=value.output.dataset_id;nextUI.nativeVersionWarning='';updateNativeSample();showNativeStep('compare');
      $('nativeProgress').textContent+=' · 样本已准备，已选中供比较使用。';
    }else $('nativeProgress').textContent+=' · 先前选择的样本已准备完成；当前选择未被替换，可在步骤 3 明确选择该版本。';
  }
  nextUI.nativeJob=null;nextUI.nativeJobDraft=null;
  updateNativeDraft();
}
async function compareNative(){
  if(nextUI.nativeComparing)return;const row=nativeSelectedVersion();if(!row?.native)throw new Error('请先明确选择本次比较的已准备样本');
  const selections=nextChoices('nativeAlgorithms'),calibration=$('nativeCalibration').value==='known';
  if(!selections.length)throw new Error('请至少选择一个算法');if(!calibration&&selections.some(value=>value.algorithm==='ace_kgiven'))throw new Error('ACE · K-given 需要提供已知内参，请切换内参条件或取消该算法');
  nextUI.nativeComparing=true;updateNativeProtocol();
  try{const result=await api(`api/comparison/native/${row.id}/compare`,{selections,calibration});$('platformDialog').close();await openComparison(result.id);}
  finally{nextUI.nativeComparing=false;updateNativeProtocol();}
}

function studyPanel(){
  const host=$('hub-research'),section=researchSection(host,'固定集回归与性能对照','固定协议负责冻结输入、GT 和划分；回归任务负责运行选定模型。性能对照串行执行各 GPU 上限，并单独记录完整结果复用，数值偏差超门槛会标记为部分通过。');
  section.classList.add('next-wide');const fields=el('div',undefined,'next-grid');fields.append(nextField('suiteComparisons','冻结比较 ID（每行一个；留空使用当前比较）','','textarea'),nextField('suiteName','协议 / 任务名称','native-fixed-suite'),researchSelect('suiteSplit','评测划分',[['test','Test'],['validation','Validation']]),researchButton('冻结当前输入与参考标注',async()=>{const ids=$('suiteComparisons').value.split(/\s+/).filter(Boolean);if(!ids.length&&state.comparison)ids.push(state.comparison.id);const protocol=await api('api/comparison/registry/protocol',{name:$('suiteName').value,comparisons:ids,splits:Object.fromEntries(ids.map(id=>[id,$('suiteSplit').value]))});await loadRegistryChoices('protocol','suiteProtocol');$('suiteProtocol').value=protocol.id;nextUIMessage('协议已冻结，后续更换算法和检查点保持同一输入与参考。');}),researchSelect('suiteProtocol','已冻结协议',[]),researchButton('刷新协议',()=>loadRegistryChoices('protocol','suiteProtocol')));section.append(fields,nextAlgorithms('studyAlgorithms',true));
  const candidate=el('div',undefined,'next-grid');candidate.append(nextField('studyCheckpoint','附加 MINT 候选检查点（可选）'),nextField('studyConfig','候选模型配置（可选）'),nextField('studyGPUs','性能试验 GPU 上限','1,2,4,8'),nextField('studyTolerance','性能试验最大关节坐标差异 mm',.1,'number'));section.append(candidate);
  const actions=el('div',undefined,'button-row');actions.append(researchButton('运行固定集回归',()=>startStudy('regression')),researchButton('对当前输入运行卡数 / 缓存试验',()=>startStudy('performance')),researchButton('取消当前试验',()=>studyAction('cancel')),researchButton('恢复未完成试验',()=>studyAction('resume')),researchButton('下载完整试验报告',async()=>{if(!nextUI.study)throw new Error('尚未选择试验');download('mint-study-'+nextUI.study+'.json',JSON.stringify(await api('api/comparison/studies/'+nextUI.study),null,2));}));section.append(actions,nextText('nextMessage'),nextText('studySummary'),nextText('studyRows','div'));
}
function nextUIMessage(text){$('nextMessage').textContent=text;if($('nextMessage').closest('[hidden]'))showNotice(text,false);}
async function startStudy(kind){let selections=nextChoices('studyAlgorithms');if($('studyCheckpoint').value.trim())selections.push({algorithm:'mint',checkpoint:$('studyCheckpoint').value.trim(),...($('studyConfig').value.trim()?{config:$('studyConfig').value.trim()}:{}),options:{max_gpus:8,resource_policy:'shared'}});const body={kind,selections,name:$('suiteName').value,split:$('suiteSplit').value,atol_m:Number($('studyTolerance').value)/1000};if(kind==='regression'){body.protocol_id=$('suiteProtocol').value;if(!body.protocol_id)throw new Error('请先选择或冻结协议');}else{if(!state.comparison)throw new Error('请先打开一个比较输入');body.comparison_id=state.comparison.id;body.gpu_counts=$('studyGPUs').value.split(',').map(v=>Number(v.trim()));}const job=await api('api/comparison/studies',body);nextUI.study=job.id;await pollNext();}
async function studyAction(action){if(!nextUI.study)throw new Error('尚未选择试验');await api(`api/comparison/studies/${nextUI.study}/${action}`,{});await pollNext();}
function showStudy(value){$('studySummary').textContent=`${value.request.name} · ${statusLabel(value.status)} · ${value.progress.done}/${value.progress.total}${value.error?' · '+value.error:''}${value.evaluation_id?' · 已生成固定协议评测':''}`;nextTable($('studyRows'),['输入 / GPU 上限','运行状态','耗时范围','数值门槛 / 结果'],value.rows.map(row=>{const link=el('a','打开比较');link.href='/compare?id='+row.comparison_id;return [`${row.case+1} / ${row.gpu_cap??'原配置'}`,row.statuses.map(statusLabel).join('、'),`${fmt(row.wall_seconds_including_queue,1)} s（含排队）${row.result_reuse?'；复用查询 '+fmt(row.result_reuse.lookup_seconds,3)+' s':''}`,el('span')].map((cell,index)=>{if(index===3){const box=el('div');box.append(el('p',row.numerical_gate===undefined?'协议评测':row.numerical_gate?'数值一致':'需检查：差异或缺失'),link);return box;}return cell;});}));}

function curvePanel(){const section=researchSection($('hub-research'),'训练曲线与完整检查点','每个实际优化器步骤记录本地 loss、学习率、耗时和 GPU 峰值。恢复后同一步以最新尝试显示，模型仍需固定集评测。');section.classList.add('next-wide');const row=el('div',undefined,'next-grid');row.append(nextField('curveWorkflow','训练 Workflow ID'),researchButton('读取训练曲线',async()=>{nextUI.curve=$('curveWorkflow').value.trim();await refreshCurves();}),researchSelect('curveMetric','曲线指标',[]));section.append(row,nextText('curveSummary'));const canvas=el('canvas');canvas.id='trainingCurve';canvas.width=1000;canvas.height=240;canvas.className='next-chart';section.append(canvas,nextText('curveCheckpoints','div'));$('curveMetric').onchange=drawTrainingCurve;}
async function refreshCurves(){const id=nextUI.curve;if(!id)return;const serial=nextUI.curveSerial=(nextUI.curveSerial||0)+1;const result=await api(`api/comparison/workflows/${id}/curves`);if(id!==nextUI.curve||serial!==nextUI.curveSerial)return;nextUI.curves=result;const metrics=[...new Set(result.points.flatMap(p=>Object.keys(p.metrics)))];const old=$('curveMetric').value;nextOptions('curveMetric',metrics.map(v=>[v,v]),'选择指标');$('curveMetric').value=metrics.includes(old)?old:(metrics.find(v=>v.toLowerCase().includes('loss'))||metrics[0]||'');$('curveSummary').textContent=`${statusLabel(result.status)} · ${result.available_points} 个优化器步骤记录 · ${result.attempts.length} 次尝试${result.tail_limited?' · 显示最近记录':''}`;drawTrainingCurve();nextTable($('curveCheckpoints'),['检查点','完整性','操作'],result.checkpoints.map(row=>{const button=researchButton('设为固定集回归候选',()=>{$('studyCheckpoint').value=row.model;$('studyConfig').value=row.path.replace(/\/training\/step_[^/]+$/,'/effective-config.yaml');showResearchView('regression');revealPlatformTarget($('studyCheckpoint'));nextUIMessage('候选已填入回归表单；请选择冻结协议后运行。');});button.disabled=!row.complete;const register=researchButton('填入模型登记',()=>{$('modelName').value='MINT · '+row.path.split('/').pop();$('modelPath').value=row.model;$('modelConfig').value=row.path.replace(/\/training\/step_[^/]+$/,'/effective-config.yaml');revealPlatformTarget($('modelPath').closest('section'));});register.disabled=!row.complete;const actions=el('div',undefined,'button-row');actions.append(button,register);return [row.path.split('/').pop(),row.complete?'文件身份校验通过':'文件缺失或变化',actions];}));}
function drawTrainingCurve(){const canvas=$('trainingCurve'),ctx=canvas.getContext('2d');ctx.clearRect(0,0,canvas.width,canvas.height);ctx.fillStyle='#93aaa3';ctx.font='13px sans-serif';const metric=$('curveMetric').value,points=(nextUI.curves?.points||[]).filter(row=>Number.isFinite(row.metrics[metric]));if(!points.length){ctx.fillText('训练开始后显示曲线',45,45);return;}const values=points.map(p=>p.metrics[metric]),low=Math.min(...values),high=Math.max(...values),first=points[0].step,last=points.at(-1).step;ctx.fillText(`${metric}  ${fmt(low,6)} – ${fmt(high,6)}`,45,22);ctx.strokeStyle='#37584e';ctx.strokeRect(45,36,920,164);ctx.strokeStyle='#b9f45a';ctx.lineWidth=2;ctx.beginPath();points.forEach((p,i)=>{const x=45+(p.step-first)/Math.max(1,last-first)*920,y=200-(p.metrics[metric]-low)/Math.max(1e-12,high-low)*164;i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.stroke();ctx.fillText(`step ${first}`,45,225);ctx.fillText(`step ${last}`,895,225);}

function qualityExtensions(){const host=$('hub-quality'),section=researchSection(host,'误差分解与二维标注修正','先选择同一输入的预测与参考。修正坐标使用原视频视图的像素网格，保存为独立人工版本，保留推理投影和审阅人；不会生成三维或 MANO GT。');const fields=el('div',undefined,'next-grid');fields.append(researchSelect('detailRun','当前输入预测结果',[]),researchSelect('detailReference','参考 / 对照结果',[]),researchButton('刷新当前结果',refreshDetailRuns),researchButton('分析左右手 / 深度 / 画面边界',showBreakdown),researchButton('下载误差分解 JSON',()=>{if(!nextUI.breakdown)throw new Error('请先分析结果');download('mint-error-breakdown.json',JSON.stringify(nextUI.breakdown,null,2));}));section.append(fields,nextText('breakdownSummary'),nextText('breakdownRows','div'));const joints=el('details');joints.append(el('summary','逐关节误差与有效计数'),nextText('jointRows','div'));section.append(joints);
  const editor=el('details');editor.append(el('summary','人工修正：21 个二维点、可见性与左右手'));const controls=el('div',undefined,'next-grid');controls.append(nextField('annotationFrame','样本帧（从 1 起）',1,'number'),researchButton('读取当前播放帧',async()=>{$('annotationFrame').value=String(state.frame+1);await loadAnnotation();}),researchButton('读取指定帧',loadAnnotation),researchSelect('annotationHand','编辑的源手槽',[['0','Left 源槽'],['1','Right 源槽']]),researchSelect('annotationJoint','关节编号',Array.from({length:21},(_,i)=>[String(i),String(i)+(i===0?' · wrist':'')])),nextCheck('annotationVisible','该点可见'),nextField('annotationX','X 像素',0,'number'),nextField('annotationY','Y 像素',0,'number'),researchButton('应用坐标 / 可见性',applyAnnotationPoint));editor.append(controls);const canvas=el('canvas');canvas.id='annotationCanvas';canvas.width=640;canvas.height=480;canvas.className='next-annotation';canvas.tabIndex=0;canvas.setAttribute('aria-label','二维关键点编辑画布，也可通过 X、Y 输入框编辑');editor.append(canvas);const save=el('div',undefined,'next-grid');save.append(nextCheck('annotationSwap','交换源槽对应的左右手标签'),nextField('annotationReviewer','审阅人（必填）'),nextField('annotationComment','修正说明'),researchButton('撤销一步',()=>{if(nextUI.undo.length){const value=nextUI.undo.pop();nextUI.annotation.points=value.points;nextUI.annotation.visible=value.visible;annotationChanged();drawAnnotation();syncAnnotationPoint();}}),researchButton('恢复原推理投影',()=>{if(!nextUI.annotation)return;pushAnnotationUndo();setAnnotationArrays(nextUI.annotation.original);annotationChanged();drawAnnotation();syncAnnotationPoint();}),researchButton('保存独立修正版本',saveAnnotation),researchButton('查看修正历史',async()=>{const value=nextUI.annotation;if(!value)throw new Error('请先读取一帧');const result=await api('api/comparison/annotations/history?'+new URLSearchParams({run_id:value.run_id,frame:value.frame}));researchDetails(result);}),researchButton('导出当前修正 JSON',()=>{if(!nextUI.savedAnnotation)throw new Error('请先保存修正版本');download('mint-2d-annotation.json',JSON.stringify(nextUI.savedAnnotation,null,2));}));const developer=el('details');developer.append(el('summary','开发与接口验收选项'),nextCheck('annotationTest','接口验收测试（不作为人工标注）'));editor.append(save,developer,nextText('annotationDraftStatus'),nextText('annotationDraftActions','div'),nextText('annotationStatus'));section.append(editor);$('annotationDraftStatus').className='annotation-draft-status';$('annotationDraftStatus').setAttribute('role','status');$('annotationHand').onchange=()=>{syncAnnotationPoint();drawAnnotation();};$('annotationJoint').onchange=()=>{syncAnnotationPoint();drawAnnotation();};for(const id of ['annotationSwap','annotationReviewer','annotationComment','annotationTest'])$(id).addEventListener('input',annotationChanged);canvas.addEventListener('keydown',annotationKeyboard);editor.prepend(el('p','画布方向键微调选中点，Shift＋方向键移动 10 像素；[ / ] 切换关节。未保存修改自动保留本机草稿，仍需保存独立版本才能提交服务器。','hint'));const issueTools=el('div',undefined,'button-row');issueTools.append(researchButton('上一问题片段',()=>nextAnnotationIssue(-1)),researchButton('下一问题片段',()=>nextAnnotationIssue(1)));editor.prepend(issueTools);
  canvas.onpointerdown=event=>{if(!nextUI.annotation)return;const point=annotationPointer(event),slot=Number($('annotationHand').value);let nearest=Number($('annotationJoint').value),distance=20*canvas.width/canvas.getBoundingClientRect().width;nextUI.annotation.points[slot].forEach((p,i)=>{const d=Math.hypot(p[0]-point[0],p[1]-point[1]);if(d<distance){distance=d;nearest=i;}});$('annotationJoint').value=String(nearest);pushAnnotationUndo();canvas.setPointerCapture(event.pointerId);moveAnnotation(event);};canvas.onpointermove=event=>{if(event.buttons===1&&canvas.hasPointerCapture(event.pointerId))moveAnnotation(event);};canvas.onpointerup=event=>{if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);};
}
function refreshDetailRuns(){const runs=(state.comparison?.runs||[]).filter(r=>r.result);nextOptions('detailRun',runs.map(r=>[r.id,r.label+' · '+r.id.slice(0,8)]));nextOptions('detailReference',runs.map(r=>[r.id,(r.result.reference_role?'参考 · ':'对照 · ')+r.label]));if(!$('detailRun').value&&runs.length)$('detailRun').value=(runs.find(r=>!r.result.reference_role)||runs[0]).id;if(!$('detailReference').value&&runs.length>1)$('detailReference').value=(runs.find(r=>r.result.reference_role)||runs[1]).id;}
async function showBreakdown(){const result=await api(`api/comparison/runs/${$('detailRun').value}/breakdown?`+new URLSearchParams({reference_run:$('detailReference').value}));nextUI.breakdown=result;const names={all:'全部',left:'左手',right:'右手',depth_0_30cm:'深度 <30 cm',depth_30_60cm:'深度 30–60 cm',depth_60_100cm:'深度 60–100 cm',depth_ge_100cm:'深度 ≥100 cm',all_gt_joints_in_frame:'参考点全部在画面内',some_gt_joints_out_of_frame:'参考点部分在画面外'};$('breakdownSummary').textContent=`对齐 ${result.aligned_frames} 帧 · ${result.comparison_role==='declared_GT_error'?'声明参考标注下的误差':'两个模型的差异'}。召回只统计有效正手标注；精确率与遮挡率需要相应标注。`;nextTable($('breakdownRows'),['分组','有效参考手帧','几何覆盖 / 存在性召回','绝对 / 腕对齐 mm','重投影 px / 腕深度 mm'],Object.entries(result.groups).map(([key,row])=>[names[key]||key,row.gt_hand_frames,`${row.geometry_coverage===null?'—':fmt(row.geometry_coverage*100,1)+'%'} / ${row.detection_recall_on_labelled_positive_hands===null?'—':fmt(row.detection_recall_on_labelled_positive_hands*100,1)+'%'}`,`${fmt(row.absolute_joint_mm.mean)} / ${fmt(row.wrist_aligned_joint_mm.mean)}`,`${fmt(row.reprojection_px.mean)} / ${fmt(row.wrist_depth_abs_mm.mean)}`]));nextTable($('jointRows'),['关节','有效手帧','绝对 mm','腕对齐 mm'],result.joints.map(row=>[row.joint,row.paired_hand_frames,fmt(row.absolute_mm),fmt(row.wrist_aligned_mm)]));}
function setAnnotationArrays(value){nextUI.annotation.points=value.points.map(hand=>hand.map(p=>p.map(v=>Number.isFinite(v)?v:0)));nextUI.annotation.visible=value.visible.map(hand=>hand.slice());}
async function loadAnnotation(){const run=$('detailRun').value;if(!run)throw new Error('请先打开有结果的比较');persistAnnotationDraft();const serial=nextUI.annotationSerial=(nextUI.annotationSerial||0)+1;const frame=Number($('annotationFrame').value)-1,context=await api(`api/comparison/runs/${run}/annotation-context?frame=${frame}`);const saved=context.head?await api('api/comparison/registry/record/'+context.head):null;const image=new Image();await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(new Error('原视频图像读取失败'));image.src=`/api/comparison/runs/${run}/image?frame=${frame}`;});if(serial!==nextUI.annotationSerial)return;nextUI.annotation={...context,original:{points:context.points,visible:context.visible}};setAnnotationArrays(saved||context);nextUI.undo=[];nextUI.savedAnnotation=saved;$('annotationSwap').checked=saved?.hand_assignment?.[0]==='right';$('annotationTest').checked=saved?.purpose==='interface_test';$('annotationReviewer').value=saved?.reviewer||$('annotationReviewer').value;$('annotationComment').value=saved?.comment||'';nextUI.image=image;$('annotationCanvas').width=context.width;$('annotationCanvas').height=context.height;drawAnnotation();syncAnnotationPoint();nextUI.annotationRevision=0;nextUI.savedRevision=0;nextUI.dirty=false;refreshAnnotationDraft();$('annotationStatus').textContent=`源帧 ${context.source_frame} · ${context.width}×${context.height} · ${context.head?'已有修正 '+context.head.slice(0,12):'尚无人工修正'}；点击或拖动点，也可输入精确坐标。`;}
function pushAnnotationUndo(){nextUI.undo.push({points:structuredClone(nextUI.annotation.points),visible:structuredClone(nextUI.annotation.visible)});if(nextUI.undo.length>50)nextUI.undo.shift();}
function annotationPointer(event){const canvas=$('annotationCanvas'),rect=canvas.getBoundingClientRect();return [Math.max(0,Math.min(canvas.width-1,(event.clientX-rect.left)*canvas.width/rect.width)),Math.max(0,Math.min(canvas.height-1,(event.clientY-rect.top)*canvas.height/rect.height))];}
function moveAnnotation(event){const slot=Number($('annotationHand').value),joint=Number($('annotationJoint').value);nextUI.annotation.points[slot][joint]=annotationPointer(event);nextUI.annotation.visible[slot][joint]=true;annotationChanged();drawAnnotation();syncAnnotationPoint();}
function syncAnnotationPoint(){if(!nextUI.annotation)return;const hand=Number($('annotationHand').value),joint=Number($('annotationJoint').value),p=nextUI.annotation.points[hand][joint];$('annotationX').value=fmt(p[0],2);$('annotationY').value=fmt(p[1],2);$('annotationVisible').checked=nextUI.annotation.visible[hand][joint];}
function applyAnnotationPoint(){if(!nextUI.annotation)throw new Error('请先读取一帧');const point=[Number($('annotationX').value),Number($('annotationY').value)];if(point.some(v=>!Number.isFinite(v)))throw new Error('坐标需要是有限数字');pushAnnotationUndo();const slot=Number($('annotationHand').value),joint=Number($('annotationJoint').value);nextUI.annotation.points[slot][joint]=point;nextUI.annotation.visible[slot][joint]=$('annotationVisible').checked;annotationChanged();drawAnnotation();}
function drawAnnotation(){const canvas=$('annotationCanvas'),ctx=canvas.getContext('2d');ctx.clearRect(0,0,canvas.width,canvas.height);if(nextUI.image)ctx.drawImage(nextUI.image,0,0,canvas.width,canvas.height);if(!nextUI.annotation)return;const radius=Math.max(3,canvas.width/190);nextUI.annotation.points.forEach((points,slot)=>{ctx.strokeStyle=ctx.fillStyle=slot?'#50d8d6':'#c4f853';ctx.lineWidth=Math.max(2,canvas.width/350);for(let finger=0;finger<5;finger++){const chain=[0,1+finger*4,2+finger*4,3+finger*4,4+finger*4];for(let i=1;i<chain.length;i++){const a=chain[i-1],b=chain[i];if(nextUI.annotation.visible[slot][a]&&nextUI.annotation.visible[slot][b]){ctx.beginPath();ctx.moveTo(...points[a]);ctx.lineTo(...points[b]);ctx.stroke();}}}points.forEach((point,joint)=>{if(!nextUI.annotation.visible[slot][joint])return;ctx.beginPath();ctx.arc(...point,radius,0,Math.PI*2);ctx.fill();ctx.font=`${radius*3}px sans-serif`;ctx.fillText(String(joint),point[0]+radius,point[1]-radius);});});const slot=Number($('annotationHand').value),joint=Number($('annotationJoint').value),point=nextUI.annotation.points[slot][joint];ctx.save();ctx.strokeStyle='#fff';ctx.lineWidth=Math.max(2,radius/2);ctx.beginPath();ctx.arc(...point,radius*2.2,0,Math.PI*2);ctx.stroke();ctx.fillStyle='#07120f';ctx.fillRect(point[0]+radius*3,point[1]-radius*5,radius*22,radius*4);ctx.fillStyle='#fff';ctx.font=`${radius*3}px sans-serif`;ctx.fillText(`${slot?'右源槽':'左源槽'} · ${joint}`,point[0]+radius*3,point[1]-radius*2);ctx.restore();}
async function saveAnnotation(){const value=nextUI.annotation;if(!value)throw new Error('请先读取一帧');const revision=nextUI.annotationRevision||0,request={run_id:value.run_id,frame:value.frame,parent:value.head,points:structuredClone(value.points),visible:structuredClone(value.visible),reviewer:$('annotationReviewer').value,purpose:$('annotationTest').checked?'interface_test':'review',comment:$('annotationComment').value,hand_assignment:$('annotationSwap').checked?['right','left']:['left','right']};let saved;try{saved=await api('api/comparison/annotations',request);}catch(error){persistAnnotationDraft();throw error;}if(nextUI.annotation!==value)return;value.head=saved.id;nextUI.savedAnnotation=saved;if(revision===(nextUI.annotationRevision||0)){nextUI.dirty=false;nextUI.savedRevision=revision;try{localStorage.removeItem(annotationDraftKey(value));}catch{}}else persistAnnotationDraft();refreshAnnotationDraft();$('annotationStatus').textContent=(saved.purpose==='interface_test'?'已保存接口验收版本 ':'已保存独立二维修正版本 ')+saved.id.slice(0,16)+(nextUI.dirty?'；保存期间的新修改仍是本机草稿。':'；原始几何与权重未修改。');}

function productionPanel(){const section=researchSection($('hub-research'),'原生后端伪标签生产','先在比较 / 批量入口运行 MINT，再将完整相机与 MANO 原生结果生成可训练版本。Ray 并行发布视频与标注，异常运动只作掩码；全时间轴和原始时间戳保留。MegaSAM / MoGe / GeoCalib 参考链仍有独立依赖检查。');section.classList.add('next-wide');section.id='nativeProductionSection';section.append(el('p','推荐路径 · 已完成原生 MINT 推理 → 只读预检 → Ray 发布伪标签版本 → 明确选择训练用途。独立参考研究链不属于此路径。','hint'));section.append(nextField('productionRuns','已完成原生 MINT Run ID（每行一个）','','textarea'),researchButton('选择当前比较的 MINT 结果',async()=>{if(!state.comparison)throw new Error('请先打开一个比较');$('productionComparisonPicker').value=state.comparison.id;await refreshProductionRunChoices();const ids=(state.comparison.runs||[]).filter(r=>r.algorithm==='mint'&&r.status==='completed'&&r.result&&!r.imported).map(r=>r.id);$('productionRuns').value=ids.join('\n');for(const option of $('productionRunPicker').options)option.selected=ids.includes(option.value);$('productionReadiness').dataset.ready='pending';$('productionReadiness').textContent='来源已选择，请预检原始文件、划分与依赖。';}),researchSelect('productionSplit','产物划分',[['train','Train（原生测试集禁止转入）'],['validation','Validation'],['test','Test']]),nextField('productionAcceleration','腕部加速度掩码阈值 m/s²',30,'number'),researchButton('仅预检生产输入',async()=>{const check=await api('api/comparison/workflows/preflight',{kind:'produce_pseudo',parameters:productionParameters()});$('productionReadiness').dataset.ready=String(check.ready);$('productionReadiness').textContent=check.ready?'可启动 · 生产输入与依赖预检通过。':'暂不可启动 · '+check.errors.join('；');}),researchButton('预检并启动 Ray 生产',async()=>{const parameters={run_ids:$('productionRuns').value.split(/\s+/).filter(Boolean),split:$('productionSplit').value,acceleration_limit:Number($('productionAcceleration').value)};const check=await api('api/comparison/workflows/preflight',{kind:'produce_pseudo',parameters});if(!check.ready)throw new Error(check.errors.join('；'));const result=await api('api/comparison/workflows',{kind:'produce_pseudo',parameters,name:'MINT native pseudo labels'});await inspectWorkflow(result.id);nextUIMessage('生产任务已进入任务中心，输出标记为模型生成伪标签。');}));const readiness=nextText('productionReadiness');readiness.dataset.ready='pending';readiness.textContent='待预检 · 选择来源结果后可先检查，不启动生产任务。';section.append(readiness);section.addEventListener('input',()=>{readiness.dataset.ready='pending';readiness.textContent='输入已变化，请重新预检。';});section.addEventListener('change',()=>{readiness.dataset.ready='pending';readiness.textContent='输入已变化，请重新预检。';});}

async function pollNext(){clearTimeout(nextUI.timer);if(!$('platformDialog').open)return;try{await pollNativePreparation();if(nextUI.study)showStudy(await api('api/comparison/studies/'+nextUI.study));if(nextUI.curve&&platform.pane==='research')await refreshCurves();}catch(error){hubError(error);}finally{if($('platformDialog').open)nextUI.timer=setTimeout(pollNext,3000);}}
const RESEARCH_VIEWS=[['train','训练任务','workflowKind'],['curves','曲线与检查点','curveWorkflow'],['regression','回归与性能','suiteProtocol'],['protocol','固定协议与评测','offlineProtocol'],['production','伪标签生产','productionRuns'],['models','模型与发布','modelName']];
function researchViewForTarget(target){const region=target?.closest('[data-research-view]');return region?.dataset.researchView||null;}
function showResearchView(view,{route=true,clearSection=false}={}){
  if(!RESEARCH_VIEWS.some(row=>row[0]===view))view='train';nextUI.researchView=view;const host=$('hub-research');host.dataset.researchTab=view;
  for(const section of host.querySelectorAll(':scope > [data-research-view]'))section.hidden=section.dataset.researchView!==view;
  for(const button of host.querySelectorAll('[data-research-tab]')){const active=button.dataset.researchTab===view;button.classList.toggle('active',active);button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;}
  if(route&&platform.pane==='research'&&$('platformDialog').open){const url=new URL(location.href);url.searchParams.set('research_tab',view);if(clearSection)url.searchParams.delete('section');window.history.replaceState(null,'',url.pathname+url.search+url.hash);}
}
function revealResearchTarget(target){const view=researchViewForTarget(target);if(view)showResearchView(view);}
function initialResearchView(params,current='train'){
  if(RESEARCH_VIEWS.some(row=>row[0]===params.get('research_tab')))return params.get('research_tab');
  if(params.has('study'))return 'regression';if(params.has('workflow'))return 'train';return current||'train';
}
function researchNavigation(){
  const host=$('hub-research'),nav=el('nav',undefined,'next-sections research-view-tabs');nav.setAttribute('aria-label','训练与模型视图');nav.setAttribute('role','tablist');
  for(const [view,label,id] of RESEARCH_VIEWS){const section=$(id)?.closest('section');if(!section)continue;section.dataset.researchView=view;if(!section.id)section.id='research-pane-'+view;section.setAttribute('role','tabpanel');const button=el('button',label);button.id='research-tab-'+view;button.dataset.researchTab=view;button.setAttribute('role','tab');button.setAttribute('aria-controls',section.id);section.setAttribute('aria-labelledby',button.id);button.onclick=()=>{showResearchView(view,{clearSection:true});document.querySelector('.platform-body').scrollTop=0;};button.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const buttons=[...nav.querySelectorAll('button')],index=buttons.indexOf(button),next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;buttons[next].click();buttons[next].focus();};nav.append(button);}
  const experiment=[...host.children].find(node=>node.contains($('experimentBaseline')));if(experiment)experiment.dataset.researchView='regression';host.prepend(nav);
  showResearchView(initialResearchView(new URLSearchParams(location.search)),{route:false});
  window.addEventListener('mint-platform-open',event=>{if(event.detail==='research')showResearchView(initialResearchView(new URLSearchParams(location.search),nextUI.researchView),{route:false});});
}
function showDataSource(kind,{route=true}={}){
  if(!['video','lerobot','native'].includes(kind))kind='video';nextUI.dataSource=kind;
  for(const section of $('hub-data').querySelectorAll('[data-data-source]'))section.hidden=section.dataset.dataSource!==kind;
  for(const button of document.querySelectorAll('[data-source-tab]')){const active=button.dataset.sourceTab===kind;button.classList.toggle('active',active);button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;}
  $('hub-data').querySelector('.hub-footer').hidden=kind==='native';
  if(route&&platform.pane==='data'&&$('platformDialog').open){const url=new URL(location.href);url.searchParams.set('source_type',kind);url.searchParams.delete('section');window.history.replaceState(null,'',url.pathname+url.search+url.hash);}
}
function initializeDataViews(){
  const host=$('hub-data'),video=$('collectionDirectory').closest('section'),lerobot=$('hubDatasetRoot').closest('section');video.dataset.dataSource='video';lerobot.dataset.dataSource='lerobot';video.parentElement.classList.add('data-source-panels');
  const nav=el('nav',undefined,'data-source-tabs');nav.id='dataSourceTabs';nav.tabIndex=-1;nav.setAttribute('role','tablist');nav.setAttribute('aria-label','选择输入来源');
  for(const [kind,label,section]of [['video','服务器视频',video],['lerobot','LeRobot Episode',lerobot],['native','公开数据集 / 参考标注',$('nativeDataPanel')]]){if(!section.id)section.id='data-source-'+kind;const button=el('button',label);button.id='data-tab-'+kind;button.dataset.sourceTab=kind;button.setAttribute('role','tab');button.setAttribute('aria-controls',section.id);section.setAttribute('role','tabpanel');section.setAttribute('aria-labelledby',button.id);button.onclick=()=>{showDataSource(kind);document.querySelector('.platform-body').scrollTop=0;};button.onkeydown=event=>{if(!['ArrowLeft','ArrowRight'].includes(event.key))return;event.preventDefault();const buttons=[...nav.children],index=buttons.indexOf(button),next=(index+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;buttons[next].click();buttons[next].focus();};nav.append(button);}
  host.prepend(nav);showDataSource(new URLSearchParams(location.search).get('source_type')||'video',{route:false});
  window.addEventListener('mint-platform-open',event=>{if(event.detail==='data')showDataSource(new URLSearchParams(location.search).get('source_type')||nextUI.dataSource,{route:false});});
}

function initializeEnhancements(){nativePanel();initializeDataViews();qualityExtensions();studyPanel();curvePanel();productionPanel();researchNavigation();window.addEventListener('mint-platform-open',event=>{const tab=event.detail;if(tab==='quality'){refreshDetailRuns();$('annotationFrame').value=String(state.frame+1);}if(tab==='research'){loadRegistryChoices('protocol','suiteProtocol').catch(hubError);if(researchUI.workflow){nextUI.curve=researchUI.workflow;$('curveWorkflow').value=researchUI.workflow;}}if(tab==='data')refreshNativeVersions().catch(hubError);pollNext();});window.addEventListener('mint-workflow-inspected',event=>{if(event.detail.request.kind==='train'){syncTrainingWorkflowPicker(event.detail);nextUI.curve=event.detail.id;$('curveWorkflow').value=event.detail.id;$('curveWorkflowPicker').value=event.detail.id;refreshCurves().catch(hubError);}});$('platformDialog').addEventListener('close',()=>clearTimeout(nextUI.timer));const id=new URLSearchParams(location.search).get('study');if(id){nextUI.study=id;openPlatform('research').catch(hubError);}}
document.addEventListener('DOMContentLoaded',initializeEnhancements);

function annotationDraftKey(value){return 'mint.annotation.draft.v1:'+value.run_id+':'+value.frame;}
function annotationDraftSnapshot(){const value=nextUI.annotation;if(!value)return null;return {run_id:value.run_id,frame:value.frame,parent:value.head,width:value.width,height:value.height,result_identity:value.result_identity,source_identity:value.source_identity,points:value.points,visible:value.visible,reviewer:$('annotationReviewer').value,comment:$('annotationComment').value,swap:$('annotationSwap').checked,purpose:$('annotationTest').checked?'interface_test':'review',at:Date.now()};}
function persistAnnotationDraft(){
  if(!nextUI.annotation||!nextUI.dirty)return;
  try{localStorage.setItem(annotationDraftKey(nextUI.annotation),JSON.stringify(annotationDraftSnapshot()));nextUI.draftPersisted=true;}catch{nextUI.draftPersisted=false;}
}
function annotationChanged(){if(!nextUI.annotation)return;nextUI.annotationRevision=(nextUI.annotationRevision||0)+1;nextUI.dirty=true;persistAnnotationDraft();refreshAnnotationDraft();}
function validAnnotationDraft(draft,value){return !!draft&&draft.run_id===value.run_id&&draft.frame===value.frame&&draft.parent===value.head&&draft.width===value.width&&draft.height===value.height&&JSON.stringify(draft.result_identity)===JSON.stringify(value.result_identity)&&JSON.stringify(draft.source_identity)===JSON.stringify(value.source_identity)&&Array.isArray(draft.points)&&draft.points.length===2&&draft.points.every(hand=>Array.isArray(hand)&&hand.length===21&&hand.every(p=>Array.isArray(p)&&p.length===2&&p.every(Number.isFinite)))&&Array.isArray(draft.visible)&&draft.visible.length===2&&draft.visible.every(hand=>Array.isArray(hand)&&hand.length===21&&hand.every(v=>typeof v==='boolean'));}
function refreshAnnotationDraft(){
  const returnButton=$('annotationDraftReturn');if(returnButton){returnButton.hidden=!nextUI.dirty;returnButton.textContent=nextUI.draftPersisted?'二维修正未提交 · 已保留草稿':'二维修正未提交 · 请先保存';}
  const status=$('annotationDraftStatus'),actions=$('annotationDraftActions');if(!status||!actions)return;actions.replaceChildren();status.dataset.dirty=String(nextUI.dirty);
  if(nextUI.dirty){status.textContent=nextUI.draftPersisted?'有未提交修改 · 已保留本机草稿，尚未保存到服务器。':'有未提交修改 · 浏览器无法保存草稿，请先保存独立版本再离开。';return;}
  status.textContent='当前没有未提交修改。';if(!nextUI.annotation)return;
  let draft;try{draft=JSON.parse(localStorage.getItem(annotationDraftKey(nextUI.annotation))||'null');}catch{return;}
  if(!draft)return;const compatible=validAnnotationDraft(draft,nextUI.annotation);status.textContent=compatible?'发现此帧的本机草稿，可以恢复；恢复后仍需保存独立版本。':'发现旧草稿，但服务器版本或图像协议已变化；不会自动覆盖当前版本。';
  if(compatible)actions.append(researchButton('恢复此帧草稿',()=>{setAnnotationArrays(draft);$('annotationReviewer').value=draft.reviewer||'';$('annotationComment').value=draft.comment||'';$('annotationSwap').checked=!!draft.swap;$('annotationTest').checked=draft.purpose==='interface_test';nextUI.undo=[];annotationChanged();drawAnnotation();syncAnnotationPoint();}));
  actions.append(researchButton('丢弃本机草稿',()=>{try{localStorage.removeItem(annotationDraftKey(nextUI.annotation));}catch{}refreshAnnotationDraft();}));
}
function annotationKeyboard(event){
  if(!nextUI.annotation||event.ctrlKey||event.metaKey||event.altKey)return;
  if(event.key==='['||event.key===']'){event.preventDefault();event.stopPropagation();$('annotationJoint').value=String((Number($('annotationJoint').value)+(event.key===']'?1:20))%21);syncAnnotationPoint();drawAnnotation();return;}
  const delta={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[event.key];if(!delta)return;event.preventDefault();event.stopPropagation();pushAnnotationUndo();const slot=Number($('annotationHand').value),joint=Number($('annotationJoint').value),point=nextUI.annotation.points[slot][joint],scale=event.shiftKey?10:1;nextUI.annotation.points[slot][joint]=[Math.max(0,Math.min(nextUI.annotation.width-1,point[0]+delta[0]*scale)),Math.max(0,Math.min(nextUI.annotation.height-1,point[1]+delta[1]*scale))];annotationChanged();drawAnnotation();syncAnnotationPoint();
}
async function nextAnnotationIssue(direction){
  const quality=researchUI.quality;if(!quality||quality.run_id!==$('detailRun').value)throw new Error('请先分析所选预测的逐帧质检，再定位问题片段');
  let issues=quality.issues,current=nextUI.annotation?.run_id===quality.run_id?nextUI.annotation.frame:Number($('annotationFrame').value)-1;
  let issue=direction>0?issues.find(row=>row.start>current):[...issues].reverse().find(row=>row.start<current);
  if(!issue&&quality.issue_count>issues.length){const offset=Math.max(0,quality.offset+direction*50);if(offset!==quality.offset&&offset<quality.issue_count){await showQuality(await api(`api/comparison/quality/${quality.id}?offset=${offset}`));issues=researchUI.quality.issues;issue=direction>0?issues[0]:issues.at(-1);}}
  if(!issue)throw new Error(direction>0?'当前已到最后一个问题片段':'当前已到第一个问题片段');$('annotationFrame').value=String(issue.start+1);await loadAnnotation();$('reviewStart').value=issue.start+1;$('reviewEnd').value=issue.end;
}
window.addEventListener('beforeunload',event=>{persistAnnotationDraft();if(nextUI.dirty){event.preventDefault();event.returnValue='';}});

function advancedResearchFields(ids,label){const existing=ids.map(id=>$(id)?.closest('label')).filter(Boolean);if(!existing.length)return;const details=el('details',undefined,'hub-secondary');details.append(el('summary',label));existing[0].before(details);for(const row of existing)details.append(row);return details;}
function selectedListValues(id){return [...$(id).selectedOptions].map(option=>option.value).filter(Boolean);}
function addRecordPicker(before,id,label,{multiple=false,onchange}={}){const select=selectInput([],'');select.id=id;select.multiple=multiple;if(multiple)select.size=5;select.onchange=()=>{if(onchange)Promise.resolve().then(onchange).catch(hubError);};const row=inputLabel(label,select);before.before(row);return select;}
function syncTrainingWorkflowPicker(workflow=null){
  if(workflow?.request?.kind==='train')nextUI.currentTrainingWorkflow={id:workflow.id,kind:'workflow',source:'train',label:workflow.request.name||workflow.id.slice(0,12),status:workflow.status};
  const select=$('curveWorkflowPicker');if(!select)return;const selected=nextUI.curve||select.value,rows=new Map((nextUI.researchTasks||[]).filter(row=>row.kind==='workflow'&&row.source==='train').map(row=>[row.id,row]));
  if(nextUI.currentTrainingWorkflow)rows.set(nextUI.currentTrainingWorkflow.id,nextUI.currentTrainingWorkflow);
  nextOptions('curveWorkflowPicker',[...rows.values()].map(row=>[row.id,row.label+' · '+statusLabel(row.status)]),'选择训练任务');
  if(selected&&rows.has(selected))select.value=selected;
}
async function refreshResearchRecordPickers(){
  const [history,workspace,models]=await Promise.all([api('api/comparisons?limit=200'),api('api/comparison/workspace?limit=200&query=train'),api('api/comparison/registry/model?limit=200')]);nextUI.researchComparisons=history.comparisons;nextUI.researchTasks=workspace.tasks;nextUI.researchModels=models.records;
  for(const id of ['suiteComparisonPicker','productionComparisonPicker']){const select=$(id),old=selectedListValues(id);nextOptions(id,history.comparisons.map(row=>[row.id,(row.title||row.id.slice(0,8))+' · '+statusLabel(row.status||'completed')]),id==='suiteComparisonPicker'?'留空使用当前比较':'选择已有比较以读取 MINT 结果');for(const option of select.options)option.selected=old.includes(option.value);}
  syncTrainingWorkflowPicker();nextOptions('workflowInitPicker',models.records.filter(row=>row.availability!=='missing_or_changed').map(row=>[row.id,(row.name||row.id.slice(0,8))+' · '+row.path.split('/').pop()]),'使用训练配置中的初始化权重');
  if(nextUI.curve&&[...$('curveWorkflowPicker').options].some(option=>option.value===nextUI.curve))$('curveWorkflowPicker').value=nextUI.curve;await refreshProductionRunChoices();
}
async function refreshProductionRunChoices(){
  const serial=nextUI.productionSerial=(nextUI.productionSerial||0)+1,id=$('productionComparisonPicker').value;const comparison=id?await api('api/comparisons/'+id):state.comparison;if(serial!==nextUI.productionSerial)return;nextUI.productionComparison=comparison;const select=$('productionRunPicker'),old=selectedListValues('productionRunPicker');nextOptions('productionRunPicker',(comparison?.runs||[]).filter(run=>run.algorithm==='mint'&&run.status==='completed'&&run.result&&!run.imported).map(run=>[run.id,run.label+' · '+(run.result.frames||'?')+' 帧 · '+run.id.slice(0,8)]),'请选择完整原生 MINT 结果');for(const option of select.options)option.selected=old.includes(option.value);$('productionPickerHint').textContent=comparison?'仅列出此比较中已完成、非导入的 MINT 结果；原始 MANO 文件、视频和官方划分仍需预检。':'先选择一个已有比较，或打开比较后使用其结果。';
}
function initializeResearchUsability(){
  const draftReturn=researchButton('二维修正未提交',returnToAnnotation);draftReturn.id='annotationDraftReturn';draftReturn.hidden=true;draftReturn.className='quiet-button annotation-draft-return';draftReturn.setAttribute('aria-live','polite');document.querySelector('.masthead-actions').prepend(draftReturn);
  const suite=$('suiteComparisons').closest('label');addRecordPicker(suite,'suiteComparisonPicker','选择要冻结的比较（可多选；留空使用当前比较）',{multiple:true,onchange:()=>{$('suiteComparisons').value=selectedListValues('suiteComparisonPicker').join('\n');}});advancedResearchFields(['suiteComparisons'],'高级：直接输入比较 ID');
  const curve=$('curveWorkflow').closest('label');addRecordPicker(curve,'curveWorkflowPicker','选择训练任务',{onchange:async()=>{const id=$('curveWorkflowPicker').value;if(id){$('curveWorkflow').value=id;nextUI.curve=id;await refreshCurves();}}});advancedResearchFields(['curveWorkflow'],'高级：直接输入训练任务 ID');
  const production=$('productionRuns').closest('label');addRecordPicker(production,'productionComparisonPicker','生产来源比较',{onchange:()=>{$('productionRuns').value='';return refreshProductionRunChoices();}});const picker=addRecordPicker(production,'productionRunPicker','完整原生 MINT 结果（可多选）',{multiple:true,onchange:()=>{$('productionRuns').value=selectedListValues('productionRunPicker').join('\n');}});const hint=nextText('productionPickerHint');picker.closest('label').after(hint);advancedResearchFields(['productionRuns'],'高级：直接输入原生 MINT Run ID');
  const init=$('workflowInit').closest('label');const initPicker=addRecordPicker(init,'workflowInitPicker','初始化模型版本',{onchange:()=>{const model=nextUI.researchModels?.find(row=>row.id===$('workflowInitPicker').value);$('workflowInit').value=model?.path||'';invalidateWorkflowReadiness();}});initPicker.closest('label').dataset.workflowKinds='train';
  const config=$('workflowConfig').closest('label');const recipe=addRecordPicker(config,'workflowRecipePicker','训练配置模板（需要数据与初始化权重预检）',{onchange:()=>{if($('workflowRecipePicker').value)$('workflowConfig').value=$('workflowRecipePicker').value;invalidateWorkflowReadiness();}});for(const [path,label]of [['/data1/lizhen_algo/mint/configs/training/mint_step2.yaml','Stage 2 · 相机微调'],['/data1/lizhen_algo/mint/configs/training/mint_step1.yaml','Stage 1 · 预训练'],['','自定义配置（展开高级路径）']]){const option=el('option',label);option.value=path;recipe.append(option);}recipe.value=$('workflowConfig').value;recipe.closest('label').dataset.workflowKinds='train';
  const advanced=advancedResearchFields(['workflowConfig','workflowInit','workflowResumePath'],'高级：配置、初始化与续训路径');if(advanced)advanced.dataset.workflowKinds='train';
  const state=$('workflowState'),details=el('details');details.append(el('summary','任务技术详情与原始输出'));state.before(details);details.append(state);
  const checkpointSelector=researchButton('从已完成训练选择续训检查点',async()=>{const id=$('curveWorkflowPicker').value;if(!id)throw new Error('请先在曲线与检查点中选择训练任务');const value=await api(`api/comparison/workflows/${id}/curves`),checkpoint=value.checkpoints.filter(row=>row.complete).at(-1);if(!checkpoint)throw new Error('该训练尚无完整检查点');$('workflowResumePath').value=checkpoint.path;$('workflowInit').value='';$('workflowInitPicker').value='';invalidateWorkflowReadiness();if(advanced)advanced.open=true;});checkpointSelector.dataset.workflowKinds='train';$('workflowResumePath').closest('label').after(checkpointSelector);
  for(const [id,field]of [['experimentBaselinePicker','experimentBaseline'],['experimentCandidatePicker','experimentCandidate']]){const select=addRecordPicker($(field).closest('label'),id,field==='experimentBaseline'?'当前比较原生基线':'当前比较候选结果',{onchange:()=>{$(field).value=$(id).value;}});select.dataset.currentResultPicker=field;}
  advancedResearchFields(['experimentBaseline','experimentCandidate'],'高级：直接输入实验结果 ID');
  window.addEventListener('mint-platform-open',event=>{if(event.detail==='research'){refreshResearchRecordPickers().catch(hubError);const runs=(state.comparison?.runs||[]).filter(run=>run.result);for(const id of ['experimentBaselinePicker','experimentCandidatePicker'])nextOptions(id,runs.map(run=>[run.id,run.label+' · '+run.id.slice(0,8)]));}});
  updateWorkflowFields();
}
document.addEventListener('DOMContentLoaded',initializeResearchUsability);

async function returnToAnnotation(){const value=nextUI.annotation;if(!value)return;if(!(state.comparison?.runs||[]).some(run=>run.id===value.run_id)){const parent=await api(`api/comparison/runs/${value.run_id}/parent`);if(!parent.comparisons.length)throw new Error('此修正来源尚未附加到比较，本机草稿仍保留');await openComparison(parent.comparisons[0]);}await openPlatform('quality');refreshDetailRuns();$('detailRun').value=value.run_id;const editor=$('annotationCanvas').closest('details');editor.open=true;editor.scrollIntoView({block:'start',behavior:'smooth'});refreshAnnotationDraft();}

function productionParameters(){return {run_ids:$('productionRuns').value.split(/\s+/).filter(Boolean),split:$('productionSplit').value,acceleration_limit:Number($('productionAcceleration').value)};}

function updateNativeTrainingAction(){const button=$('nativeUseTraining');if(!button)return;const row=(nextUI.nativeVersions||[]).find(value=>value.id===$('nativeVersion').value);button.disabled=!row?.training_ready;button.title=row?.training_ready?'选择此已验证训练版本，继续配置与预检':'仅完成训练格式转换的官方 Train 版本可用于训练';}
