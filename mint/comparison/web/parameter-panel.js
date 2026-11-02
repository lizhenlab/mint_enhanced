'use strict';

// Raw model parameters are explicit snapshots. Playback only reads geometry that
// the viewer already requested; it never starts a native-parameter/mesh request.
const parameterView={open:false,runId:null,slot:0,tab:'hand',joint:null,selection:0,comparison:null,renderKey:'',cache:new Map(),meshCache:new Map(),pending:new Map(),errors:new Map(),page:0,pageSize:20,trigger:null,nodes:null,exporting:false};
const PARAMETER_CACHE_LIMIT=8;
const PARAMETER_TABS=[['hand','手部'],['camera','相机'],['quality','质量'],['source','来源']];
const PARAMETER_FRAME_FIELDS=['available','K','T_world_camera','pose_valid','geometry_valid','presence','detected','tracked','filled','confidence','exists_2d','exists_3d'];

function parameterNumber(value,digits=3){return typeof value==='number'&&Number.isFinite(value)?value.toFixed(digits):'—';}
function parameterValue(value){return value===true?'是':value===false?'否':typeof value==='number'&&Number.isFinite(value)?parameterNumber(value,4):'— · 未提供';}
function parameterSlotValue(value,slot){return Array.isArray(value)?value[slot]:value;}
function parameterRequestKey(context){return JSON.stringify([context.comparisonId,context.pollToken,context.runId,context.revision,context.origin??null,context.frame]);}
function parameterPagination(length,page,size=20){
  const pageSize=size===50?50:20,total=Number.isInteger(length)&&length>0?length:0,pages=Math.max(1,Math.ceil(total/pageSize)),current=Math.max(0,Math.min(pages-1,Number.isInteger(page)?page:0));
  return {page:current,pageSize,pages,total,start:current*pageSize,end:Math.min(total,(current+1)*pageSize)};
}
function parameterBoundedSet(cache,key,value){cache.delete(key);cache.set(key,value);while(cache.size>PARAMETER_CACHE_LIMIT)cache.delete(cache.keys().next().value);}
function parameterNativeFieldModel(field){
  return {name:String(field?.name||'未命名字段'),label:String(field?.label||field?.name||'原生字段'),representation:String(field?.representation||'未声明'),units:String(field?.units||'未声明'),shape:Array.isArray(field?.shape)?field.shape.join(' × '):'未声明',coordinate:String(field?.coordinate_frame||'未声明'),source:String(field?.source_file||'未提供'),note:String(field?.note||''),value:JSON.stringify(field?.value??null,null,2)};
}
function parameterQualityRows(standard,metadata,slot){
  const meanings={
    geometry_valid:'标准几何是否有效；有限几何不等于手实际出现。',
    presence:metadata?.presence_semantics||'算法自身存在性判定；阈值和含义依算法而异。',
    detected:metadata?.detected_semantics||'原生检测状态；未发布时保持缺失。',
    tracked:metadata?.tracked_semantics||'算法原生跟踪/状态标记；不等于检测置信度。',
    filled:metadata?.filled_semantics||'该帧是否由补全流程生成；不代表实测观测。',
    confidence:metadata?.confidence_semantics||'原生置信度；未进行跨算法概率标定。',
    exists_2d:metadata?.exists_2d_semantics||'原生 2D 存在性分数；并非统一准确率。',
    exists_3d:metadata?.exists_3d_semantics||'原生 3D 存在性分数；并非统一准确率。'
  };
  return [['available','本帧结果映射','结果是否确实包含此样本帧。'],['geometry_valid','有效几何',meanings.geometry_valid],['presence','存在性',meanings.presence],['detected','检测',meanings.detected],['tracked','跟踪',meanings.tracked],['filled','补全',meanings.filled],['confidence','置信度',meanings.confidence],['exists_2d','2D 存在分数',meanings.exists_2d],['exists_3d','3D 存在分数',meanings.exists_3d],['pose_valid','相机外参有效','相机姿态有效性独立于手部存在性。']].map(([name,label,meaning])=>({name,label,meaning,value:parameterValue(parameterSlotValue(standard?.[name],slot))}));
}
function parameterEl(tag,text,className){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;}
function parameterNode(tag,id,text,className){const node=parameterEl(tag,text,className);if(id)node.id=id;return node;}
function parameterButton(id,text,handler){const node=parameterNode('button',id,text);node.type='button';node.onclick=handler;return node;}
function parameterLabel(label,input){const node=parameterEl('label',label);node.append(input);return node;}
function parameterNote(text){return parameterEl('p',text,'parameter-note');}
function parameterPairs(host,rows){const list=parameterEl('dl',undefined,'parameter-facts');for(const [label,value]of rows){list.append(parameterEl('dt',label),parameterEl('dd',value===null||value===undefined||value===''?'— · 未提供':String(value)));}host.append(list);return list;}
function parameterState(){return typeof state==='undefined'?null:state;}
function parameterPanelIsOpen(){return parameterView.open;}
function parameterPanelJointSelection(){return parameterView.open&&Number.isInteger(parameterView.joint)?{runId:parameterView.runId,slot:parameterView.slot,joint:parameterView.joint}:null;}
function parameterPanelReadCards(){const s=parameterState();if(!parameterView.open||!s)return [];const card=s.cards.get(parameterView.runId);return card?.run.result?[card]:[];}
function parameterCard(){return parameterState()?.cards.get(parameterView.runId)||null;}
function parameterReferenceRead(){const card=typeof baselineCard==='function'?baselineCard():null;return {card,data:card&&typeof dataFor==='function'?dataFor(card):null};}
function parameterReferenceIdentity(){const {card}=parameterReferenceRead();return card?JSON.stringify([card.run.id,typeof resultRevision==='function'?resultRevision(card.run):JSON.stringify(card.run.result),card.run.result?.reference_role??null]):null;}
function parameterSpace(){return document.getElementById('space')?.value==='world'?'world':'camera';}
function parameterContext(){
  const s=parameterState(),comparison=s?.comparison,card=parameterCard();if(!comparison?.sample||!card?.run.result)return null;
  return {comparisonId:comparison.id,pollToken:s.pollToken,runId:card.run.id,revision:typeof resultRevision==='function'?resultRevision(card.run):JSON.stringify(card.run.result),origin:comparison.common_world_origin_source_frame??null,frame:s.frame,selection:parameterView.selection,slot:parameterView.slot,space:parameterSpace(),referenceIdentity:parameterReferenceIdentity(),card};
}
function parameterContextCurrent(context){const next=parameterContext();return !!next&&parameterView.open&&context.selection===next.selection&&context.slot===next.slot&&context.space===next.space&&context.card===next.card&&parameterRequestKey(context)===parameterRequestKey(next);}
function parameterExportCurrent(context){return parameterContextCurrent(context)&&context.referenceIdentity===parameterReferenceIdentity();}
function parameterCurrentData(){const card=parameterCard();return card&&typeof dataFor==='function'?dataFor(card):null;}
function parameterStandard(data,snapshot){
  const standard={...(snapshot?.standard||{})};
  if(data)for(const key of PARAMETER_FRAME_FIELDS)if(data.payload[key]!==undefined&&data.payload[key]!==null)standard[key]=data.payload[key][data.index];
  return standard;
}
function parameterValidHand(data,slot,space='camera'){
  if(!data||data.payload.available?.[data.index]===false||data.payload.geometry_valid?.[data.index]?.[slot]!==true)return false;
  return space!=='world'||data.payload.pose_valid?.[data.index]===true;
}
function parameterCoordinates(context){return context?.space==='world'?`世界系 · 米；共同原点 ${context.origin===null?'未建立':`源帧 ${context.origin}（0 起）`}；列向量，T_world_camera 将相机变换到世界。`:'相机系 · 米；OpenCV：X 向右、Y 向下、Z 向前。表格显示厘米（cm）。';}
function parameterCoordinateLabel(context){return context?.space==='world'?`世界系 · cm；${context.origin===null?'共同原点未建立':'原点：源帧 '+context.origin}`:'相机系 · cm；X右/Y下/Z前';}
function parameterStatusContext(){const context=parameterContext();return JSON.stringify([context&&parameterRequestKey(context),context?.slot,context?.space]);}
function parameterStatus(text,error=false){const node=parameterView.nodes?.status;if(node){node.textContent=text;node.dataset.state=error?'error':'ready';parameterView.nodes.footer.hidden=!text;parameterView.statusContext=parameterStatusContext();}}
function parameterRefreshGeometry(){const s=parameterState();if(!s)return;if(typeof ensureFrame==='function')Promise.resolve(ensureFrame(s.frame)).then(()=>{updateParameterPanel();if(typeof render==='function')render(true);}).catch(error=>parameterStatus(error.message,true));}

