'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const web=path.join(__dirname,'../mint/comparison/web'),source=fs.readFileSync(path.join(web,'frame-loading.js'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function payload(start,count=16,tag='data'){
  const positions=Array.from({length:Math.min(count,677-start)},(_,i)=>start+i);
  return {sample_positions:positions,source_frame_indices:positions,timestamps_s:positions.map(i=>i/30),joints_camera_m:positions.map(i=>[[[i,0,1]],[[i,0,2]]]),geometry_valid:positions.map(()=>[true,true]),K:positions.map(()=>[[1,0,0],[0,1,0],[0,0,1]]),presence:null,available:positions.map(()=>true),metadata:{tag,notFrames:Array(64).fill('keep')},connections:Array.from({length:21},(_,i)=>[i,i+1])};
}
function fixture({count=3,timeoutCap=Infinity}={}){
  const cards=Array.from({length:count},(_,i)=>({run:{id:String.fromCharCode(65+i),status:'completed',result:{arrays:{},revision:1}}})),calls=[],notifications=[],renders=[];
  const state={comparison:{id:'comparison',sample:{frames:677},common_world_origin_source_frame:null},pollToken:1,frame:31,cards:new Map(cards.map(c=>[c.run.id,c])),cache:new Map(),loading:new Map()};
  let visible=cards;
  const context=vm.createContext({state,FRAME_READ_COUNT:16,Promise,URLSearchParams,AbortController,Date,Map,Set,Number,Math,Error,Array,Object,
    setTimeout:(callback,ms)=>{const timer=setTimeout(callback,Math.min(ms,timeoutCap));timer.unref();return timer;},clearTimeout,requestAnimationFrame:callback=>setImmediate(callback),
    frameReadCards:()=>visible,parameterPanelIsOpen:()=>false,parameterPanelReadCards:()=>[],resultRevision:run=>run.result.revision,
    resultFrameKey:(run,start,origin)=>JSON.stringify([run.id,run.result.revision,start,origin??null]),
    currentResultRead:(card,revision,token,id,origin)=>token===state.pollToken&&state.comparison?.id===id&&(state.comparison.common_world_origin_source_frame??null)===(origin??null)&&state.cards.get(card.run.id)===card&&card.run.result.revision===revision,
    render:()=>renders.push([...state.cache.keys()]),notify:message=>notifications.push(message),
    api:(url,_data,options={})=>new Promise((resolve,reject)=>{const query=new URLSearchParams(url.split('?')[1]);calls.push({url,run:url.split('/')[3],start:Number(query.get('start')),count:Number(query.get('count')),signal:options.signal,resolve,reject});})});
  vm.runInContext(source,context);const run=s=>vm.runInContext(s,context),key=(card,start)=>context.resultFrameKey(card.run,start,null);
  const flush=async()=>{await tick();await tick();};
  const close=async()=>{run('resetFrameLoading()');await flush();};
  return {cards,calls,state,context,run,key,flush,close,renders,notifications,setVisible:value=>visible=value,resolve:call=>call.resolve(payload(call.start,call.count))};
}
test('fast columns render independently while another current-frame request remains slow',async()=>{
  const x=fixture();try{x.run('scheduleVisibleFrames(31)');assert.equal(x.calls.filter(c=>c.start===16).length,3);for(const call of x.calls.filter(c=>c.start===16&&c.run!=='B'))x.resolve(call);await x.flush();assert.ok(x.state.cache.has(x.key(x.cards[0],16)));assert.ok(x.state.cache.has(x.key(x.cards[2],16)));assert.ok(x.renders.length>0);assert.ok(x.calls.find(c=>c.run==='B'&&c.start===16));assert.equal(x.state.cache.has(x.key(x.cards[1],16)),false);}finally{await x.close();}
});
test('123 stale block intents cannot evict a newly loaded paused frame or cause unbounded concurrency',async()=>{
  const x=fixture();try{
    for(let frame=32;frame<677;frame+=16){x.state.frame=frame;x.run(`scheduleVisibleFrames(${frame})`);}assert.ok(x.run('frameLoader.active')<=4);assert.ok(x.run('frameLoader.queue.length')<=6);assert.ok(x.calls.length<=4);
    x.state.frame=31;x.run('scheduleVisibleFrames(31)');await x.flush();const current=x.calls.filter(c=>c.start===16&&!c.signal.aborted);assert.equal(current.length,3);current.forEach(x.resolve);await x.flush();assert.ok(x.cards.every(c=>x.state.cache.has(x.key(c,16))));
    for(const call of x.calls)x.resolve(call);await x.flush();assert.ok(x.cards.every(c=>x.state.cache.has(x.key(c,16))));assert.ok(x.state.cache.size<=72);assert.equal(x.notifications.length,0);
  }finally{await x.close();}
});
test('cache pressure preserves the current visible and parameter block',async()=>{
  const x=fixture();try{for(let i=0;i<100;i++){x.context.key='old-'+i;x.context.value={};x.run('storeFrameBlock(key,value)');}for(const card of x.cards){const key=x.key(card,16);x.context.key=key;x.context.value=payload(16);x.run('storeFrameBlock(key,value)');}for(let i=100;i<200;i++){x.context.key='old-'+i;x.run('storeFrameBlock(key,value)');}assert.equal(x.state.cache.size,72);assert.ok(x.cards.every(card=>x.state.cache.has(x.key(card,16))));}finally{await x.close();}
});
test('cancelled old finally cannot clear a replacement request under the same cache key',async()=>{
  const x=fixture({count:1});try{x.context.card=x.cards[0];const old=x.run('loadFrameBlock(card,16)');const first=x.calls[0];x.run('resetFrameLoading()');x.state.pollToken++;const latest=x.run('loadFrameBlock(card,16)');assert.equal(x.state.loading.get(x.key(x.cards[0],16)),latest);await x.flush();first.resolve(payload(16,16,'old'));await old;assert.equal(x.state.loading.get(x.key(x.cards[0],16)),latest);const next=x.calls.find(c=>!c.signal.aborted);next.resolve(payload(16,16,'new'));await latest;assert.equal(x.state.cache.get(x.key(x.cards[0],16)).metadata.tag,'new');assert.equal(x.state.loading.size,0);assert.equal(x.notifications.length,0);}finally{await x.close();}
});
test('a 64-frame prefetch splits only frame arrays and keeps graph/metadata unchanged',async()=>{
  const x=fixture({count:1});try{x.state.frame=0;x.context.card=x.cards[0];const pending=x.run('loadFrameBlock(card,16,{prefetch:true,count:64})');assert.equal(x.calls[0].count,64);x.resolve(x.calls[0]);await pending;for(const start of [16,32,48,64]){const p=x.state.cache.get(x.key(x.cards[0],start));assert.equal(p.sample_positions.length,16);assert.equal(p.sample_positions[0],start);assert.equal(p.joints_camera_m[0][0][0][0],start);assert.equal(p.connections.length,21);assert.equal(p.metadata.notFrames.length,64);assert.equal(p.presence,null);}x.state.frame=48;x.run('scheduleVisibleFrames(48)');assert.equal(x.calls.length,1);}finally{await x.close();}
});
test('prefetch is reused when its block becomes current instead of issuing a duplicate request',async()=>{
  const x=fixture({count:1});try{x.state.frame=0;x.context.card=x.cards[0];const prefetched=x.run('loadFrameBlock(card,16,{prefetch:true,count:64})');x.state.frame=32;const current=x.run('loadFrameBlock(card,32)');assert.equal(current,prefetched);assert.equal(x.calls.length,1);x.resolve(x.calls[0]);await current;assert.ok(x.state.cache.has(x.key(x.cards[0],32)));}finally{await x.close();}
});
test('errors stop automatic retry loops, and explicit retry clears the failed state',async()=>{
  const x=fixture({count:1});try{x.context.card=x.cards[0];const pending=x.run('loadFrameBlock(card,16)');x.calls[0].reject(Error('503 unavailable'));await pending;assert.match(x.run('frameReadFailure(card).message'),/503/);await x.run('loadFrameBlock(card,16)');assert.equal(x.calls.length,1);x.run('retryFrameRead(card)');assert.equal(x.calls.length,2);x.resolve(x.calls[1]);await x.flush();assert.equal(x.run('frameReadFailure(card)'),null);assert.ok(x.state.cache.has(x.key(x.cards[0],16)));}finally{await x.close();}
});
test('a stalled request has a bounded deadline and cancellation is not reported as a failure',async()=>{
  const x=fixture({count:1,timeoutCap:10});try{x.context.card=x.cards[0];const pending=x.run('loadFrameBlock(card,16)');await new Promise(resolve=>setTimeout(resolve,20));await pending;assert.equal(x.calls[0].signal.aborted,true);assert.match(x.run('frameReadFailure(card).message'),/超时/);x.run('retryFrameRead(card)');x.run('resetFrameLoading()');await x.flush();assert.equal(x.run('frameLoader.errors.size'),0);assert.equal(x.notifications.length,0);}finally{await x.close();}
});
test('newly visible columns are read by the next render scheduling pass',async()=>{
  const x=fixture({count:2});try{x.setVisible([x.cards[0]]);x.run('scheduleVisibleFrames(31)');assert.equal(x.calls.some(c=>c.run==='B'),false);x.setVisible(x.cards);x.run('scheduleVisibleFrames(31)');assert.equal(x.calls.some(c=>c.run==='B'&&c.start===16),true);}finally{await x.close();}
});
test('basic frame readiness does not wait for optional trajectory or mesh requests',async()=>{
  const x=fixture({count:1});let finishMesh;const mesh=new Promise(resolve=>finishMesh=resolve);let meshStarted=false;
  try{const app=fs.readFileSync(path.join(web,'app.js'),'utf8');x.context.$=()=>({checked:true,open:false});x.context.ensureMeshFrame=()=>{meshStarted=true;return mesh;};x.context.ensureParameterPreviousFrame=async()=>{};vm.runInContext(app.slice(app.indexOf('async function ensureFrame('),app.indexOf('function dataFor(')),x.context);const ready=x.run('ensureFrame(31)');const basic=x.calls.find(c=>c.start===16&&c.count===16);x.resolve(basic);await ready;await x.flush();assert.equal(meshStarted,true);assert.ok(x.renders.length>0);assert.ok(x.calls.some(c=>c.url.includes('/trajectory')));assert.equal(x.cards[0].trajectory,undefined);x.calls.find(c=>c.url.includes('/trajectory')).resolve({camera_wrists_m:[]});finishMesh();await x.flush();}finally{finishMesh();await x.close();}
});
test('a whole-clip trajectory remains useful when playback advances before its response',async()=>{
  const x=fixture({count:1});try{const app=fs.readFileSync(path.join(web,'app.js'),'utf8');x.context.$=()=>({checked:true,open:false});vm.runInContext(app.slice(app.indexOf('async function ensureFrameDetails('),app.indexOf('function dataFor(')),x.context);x.context.cards=x.cards;const pending=x.run('ensureFrameDetails(31,cards,state.comparison,state.pollToken,null)');x.state.frame=32;const trajectory={camera_wrists_m:[[1,2,3]]};x.calls[0].resolve(trajectory);await pending;assert.equal(x.cards[0].trajectory,trajectory);}finally{await x.close();}
});
