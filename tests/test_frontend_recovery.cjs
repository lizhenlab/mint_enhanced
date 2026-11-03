'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const web='../mint/comparison/web/',source=fs.readFileSync(require.resolve(web+'app.js'),'utf8');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
async function flush(){for(let index=0;index<10;index++)await Promise.resolve();}
function fixture(search='?id=old&frame=32&run=old-run'){
  const nodes={},urls=[],notices=[],calls=[];
  const get=id=>nodes[id]??={value:'',open:false,hidden:true,disabled:false,attributes:{},dataset:{},children:[],
    setAttribute(name,value){this.attributes[name]=value;},replaceChildren(...values){this.children=values;},append(...values){this.children.push(...values);},close(){this.open=false;},classList:{toggle(){}}};
  const location={search,href:'http://localhost/compare'+search},history={};
  for(const method of ['pushState','replaceState'])history[method]=(_state,_title,url)=>{urls.push([method,url]);location.search=new URL(url,'http://localhost').search;location.href='http://localhost'+url;};
  const state={pollToken:1,frame:31,cards:new Map(),cache:new Map(),loading:new Map(),comparison:{id:'old',runs:[]}};
  const context=vm.createContext({state,workspaceUI:{taskDialogSession:1,history:[]},URLSearchParams,location,window:{history},localStorage:{setItem(){}},$:get,
    setTimeout,clearTimeout,AbortController,document:{querySelectorAll:()=>[]},notify:message=>notices.push(message),api:async()=>({comparisons:[]}),
    TERMINAL:new Set(['completed','failed','cancelled','interrupted','partial'])});
  const run=code=>vm.runInContext(code,context);
  run(source.slice(source.indexOf('function inputLabel('),source.indexOf("$('start').onclick")));
  const actualPoll=run('poll'),actualHistory=run('loadHistory');
  Object.assign(context,{pause(){},resetFrameLoading(){},resetGeometryCache(){},setWorkspaceTab(){},restoreForm(){},updateHistorySelection(){},
    restoreDatasetForm(){},syncPlaybackReadiness(){},renderHistoryList(){calls.push('history-render');},refreshExisting:async()=>{},poll:(id,token)=>calls.push(['poll',id,token]),
    refresh:async id=>{state.comparison={id,runs:[{status:'completed'}]};get('sessionTitle').textContent='Loaded '+id;}});
  return {context,state,get,urls,notices,calls,run,actualPoll,actualHistory};
}
function timers(context){
  let next=0;const pending=new Map(),delays=[];
  context.setTimeout=(fn,delay)=>{const id=++next;pending.set(id,fn);delays.push(delay);return id;};context.clearTimeout=id=>pending.delete(id);
  return {delays,pending,async tick(){const entry=pending.entries().next().value;assert.ok(entry,'Expected a scheduled retry');pending.delete(entry[0]);entry[1]();await flush();}};
}

test('overview hint is outside both canvas flex siblings and the parameter panel',()=>{
  const html=fs.readFileSync(require.resolve(web+'index.html'),'utf8').replace(/<script[\s\S]*?<\/script>/g,'');
  const stack=[],parents={};const voids=new Set(['area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr']);
  for(const match of html.matchAll(/<\/?([a-z][a-z0-9:-]*)\b([^>]*)>/gi)){
    const [tag,name,attrs]=match,lower=name.toLowerCase();
    if(tag.startsWith('</')){while(stack.length&&stack.pop().name!==lower){}continue;}
    const id=attrs.match(/\bid="([^"]+)"/)?.[1],className=attrs.match(/\bclass="([^"]+)"/)?.[1];
    if(id)parents[id]=stack.map(value=>value.id||value.className||value.name);
    if(!voids.has(lower)&&!tag.endsWith('/>'))stack.push({name:lower,id,className});
  }
  assert.equal(parents.resultLayoutHint.at(-1),'panel-view');
  assert.equal(parents.runs.at(-1),'parameter-workspace');
  assert.equal(parents.parameterPanel.at(-1),'parameter-workspace');
  assert.ok(!parents.resultLayoutHint.includes('parameter-workspace'));
});

