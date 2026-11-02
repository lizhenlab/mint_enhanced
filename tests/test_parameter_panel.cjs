'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const helpers=require('../mint/comparison/web/parameter-panel.js');
const math=require('../mint/comparison/web/parameter-math.js');

function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function fixture(){
  const nodes={},requests=[],downloads=[],listeners={};let paused=0;
  class Element{
    constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.attributes={};this._text='';this._value='';this.hidden=false;this.open=false;this.disabled=false;this.isConnected=true;this.style={};this._classes=new Set();this.events={};this.classList={add:(...names)=>names.forEach(name=>this._classes.add(name)),remove:(...names)=>names.forEach(name=>this._classes.delete(name)),contains:name=>this._classes.has(name)};}
    set id(value){this._id=value;nodes[value]=this;}get id(){return this._id;}
    set className(value){this._classes=new Set(String(value||'').split(/\s+/).filter(Boolean));}get className(){return [...this._classes].join(' ');}
    set textContent(value){this._text=String(value??'');this.children=[];}get textContent(){return this._text+this.children.map(child=>child?.textContent??child).join('');}
    set value(value){this._value=String(value??'');}get value(){return this._value;}
    append(...children){for(const child of children){this.children.push(child);if(child&&typeof child==='object')child.parentElement=this;}if(this.tagName==='SELECT'&&!this.value&&this.children.length)this.value=this.children[0].value;}
    replaceChildren(...children){this.children=[];this._text='';this.append(...children);}
    setAttribute(name,value){this.attributes[name]=String(value);}
    getAttribute(name){return this.attributes[name];}
    addEventListener(name,callback){(this.events[name]??=[]).push(callback);}
    dispatch(name,event={}){event.currentTarget??=this;for(const callback of this.events[name]||[])callback(event);}
    focus(){document.activeElement=this;this.focused=true;}
    remove(){this.parentElement.children=this.parentElement.children.filter(child=>child!==this);}
    click(){if(this.tagName==='A')downloads.push({name:this.download,url:this.href});else this.onclick?.({currentTarget:this});}
    getContext(){return null;}
  }
  const document={createElement:tag=>new Element(tag),getElementById:id=>nodes[id]||null,body:new Element('body'),activeElement:null,addEventListener:(name,callback)=>(listeners[name]??=[]).push(callback)};
  const add=(id,tag='div')=>{const node=new Element(tag);node.id=id;return node;};
  const wrapper=add('wrapper'),panel=add('parameterPanel','aside');wrapper.append(add('runs'),panel);const trigger=add('toggleParameterPanel','button');add('space','select').value='camera';
  const hand=Array.from({length:21},(_,i)=>[i*.01,i*.005,1+i*.002]),frames=[0,1,2].map(frame=>hand.map(point=>[point[0]+frame*.01,point[1],point[2]]));
  const payload={sample_positions:[0,1,2],source_frame_indices:[10,12,17],timestamps_s:[0,.1,.3],available:[true,true,true],joints_camera_m:frames.map(points=>[points,hand]),joints_world_m:frames.map(points=>[points,hand]),geometry_valid:[[true,true],[true,true],[true,true]],presence:[[true,true],[false,true],[true,true]],pose_valid:[true,true,true],K:[0,1,2].map(()=>[[600,0,320],[0,600,240],[0,0,1]]),T_world_camera:[0,1,2].map(()=>[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]])};
  const runs=['a','b'].map(id=>({id,label:'Algorithm '+id,algorithm:'mint',status:'completed',options:{},result:{arrays:{joints_camera_m:{},T_world_camera:{},vertices_camera_m:{}},backend_version:'rev1'}})),cards=new Map(runs.map(run=>[run.id,{run,trajectory:null,trajectoryKey:''}]));
  const state={comparison:{id:'comparison-1',sample_id:'sample-1',common_world_origin_source_frame:10,sample:{id:'sample-1',frames:3,width:640,height:480,source_frame_indices:[10,12,17],timestamps_s:[0,.1,.3]},runs},cards,frame:0,playing:false,pollToken:1,cache:new Map()};
  const context=vm.createContext({console,document,URL:Object.assign(class extends URL {},{createObjectURL:blob=>{downloads.blob=blob;return 'blob:test';},revokeObjectURL(){}}),URLSearchParams,Blob,setTimeout:()=>0,ParameterMath:math,state,resultView:{candidate:'a',reference:'b'},FRAME_READ_COUNT:16,
    resultRevision:run=>JSON.stringify([run.id,run.result.backend_version]),resultFrameKey:run=>run.id,dataFor:()=>({payload,index:state.frame}),ensureFrame:async()=>{},pause:()=>{paused++;state.playing=false;},render:()=>{},api:async url=>{requests.push(url);throw new Error('Unexpected API '+url);}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../mint/comparison/web/parameter-panel.js'),'utf8'),context,{filename:'parameter-panel.js'});
  const run=source=>vm.runInContext(source,context);
  const response=(frame=state.frame,id='a')=>({schema:1,run_id:id,sample_id:'sample-1',sample_position:frame,source_frame_index:state.comparison.sample.source_frame_indices[frame],timestamp_s:state.comparison.sample.timestamps_s[frame],standard:{available:true,K:payload.K[frame]},native:{available:true,source:'native/cache.npz',fields:[{name:'pose6d',representation:'6d_rotation',shape:[2,15,6],units:'dimensionless',coordinate_frame:'native',value:[1,null,0],source_file:'/native/cache.npz'}]}});
  run('initializeParameterPanel()');
  return {context,run,nodes,state,payload,cards,requests,response,downloads,trigger,listeners,document,paused:()=>paused};
}

test('missing values stay missing while explicit zero and false retain their meanings',()=>{
  assert.equal(helpers.parameterValue(null),'— · 未提供');assert.equal(helpers.parameterValue(undefined),'— · 未提供');assert.equal(helpers.parameterValue(NaN),'— · 未提供');assert.equal(helpers.parameterValue(0),'0.0000');assert.equal(helpers.parameterValue(false),'否');
  const rows=helpers.parameterQualityRows({confidence:[null,0],presence:[false,true]},{confidence_semantics:'Native score, uncalibrated'},0);
  assert.equal(rows.find(row=>row.name==='confidence').value,'— · 未提供');assert.equal(rows.find(row=>row.name==='presence').value,'否');assert.match(rows.find(row=>row.name==='confidence').meaning,/uncalibrated/);
});
test('request identity includes comparison, publication revision, token, origin and sample position',()=>{
  const context={comparisonId:'comparison',pollToken:1,runId:'run',revision:'v1',origin:0,frame:0};const original=helpers.parameterRequestKey(context);
  for(const [key,value]of Object.entries({comparisonId:'other',pollToken:2,runId:'other',revision:'v2',origin:1,frame:1}))assert.notEqual(original,helpers.parameterRequestKey({...context,[key]:value}));
});
test('778 vertices are bounded to 20 or 50 rows, including the last page',()=>{
  assert.deepEqual(helpers.parameterPagination(778,99,50),{page:15,pageSize:50,pages:16,total:778,start:750,end:778});assert.equal(helpers.parameterPagination(778,0,778).end,20);assert.equal(helpers.parameterPagination(778,-1,20).page,0);
});
test('native metadata preserves representation and units instead of inventing unified angles',()=>{
  const row=helpers.parameterNativeFieldModel({name:'pose6d',representation:'6d_rotation',units:'dimensionless',shape:[2,15,6],coordinate_frame:'native',value:[null,0]});assert.equal(row.representation,'6d_rotation');assert.equal(row.units,'dimensionless');assert.equal(row.shape,'2 × 15 × 6');assert.match(row.value,/null/);assert.equal(row.coordinate,'native');
});
test('snapshot caches and error caches stay bounded',()=>{
  const cache=new Map();for(let i=0;i<20;i++)helpers.parameterBoundedSet(cache,String(i),i);assert.equal(cache.size,helpers.PARAMETER_CACHE_LIMIT);assert.equal(cache.has('0'),false);assert.equal(cache.get('19'),19);
});
test('the panel starts closed and playback/tab changes do not request native or mesh data',()=>{
  const x=fixture();assert.equal(x.nodes.parameterPanel.hidden,true);x.run('openParameterPanel("a")');assert.equal(x.nodes.parameterPanel.hidden,false);assert.equal(x.nodes.parameterRun.value,'a');
  x.state.playing=true;for(let frame=0;frame<3;frame++){x.state.frame=frame;x.run('updateParameterPanel()');}
  for(const key of ['camera','quality','source','hand'])x.run(`parameterActivateTab('${key}')`);
  x.nodes.parameterAdvanced.open=true;x.run('updateParameterPanel()');assert.equal(x.requests.length,0);assert.equal(x.state.playing,true);assert.match(x.nodes.parameterFrame.textContent,/源帧 17/);assert.match(x.nodes.parameterRawStatus.textContent,/尚未手动加载/);
});
test('manual native loads pause, preserve raw data and reuse only the exact frame cache',async()=>{
  const x=fixture();x.run('openParameterPanel("a")');x.context.api=async url=>{x.requests.push(url);return x.response();};x.state.playing=true;await x.run('loadParameterSnapshot()');
  assert.equal(x.paused(),1);assert.equal(x.state.playing,false);assert.equal(x.requests.length,1);assert.match(x.requests[0],/parameters\?frame=0&origin=10/);assert.match(x.nodes.parameterNativeFields.textContent,/6d_rotation/);assert.match(x.nodes.parameterNativeFields.textContent,/dimensionless/);assert.match(x.nodes.parameterNativeFields.textContent,/原生参数保留其原始坐标/);
  await x.run('loadParameterSnapshot()');assert.equal(x.requests.length,1);x.state.frame=1;x.run('updateParameterPanel()');assert.match(x.nodes.parameterRawStatus.textContent,/尚未手动加载/);assert.equal(x.requests.length,1);
});
test('late raw responses never populate the new frame, run, revision, comparison or hand selection',async()=>{
  for(const change of ['frame','run','revision','origin','comparison','token','hand','close']){
    const x=fixture(),request=deferred();x.run('openParameterPanel("a")');x.context.api=async url=>{x.requests.push(url);return request.promise;};const pending=x.run('loadParameterSnapshot()');await Promise.resolve();const response=x.response(0);
    if(change==='frame')x.state.frame=1;if(change==='run')x.run('parameterView.runId="b";parameterView.selection++');if(change==='revision')x.cards.get('a').run.result.backend_version='rev2';if(change==='origin')x.state.comparison.common_world_origin_source_frame=12;if(change==='comparison')x.state.comparison.id='comparison-2';if(change==='token')x.state.pollToken++;if(change==='hand')x.nodes.parameterHand1.click();if(change==='close')x.run('closeParameterPanel()');
    x.run('updateParameterPanel()');request.resolve(response);await pending;assert.equal(x.run('parameterView.cache.size'),0,change);assert.doesNotMatch(x.nodes.parameterNativeFields.textContent,/6d_rotation/,change);
  }
});
test('a mismatched backend frame is rejected visibly and can be retried',async()=>{
  const x=fixture();x.run('openParameterPanel("a")');x.context.api=async()=>x.response(1);await assert.rejects(x.run('loadParameterSnapshot()'),/响应与当前帧不匹配/);assert.equal(x.run('parameterView.cache.size'),0);assert.match(x.nodes.parameterRawStatus.textContent,/读取失败/);
  x.context.api=async()=>x.response(0);await x.run('loadParameterSnapshot()');assert.equal(x.run('parameterView.cache.size'),1);
});
test('mesh loading is explicit, one frame only and table pagination never expands all 778 rows',async()=>{
  const x=fixture();x.run('openParameterPanel("a")');x.context.api=async url=>{x.requests.push(url);return {sample_positions:[0],source_frame_indices:[10],geometry_valid:[[true,true]],pose_valid:[true],vertices_camera_m:[Array.from({length:2},()=>Array.from({length:778},()=>[.1,.2,1]))]};};
  await x.run('loadParameterMesh()');assert.match(x.requests[0],/start=0&count=1&vertices=1/);assert.equal(x.nodes.parameterMeshValues.children[0].children.length,21);x.nodes.parameterMeshPageSize.value='50';x.nodes.parameterMeshPageSize.onchange();assert.equal(x.nodes.parameterMeshValues.children[0].children.length,51);assert.match(x.nodes.parameterMeshPage.textContent,/1–50 \/ 778/);assert.equal(x.paused(),1);
});
test('close button and composition-safe Escape return focus to the actual trigger',()=>{
  const x=fixture();x.trigger.focus();x.run('openParameterPanel("a",document.activeElement)');x.nodes.closeParameterPanel.click();assert.equal(x.document.activeElement,x.trigger);assert.equal(x.nodes.parameterPanel.hidden,true);
  x.run('openParameterPanel("a",document.getElementById("toggleParameterPanel"))');const composition={key:'Escape',isComposing:true,preventDefault(){this.prevented=true;}};for(const callback of x.listeners.keydown)callback(composition);assert.equal(x.nodes.parameterPanel.hidden,false);const escape={key:'Escape',preventDefault(){this.prevented=true;}};for(const callback of x.listeners.keydown)callback(escape);assert.equal(x.nodes.parameterPanel.hidden,true);assert.equal(escape.prevented,true);
});
test('tab keyboard navigation stays inside the panel and announces the selected tab',()=>{
  const x=fixture();x.run('openParameterPanel("a")');const event={key:'ArrowRight',preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;}};x.nodes['parameterTab-hand'].onkeydown(event);assert.equal(x.nodes['parameterTab-camera'].getAttribute('aria-selected'),'true');assert.equal(x.nodes['parameterContent-camera'].hidden,false);assert.equal(event.stopped,true);assert.equal(x.requests.length,0);
});
test('joint selections affect only the highlight contract and closing removes it',()=>{
  const x=fixture();x.run('openParameterPanel("a")');const table=x.nodes['parameterContent-hand'].children.find(node=>node.className==='parameter-joints').children.find(node=>node.className==='parameter-table-scroll').children[0];table.children[1].children[0].children[0].click();assert.deepEqual(JSON.parse(x.run('JSON.stringify(parameterPanelJointSelection())')),{runId:'a',slot:0,joint:0});assert.equal(x.context.resultView.candidate,'a');x.run('closeParameterPanel()');assert.equal(x.run('parameterPanelJointSelection()'),null);
});
test('export is explicit, pauses, includes full native and mesh values, and does not run on tab changes',async()=>{
  const x=fixture();x.run('openParameterPanel("a")');x.context.api=async url=>{x.requests.push(url);return url.includes('/parameters?')?x.response():{sample_positions:[0],source_frame_indices:[10],geometry_valid:[[true,true]],pose_valid:[true],vertices_camera_m:[[[],[]]]};};
  await x.run('exportParameterFrame()');assert.equal(x.downloads.length,1);assert.match(x.downloads[0].name,/parameters-a-frame-1\.json/);const value=JSON.parse(await x.downloads.blob.text());assert.equal(value.native.fields[0].representation,'6d_rotation');assert.equal(value.geometry.joints_camera_m.length,2);assert.deepEqual(value.mesh.vertices_camera_m,[[],[]]);assert.equal(x.requests.length,2);
});
test('a newly loaded adjacent block refreshes speed at the same displayed frame',()=>{
  const x=fixture();let ready=false;x.state.frame=1;x.context.parameterPreviousFrame=()=>ready?{points:x.payload.joints_camera_m[0][0],valid:true,sampleIndex:0,timestampS:0,space:'camera'}:null;x.run('openParameterPanel("a")');assert.match(x.nodes['parameterContent-hand'].textContent,/需要相邻有效样本帧/);ready=true;x.run('updateParameterPanel()');assert.match(x.nodes['parameterContent-hand'].textContent,/0\.100 m\/s/);
});
test('late mesh data and exports are discarded after a frame or algorithm switch',async()=>{
  const x=fixture(),mesh=deferred();x.run('openParameterPanel("a")');x.context.api=()=>mesh.promise;const pending=x.run('loadParameterMesh()');await Promise.resolve();x.state.frame=1;mesh.resolve({sample_positions:[0],source_frame_indices:[10],geometry_valid:[[true,true]],vertices_camera_m:[[[],[]]]});await pending;assert.equal(x.run('parameterView.meshCache.size'),0);
  const y=fixture(),native=deferred();y.run('openParameterPanel("a")');y.context.api=()=>native.promise;const exporting=y.run('exportParameterFrame()');await new Promise(resolve=>setImmediate(resolve));y.run('parameterView.runId="b";parameterView.selection++;updateParameterPanel()');native.resolve(y.response(0));await exporting;assert.equal(y.downloads.length,0);assert.equal(y.run('parameterView.cache.size'),0);
});
test('unavailable native parameters retain the backend reason instead of synthesizing pose',async()=>{
  const x=fixture();x.run('openParameterPanel("a")');x.context.api=async()=>({...x.response(),native:{available:false,fields:[],reason:'无法证明原生文件到此帧的映射'}});await x.run('loadParameterSnapshot()');assert.match(x.nodes.parameterNativeFields.textContent,/无法证明原生文件到此帧的映射/);assert.doesNotMatch(x.nodes.parameterNativeFields.textContent,/6d_rotation/);
});
test('pending geometry has an explicit loading state instead of reporting missing camera fields',()=>{
  const x=fixture();x.context.dataFor=()=>null;x.run('openParameterPanel("a");parameterActivateTab("camera")');assert.equal(x.nodes.parameterGeometryStatus.hidden,false);assert.match(x.nodes['parameterContent-camera'].textContent,/正在读取当前帧几何/);assert.doesNotMatch(x.nodes['parameterContent-camera'].textContent,/fx \/ fy/);
  x.context.dataFor=()=>({payload:x.payload,index:x.state.frame});x.run('updateParameterPanel()');assert.equal(x.nodes.parameterGeometryStatus.hidden,true);assert.match(x.nodes['parameterContent-camera'].textContent,/fx \/ fy/);
});
test('inspecting a hidden result explains the highlight and changes the picture only on explicit action',async()=>{
  const x=fixture(),focused=[];let visible=[x.cards.get('b')];x.context.resultView.candidate='b';x.context.visibleResultCards=()=>visible;x.context.focusParameterResult=async id=>{focused.push(id);visible=[x.cards.get(id)];x.context.resultView.expanded=id;};x.run('openParameterPanel("a")');assert.equal(x.run('parameterView.nodes.visibility.hidden'),false);assert.match(x.run('parameterView.nodes.visibility.textContent'),/当前未显示/);assert.equal(x.context.resultView.candidate,'b');assert.equal(focused.length,0);
  await x.nodes.showParameterResult.onclick();assert.deepEqual(focused,['a']);assert.equal(x.context.resultView.expanded,'a');assert.equal(x.context.resultView.candidate,'b');assert.equal(x.run('parameterView.nodes.visibility.hidden'),true);
});
test('the initial hand panel is compact with collapsed details and JSON alongside the close control',()=>{
  const x=fixture();x.run('openParameterPanel("a")');const hand=x.nodes['parameterContent-hand'],details=hand.children.filter(node=>node.tagName==='DETAILS');assert.equal(details.length,3);assert.ok(details.every(node=>node.open===false));assert.equal(x.nodes.parameterCoordinates.textContent,'相机系 · cm；X右/Y下/Z前');assert.match(x.nodes.parameterCoordinates.title,/列向量|OpenCV/);assert.equal(x.nodes.exportParameterFrame.parentElement.className,'parameter-heading-actions');assert.equal(x.nodes.exportParameterFrame.parentElement.children.at(-1),x.nodes.closeParameterPanel);assert.equal(x.run('parameterView.nodes.footer.hidden'),true);
});
test('current-frame comparison keeps raw per-joint millimetres and each result own-K projection semantics',()=>{
  const x=fixture(),reference=structuredClone(x.payload);reference.joints_camera_m=reference.joints_camera_m.map(hands=>hands.map(points=>points.map(point=>[point[0]+.003,point[1],point[2]])));reference.K=reference.K.map(()=>[[800,0,320],[0,800,240],[0,0,1]]);x.context.baselineCard=()=>x.cards.get('b');x.context.dataFor=card=>({payload:card.run.id==='b'?reference:x.payload,index:x.state.frame});x.run('openParameterPanel("a")');const joints=x.nodes['parameterContent-hand'].children.find(node=>node.className==='parameter-joints').children.find(node=>node.className==='parameter-table-scroll').children[0];assert.equal(joints.children[0].children[4].textContent,'对基准 · mm');assert.equal(joints.children[1].children[4].textContent,'3.00');
  x.run('parameterActivateTab("quality")');const text=x.nodes['parameterContent-quality'].textContent;assert.match(text,/Algorithm b/);assert.match(text,/相机系关节均差3\.00 mm/);assert.match(text,/相机系腕差3\.00 mm/);assert.match(text,/算法差异，不表示准确率/);assert.match(text,/全部21点 Z > 0/);x.cards.get('b').run.result.reference_role='user_declared_ground_truth';x.run('updateParameterPanel()');assert.match(x.nodes['parameterContent-quality'].textContent,/基准为用户声明的 GT/);
});
test('self-reference and invalid reference geometry never produce synthetic zero differences',()=>{
  const x=fixture();x.context.baselineCard=()=>x.cards.get('a');x.run('openParameterPanel("a");parameterActivateTab("quality")');assert.match(x.nodes['parameterContent-quality'].textContent,/当前结果就是基准/);assert.equal(x.run('parameterFrameComparison(parameterCurrentData()).metrics'),null);
  const invalid=structuredClone(x.payload);invalid.geometry_valid[0][0]=false;x.context.baselineCard=()=>x.cards.get('b');x.context.dataFor=card=>({payload:card.run.id==='b'?invalid:x.payload,index:x.state.frame});x.run('updateParameterPanel()');assert.equal(x.run('parameterFrameComparison(parameterCurrentData()).metrics'),null);assert.match(x.nodes['parameterContent-quality'].textContent,/无有效几何，差异保持缺失/);
});
test('late baseline geometry and a newly published baseline refresh the same displayed frame',()=>{
  const x=fixture();let ready=false;const reference=structuredClone(x.payload);reference.joints_camera_m=reference.joints_camera_m.map(hands=>hands.map(points=>points.map(point=>[point[0]+.003,point[1],point[2]])));x.context.baselineCard=()=>x.cards.get('b');x.context.dataFor=card=>card.run.id==='b'?(ready?{payload:reference,index:x.state.frame}:null):{payload:x.payload,index:x.state.frame};x.run('openParameterPanel("a");parameterActivateTab("quality")');assert.match(x.nodes['parameterContent-quality'].textContent,/正在读取当前帧基准几何/);ready=true;x.run('updateParameterPanel()');assert.match(x.nodes['parameterContent-quality'].textContent,/相机系关节均差3\.00 mm/);reference.joints_camera_m[0][0]=x.payload.joints_camera_m[0][0].map(point=>[point[0]+.006,point[1],point[2]]);x.cards.get('b').run.result.backend_version='rev2';x.run('updateParameterPanel()');assert.match(x.nodes['parameterContent-quality'].textContent,/相机系关节均差6\.00 mm/);
});
test('JSON includes reproducible derived values and cancels when the clicked baseline publication changes',async()=>{
  const x=fixture();x.state.frame=1;x.context.baselineCard=()=>x.cards.get('b');x.run('openParameterPanel("a")');x.context.api=async url=>url.includes('/parameters?')?x.response(1):{sample_positions:[1],source_frame_indices:[12],geometry_valid:[[true,true]],pose_valid:[true],vertices_camera_m:[[[],[]]]};await x.run('exportParameterFrame()');const json=JSON.parse(await x.downloads.blob.text()),derived=json.derived_geometry;assert.equal(derived.hand_metrics.wristM[0],.01);assert.equal(derived.wrist_camera_velocity.previous_sample_position,0);assert.equal(derived.wrist_camera_velocity.dtS,.1);assert.ok(derived.camera_metrics.horizontalFovDeg>0);assert.equal(derived.reference_comparison.baseline_run_id,'b');assert.match(derived.description,/not native MANO/);assert.match(derived.wrist_camera_velocity.note,/not physical world velocity/);
  const y=fixture(),pending=deferred();y.context.baselineCard=()=>y.cards.get('b');y.run('openParameterPanel("a")');y.context.api=()=>pending.promise;const exporting=y.run('exportParameterFrame()');await new Promise(resolve=>setImmediate(resolve));y.cards.get('b').run.result.backend_version='rev2';pending.resolve(y.response(0));await exporting;assert.equal(y.downloads.length,0);assert.equal(y.run('parameterView.cache.size'),1);assert.match(y.nodes.parameterStatus.textContent,/基准已变化/);
});

test('success feedback belongs to its frame and is cleared after seeking',()=>{const x=fixture();x.run('openParameterPanel("a");parameterStatus("current frame exported")');assert.equal(x.nodes.parameterStatus.textContent,'current frame exported');x.context.state.frame=1;x.run('updateParameterPanel()');assert.equal(x.nodes.parameterStatus.textContent,'');assert.equal(x.run('parameterView.nodes.footer.hidden'),true);});
