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
  const originals=['重试连接','比较读取失败','连接异常','连接中断，8 秒后自动重试；已有结果保留。ECONNRESET /data1/中文目录','无法读取比较，请检查服务或访问通道。读取比较信息超时，请检查服务连接后重试。','比较记录读取失败；点击“刷新比较记录”重试。HTTP 503'];
  const nodes=originals.map(nodeValue=>({nodeValue,parentElement:{closest:()=>false}})),listeners={};
  const select={value:'',addEventListener:(name,callback)=>listeners[name]=callback};
  const document={body:{},documentElement:{},getElementById:()=>select,createTreeWalker:()=>{let index=0;return {nextNode:()=>nodes[index++]||null};}};
  vm.runInNewContext(fs.readFileSync(require.resolve(web+'locale.js'),'utf8'),{document,NodeFilter:{SHOW_TEXT:4},MutationObserver:class{observe(){}},localStorage:{getItem:()=> 'en',setItem(){}},window:{addEventListener(){}},queueMicrotask});
  assert.equal(nodes[0].nodeValue,'Retry connection');assert.equal(nodes[1].nodeValue,'Comparison unavailable');assert.equal(nodes[2].nodeValue,'Connection issue');
  assert.match(nodes[3].nodeValue,/Retrying in 8 seconds/);assert.match(nodes[3].nodeValue,/ECONNRESET \/data1\/中文目录$/);
  assert.match(nodes[4].nodeValue,/Comparison could not be loaded.*Comparison read timed out/);assert.match(nodes[5].nodeValue,/Refresh comparison history.*HTTP 503/);
  select.value='zh';listeners.change();assert.deepEqual(nodes.map(node=>node.nodeValue),originals);
});