test('an active comparison choice clears a prior frame and inspected run while starting at frame one',async()=>{
  const x=fixture('?id=old&frame=32&run=old-run&hub=tools&custom=keep');
  await x.run('openComparison("new")');
  const query=new URL(x.urls[0][1],'http://localhost').searchParams;
  assert.equal(x.state.frame,0);assert.equal(query.get('id'),'new');
  assert.equal(query.has('frame'),false);assert.equal(query.has('run'),false);assert.equal(query.has('hub'),false);assert.equal(query.get('custom'),'keep');
});

test('startup deep links retain their explicit frame and run for the route restorer',async()=>{
  const x=fixture('?id=deep&frame=32&run=inspect');
  await x.run('openComparison("deep",{replace:true,keepPlatform:true,restorePosition:true})');
  assert.equal(x.urls[0][0],'replaceState');const query=new URL(x.urls[0][1],'http://localhost').searchParams;
  assert.equal(query.get('frame'),'32');assert.equal(query.get('run'),'inspect');
});

test('keeping a platform pane during active navigation does not inherit another record position',async()=>{
  const x=fixture('?id=old&frame=32&run=old-run&hub=tools');
  await x.run('openComparison("new",{keepPlatform:true})');
  const query=new URL(x.urls[0][1],'http://localhost').searchParams;
  assert.equal(query.get('hub'),'tools');assert.equal(query.get('id'),'new');assert.equal(query.has('frame'),false);assert.equal(query.has('run'),false);
});

test('back and forward comparison restoration never edits its incoming URL',async()=>{
  const x=fixture('?id=previous&frame=17');
  await x.run('openComparison("previous",{history:false,keepPlatform:true})');
  assert.equal(x.urls.length,0);assert.equal(x.context.location.search,'?id=previous&frame=17');
});

test('failed comparison opening settles its busy state and a visible retry loads the same intended record',async()=>{
  const x=fixture();let reads=0;x.context.refresh=async id=>{if(++reads===1)throw Error('Server unavailable');x.state.comparison={id,runs:[{status:'completed'}]};x.get('sessionTitle').textContent=id;};
  await assert.rejects(x.run('openComparison("wanted")'),/Server unavailable/);
  assert.equal(x.get('workspace').attributes['aria-busy'],'false');assert.equal(x.get('sessionTitle').textContent,'比较读取失败');
  assert.equal(x.get('comparisonConnection').hidden,false);assert.match(x.get('comparisonConnectionMessage').textContent,/Server unavailable/);
  assert.equal(x.get('retryComparison').hidden,false);assert.equal(x.get('retryComparison').disabled,false);
  await x.get('retryComparison').onclick();
  assert.equal(x.state.comparison.id,'wanted');assert.equal(x.get('comparisonConnection').hidden,true);assert.equal(x.get('editComparison').disabled,false);
  assert.equal(x.get('downloadReport').disabled,false);assert.equal(x.get('workspace').attributes['aria-busy'],'false');
});

test('a stale open failure cannot replace a newer record with its error or retry target',async()=>{
  const x=fixture(),older=deferred();x.context.refresh=async id=>{if(id==='older')return older.promise;x.state.comparison={id,runs:[]};x.get('sessionTitle').textContent=id;};
  const before=x.run('openComparison("older")');await x.run('openComparison("newer")');older.reject(Error('Old request failed'));
  await assert.rejects(before,/Old request failed/);assert.equal(x.state.comparison.id,'newer');assert.equal(x.get('sessionTitle').textContent,'newer');assert.equal(x.get('comparisonConnection').hidden,true);
});

test('the optional existing-result list cannot prevent primary comparison readiness or polling',async()=>{
  const x=fixture();x.context.refreshExisting=async()=>{throw Error('History read failed');};
  await x.run('openComparison("ready")');assert.equal(x.state.comparison.id,'ready');assert.equal(x.get('workspace').attributes['aria-busy'],'false');
  assert.ok(x.calls.some(call=>call[0]==='poll'&&call[1]==='ready'));assert.match(x.notices[0],/可加入结果列表读取失败/);assert.equal(x.get('comparisonConnection').hidden,true);
});

