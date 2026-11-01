'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const root='../mint/comparison/web/',app=fs.readFileSync(require.resolve(root+'app.js'),'utf8');
const {resultRevision}=require(root+'result-tools.js');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function runResult(id='run',revision=1,status='partial'){return {id,status,label:id,algorithm:'mint',options:{},result:{run_id:id,frames:revision,arrays:{joints_camera_m:{file:{path:'/result/joints.npy',mtime_ns:revision,inode:7},shape:[revision,2,21,3],dtype:'float32'},vertices_camera_m:{file:{path:'/result/vertices.npy',mtime_ns:revision,inode:8}}}}};}
function fixture(){
 const nodes={},calls=[],notices=[],rendered=[];const get=id=>nodes[id]??={value:'',dataset:{},open:false,checked:false,disabled:false,children:[],replaceChildren(...v){this.children=v;},append(...v){this.children.push(...v);},close(){this.open=false;calls.push(['close',id]);},setAttribute(){},classList:{toggle(){},add(){},remove(){}}};
 const state={comparison:{id:'comparison',sample_id:'sample',sample:{frames:32},common_world_origin_source_frame:null,runs:[]},pollToken:1,cards:new Map(),cache:new Map(),loading:new Map(),frame:0,metricsKey:''};
 const sandbox={console,state,workspaceUI:{tab:'view',taskDialogSession:1},platform:{serial:1},URLSearchParams,structuredClone,Number,FRAME_READ_COUNT:16,TERMINAL:new Set(['completed','partial','failed','cancelled','interrupted']),STAGES:{},$ : get,
  document:{createElement:tag=>({tag,dataset:{},children:[],append(...v){this.children.push(...v);},click(){calls.push(['download',this.href]);}})},
  api:async(url,data)=>{calls.push([url,data]);throw Error('Unexpected request '+url);},notify:(message,error)=>notices.push([message,error]),showNotice:(...v)=>notices.push(v),
  setTimeout:fn=>{fn();return 1;},clearTimeout(){},window:{innerWidth:1280},
  decorateInputBody:v=>v,selectedAlgorithmsFrom:()=>[{algorithm:'mint',options:{}}],validateTaskInput(){},preflightComparison:async()=>({ready:true}),
  frameReadCards:()=>[...state.cards.values()],visibleResultCards:()=>[...state.cards.values()],renderMetrics:v=>rendered.push(v),geometryMode:()=>get('geometryStyle').value||'skeleton',
  loadHistory:async()=>calls.push(['history']),refresh:async(id,token)=>calls.push(['refresh',id,token]),poll:(id,token)=>calls.push(['poll',id,token]),updateCard(){},syncPlaybackReadiness(){},
 };
 const context=vm.createContext(sandbox),run=source=>vm.runInContext(source,context);
 run(fs.readFileSync(require.resolve(root+'result-tools.js'),'utf8'));
 run(app.slice(app.indexOf('function inputLabel('),app.indexOf("$('start').onclick")));
 run(app.slice(app.indexOf('function importDraftPayload(){'),app.indexOf("$('import').onclick")));
 run(app.slice(app.indexOf('async function exportSelectedVideo(){'),app.indexOf("$('snapshot').onclick")));
 run(fs.readFileSync(require.resolve(root+'geometry-tools.js'),'utf8'));
 const actualRefresh=run('refresh');
 Object.assign(context,{renderMetrics:v=>rendered.push(v),updateCard(){},ensureMeshFrame:async()=>{},loadHistory:async()=>calls.push(['history']),refresh:async(id,token)=>calls.push(['refresh',id,token]),poll:(id,token)=>calls.push(['poll',id,token])});
 get('taskDialog').open=true;for(const [id,value]of Object.entries({source:'/data/a.mp4',view:'full',targetFps:'0',startFrame:'0',maxFrames:'',camera:'',executionPriority:'interactive',executionCache:'exact',importAlgorithm:'mint',importFormat:'normalized_npz',importPath:'/data/result.npz',importMap:'[0]',importSource:'source',importGrid:'',exportGeometry:'skeleton',exportScope:'visible'}))get(id).value=value;
 return {context,state,get,run,calls,notices,rendered,actualRefresh};
}
function setRun(f,run=runResult()){f.state.comparison.runs=[run];const card={run,trajectory:null,trajectoryKey:'',trajectoryRequest:null};f.state.cards.set(run.id,card);return card;}
function replaceRun(f,card,run){card.run=run;f.state.comparison.runs=[run];}