function initializeParameterPanel(){
  const host=document.getElementById('parameterPanel');if(!host||parameterView.nodes)return;
  host.hidden=true;host.classList.add('parameter-panel');host.setAttribute('aria-labelledby','parameterPanelTitle');
  const heading=parameterEl('div',undefined,'parameter-heading'),title=parameterNode('h2','parameterPanelTitle','逐帧分析');
  const close=parameterButton('closeParameterPanel','×',closeParameterPanel);close.className='parameter-close';close.setAttribute('aria-label','关闭逐帧分析');close.title='关闭逐帧分析（Esc）';const headingActions=parameterEl('div',undefined,'parameter-heading-actions');heading.append(title,headingActions);
  const controls=parameterEl('div',undefined,'parameter-controls'),run=parameterNode('select','parameterRun');run.setAttribute('aria-label','参数所属结果');
  run.onchange=()=>{parameterView.runId=run.value;parameterView.joint=null;parameterView.selection++;parameterView.page=0;parameterView.renderKey='';updateParameterPanel();parameterRefreshGeometry();};
  const hand=parameterEl('div',undefined,'parameter-hands');hand.setAttribute('role','group');hand.setAttribute('aria-label','查看哪只手');
  const handButtons=[0,1].map(slot=>{const button=parameterButton('parameterHand'+slot,slot?'右手 R':'左手 L',()=>{parameterView.slot=slot;parameterView.selection++;parameterView.page=0;parameterView.renderKey='';updateParameterPanel();if(typeof render==='function')render(true);});button.setAttribute('aria-pressed',String(!slot));hand.append(button);return button;});
  controls.append(parameterLabel('结果',run),hand);
  const frame=parameterNode('p','parameterFrame',undefined,'parameter-frame'),coordinates=parameterNode('p','parameterCoordinates',undefined,'parameter-coordinate');coordinates.dataset.i18nIgnore='';
  const geometryStatus=parameterNode('p','parameterGeometryStatus','正在读取当前帧几何…','parameter-loading');geometryStatus.hidden=true;geometryStatus.setAttribute('role','status');geometryStatus.setAttribute('aria-live','polite');
  const visibility=parameterEl('div',undefined,'parameter-visibility');visibility.hidden=true;visibility.append(parameterNote('此结果当前未显示在画面中；关节高亮需先在画面中查看。'));
  const showResult=parameterButton('showParameterResult','在画面中查看',()=>{if(typeof focusParameterResult!=='function')return;return Promise.resolve(focusParameterResult(parameterView.runId)).then(()=>{parameterView.renderKey='';updateParameterPanel();}).catch(error=>parameterStatus(error.message,true));});visibility.append(showResult);
  const tabs=parameterEl('div',undefined,'parameter-tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','当前帧参数类别');
  const buttons=PARAMETER_TABS.map(([key,label],index)=>{const button=parameterButton('parameterTab-'+key,label,()=>parameterActivateTab(key));button.setAttribute('role','tab');button.setAttribute('aria-controls','parameterContent-'+key);button.setAttribute('aria-selected',String(!index));button.tabIndex=index?-1:0;
    button.onkeydown=event=>{if(event.isComposing||event.keyCode===229||!['ArrowRight','ArrowLeft','Home','End'].includes(event.key))return;event.preventDefault();event.stopPropagation();let next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(buttons.indexOf(button)+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;parameterActivateTab(PARAMETER_TABS[next][0]);buttons[next].focus();};tabs.append(button);return button;});
  const scroller=parameterEl('div',undefined,'parameter-scroll'),panels={};for(const [key]of PARAMETER_TABS){const panel=parameterNode('section','parameterContent-'+key);panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby','parameterTab-'+key);panel.tabIndex=0;panel.hidden=key!=='hand';panels[key]=panel;scroller.append(panel);}
  const advanced=parameterNode('details','parameterAdvanced',undefined,'parameter-advanced');advanced.append(parameterEl('summary','高级：原生参数与网格'));
  advanced.append(parameterNote('播放只更新上方基础参数。原生模型输出与网格按点击时的帧读取；加载会暂停播放，不会随播放连续请求。'));
  const rawActions=parameterEl('div',undefined,'parameter-actions'),load=parameterButton('loadParameterSnapshot','暂停并加载当前帧原生参数',()=>loadParameterSnapshot().catch(error=>parameterStatus(error.message,true)));
  const rawStatus=parameterNode('p','parameterRawStatus',undefined,'parameter-note'),native=parameterNode('div','parameterNativeFields');rawActions.append(load);advanced.append(rawActions,rawStatus,native);
  const mesh=parameterNode('details','parameterMesh',undefined,'parameter-mesh');mesh.append(parameterEl('summary','网格顶点 · 按需分页'));
  const meshActions=parameterEl('div',undefined,'parameter-actions'),meshLoad=parameterButton('loadParameterMesh','暂停并加载当前帧网格',()=>loadParameterMesh().catch(error=>parameterStatus(error.message,true))),meshSize=parameterNode('select','parameterMeshPageSize');
  for(const size of [20,50]){const option=parameterEl('option',size+' 行 / 页');option.value=String(size);meshSize.append(option);}meshSize.value='20';meshSize.setAttribute('aria-label','网格每页顶点数');meshSize.onchange=()=>{parameterView.pageSize=Number(meshSize.value);parameterView.page=0;parameterRenderMesh();};meshActions.append(meshLoad,meshSize);
  const meshStatus=parameterNode('p','parameterMeshStatus',undefined,'parameter-note'),meshValues=parameterNode('div','parameterMeshValues',undefined,'parameter-table-scroll'),pager=parameterEl('div',undefined,'parameter-pagination');
  const prev=parameterButton('parameterMeshPrevious','上一页',()=>{parameterView.page--;parameterRenderMesh();}),next=parameterButton('parameterMeshNext','下一页',()=>{parameterView.page++;parameterRenderMesh();}),pageLabel=parameterNode('span','parameterMeshPage');pager.append(prev,pageLabel,next);mesh.append(parameterNote('顶点编号保持原生 MANO 顺序；缺失值显示 —，不替换为 0。'),meshActions,meshStatus,meshValues,pager);advanced.append(mesh);scroller.append(advanced);
  const footer=parameterEl('div',undefined,'parameter-footer'),exportButton=parameterButton('exportParameterFrame','导出 JSON',()=>exportParameterFrame().catch(error=>parameterStatus(error.message,true))),status=parameterNode('p','parameterStatus',undefined,'parameter-status');exportButton.title='暂停并导出当前帧完整原生参数与可用网格 JSON';exportButton.setAttribute('aria-label','导出当前帧完整参数 JSON');status.setAttribute('role','status');status.setAttribute('aria-live','polite');footer.append(status);footer.hidden=true;headingActions.append(exportButton,close);
  host.replaceChildren(heading,controls,frame,coordinates,geometryStatus,visibility,tabs,scroller,footer);
  parameterView.nodes={host,run,frame,coordinates,geometryStatus,visibility,showResult,handButtons,buttons,panels,advanced,load,rawStatus,native,meshLoad,meshStatus,meshValues,prev,next,pageLabel,pager,exportButton,status,footer};
  document.getElementById('toggleParameterPanel')?.addEventListener('click',event=>{if(parameterView.open)closeParameterPanel();else openParameterPanel(undefined,event.currentTarget);});
  host.addEventListener('keydown',event=>{if(event.isComposing||event.keyCode===229||event.key!=='Escape'||event.defaultPrevented)return;event.preventDefault();event.stopPropagation();closeParameterPanel();});
  // Escape also works while focus is on the initiating card; composition stays intact.
  document.addEventListener('keydown',event=>{if(!parameterView.open||event.isComposing||event.keyCode===229||event.key!=='Escape'||event.defaultPrevented)return;event.preventDefault();closeParameterPanel();});
}
function parameterActivateTab(key){if(!PARAMETER_TABS.some(([id])=>id===key))return;parameterView.tab=key;parameterView.renderKey='';updateParameterPanel();}
function openParameterPanel(runId,trigger){
  initializeParameterPanel();if(!parameterView.nodes)return;
  const s=parameterState(),runs=s?.comparison?.runs||[],preferred=runId||(typeof resultView!=='undefined'?(resultView.expanded||resultView.candidate||resultView.reference):null);
  if(!parameterView.open)parameterView.trigger=trigger||document.activeElement;
  else if(trigger)parameterView.trigger=trigger;
  if(runs.some(run=>run.id===preferred))parameterView.runId=preferred;
  else if(!runs.some(run=>run.id===parameterView.runId))parameterView.runId=runs.find(run=>run.result)?.id||runs[0]?.id||null;
  parameterView.open=true;parameterView.selection++;parameterView.renderKey='';parameterView.nodes.host.hidden=false;parameterView.nodes.host.parentElement?.classList.add('parameter-is-open');
  document.getElementById('toggleParameterPanel')?.setAttribute('aria-expanded','true');
  updateParameterPanel();parameterView.nodes.run.focus();parameterRefreshGeometry();
}
function closeParameterPanel(){
  if(!parameterView.nodes)return;parameterView.open=false;parameterView.selection++;parameterView.nodes.host.hidden=true;parameterView.nodes.host.parentElement?.classList.remove('parameter-is-open');document.getElementById('toggleParameterPanel')?.setAttribute('aria-expanded','false');
  const trigger=parameterView.trigger;parameterView.trigger=null;if(trigger?.isConnected!==false&&typeof trigger?.focus==='function')trigger.focus();else document.getElementById('toggleParameterPanel')?.focus();
  if(typeof render==='function')render(true);
}
function resetParameterPanel(){parameterView.selection++;parameterView.cache.clear();parameterView.meshCache.clear();parameterView.pending.clear();parameterView.errors.clear();parameterView.comparison=null;parameterView.joint=null;parameterView.runId=null;parameterView.renderKey='';parameterView.exporting=false;if(parameterView.nodes){parameterStatus('');updateParameterPanel();}}

function updateParameterPanel(){
  const n=parameterView.nodes;if(!n||!parameterView.open)return;
  const s=parameterState(),comparison=s?.comparison,runs=comparison?.runs||[];
  if(parameterView.comparison!==comparison?.id){parameterView.comparison=comparison?.id;parameterView.selection++;parameterView.joint=null;parameterView.cache.clear();parameterView.meshCache.clear();parameterView.errors.clear();parameterView.renderKey='';}
  if(!runs.some(run=>run.id===parameterView.runId)){parameterView.runId=runs.find(run=>run.result)?.id||runs[0]?.id||null;parameterView.selection++;}
  const optionsSignature=JSON.stringify(runs.map(run=>[run.id,run.label,run.status,!!run.result]));
  if(n.run.dataset.signature!==optionsSignature){n.run.dataset.signature=optionsSignature;n.run.replaceChildren();for(const run of runs){const option=parameterEl('option',(run.label||run.algorithm||run.id)+(run.result?'':' · 尚无结果'));option.value=run.id;n.run.append(option);}}n.run.value=parameterView.runId||'';n.run.disabled=!runs.length;
  const context=parameterContext(),sample=comparison?.sample,frame=s?.frame??0,data=parameterCurrentData(),key=context&&parameterRequestKey(context),snapshot=key&&parameterView.cache.get(key);
  if(n.status.textContent&&parameterView.statusContext!==parameterStatusContext())parameterStatus('');
  n.frame.textContent=sample?`样本帧 ${frame+1} / ${sample.frames} · 源帧 ${sample.source_frame_indices?.[frame]??'—'}（0 起） · ${parameterNumber(sample.timestamps_s?.[frame],3)} s${s.playing?' · 播放中':' · 已暂停'}`:'尚无准备完成的样本。';
  n.geometryStatus.hidden=!context||!!data;
  n.visibility.hidden=!context||typeof visibleResultCards!=='function'||visibleResultCards().some(card=>card.run.id===context.runId);n.showResult.disabled=typeof focusParameterResult!=='function';
  n.coordinates.textContent=parameterCoordinateLabel(context);n.coordinates.title=parameterCoordinates(context);for(let slot=0;slot<2;slot++)n.handButtons[slot].setAttribute('aria-pressed',String(parameterView.slot===slot));
  for(let i=0;i<PARAMETER_TABS.length;i++){const active=PARAMETER_TABS[i][0]===parameterView.tab;n.buttons[i].setAttribute('aria-selected',String(active));n.buttons[i].tabIndex=active?0:-1;n.panels[PARAMETER_TABS[i][0]].hidden=!active;}
  n.load.disabled=!context||parameterView.pending.has('native:'+key);n.meshLoad.disabled=!context||!parameterCard()?.run.result?.arrays?.vertices_camera_m||parameterView.pending.has('mesh:'+key);n.exportButton.disabled=!context||parameterView.exporting;
  const previousReady=context&&typeof parameterPreviousFrame==='function'?!!parameterPreviousFrame(context.card,parameterView.slot):!!(context&&data&&parameterPreviousData(context,data));
  const reference=parameterReferenceRead(),referenceReady=[parameterReferenceIdentity(),!!reference.data,reference.data?.payload.available?.[reference.data?.index],reference.data?.payload.geometry_valid?.[reference.data?.index]?.[parameterView.slot]];
  const renderKey=JSON.stringify([key,parameterView.selection,parameterView.slot,parameterView.tab,parameterSpace(),!!data,!!snapshot,previousReady,referenceReady,parameterView.joint,parameterCard()?.trajectoryKey||'',parameterView.pending.has('native:'+key),parameterView.pending.has('mesh:'+key),parameterView.errors.get('native:'+key)||'',parameterView.errors.get('mesh:'+key)||'']);
  if(parameterView.renderKey===renderKey)return;parameterView.renderKey=renderKey;
  const host=n.panels[parameterView.tab];host.replaceChildren();
  if(!context)host.append(parameterNote(runs.length?'所选结果尚未发布几何；发布后可在这里查看。':'当前比较没有可查看的结果。'));
  else if(!data&&!snapshot&&parameterView.tab!=='source')host.append(parameterNote('正在读取当前帧几何，加载完成后显示手部、相机和质量字段。'));
  else if(parameterView.tab==='hand')parameterRenderHand(host,data,context);
  else if(parameterView.tab==='camera')parameterRenderCamera(host,data,snapshot,context);
  else if(parameterView.tab==='quality')parameterRenderQuality(host,data,snapshot);
  else parameterRenderSource(host,context);
  parameterRenderNative(snapshot,context);parameterRenderMesh();
}

function parameterMath(){return typeof ParameterMath!=='undefined'?ParameterMath:null;}
function parameterPreviousData(context,data){
  if(context.frame<1||!data)return null;
  if(data.index>0&&data.payload.sample_positions?.[data.index-1]===context.frame-1)return {payload:data.payload,index:data.index-1};
  // Reuse only an already loaded adjacent geometry block. Never use decimated trajectories.
  const s=parameterState(),count=typeof FRAME_READ_COUNT!=='undefined'?FRAME_READ_COUNT:64,start=Math.floor((context.frame-1)/count)*count;
  if(typeof resultFrameKey!=='function')return null;const payload=s.cache?.get(resultFrameKey(context.card.run,start,context.origin));if(!payload)return null;
  const index=payload.sample_positions?.indexOf(context.frame-1);return index>=0?{payload,index}:null;
}
function parameterFrameComparison(data){
  const {card:reference,data:referenceData}=parameterReferenceRead(),slot=parameterView.slot,declared=reference?.run.result?.reference_role==='user_declared_ground_truth';
  const result={baseline_run_id:reference?.run.id??null,baseline_label:reference?.run.label??null,baseline_revision:reference?(typeof resultRevision==='function'?resultRevision(reference.run):JSON.stringify(reference.run.result)):null,baseline_role:reference?.run.result?.reference_role??null,declared_ground_truth:declared,coordinate_frame:'camera',alignment:'none; raw camera-coordinate differences',metrics:null,reason:null,role_note:declared?'基准为用户声明的 GT；此处显示对该参考的相机系差异。':'基准为算法结果；数值仅表示算法差异，不表示准确率。'};
  if(!reference)result.reason='尚未选择可用对照基准。';
  else if(reference.run.id===parameterView.runId)result.reason='当前结果就是基准；不显示自比较产生的零差异。';
  else if(!data||!referenceData)result.reason='正在读取当前帧基准几何…';
  else if(!parameterValidHand(data,slot,'camera')||!parameterValidHand(referenceData,slot,'camera'))result.reason='当前帧所选手在结果或基准中无有效几何，差异保持缺失。';
  else result.metrics=parameterMath()?.handPairMetrics(data.payload.joints_camera_m?.[data.index]?.[slot],referenceData.payload.joints_camera_m?.[referenceData.index]?.[slot],data.payload.K?.[data.index],referenceData.payload.K?.[referenceData.index])??null;
  if(!result.metrics&&!result.reason)result.reason='当前帧关节点缺失，差异保持缺失。';return result;
}
function parameterDerivedGeometry(data,context,snapshot){
  const math=parameterMath(),slot=context.slot,sample=parameterState().comparison.sample,valid=parameterValidHand(data,slot,context.space),points=(context.space==='world'?data?.payload.joints_world_m:data?.payload.joints_camera_m)?.[data?.index]?.[slot],cameraHand=data?.payload.joints_camera_m?.[data?.index]?.[slot],cameraValid=parameterValidHand(data,slot,'camera'),previous=parameterPreviousData(context,data);
  const prior=typeof parameterPreviousFrame==='function'?parameterPreviousFrame(context.card,slot):previous?{points:previous.payload.joints_camera_m?.[previous.index]?.[slot],valid:parameterValidHand(previous,slot,'camera'),sampleIndex:context.frame-1,timestampS:previous.payload.timestamps_s?.[previous.index]??sample.timestamps_s?.[context.frame-1],space:'camera'}:null;
  const timestamp=data?.payload.timestamps_s?.[data?.index]??sample.timestamps_s?.[context.frame],speed=math&&prior?math.wristSpeed(prior,{points:cameraHand,valid:cameraValid,sampleIndex:context.frame,timestampS:timestamp,space:'camera'}):null,both=[0,1].every(hand=>parameterValidHand(data,hand,context.space)),framePoints=(context.space==='world'?data?.payload.joints_world_m:data?.payload.joints_camera_m)?.[data?.index];
  return {description:'Geometry-derived values; not native MANO parameters. Finger flexion proxies are 180 degrees minus landmark interior angles. Camera-coordinate wrist velocity includes camera-relative motion and is not physical world velocity.',selected_hand:slot?'right':'left',display_space:context.space,hand_metrics:math?.handMetrics(points,{valid,slot,space:context.space})??null,camera_wrist_depth_m:cameraValid&&math?.validPoint(cameraHand?.[0])?cameraHand[0][2]:null,wrist_distance_m:both&&math?math.wristDistanceM(framePoints?.[0],framePoints?.[1]):null,wrist_camera_velocity:speed?{...speed,previous_sample_position:prior.sampleIndex,current_sample_position:context.frame,previous_timestamp_s:prior.timestampS,current_timestamp_s:timestamp,coordinate_frame:'camera',note:'Exact adjacent sample timestamps; includes camera-relative motion, not physical world velocity.'}:null,camera_metrics:math?.cameraMetrics(parameterStandard(data,snapshot).K,sample.width,sample.height)??null,reference_comparison:parameterFrameComparison(data)};
}
function parameterRenderHand(host,data,context){
  if(!data){host.append(parameterNote('正在加载所选结果的当前帧几何…'));return;}
  const math=parameterMath(),slot=parameterView.slot,valid=parameterValidHand(data,slot,context.space),points=(context.space==='world'?data.payload.joints_world_m:data.payload.joints_camera_m)?.[data.index]?.[slot];
  if(!valid)host.append(parameterNote(context.space==='world'&&!data.payload.joints_world_m?'此结果没有可用世界坐标。切换“3D 坐标”为相机系查看。':'该手当前帧无有效几何；缺失值保留为 —。'));
  const derived=parameterDerivedGeometry(data,context),metrics=derived.hand_metrics,speed=derived.wrist_camera_velocity,distance=derived.wrist_distance_m,pair=derived.reference_comparison;
  parameterPairs(host,[['手腕 · '+(context.space==='world'?'世界系':'相机系'),metrics?math.formatXYZ(metrics.wristM):'—'],['相机深度 Z',derived.camera_wrist_depth_m===null?'—':parameterNumber(derived.camera_wrist_depth_m,3)+' m'],['双腕距离',typeof distance==='number'?parameterNumber(distance*100,2)+' cm':'— · 需要双手有效'],['腕部三维速度 · 相机系',speed?parameterNumber(speed.speedMps,3)+' m/s':'— · 需要相邻有效样本帧']]);
  host.append(parameterNote('速度为相邻样本帧腕点的三维差分，使用实际时间戳；包含相机相对运动，不使用显示抽样轨迹。'));
  const palm=parameterEl('details');palm.append(parameterEl('summary','掌面几何方向'));if(metrics?.palm)parameterPairs(palm,[['法向量',metrics.palm.normal.map(value=>parameterNumber(value,4)).join(' / ')],['法向方位 / 仰角',parameterNumber(metrics.palm.normalAzimuthDeg,1)+'° / '+parameterNumber(metrics.palm.normalElevationDeg,1)+'°']]);else palm.append(parameterNote('当前关节点缺失或掌面退化，无法确定几何方向。'));palm.append(parameterNote('法向由 (关节5−腕) × (关节17−腕) 归一化得到；属于几何方向，不是 MANO 全局旋转或骨骼姿态。'));host.append(palm);
  const jointDetails=parameterEl('details',undefined,'parameter-joints');jointDetails.open=parameterView.jointDetailsOpen===true;jointDetails.ontoggle=()=>{parameterView.jointDetailsOpen=jointDetails.open;};jointDetails.append(parameterEl('summary','21 点关节坐标 · 点击行联动画面'));
  const table=parameterEl('table'),head=parameterEl('tr');for(const label of ['关节','X · cm','Y · cm','Z · cm','对基准 · mm'])head.append(parameterEl('th',label));table.append(head);
  for(let joint=0;joint<21;joint++){const row=parameterEl('tr');row.dataset.joint=String(joint);const cell=parameterEl('td'),button=parameterButton('parameterJoint-'+joint,(joint===0?'0 · 腕':String(joint)),()=>{parameterView.jointDetailsOpen=jointDetails.open;parameterView.joint=parameterView.joint===joint?null:joint;parameterView.renderKey='';updateParameterPanel();document.getElementById('parameterJoint-'+joint)?.focus();if(typeof render==='function')render(true);});button.setAttribute('aria-pressed',String(parameterView.joint===joint));button.setAttribute('aria-label',`联动高亮${slot?'右':'左'}手关节 ${joint}`);cell.append(button);row.append(cell);const point=valid&&math?.validPoint(points?.[joint])?points[joint]:null;for(let axis=0;axis<3;axis++)row.append(parameterEl('td',point?parameterNumber(point[axis]*100,2):'—'));row.append(parameterEl('td',parameterNumber(pair.metrics?.jointDifferencesMm[joint],2)));table.append(row);}
  const scroll=parameterEl('div',undefined,'parameter-table-scroll');scroll.append(table);jointDetails.append(scroll,parameterNote(pair.reason||`基准：${pair.baseline_label||pair.baseline_run_id}。${pair.role_note} 原始相机系欧距；不作腕或刚体对齐。`));host.append(jointDetails);
  const fingers=parameterEl('details',undefined,'parameter-fingers');fingers.append(parameterEl('summary','手指关节夹角 / 屈曲代理'));
  if(metrics?.fingers){const fingerTable=parameterEl('table'),header=parameterEl('tr');for(const label of ['手指 / 关节','内角 °','屈曲代理 °'])header.append(parameterEl('th',label));fingerTable.append(header);for(const finger of metrics.fingers)for(let i=0;i<finger.jointNames.length;i++){const row=parameterEl('tr');row.append(parameterEl('th',finger.label+' · '+finger.jointNames[i]),parameterEl('td',parameterNumber(finger.anglesDeg[i],1)),parameterEl('td',parameterNumber(finger.flexionProxyDeg[i],1)));fingerTable.append(row);}fingers.append(fingerTable);}else fingers.append(parameterNote('当前帧没有足够的有效关节计算夹角。'));
  fingers.append(parameterNote('由三点计算关节内角；屈曲代理 = 180° − 内角。不区分解剖轴旋转，不等于原生 MANO pose，不作为统一姿态角或准确率。'));host.append(fingers);
}

function parameterRenderTrend(host,card){
  const trajectory=card.trajectory;if(!trajectory?.camera_wrists_m)return;
  const details=parameterEl('details',undefined,'parameter-trend');details.append(parameterEl('summary','双手腕相机深度趋势'));
  const canvas=parameterNode('canvas','parameterDepthTrend');canvas.width=620;canvas.height=150;canvas.setAttribute('role','img');canvas.setAttribute('aria-label','左右手腕相机深度显示抽样曲线，单位米；竖线为当前帧。');details.append(canvas,parameterNote('L 青色 / R 黄色 · 仅显示抽样，不用于速度或指标计算；断点表示缺失。'));host.append(details);
  const ctx=canvas.getContext?.('2d');if(!ctx)return;
  const times=trajectory.timestamps_s,wrists=trajectory.camera_wrists_m;if(!Array.isArray(times)||times.length!==wrists.length)return;
  const values=wrists.flatMap(hands=>hands.map(point=>point?.[2])).filter(value=>typeof value==='number'&&Number.isFinite(value));if(!values.length)return;
  const finiteTimes=times.filter(value=>typeof value==='number'&&Number.isFinite(value));if(finiteTimes.length<2)return;const min=Math.min(...values),max=Math.max(...values),range=Math.max(.01,max-min),first=finiteTimes[0],duration=Math.max(.001,finiteTimes.at(-1)-first),x=time=>40+(time-first)/duration*565,y=z=>124-(z-min)/range*100;
  ctx.clearRect(0,0,620,150);ctx.font='12px sans-serif';ctx.fillStyle='#9cafaa';ctx.fillText(parameterNumber(max,2)+' m',0,17);ctx.fillText(parameterNumber(min,2)+' m',0,136);
  for(let slot=0;slot<2;slot++){ctx.strokeStyle=slot?'#ffdf80':'#6ad5f0';ctx.lineWidth=2;ctx.beginPath();let started=false;wrists.forEach((hands,index)=>{const z=hands[slot]?.[2],time=times[index];if(typeof z!=='number'||!Number.isFinite(z)||typeof time!=='number'||!Number.isFinite(time)){started=false;return;}if(started)ctx.lineTo(x(time),y(z));else ctx.moveTo(x(time),y(z));started=true;});ctx.stroke();}
  const time=parameterState()?.comparison?.sample?.timestamps_s?.[parameterState()?.frame];if(typeof time==='number'&&Number.isFinite(time)){ctx.strokeStyle='#edf5efaa';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(x(time),10);ctx.lineTo(x(time),135);ctx.stroke();}
}

function parameterMatrix(host,label,matrix,rows,columns){
  const details=parameterEl('details');details.append(parameterEl('summary',label));if(!parameterMath()?.validMatrix(matrix,rows,columns)){details.append(parameterNote('— · 此帧未提供有效矩阵。'));host.append(details);return;}
  const table=parameterEl('table',undefined,'parameter-matrix');for(const values of matrix){const row=parameterEl('tr');for(const value of values)row.append(parameterEl('td',parameterNumber(value,5)));table.append(row);}details.append(table);host.append(details);
}
function parameterRenderCamera(host,data,snapshot,context){
  const standard=parameterStandard(data,snapshot),sample=parameterState().comparison.sample,math=parameterMath(),camera=math?.cameraMetrics(standard.K,sample.width,sample.height);
  parameterPairs(host,[['fx / fy',camera?parameterNumber(camera.fx,2)+' / '+parameterNumber(camera.fy,2)+' px':'—'],['cx / cy',camera?parameterNumber(camera.cx,2)+' / '+parameterNumber(camera.cy,2)+' px':'—'],['水平 / 垂直 FOV',camera?parameterNumber(camera.horizontalFovDeg,2)+'° / '+parameterNumber(camera.verticalFovDeg,2)+'°':'—'],['K 像素网格',`${sample.width} × ${sample.height} px · 标准样本完整网格`],['K 来源',context.card.run.result.K_source],['相机来源',context.card.run.result.camera_source],['相机外参有效',parameterValue(standard.pose_valid)],['世界系原点',context.origin===null?'未建立共同世界原点':`源帧 ${context.origin}（0 起）的相机坐标系`]]);
  host.append(parameterNote('FOV 从当前帧 K 与像素网格推得；不代表直接预测的原生视场角。K 保持标准完整样本网格，不使用播放代理尺寸。'));
  parameterMatrix(host,'完整内参 K · 3 × 3',standard.K,3,3);parameterMatrix(host,'完整外参 T_world_camera · 4 × 4',standard.pose_valid===true?standard.T_world_camera:null,4,4);
  host.append(parameterNote('外参采用列向量：p_world = T_world_camera × p_camera；平移单位米。世界系经共同源帧原点对齐；外参缺失时不以单位阵冒充预测。'));
}
function parameterRenderQuality(host,data,snapshot){
  parameterRenderTrend(host,parameterCard());
  const pair=parameterFrameComparison(data);host.append(parameterEl('h3','当前帧对基准差异'));parameterPairs(host,[['基准',pair.baseline_label||pair.baseline_run_id],['基准角色',pair.declared_ground_truth?'用户声明的 GT':'算法结果 / 未声明 GT'],['相机系关节均差',pair.metrics?parameterNumber(pair.metrics.meanJointMm,2)+' mm':'—'],['相机系腕差',pair.metrics?parameterNumber(pair.metrics.wristMm,2)+' mm':'—'],['各自 K 下投影差',pair.metrics?.reprojectionDifferencePx===null||!pair.metrics?'—':parameterNumber(pair.metrics.reprojectionDifferencePx,2)+' px']]);host.append(parameterNote((pair.reason?pair.reason+' ':'')+pair.role_note+' 原始相机系欧距；无对齐。投影差为21点在各自 K 下投影后的二维距离均值，仅双方全部21点 Z > 0 且两组 K 有效时计算；不是整段统计指标。'));
  const standard=parameterStandard(data,snapshot),metadata={...parameterCard()?.run.result,...data?.payload.metadata};host.append(parameterNote('这些字段反映各算法自己的观测、存在性与补全过程，不能作为跨算法统一准确率。— 表示未提供，0 为真实数值。'));
  const table=parameterEl('table',undefined,'parameter-quality-table'),header=parameterEl('tr');for(const label of ['字段','当前值'])header.append(parameterEl('th',label));table.append(header);
  for(const row of parameterQualityRows(standard,metadata,parameterView.slot)){const tr=parameterEl('tr'),label=parameterEl('th',row.label),name=parameterEl('code',row.name);label.append(name);const value=parameterEl('td',row.value);tr.append(label,value);table.append(tr);const meaning=parameterEl('tr'),cell=parameterEl('td',row.meaning,'parameter-quality-meaning');cell.colSpan=2;meaning.append(cell);table.append(meaning);}host.append(table);
}
function parameterSourceDetails(host,label,value){if(value===null||value===undefined)return;const details=parameterEl('details');details.append(parameterEl('summary',label));const pre=parameterEl('pre',JSON.stringify(value,null,2));pre.dataset.i18nIgnore='';details.append(pre);host.append(details);}
function parameterRenderSource(host,context){
  const run=context.card.run,result=run.result,sample=parameterState().comparison.sample,protocol=typeof runProtocol==='function'?runProtocol(run,sample):null;
  parameterPairs(host,[['结果',run.label||run.algorithm],['结果 ID',run.id],['算法',run.algorithm],['状态',typeof resultStateLabel==='function'?resultStateLabel(run):run.status],['参考角色',result.reference_role==='user_declared_ground_truth'?'用户声明的 GT':'算法结果 / 未声明 GT'],['实现版本',result.backend_version],['检查点 / 权重',result.selected_checkpoint||run.options?.checkpoint],['模型配置',result.selected_config||run.options?.config],['工作进程耗时',typeof result.timings?.worker_end_to_end_seconds==='number'?parameterNumber(result.timings.worker_end_to_end_seconds,2)+' s':null],['主进程 RSS 峰值',typeof result.peak_rss_gib==='number'?parameterNumber(result.peak_rss_gib,2)+' GiB':null]]);
  if(protocol)parameterPairs(host,protocol.rows.filter(([label])=>!['结果 ID','实现版本','检查点','模型配置'].includes(label)));
  if(run.reuse||run.imported||result.component_sources)host.append(parameterNote(run.imported?'已有数值导入；资源和耗时不包含原模型推理。':result.component_sources?'手部与相机来自不同已发布组件；组合过程不执行模型推理。':'复用已校验结果；资源和耗时属于原运行。'));
  for(const [label,value]of [['运行选项',run.options],['并行阶段与资源',result.parallel_stages],['阶段计时',result.timings],['SLAM 阶段',result.slam_runtime],['组件来源',result.component_sources],['来源清单摘要',result.provenance_summary],['阶段复用',result.reused_native_stages_from],['导入来源',result.import_source]])parameterSourceDetails(host,label,value);
}

function parameterRenderNative(snapshot,context){
  const n=parameterView.nodes,key=context&&parameterRequestKey(context),pending=parameterView.pending.has('native:'+key),error=parameterView.errors.get('native:'+key);n.native.replaceChildren();
  n.rawStatus.textContent=!context?'所选结果尚无可读参数。':pending?'正在读取点击时的帧快照…':error?'读取失败：'+error:!snapshot?'当前帧尚未手动加载。切帧后请再次点击；播放不会触发原生参数请求。':`快照：样本帧 ${snapshot.sample_position+1} · 源帧 ${snapshot.source_frame_index} · ${parameterNumber(snapshot.timestamp_s,3)} s`;
  if(!snapshot)return;
  const native=snapshot.native;if(!native?.available){n.native.append(parameterNote(native?.reason||'此结果未发布可证明映射到当前帧的原生参数。'));return;}
  n.native.append(parameterNote('保持原生参数表示、维度、单位和左右手槽位；不将旋转矩阵、轴角或潜变量伪造为统一关节角。'));
  n.native.append(parameterNote('原生参数保留其原始坐标，不随共同世界原点变换。标准外参按当前共同原点变换。'));
  n.native.append(parameterNote('重叠片段原生值可能不同于最终融合几何。'));
  if(native.note)n.native.append(parameterNote(native.note));
  if(native.source)parameterPairs(n.native,[['原生来源',typeof native.source==='string'?native.source:JSON.stringify(native.source)]]);
  for(const field of native.fields||[]){const model=parameterNativeFieldModel(field),details=parameterEl('details',undefined,'parameter-native-field');details.append(parameterEl('summary',`${model.label} · ${model.representation} · ${model.shape}`));parameterPairs(details,[['字段名',model.name],['表示',model.representation],['单位',model.units],['坐标系',model.coordinate],['来源文件',model.source],['说明',model.note]]);const pre=parameterEl('pre',model.value);pre.dataset.i18nIgnore='';details.append(pre);n.native.append(details);}
}
function parameterRenderMesh(){
  const n=parameterView.nodes;if(!n)return;const context=parameterContext(),key=context&&parameterRequestKey(context),payload=key&&parameterView.meshCache.get(key),space=parameterSpace(),slot=parameterView.slot,vertices=(space==='world'?payload?.vertices_world_m:payload?.vertices_camera_m)?.[0]?.[slot],valid=payload?.geometry_valid?.[0]?.[slot]===true&&(space!=='world'||payload?.pose_valid?.[0]===true),pending=parameterView.pending.has('mesh:'+key),error=parameterView.errors.get('mesh:'+key);
  n.meshValues.replaceChildren();n.meshStatus.textContent=!context?'尚无可读结果。':pending?'正在读取当前帧网格…':error?'网格读取失败：'+error:!context.card.run.result.arrays?.vertices_camera_m?'此结果未发布网格顶点。':!payload?'尚未手动加载；每次仅取一帧。':!vertices?'当前坐标系没有可用网格。':`${slot?'右':'左'}手 · ${vertices.length} 顶点 · ${space==='world'?'世界系':'相机系'} · cm${valid?'':' · 几何无效，值保留缺失'}`;
  const page=parameterPagination(vertices?.length,parameterView.page,parameterView.pageSize);parameterView.page=page.page;n.prev.disabled=pending||page.page===0;n.next.disabled=pending||page.end>=page.total;n.pager.hidden=!vertices;n.pageLabel.textContent=`${page.total?page.start+1:0}–${page.end} / ${page.total} · ${page.page+1}/${page.pages} 页`;
  if(!vertices)return;const table=parameterEl('table'),header=parameterEl('tr');for(const label of ['顶点','X · cm','Y · cm','Z · cm'])header.append(parameterEl('th',label));table.append(header);
  for(let index=page.start;index<page.end;index++){const row=parameterEl('tr');row.append(parameterEl('th',String(index)));const point=valid&&parameterMath()?.validPoint(vertices[index])?vertices[index]:null;for(let axis=0;axis<3;axis++)row.append(parameterEl('td',point?parameterNumber(point[axis]*100,2):'—'));table.append(row);}n.meshValues.append(table);
}

function parameterPauseSnapshot(){if(typeof pause==='function')pause();const context=parameterContext();if(!context)throw new Error('所选结果尚无当前帧参数。');return context;}
function parameterResponseMatches(value,context){const sample=parameterState()?.comparison?.sample;return value?.schema===1&&value.run_id===context.runId&&value.sample_position===context.frame&&value.source_frame_index===sample?.source_frame_indices?.[context.frame]&&(!sample?.id||!value.sample_id||value.sample_id===sample.id);}
async function parameterFetchNative(context){
  const key=parameterRequestKey(context),pendingKey='native:'+key;if(parameterView.cache.has(key))return parameterView.cache.get(key);
  if(parameterView.pending.has(pendingKey)){await parameterView.pending.get(pendingKey);return parameterView.cache.get(key)||null;}
  const query=new URLSearchParams({frame:context.frame});if(context.origin!==null)query.set('origin',context.origin);parameterView.errors.delete(pendingKey);
  const request=Promise.resolve().then(()=>api(`api/comparison/runs/${encodeURIComponent(context.runId)}/parameters?${query}`)).then(value=>{if(!parameterContextCurrent(context))return null;if(!parameterResponseMatches(value,context))throw new Error('参数响应与当前帧不匹配，请重新加载。');parameterBoundedSet(parameterView.cache,key,value);return value;}).catch(error=>{if(parameterContextCurrent(context)){parameterBoundedSet(parameterView.errors,pendingKey,error.message);throw error;}return null;}).finally(()=>{if(parameterView.pending.get(pendingKey)===request)parameterView.pending.delete(pendingKey);if(parameterContextCurrent(context)){parameterView.renderKey='';updateParameterPanel();}});
  parameterBoundedSet(parameterView.pending,pendingKey,request);updateParameterPanel();return request;
}
async function loadParameterSnapshot(){const context=parameterPauseSnapshot();parameterStatus('已暂停；读取当前帧原生参数。');const value=await parameterFetchNative(context);if(value&&parameterContextCurrent(context)){parameterStatus('当前帧原生参数快照已加载。');parameterView.renderKey='';updateParameterPanel();}return value;}
async function parameterFetchMesh(context){
  const key=parameterRequestKey(context),pendingKey='mesh:'+key;if(parameterView.meshCache.has(key))return parameterView.meshCache.get(key);
  if(!context.card.run.result.arrays?.vertices_camera_m)return null;
  if(parameterView.pending.has(pendingKey)){await parameterView.pending.get(pendingKey);return parameterView.meshCache.get(key)||null;}
  const query=new URLSearchParams({start:context.frame,count:1,vertices:1});if(context.origin!==null&&context.card.run.result.arrays.T_world_camera)query.set('origin',context.origin);parameterView.errors.delete(pendingKey);
  const request=Promise.resolve().then(()=>api(`api/comparison/runs/${encodeURIComponent(context.runId)}/frames?${query}`)).then(value=>{if(!parameterContextCurrent(context))return null;if(value?.sample_positions?.[0]!==context.frame||value?.source_frame_indices?.[0]!==parameterState().comparison.sample.source_frame_indices[context.frame])throw new Error('网格响应与当前帧不匹配，请重新加载。');parameterBoundedSet(parameterView.meshCache,key,value);return value;}).catch(error=>{if(parameterContextCurrent(context)){parameterBoundedSet(parameterView.errors,pendingKey,error.message);throw error;}return null;}).finally(()=>{if(parameterView.pending.get(pendingKey)===request)parameterView.pending.delete(pendingKey);if(parameterContextCurrent(context)){parameterView.renderKey='';updateParameterPanel();}});
  parameterBoundedSet(parameterView.pending,pendingKey,request);updateParameterPanel();return request;
}
async function loadParameterMesh(){const context=parameterPauseSnapshot();parameterStatus('已暂停；读取当前帧网格。');const value=await parameterFetchMesh(context);if(value&&parameterContextCurrent(context)){parameterView.page=0;parameterView.renderKey='';updateParameterPanel();parameterStatus('当前帧网格已加载，按页显示。');}return value;}
function parameterFrameGeometry(data){if(!data)return null;const result={};for(const key of [...PARAMETER_FRAME_FIELDS,'joints_camera_m','joints_world_m'])if(data.payload[key]!==undefined)result[key]=data.payload[key]?.[data.index]??null;return result;}
async function exportParameterFrame(){
  if(parameterView.exporting)return;const context=parameterPauseSnapshot();parameterView.exporting=context;updateParameterPanel();parameterStatus('已暂停；准备当前帧完整参数 JSON…');
  try{if(typeof ensureFrame==='function')await ensureFrame(context.frame);if(!parameterExportCurrent(context))return;const data=parameterCurrentData(),native=await parameterFetchNative(context);if(!native||!parameterExportCurrent(context))return;
    const mesh=await parameterFetchMesh(context);if(!parameterExportCurrent(context))return;
    const value={...native,export_context:{comparison_id:context.comparisonId,result_revision:context.revision,selected_hand:context.slot?'right':'left',display_space:context.space,origin_source_frame:context.origin,geometry_units:'m',export_scope:'current frame; both native hand slots preserved'},geometry:parameterFrameGeometry(data),derived_geometry:parameterDerivedGeometry(data,context,native),mesh:mesh?{vertices_camera_m:mesh.vertices_camera_m?.[0]??null,vertices_world_m:mesh.vertices_world_m?.[0]??null}:null,run_provenance:{label:context.card.run.label,algorithm:context.card.run.algorithm,options:context.card.run.options,result:context.card.run.result}};
    const blob=new Blob([JSON.stringify(value,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=parameterEl('a');link.href=url;link.download=`parameters-${context.runId}-frame-${context.frame+1}.json`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);parameterStatus('当前帧参数 JSON 已导出。');
  }finally{if(parameterView.exporting===context){parameterView.exporting=false;if(parameterView.open){if(!parameterExportCurrent(context))parameterStatus('当前选择或基准已变化，本次导出已取消。请在所需帧重新导出。');parameterView.renderKey='';updateParameterPanel();}}}
}

if(typeof module!=='undefined')module.exports={parameterNumber,parameterValue,parameterSlotValue,parameterRequestKey,parameterPagination,parameterNativeFieldModel,parameterQualityRows,parameterBoundedSet,PARAMETER_CACHE_LIMIT};
if(typeof document!=='undefined')initializeParameterPanel();