test('a pending optional result catalog never holds the comparison opening or its busy state',async()=>{
  const x=fixture();x.context.refreshExisting=()=>new Promise(()=>{});
  await x.run('openComparison("ready")');assert.equal(x.state.comparison.id,'ready');assert.equal(x.get('workspace').attributes['aria-busy'],'false');
  assert.equal(x.get('editComparison').disabled,false);assert.ok(x.calls.some(call=>call[0]==='poll'));
});

test('retrying a failed deep link restores the requested run and sample frame after startup has exited',async()=>{
  const x=fixture('?id=deep&frame=32&run=inspect'),restored=[];let attempt=0;
  x.context.refresh=async id=>{if(!attempt++)throw Error('offline');x.state.comparison={id,runs:[],sample:{frames:100}};};
  x.context.inspectResult=id=>restored.push(['run',id]);x.context.seek=frame=>{x.state.frame=frame;restored.push(['frame',frame]);};x.context.comparisonJumpFrame=(value,total)=>Math.max(0,Math.min(total-1,Number(value)-1));
  await assert.rejects(x.run('openComparison("deep",{replace:true,keepPlatform:true,restorePosition:true})'),/offline/);
  await x.get('retryComparison').onclick();assert.deepEqual(restored,[['run','inspect'],['frame',31]]);assert.equal(x.state.frame,31);
});

test('comparison GET reads abort a stalled request at their own deadline',async()=>{
  const x=fixture();let signal;x.context.api=(path,data,options)=>{assert.equal(path,'api/comparisons/a');assert.equal(data,undefined);signal=options.signal;return new Promise(()=>{});};
  await assert.rejects(x.run('comparisonRead("api/comparisons/a",{timeoutMs:8})'),error=>error.name==='TimeoutError'&&/超时/.test(error.message));
  assert.equal(signal.aborted,true);
});

test('history failure preserves existing records, exposes refresh guidance and clears after recovery',async()=>{
  const x=fixture();x.context.loadHistory=x.actualHistory;x.context.workspaceUI.history=[{id:'kept'}];let attempt=0;
  x.context.api=async()=>{if(!attempt++)throw Error('offline');return {comparisons:[{id:'recovered'}]};};
  await assert.rejects(x.run('loadHistory()'),/offline/);assert.equal(x.context.workspaceUI.history[0].id,'kept');assert.equal(x.get('historyReadStatus').hidden,false);
  assert.match(x.get('historyReadStatus').textContent,/刷新比较记录/);await x.run('loadHistory()');assert.equal(x.context.workspaceUI.history[0].id,'recovered');assert.equal(x.get('historyReadStatus').hidden,true);
});

test('an obsolete history failure cannot overwrite or notify after a newer successful history read',async()=>{
  const x=fixture(),older=deferred();x.context.loadHistory=x.actualHistory;let attempt=0;x.context.api=()=>attempt++?Promise.resolve({comparisons:[{id:'latest'}]}):older.promise;
  const pending=x.run('loadHistory()');await x.run('loadHistory()');older.reject(Error('obsolete'));await pending;
  assert.equal(x.context.workspaceUI.history[0].id,'latest');assert.equal(x.get('historyReadStatus').hidden,true);
});

test('polling failures back off to thirty seconds and recover without repeating notifications',async()=>{
  const x=fixture();x.context.poll=x.actualPoll;const clock=timers(x.context);x.state.comparison={id:'current',runs:[{status:'running'}]};let reads=0;
  x.context.refresh=async()=>{if(reads++<6)throw Error('connection lost');x.state.comparison.runs=[{status:'completed'}];};
  const pending=x.run('poll("current",1)');assert.equal(x.run('poll("current",1)'),pending);
  for(let index=0;index<7;index++)await clock.tick();await pending;
  assert.deepEqual(clock.delays,[1000,2000,4000,8000,16000,30000,30000]);assert.equal(x.notices.length,0);assert.equal(x.get('comparisonConnection').hidden,true);assert.equal(x.state.comparisonPoll,null);
});