test('result identity is stable across metadata key order and changes when a publication or status changes',()=>{
 const a=runResult(),b=structuredClone(a);b.result.arrays={vertices_camera_m:b.result.arrays.vertices_camera_m,joints_camera_m:b.result.arrays.joints_camera_m};assert.equal(resultRevision(a),resultRevision(b));
 b.result.arrays.joints_camera_m.file.mtime_ns++;assert.notEqual(resultRevision(a),resultRevision(b));b.result.arrays.joints_camera_m.file.mtime_ns--;b.status='completed';assert.notEqual(resultRevision(a),resultRevision(b));
});
test('an old frame response cannot populate or replace the same run after new arrays are published',async()=>{
 const f=fixture(),card=setRun(f),old=deferred(),latest=deferred();let count=0;f.context.api=()=>++count===1?old.promise:latest.promise;
 const before=f.run('ensureFrame(0)');replaceRun(f,card,runResult('run',2,'completed'));const after=f.run('ensureFrame(0)');latest.resolve({tag:'new'});await after;old.resolve({tag:'old'});await before;
 assert.equal(f.state.cache.size,1);assert.equal(f.run('dataFor(state.cards.get("run")).payload.tag'),'new');assert.equal(count,2);
});
test('trajectory replies are tied to the publication, rather than only its run ID',async()=>{
 const f=fixture(),card=setRun(f),old=deferred(),latest=deferred();f.get('showTrails').checked=true;let count=0;f.context.api=()=>++count===1?old.promise:latest.promise;
 f.context.card=card;f.run('state.cache.set(resultFrameKey(card.run,0,null),{})');const before=f.run('ensureFrame(0)');await Promise.resolve();
 replaceRun(f,card,runResult('run',2,'completed'));f.run('state.cache.set(resultFrameKey(card.run,0,null),{})');const after=f.run('ensureFrame(0)');await Promise.resolve();latest.resolve({tag:'new trajectory'});await after;old.resolve({tag:'old trajectory'});await before;assert.equal(card.trajectory.tag,'new trajectory');
});
test('mesh responses from a retired result publication are discarded',async()=>{
 const f=fixture(),card=setRun(f),old=deferred(),latest=deferred();f.get('geometryStyle').value='mesh';const geometry=fs.readFileSync(require.resolve(root+'geometry-tools.js'),'utf8');f.run(geometry.slice(geometry.indexOf('async function ensureMeshFrame('),geometry.indexOf('function meshFor(')));f.run('meshView.topology={hands:{}}');let count=0;f.context.api=()=>++count===1?old.promise:latest.promise;
 const before=f.run('ensureMeshFrame(0)');replaceRun(f,card,runResult('run',2,'completed'));const after=f.run('ensureMeshFrame(0)');latest.resolve({tag:'new mesh'});await after;old.resolve({tag:'old mesh'});await before;assert.equal(f.run('meshView.cache.size'),1);assert.equal(f.run('meshFor(state.cards.get("run")).payload.tag'),'new mesh');
});
test('new metrics win over a delayed response for old arrays and failed requests remain retryable',async()=>{
 const f=fixture(),card=setRun(f),other=runResult('other'),old=deferred(),latest=deferred();f.state.comparison.runs.push(other);let count=0;f.context.api=()=>++count===1?old.promise:latest.promise;
 const before=f.run('loadComparisonMetrics()');card.run=runResult('run',2,'completed');f.state.comparison.runs=[card.run,other];const after=f.run('loadComparisonMetrics()');latest.resolve({metrics:[{tag:'new metrics'}]});await after;old.resolve({metrics:[{tag:'old metrics'}]});await before;assert.equal(f.state.metricsRows[0].tag,'new metrics');assert.equal(f.rendered.length,1);assert.equal(f.get('refreshMetrics').disabled,false);
 f.state.metricsRows=null;f.context.api=async()=>{throw Error('temporary');};await assert.rejects(f.run('loadComparisonMetrics()'),/temporary/);assert.equal(f.state.metricRequest,null);assert.equal(f.get('refreshMetrics').disabled,false);
});
test('a pending card action neither refreshes nor restarts polling for a later comparison',async()=>{
 const f=fixture(),request=deferred(),button={dataset:{},disabled:false};f.context.button=button;f.context.api=(url,body)=>{f.calls.push([url,body]);return request.promise;};const pending=f.run('performRunAction("run","resume",button)');await f.run('performRunAction("run","resume",button)');assert.equal(f.calls.length,1);
 f.state.pollToken++;f.state.comparison={id:'later',runs:[]};request.resolve({});await pending;assert.equal(f.calls.some(v=>['refresh','poll'].includes(v[0])),false);
});
test('editing a comparison during preflight never submits the new unvalidated input',async()=>{
 const f=fixture(),preflight=deferred();f.context.preflightComparison=()=>preflight.promise;f.context.api=(url,data)=>{f.calls.push([url,data]);return Promise.resolve({});};const pending=f.run('submit()');f.get('source').value='/data/later.mp4';preflight.resolve({ready:true});await pending;assert.equal(f.calls.length,0);assert.match(f.get('formError').textContent,/输入或算法参数已变化/);assert.equal(f.get('source').value,'/data/later.mp4');
});
test('leaving or reopening the dialog during preflight cancels submission intent without a server task',async()=>{
 const f=fixture(),preflight=deferred();let checks=0;f.context.preflightComparison=()=>{checks++;return preflight.promise;};const pending=f.run('submit()');await f.run('submit()');assert.equal(checks,1);f.context.workspaceUI.taskDialogSession++;preflight.resolve({ready:true});await pending;assert.equal(f.calls.length,0);assert.equal(f.get('formError').textContent,'');
});
test('a late successful submit keeps the later draft and uses only its frozen payload',async()=>{
 const f=fixture(),request=deferred(),posted=deferred();f.context.openComparison=async id=>f.calls.push(['open',id]);f.context.api=(url,data)=>{f.calls.push([url,structuredClone(data)]);posted.resolve();return request.promise;};const pending=f.run('submit()');await posted.promise;f.get('source').value='/data/new-draft.mp4';request.resolve({id:'created'});await pending;
 assert.equal(f.calls[0][0],'api/comparisons');assert.equal(f.calls[0][1].source,'/data/a.mp4');assert.equal(f.calls.some(v=>v[0]==='open'),false);assert.equal(f.get('taskDialog').open,true);assert.match(f.notices[0][0],/比较已提交/);
});
test('late imported results cannot be attached to whichever comparison happens to be open later',async()=>{
 const f=fixture(),request=deferred();f.state.comparison.sample_id='original-sample';f.context.api=(url,data)=>{f.calls.push([url,structuredClone(data)]);return request.promise;};f.context.openComparison=async id=>f.calls.push(['open',id]);const pending=f.run('importExistingResult()');f.state.pollToken++;f.state.comparison={id:'unrelated',sample_id:'returned-sample',runs:[]};request.resolve({id:'imported',sample_id:'returned-sample',run_ids:['new-result']});await pending;
 assert.equal(f.calls.filter(v=>v[0].startsWith('api/')).length,1);assert.equal(f.calls.some(v=>v[0].includes('/attach')||v[0]==='open'),false);assert.equal(f.state.comparison.id,'unrelated');assert.match(f.notices[0][0],/结果已导入/);
});
test('background export snapshots its original results and never auto-downloads into a later comparison',async()=>{
 const f=fixture(),request=deferred(),card=setRun(f);f.get('exportScope').value='visible';f.context.api=(url,data)=>{f.calls.push([url,structuredClone(data)]);return request.promise;};const pending=f.run('exportSelectedVideo()');f.state.pollToken++;f.state.comparison={id:'later',runs:[],sample:null};card.run=runResult('different');request.resolve({id:'export',status:'completed',url:'/old.mp4'});await pending;
 const call=f.calls.find(v=>v[0]==='api/comparisons/comparison/export');assert.ok(call);assert.deepEqual(call[1].run_ids,['run']);assert.equal(f.calls.some(v=>v[0]==='download'),false);assert.equal(f.get('exportVideo').disabled,true);assert.equal(f.state.exporting,false);
});