test('manual connection retry wakes the same polling loop rather than starting another one',async()=>{
  const x=fixture();x.context.poll=x.actualPoll;const clock=timers(x.context);x.state.comparison={id:'current',runs:[{status:'running'}]};let reads=0;
  x.context.refresh=async()=>{if(!reads++)throw Error('offline');x.state.comparison.runs=[{status:'completed'}];};
  const pending=x.run('poll("current",1)');await clock.tick();assert.equal(clock.pending.size,1);assert.equal(x.get('comparisonConnection').hidden,false);
  await x.get('retryComparison').onclick();await flush();await pending;assert.equal(reads,2);assert.equal(clock.pending.size,0);assert.equal(x.get('comparisonConnection').hidden,true);
});

test('a polling failure from a replaced comparison never leaves an obsolete connection warning',async()=>{
  const x=fixture(),request=deferred();x.context.poll=x.actualPoll;const clock=timers(x.context);x.state.comparison={id:'current',runs:[{status:'running'}]};
  x.context.refresh=()=>request.promise;const pending=x.run('poll("current",1)');await clock.tick();x.state.pollToken++;x.state.comparison={id:'next',runs:[]};request.reject(Error('old offline'));await pending;
  assert.equal(x.get('comparisonConnection').hidden,true);assert.equal(clock.pending.size,0);
});

test('connection recovery controls and dynamic notices translate while retaining server evidence',()=>{
  const originals=['重试连接','比较读取失败','连接异常','连接中断，8 秒后自动重试；已有结果保留。ECONNRESET /data1/中文目录','无法读取比较，请检查服务或访问通道。读取比较信息超时，请检查服务连接后重试。','比较记录读取失败；点击“刷新比较记录”重试。HTTP 503','参考标注已撤销：原始 MANO 约定错误','产物暂不可用：Server evidence','查看历史比较'];
  const nodes=originals.map(nodeValue=>({nodeValue,parentElement:{closest:()=>false}})),listeners={};
  const select={value:'',addEventListener:(name,callback)=>listeners[name]=callback};
  const document={body:{},documentElement:{},getElementById:()=>select,createTreeWalker:()=>{let index=0;return {nextNode:()=>nodes[index++]||null};}};
  vm.runInNewContext(fs.readFileSync(require.resolve(web+'locale.js'),'utf8'),{document,NodeFilter:{SHOW_TEXT:4},MutationObserver:class{observe(){}},localStorage:{getItem:()=> 'en',setItem(){}},window:{addEventListener(){}},queueMicrotask});
  assert.equal(nodes[0].nodeValue,'Retry connection');assert.equal(nodes[1].nodeValue,'Comparison unavailable');assert.equal(nodes[2].nodeValue,'Connection issue');
  assert.match(nodes[3].nodeValue,/Retrying in 8 seconds/);assert.match(nodes[3].nodeValue,/ECONNRESET \/data1\/中文目录$/);
  assert.match(nodes[4].nodeValue,/Comparison could not be loaded.*Comparison read timed out/);assert.match(nodes[5].nodeValue,/Refresh comparison history.*HTTP 503/);
  assert.equal(nodes[6].nodeValue,'Reference withdrawn: 原始 MANO 约定错误');assert.equal(nodes[7].nodeValue,'Artifact unavailable: Server evidence');assert.equal(nodes[8].nodeValue,'View comparison history');
  select.value='zh';listeners.change();assert.deepEqual(nodes.map(node=>node.nodeValue),originals);
});

// Reuse the existing platform DOM fixture without registering its test cases.
function registryFixture(){
  const path=require('node:path'),file=require.resolve('./test_module_handoffs.cjs'),text=fs.readFileSync(file,'utf8');
  const build=vm.runInNewContext(text.slice(text.indexOf('function fixture(){'),text.indexOf('function plain('))+';fixture;',
    {vm,fs,path,URL,URLSearchParams,structuredClone,__dirname:path.dirname(file)});
  return build();
}

test('registry availability blocks every explicit unavailable state without treating capability flags as availability',()=>{
  const x=registryFixture();
  assert.equal(x.run('registryRecordAvailable({})'),true);assert.equal(x.run("registryRecordAvailable({availability:'available',status:'failed',ready:false,training_ready:false})"),true);
  for(const availability of ['annotation_withdrawn','missing_or_changed','future_quarantine',null,'']){
    x.context.record={availability,annotation_withdrawal:{reason:'Incorrect MANO convention'}};
    assert.equal(x.run('registryRecordAvailable(record)'),false);assert.throws(()=>x.run('assertRegistryRecordAvailable(record)'),/Incorrect MANO convention/);
  }
});

test('registry choices preserve a withdrawn selection with its reason even when its training capability was removed',async()=>{
  const x=registryFixture();x.get('workflowDataset').value='withdrawn';
  x.context.api=async()=>({records:[{id:'withdrawn',name:'Old labels',availability:'annotation_withdrawn',training_ready:false,annotation_withdrawal:{reason:'Mean pose missing'}},
    {id:'changed',availability:'missing_or_changed',training_ready:true,reason:'Checksum changed'},
    {id:'future',availability:'future_quarantine',training_ready:true,reason:'Review required'},
    {id:'valid',availability:'available',training_ready:true}]});
  await x.run("loadRegistryChoices('dataset','workflowDataset',{filter:row=>row.training_ready})");
  assert.equal(x.get('workflowDataset').value,'withdrawn');
  const options=x.get('workflowDataset').options;for(const id of ['withdrawn','changed','future'])assert.equal(options.find(option=>option.value===id).disabled,true);
  assert.equal(options.find(option=>option.value==='valid').disabled,false);assert.match(options.find(option=>option.value==='withdrawn').textContent,/参考标注已撤销.*Mean pose missing/);
  assert.throws(()=>x.run("assertRegistrySelectionAvailable('workflowDataset')"),/Mean pose missing/);
});

test('withdrawn registry rows retain history and details while excluding import, training, protocol and model actions',async()=>{
  const x=registryFixture();x.run("platform.pane='catalog'");x.get('registryKind').value='artifact';x.get('registryQuery').value='';
  const records=[{id:'artifact',kind:'artifact',run_id:'old-run',importable:true},{id:'dataset',kind:'dataset',native:{},training_ready:true},
    {id:'model',kind:'model'},{id:'protocol',kind:'protocol'},{id:'evaluation',kind:'evaluation',protocol_id:'p',rows:[{origin:'native',model:{}}]}]
    .map(row=>({...row,availability:'annotation_withdrawn',reason:'Official reference superseded'}));
  x.context.api=async()=>({records,total:records.length,offset:0});await x.run('refreshArtifactCatalog()');
  const rows=x.get('artifactRows').querySelectorAll('tr').slice(1);
  assert.deepEqual(Array.from(rows,row=>Array.from(row.querySelectorAll('button'),button=>button.textContent)),
    [['来源详情','查看历史比较'],['来源详情'],['来源详情'],['来源详情'],['来源详情']]);
  assert.ok(rows.every(row=>/参考标注已撤销.*Official reference superseded/.test(row.textContent)));
  const inspected=[];x.context.inspectResult=id=>inspected.push(id);x.context.api=async()=>({comparisons:['history']});
  await rows[0].querySelectorAll('button')[1].onclick();assert.deepEqual(Array.from(x.opened),['history']);assert.equal(inspected.length,0);
  await rows[0].querySelector('button').onclick();assert.equal(x.get('logDialog').open,true);assert.match(x.get('logText').textContent,/annotation_withdrawn/);
});

test('unavailable catalog handoffs fail before navigation or submission and report the server withdrawal reason',async()=>{
  const x=registryFixture();x.context.unavailable={id:'old',availability:'annotation_withdrawn',reason:'Known bad reference',native:{},training_ready:true,status:'reviewed_manifest'};
  for(const expression of ['importCatalogArtifact(unavailable)','useSavedProtocol(unavailable)','useEvaluationReport(unavailable)',
    'useEvaluationProtocol(unavailable)','useTrainingDataset(unavailable)','useReviewedDataset(unavailable)','useNativeDataset(unavailable)']){
    await assert.rejects(x.run(expression),/Known bad reference/);
  }
  assert.throws(()=>x.run('useRegisteredModel(unavailable)'),/Known bad reference/);
  assert.equal(x.requests.length,0);assert.equal(x.opened.length,0);
});