test('out-of-order comparison metadata cannot roll a completed publication back to partial',async()=>{
 const f=fixture(),card=setRun(f),old=deferred(),latest=deferred();f.context.refresh=f.actualRefresh;let count=0;f.context.api=()=>++count===1?old.promise:latest.promise;
 Object.assign(f.context,{syncSessionSummary(){},comparisonDisplayName:v=>v.id,updateCard:(card,run)=>{card.run=run;},syncResultTools(){},updateCombination(){},render(){},ensureFrame:async()=>{}});
 const value=run=>({id:'comparison',sample_id:'sample',sample:null,common_world_origin_source_frame:null,runs:[run]});const before=f.run('refresh("comparison",1)'),after=f.run('refresh("comparison",1)');latest.resolve(value(runResult('run',2,'completed')));await after;old.resolve(value(runResult('run',1,'partial')));await before;assert.equal(f.state.comparison.runs[0].status,'completed');assert.equal(card.run.result.frames,2);
});
test('a comparison that loads later cannot overwrite a newly opened input draft',async()=>{
 const f=fixture(),ready=deferred();f.get('taskDialog').open=false;Object.assign(f.context,{pause(){},setWorkspaceTab(){},resetGeometryCache(){},refresh:()=>ready.promise,updateHistorySelection(){},refreshExisting:async()=>{},restoreForm:()=>f.calls.push(['restore-form']),restoreDatasetForm:()=>f.calls.push(['restore-dataset'])});f.context.document.querySelectorAll=()=>[];
 const pending=f.run('openComparison("loading",{history:false,keepPlatform:true})');f.get('source').value='/data/new-user-input.mp4';f.context.workspaceUI.taskDialogSession++;f.get('taskDialog').open=true;f.state.comparison={id:'loading',runs:[]};ready.resolve();await pending;assert.equal(f.calls.some(v=>v[0].startsWith('restore-')),false);assert.equal(f.get('source').value,'/data/new-user-input.mp4');assert.equal(f.get('taskDialog').open,true);
});