test('withdrawn selected training data disables preflight and submission while keeping an explanatory summary',async()=>{
  const x=registryFixture();x.get('workflowKind').value='train';x.get('workflowDataset').value='old';
  x.context.api=async()=>({records:[{id:'old',name:'Legacy data',training_ready:false,availability:'annotation_withdrawn',reason:'Re-export official GT'}]});
  await x.run('refreshWorkflowDatasetChoices()');
  assert.match(x.get('workflowDatasetSummary').textContent,/Legacy data.*参考标注已撤销.*Re-export official GT/);
  assert.equal(x.get('workflowPreflight').disabled,true);assert.equal(x.get('workflowSubmit').disabled,true);
  assert.throws(()=>x.run('workflowParameters()'),/Re-export official GT/);
  x.get('workflowKind').value='diagnostic';x.get('workflowProfile').value='data';x.run('updateWorkflowDatasetSummary()');
  assert.equal(x.get('workflowPreflight').disabled,false);assert.equal(x.run('workflowParameters().profile'),'data');
});

test('a completed workflow keeps its historical training view but offers no reuse shortcuts for withdrawn output',async()=>{
  const x=registryFixture();x.context.api=async()=>({id:'old',native:{},training_ready:true,availability:'annotation_withdrawn',annotation_withdrawal:{reason:'Pose convention corrected'}});
  await x.run("renderWorkflowNextSteps({id:'finished',request:{kind:'train'},status:'completed',output:{dataset_id:'old'}})");
  const host=x.get('workflowNextSteps');assert.deepEqual(Array.from(host.querySelectorAll('button'),button=>button.textContent),['查看训练曲线与检查点']);
  assert.match(host.textContent,/参考标注已撤销.*Pose convention corrected/);
});

test('training initialization picker retains an unavailable selection with its reason and cannot build a training request',async()=>{
  const x=registryFixture(),old=x.create('option','Prior model');old.value='old';x.get('workflowInitPicker').append(old);x.get('workflowInitPicker').value='old';
  x.get('workflowKind').value='train';
  x.context.api=async url=>url.startsWith('api/comparisons?')?{comparisons:[]}:url.startsWith('api/comparison/workspace?')?{tasks:[]}:
    {records:[{id:'old',name:'Prior model',path:'/old',availability:'annotation_withdrawn',reason:'Evaluation reference withdrawn'},{id:'valid',path:'/valid',availability:'available'}]};
  x.context.refreshProductionRunChoices=async()=>{};await x.run('refreshResearchRecordPickers()');
  const option=x.get('workflowInitPicker').options.find(row=>row.value==='old');assert.equal(option.disabled,true);assert.match(option.textContent,/Evaluation reference withdrawn/);
  assert.throws(()=>x.run('workflowParameters()'),/Evaluation reference withdrawn/);assert.equal(x.get('workflowPreflight').disabled,true);assert.equal(x.get('workflowSubmit').disabled,true);assert.match(x.get('workflowCheck').textContent,/Evaluation reference withdrawn/);
  x.get('workflowInitPicker').value='valid';x.run('invalidateWorkflowReadiness()');assert.equal(x.get('workflowPreflight').disabled,false);
});

test('native sample comparison and training reject all explicit unavailable versions, including retained selections',async()=>{
  const x=registryFixture();x.get('nativeVersion').value='selected';
  for(const availability of ['annotation_withdrawn','missing_or_changed','future_quarantine']){
    x.context.selected={id:'selected',native:{},training_ready:true,availability,reason:'Corrected annotation required'};
    x.run('nextUI.nativeVersions=[selected];updateNativeProtocol();updateNativeTrainingAction()');
    assert.equal(x.get('nativeCompare').disabled,true);assert.equal(x.get('nativeUseTraining').disabled,true);
    assert.match(x.get('nativeProtocolNote').textContent,/Corrected annotation required/);assert.match(x.get('nativeUseTraining').title,/Corrected annotation required/);
    await assert.rejects(x.run('compareNative()'),/Corrected annotation required/);
  }
  assert.equal(x.requests.length,0);assert.equal(x.opened.length,0);
  x.run("nextUI.nativeVersions=[{id:'selected',native:{},availability:'available',training_ready:true}];updateNativeProtocol();updateNativeTrainingAction()");
  assert.equal(x.get('nativeCompare').disabled,false);assert.equal(x.get('nativeUseTraining').disabled,false);assert.doesNotMatch(x.get('nativeProtocolNote').textContent,/Corrected annotation required/);
  x.run("nextUI.nativeVersions=[{id:'selected',native:{},training_ready:false}];updateNativeProtocol();updateNativeTrainingAction()");
  assert.equal(x.get('nativeCompare').disabled,false);assert.equal(x.get('nativeUseTraining').disabled,true);
});

function trainingCurveFixture(){
  const x=registryFixture(),draws=[];
  x.context.updateDiagnosticReturnLinks=()=>{};x.context.revealPlatformTarget=()=>{};
  x.run("nextUI.researchView='curves';curvePanel()");
  x.get('trainingCurve').getContext=()=>new Proxy({}, {get:(_,key)=>(...args)=>draws.push([key,...args])});
  return {...x,draws};
}
function trainingCurves(id,{points=[],checkpoints=[],status='completed',error=null}={}){
  return {workflow_id:id,status,points,checkpoints,error,available_points:points.length,attempts:points.length?['1']:[],progress:{done:2,total:3}};
}
function trainingTask(id){return {id,kind:'workflow',source:'train',label:'Task '+id,status:'completed'};}

test('curve task selector shows an empty state, loads the latest training automatically and respects a deliberate clear',async()=>{
  const x=trainingCurveFixture(),calls=[];let tasks=[];
  x.context.api=async url=>{calls.push(url);return url.includes('/workspace?')?{tasks}:trainingCurves('latest');};
  await x.run('loadTrainingWorkflowChoices()');
  assert.deepEqual(calls,['api/comparison/workspace?kind=workflow&source=train&limit=200']);
  assert.match(x.get('curveTaskState').textContent,/暂无训练任务/);assert.equal(x.get('curveOpenTrain').hidden,false);
  assert.equal(x.get('curveMetricPanel').hidden,true);assert.equal(x.get('curveCheckpointPanel').hidden,true);
  tasks=[trainingTask('latest'),trainingTask('older'),{id:'other',kind:'workflow',source:'diagnostic',label:'Diagnostic'}];
  await x.get('curveRefreshTasks').onclick();await flush();
  assert.equal(x.get('curveWorkflowPicker').value,'latest');assert.match(x.get('curveSummary').textContent,/Task latest/);
  assert.match(x.get('curveEmpty').textContent,/尚无优化器步骤记录/);assert.equal(x.get('curveMetricPanel').hidden,true);
  assert.equal(x.get('curveCheckpointPanel').hidden,false);assert.doesNotMatch(x.get('curveWorkflowPicker').textContent,/Diagnostic/);
  await x.run('selectTrainingCurve(null)');await x.run('loadTrainingWorkflowChoices()');
  assert.equal(x.get('curveWorkflowPicker').value,'');assert.equal(x.get('curveRefresh').disabled,true);
  assert.equal(x.get('curveCheckpointPanel').hidden,true);assert.equal(calls.filter(url=>url.endsWith('/curves')).length,1);
});

test('the curve picker never replaces a deep link or a newer list request, and list failures have a local retry',async()=>{
  const x=trainingCurveFixture(),older=deferred(),newer=deferred();let reads=0;
  x.location.href='http://localhost/compare?hub=research&research_tab=curves&workflow=linked';
  x.location.search=new URL(x.location.href).search;
  x.context.api=()=>reads++?newer.promise:older.promise;
  const first=x.run('loadTrainingWorkflowChoices()'),second=x.run('loadTrainingWorkflowChoices()');
  newer.resolve({tasks:[trainingTask('recent')]});await second;older.reject(Error('Late failure'));assert.equal(await first,null);
  assert.equal(x.run('nextUI.curve'),null);assert.doesNotMatch(x.get('curveTaskState').textContent,/Late failure/);
  x.context.api=async()=>{throw Error('Network unavailable\nInternal trace');};await x.get('curveRefreshTasks').onclick();
  assert.equal(x.get('curveTaskState').dataset.state,'error');assert.match(x.get('curveTaskState').textContent,/Network unavailable/);
  assert.doesNotMatch(x.get('curveTaskState').textContent,/Internal trace/);assert.equal(x.get('curveRefreshTasks').disabled,false);
  assert.ok(x.get('curveWorkflowPicker').options.some(row=>row.value==='recent'));assert.equal(x.errors.length,0);
});