test('back and forward navigation has one route owner and opens its comparison only once',async()=>{
 const platformSource=fs.readFileSync(require.resolve(root+'platform.js'),'utf8'),listeners=[],calls=[];
 const context=vm.createContext({URLSearchParams,location:{href:'http://localhost/compare?id=next',search:'?id=next'},platformNavigation:{},state:{comparison:{id:'previous'}},HUB_LABELS:{},
  window:{addEventListener:(name,handler)=>{if(name==='popstate')listeners.push(handler);}},hubError:error=>{throw error;},
  openComparison:async(id,options)=>{calls.push([id,options]);context.state.comparison={id};},restoringRoute:fn=>fn(),closePlatformWorkspace(){},focusWorkspaceContent(){},updateDiagnosticReturnLinks(){}});
 vm.runInContext(platformSource.slice(platformSource.indexOf('async function restoreWorkspaceRoute(){'),platformSource.indexOf('function workspaceTaskFilterSignature(){')),context);
 for(const source of [app,platformSource])for(const registration of source.match(/^.*window\.addEventListener\('popstate'.*$/gm)||[])vm.runInContext(registration,context);
 assert.equal(listeners.length,1);await listeners[0]();assert.equal(calls.length,1);assert.equal(calls[0][0],'next');assert.equal(calls[0][1].history,false);assert.equal(calls[0][1].keepPlatform,true);
 context.location={href:'http://localhost/compare?id=previous',search:'?id=previous'};await listeners[0]();assert.deepEqual(calls.map(call=>call[0]),['next','previous']);
});

function startupNavigationFixture(search,{waitMetadata=false,waitHistory=false,waitingComparisons=[]}={}){
 const metadata=deferred(),history=deferred(),parent=deferred(),nodes={},calls=[],comparisonWaits=new Map(waitingComparisons.map(id=>[id,deferred()]));
 const get=id=>nodes[id]??={value:'',open:false,append(){}};
 const context=vm.createContext({URLSearchParams,state:{pollToken:0,specs:[],comparison:null},workspaceUI:{history:[]},platform:{pane:'data'},HUB_LABELS:{data:'Data'},
  location:{search,pathname:'/compare',hash:''},localStorage:{getItem:()=>null},$:get,el:()=>({}),legacyDiagnosticHashURL:()=>null,
  buildAlgorithms(){},refreshComposerAlgorithms(){},updateImportFormats(){},preferredComparison:history=>history[0],notify:message=>calls.push(['notice',message]),
  api:path=>path==='api/algorithms'?waitMetadata?metadata.promise:Promise.resolve({algorithms:[]}):path==='api/comparison/examples'?Promise.resolve({examples:[]}):parent.promise,
  loadHistory:async()=>{if(waitHistory)await history.promise;context.workspaceUI.history=[{id:'remembered'}];},
  openPlatform:async pane=>{calls.push(['platform',pane]);get('platformDialog').open=true;context.platform.pane=pane;},
  openComparison:async(id,options)=>{const token=++context.state.pollToken;context.state.comparison=null;calls.push(['comparison',id,options]);if(comparisonWaits.has(id))await comparisonWaits.get(id).promise;if(token!==context.state.pollToken)return;context.state.comparison={id};context.location.search='?id='+id;},
  inspectResult:id=>calls.push(['inspect',id]),seek:frame=>calls.push(['seek',frame]),openCollection:async id=>calls.push(['collection',id])});
 const expression=app.slice(app.lastIndexOf('(async()=>{try{')).replace(/tick\(\);\s*$/,'');
 return {context,calls,metadata,history,parent,comparisonWaits,promise:vm.runInContext(expression,context)};
}
async function flushStartup(){for(let index=0;index<8;index++)await Promise.resolve();}

test('a history choice made during metadata loading wins while its URL is still pending',async()=>{
 const f=startupNavigationFixture('?id=old',{waitMetadata:true,waitingComparisons:['chosen']});const chosen=f.context.openComparison('chosen');assert.equal(f.context.location.search,'?id=old');
 f.metadata.resolve({algorithms:[]});await f.promise;assert.deepEqual(f.calls.filter(call=>call[0]==='comparison').map(call=>call[1]),['chosen']);f.comparisonWaits.get('chosen').resolve();await chosen;assert.equal(f.context.state.comparison.id,'chosen');
});
test('a history choice made during catalog loading cancels automatic remembered navigation',async()=>{
 const f=startupNavigationFixture('',{waitHistory:true,waitingComparisons:['chosen']});await flushStartup();const chosen=f.context.openComparison('chosen');f.history.resolve();await f.promise;
 assert.deepEqual(f.calls.filter(call=>call[0]==='comparison').map(call=>call[1]),['chosen']);f.comparisonWaits.get('chosen').resolve();await chosen;
});
test('a delayed run-parent lookup cannot reclaim navigation from a pending user comparison',async()=>{
 const f=startupNavigationFixture('?run=old-run',{waitingComparisons:['chosen']});await flushStartup();const chosen=f.context.openComparison('chosen');f.parent.resolve({comparisons:['old-parent']});await f.promise;
 assert.deepEqual(f.calls.filter(call=>call[0]==='comparison').map(call=>call[1]),['chosen']);assert.equal(f.calls.some(call=>call[0]==='inspect'),false);f.comparisonWaits.get('chosen').resolve();await chosen;
});
test('startup stops restoring the old frame after the user replaces its pending comparison',async()=>{
 const f=startupNavigationFixture('?id=old&frame=9',{waitingComparisons:['old','chosen']});await flushStartup();assert.equal(f.calls[0][1],'old');const chosen=f.context.openComparison('chosen');f.comparisonWaits.get('old').resolve();await f.promise;
 assert.equal(f.calls.some(call=>call[0]==='seek'),false);assert.deepEqual(f.calls.filter(call=>call[0]==='comparison').map(call=>call[1]),['old','chosen']);f.comparisonWaits.get('chosen').resolve();await chosen;assert.equal(f.context.state.comparison.id,'chosen');
});