test('curve loading coalesces repeated reads, ignores stale failures and recovers in place with a single-point chart',async()=>{
  const x=trainingCurveFixture(),older=deferred(),newer=deferred(),calls=[];
  x.context.api=url=>{calls.push(url);return url.includes('/old/')?older.promise:newer.promise;};
  const first=x.run("selectTrainingCurve('old')"),second=x.run("selectTrainingCurve('new')");
  const same=x.run('refreshCurves()');assert.equal(calls.length,2);
  newer.resolve(trainingCurves('new',{points:[{step:8,metrics:{loss:0.1,lr:0.00001}}]}));await second;await same;
  older.reject(Error('Obsolete failure'));assert.equal(await first,null);
  assert.equal(x.get('curveMetric').value,'loss');assert.equal(x.get('curveMetricPanel').hidden,false);
  assert.match(x.get('curveMetricHint').textContent,/总损失/);assert.ok(x.draws.some(([name])=>name==='arc'));
  assert.equal(x.get('curveEmpty').hidden,true);
  x.context.api=async()=>{throw Error('Temporary failure\nInternal trace');};await x.get('curveRefresh').onclick();
  assert.equal(x.get('curveEmpty').dataset.state,'error');assert.match(x.get('curveEmpty').textContent,/Temporary failure/);
  assert.doesNotMatch(x.get('curveEmpty').textContent,/Internal trace/);assert.equal(x.get('curveRefresh').disabled,false);
  assert.equal(x.get('curveMetricPanel').hidden,false);
  x.context.api=async()=>trainingCurves('new',{points:[{step:9,metrics:{lr:0.00001}}]});await x.get('curveRefresh').onclick();
  assert.equal(x.get('curveEmpty').hidden,true);assert.equal(x.get('curveMetric').value,'lr');assert.match(x.get('curveMetricHint').textContent,/学习率/);
});

test('checkpoint actions reject incomplete or superseded rows, and registration opens the Models view',async()=>{
  const x=trainingCurveFixture(),checkpoint={path:'/job/training/step_00000002',model:'/job/training/step_00000002/model.safetensors',step:2,complete:true};
  x.context.api=async url=>url.includes('/old/')?trainingCurves('old',{checkpoints:[checkpoint,{...checkpoint,path:'/damaged',step:3,complete:false,error:'Missing optimizer'}]}):trainingCurves('new',{checkpoints:[checkpoint]});
  await x.run("selectTrainingCurve('old')");const buttons=x.get('curveCheckpoints').querySelectorAll('button');
  assert.deepEqual(Array.from(buttons.slice(0,3),button=>button.textContent),['用此检查点配置续训','设为固定集回归候选','填入模型登记']);
  assert.ok(buttons.slice(3).every(button=>button.disabled));assert.match(x.get('curveCheckpoints').textContent,/Missing optimizer/);
  await x.run("selectTrainingCurve('new')");await buttons[2].onclick();assert.equal(x.get('modelPath').value,'');assert.match(String(x.errors[0]),/检查点状态已变化/);
  await x.get('curveCheckpoints').querySelectorAll('button')[2].onclick();
  assert.equal(x.run('nextUI.researchView'),'models');assert.equal(x.get('modelPath').value,checkpoint.model);
  assert.equal(x.get('modelConfig').value,'/job/effective-config.yaml');assert.equal(x.get('modelRegistrationDetails').open,true);
  await x.run("selectTrainingCurve('new')");assert.equal(x.run('nextUI.researchView'),'curves');
  const advanced=x.create('details');advanced.append(x.get('workflowResumePath'));x.body.append(advanced);
  x.context.updateWorkflowFields=()=>{};
  await x.get('curveCheckpoints').querySelectorAll('button')[0].onclick();
  assert.equal(x.run('nextUI.researchView'),'train');assert.equal(advanced.open,true);
  assert.equal(x.get('workflowResumePath').value,checkpoint.path);assert.equal(x.get('workflowConfig').value,'/job/effective-config.yaml');
});
