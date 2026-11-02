'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const summarySource=fs.readFileSync(require.resolve('../mint/comparison/web/parameter-summary.js'),'utf8');
const appSource=fs.readFileSync(require.resolve('../mint/comparison/web/app.js'),'utf8');
const loadingSource=fs.readFileSync(require.resolve('../mint/comparison/web/frame-loading.js'),'utf8');
const {wristSummaryRows}=require('../mint/comparison/web/parameter-summary.js');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function hand(wrist){return Array.from({length:21},()=>wrist.slice());}
function block(start=0,count=16){
  return {sample_positions:Array.from({length:count},(_,i)=>start+i),available:Array(count).fill(true),
    geometry_valid:Array.from({length:count},()=>[true,true]),presence:Array.from({length:count},()=>[true,true]),
    joints_camera_m:Array.from({length:count},(_,i)=>[hand([.01*(start+i),0,.5]),hand([-.01*(start+i),.1,.8])]),
    pose_valid:Array(count).fill(true)};
}
function fixture(){
  const nodes={parameterPanel:{hidden:false},space:{value:'camera'},showAbsent:{checked:false}},calls=[],notices=[];
  const card={run:{id:'run-a',result:{revision:1,arrays:{T_world_camera:{}}}}};
  const state={comparison:{id:'comparison-a',common_world_origin_source_frame:8,sample:{frames:64,timestamps_s:Array.from({length:64},(_,i)=>i*.1+i*i*.001)}},
    pollToken:1,frame:16,cards:new Map([[card.run.id,card]]),cache:new Map(),loading:new Map()};
  const requests=[];
  const context=vm.createContext({state,FRAME_READ_COUNT:16,URLSearchParams,AbortController,Date,setTimeout:(fn,ms)=>{const timer=setTimeout(fn,ms);timer.unref();return timer;},clearTimeout,requestAnimationFrame:fn=>setImmediate(fn),render(){},$ :id=>nodes[id],
    resultFrameKey:(run,start,origin)=>JSON.stringify([run.id,run.result.revision,start,origin??null]),resultRevision:run=>run.result.revision,
    frameReadCards:()=>[card],parameterPanelIsOpen:()=>!nodes.parameterPanel.hidden,parameterPanelReadCards:()=>[card],parameterPanelJointSelection:()=>({runId:card.run.id,slot:0,joint:0}),
    api:url=>{const request=deferred();requests.push({url,...request});return request.promise;},notify:message=>notices.push(message),
    handColor:slot=>slot?'right-color':'left-color',el:(tag,text)=>({tag,text}),
    visible:(payload,index,slot)=>payload.geometry_valid?.[index]?.[slot]&&(nodes.showAbsent.checked||payload.presence===null||payload.presence?.[index]?.[slot])});
  vm.runInContext(loadingSource,context);vm.runInContext(summarySource,context);
  vm.runInContext(appSource.slice(appSource.indexOf('function currentResultRead('),appSource.indexOf('async function ensureFrame(')),context);
  const canvas={save:()=>calls.push(['save']),restore:()=>calls.push(['restore']),beginPath:()=>calls.push(['begin']),
    arc:(...args)=>calls.push(['arc',...args]),fill:()=>calls.push(['fill']),stroke:()=>calls.push(['stroke']),fillText:(...args)=>calls.push(['text',...args])};
  const key=(start=0)=>context.resultFrameKey(card.run,start,state.comparison?.common_world_origin_source_frame);
  return {state,card,context,nodes,calls,notices,requests,canvas,key};
}

test('wrist summaries label left/right and XYZ centimetres, including a valid zero wrist',()=>{
  const payload=block(0,1);payload.joints_camera_m[0]=[hand([0,0,0]),hand([.123,-.045,.6])];
  const rows=wristSummaryRows(payload,0);
  assert.equal(rows[0].slot,0);assert.equal(rows[0].label,'左腕 L');assert.equal(rows[0].position,'X 0.0 / Y 0.0 / Z 0.0 cm');
  assert.equal(rows[1].slot,1);assert.equal(rows[1].label,'右腕 R');assert.equal(rows[1].position,'X 12.3 / Y -4.5 / Z 60.0 cm');
  assert.match(rows[0].title,/相机轴：X右、Y下、Z前/);assert.match(rows[0].text,/有效/);
});

test('null, invalid geometry and unmapped frames never become zero coordinates',()=>{
  const payload=block(0,1);payload.joints_camera_m[0][0][0]=[null,0,.5];payload.geometry_valid[0][1]=false;
  let rows=wristSummaryRows(payload,0);assert.equal(rows[0].position,'几何缺失');assert.equal(rows[1].position,'几何缺失');
  assert.equal(rows[1].states.includes('有效'),false);
  payload.available[0]=false;payload.geometry_valid[0]=[true,true];payload.joints_camera_m[0][0][0]=[1,2,3];
  rows=wristSummaryRows(payload,0);assert.equal(rows[0].position,'几何缺失');assert.equal(rows[0].states.includes('有效'),false);
  payload.available[0]=true;payload.presence[0][1]=false;payload.filled=[[false,true]];payload.confidence=[[null,.7]];
  rows=wristSummaryRows(payload,0);assert.match(rows[1].text,/低存在性.*补全/);assert.match(rows[1].title,/70\.0%/);assert.doesNotMatch(rows[0].title,/0\.0%/);
});

test('world summary requires both provided coordinates and a valid camera pose',()=>{
  const payload=block(0,1);
  assert.equal(wristSummaryRows(payload,0,'world')[0].position,'世界坐标未提供');
  payload.joints_world_m=[[hand([1,2,3]),hand([4,5,6])]];
  assert.equal(wristSummaryRows(payload,0,'world')[0].position,'X 100.0 / Y 200.0 / Z 300.0 cm');
  payload.pose_valid[0]=false;
  assert.doesNotMatch(wristSummaryRows(payload,0,'world')[0].position,/X 100/);
  assert.equal(wristSummaryRows(payload,0,'camera')[0].position,'X 0.0 / Y 0.0 / Z 50.0 cm','invalid world pose does not erase valid camera coordinates');
});

test('joint highlight follows only the selected result and visible valid hand slot',()=>{
  const f=fixture(),payload=block(0,1),data={payload,index:0},project=point=>[point[0]*100,point[2]*100];
  payload.joints_camera_m[0][1][7]=[.2,.1,.7];f.context.parameterPanelJointSelection=()=>({runId:'run-a',slot:1,joint:7});
  f.context.drawParameterJoint(f.card,data,f.canvas,project,{ratio:2});
  assert.deepEqual(f.calls.find(call=>call[0]==='arc'),['arc',20,70,12,0,Math.PI*2]);assert.match(f.calls.find(call=>call[0]==='text')[1],/R 7/);
  for(const change of [()=>f.context.parameterPanelJointSelection=()=>({runId:'another-run',slot:1,joint:7}),
    ()=>{f.context.parameterPanelJointSelection=()=>({runId:'run-a',slot:1,joint:7});payload.geometry_valid[0][1]=false;},
    ()=>{payload.geometry_valid[0][1]=true;payload.presence[0][1]=false;},
    ()=>{payload.presence[0][1]=true;payload.joints_camera_m[0][1][7]=[null,0,1];},
    ()=>f.context.parameterPanelJointSelection=()=>null]){
    f.calls.length=0;change();f.context.drawParameterJoint(f.card,data,f.canvas,project);assert.equal(f.calls.length,0);
  }
});

test('world highlight requires pose validity and never falls back to camera points',()=>{
  const f=fixture(),payload=block(0,1),data={payload,index:0},project=point=>[point[0],point[2]];
  f.context.drawParameterJoint(f.card,data,f.canvas,project,{world:true});assert.equal(f.calls.length,0);
  payload.joints_world_m=[[hand([1,2,3]),hand([4,5,6])]];payload.pose_valid[0]=false;
  f.context.drawParameterJoint(f.card,data,f.canvas,project,{world:true});assert.equal(f.calls.length,0);
  payload.pose_valid[0]=true;f.context.drawParameterJoint(f.card,data,f.canvas,project,{world:true});assert.ok(f.calls.some(call=>call[0]==='arc'));
});

test('previous wrist data uses the exact preceding sample and its actual irregular timestamp for either hand',()=>{
  const f=fixture(),payload=block();f.state.cache.set(f.key(),payload);
  const left=f.context.parameterPreviousFrame(f.card,0),right=f.context.parameterPreviousFrame(f.card,1);
  assert.equal(left.points,payload.joints_camera_m[15][0]);assert.equal(right.points,payload.joints_camera_m[15][1]);
  assert.equal(left.sampleIndex,15);assert.equal(left.timestampS,f.state.comparison.sample.timestamps_s[15]);assert.equal(left.space,'camera');assert.equal(left.valid,true);
  f.state.frame=10;const within=f.context.parameterPreviousFrame(f.card,0);assert.equal(within.sampleIndex,9);assert.equal(within.points,payload.joints_camera_m[9][0]);
  assert.notEqual(f.state.comparison.sample.timestamps_s[15]-f.state.comparison.sample.timestamps_s[14],f.state.comparison.sample.timestamps_s[9]-f.state.comparison.sample.timestamps_s[8]);
});

test('missing previous samples stay unavailable instead of using a trajectory or last known wrist',()=>{
  const f=fixture(),payload=block();f.card.trajectory={camera_wrists_m:[[hand([9,9,9])[0],hand([9,9,9])[0]]],timestamps_s:[0]};
  assert.equal(f.context.parameterPreviousFrame(f.card,0),null);
  f.state.cache.set(f.key(),payload);payload.sample_positions[15]=14;assert.equal(f.context.parameterPreviousFrame(f.card,0),null);
  payload.sample_positions[15]=15;payload.available[15]=false;assert.equal(f.context.parameterPreviousFrame(f.card,0),null);
  payload.available[15]=true;payload.geometry_valid[15][1]=false;assert.equal(f.context.parameterPreviousFrame(f.card,1).valid,false);
  assert.equal(f.context.parameterPreviousFrame(f.card,3),null);f.state.frame=0;assert.equal(f.context.parameterPreviousFrame(f.card,0),null);
});

test('previous-block reads are limited to an open panel boundary, share pending work and cache the exact block',async()=>{
  const f=fixture();f.nodes.parameterPanel.hidden=true;await f.context.ensureParameterPreviousFrame(16);assert.equal(f.requests.length,0);
  f.nodes.parameterPanel.hidden=false;await f.context.ensureParameterPreviousFrame(15);await f.context.ensureParameterPreviousFrame(0);assert.equal(f.requests.length,0);
  const first=f.context.ensureParameterPreviousFrame(16),second=f.context.ensureParameterPreviousFrame(16);assert.equal(f.requests.length,1);
  const url=new URL(f.requests[0].url,'http://local/');assert.equal(url.searchParams.get('start'),'0');assert.equal(url.searchParams.get('count'),'16');assert.equal(url.searchParams.get('origin'),'8');
  const payload=block();f.requests[0].resolve(payload);await Promise.all([first,second]);assert.equal(f.state.cache.get(f.key()),payload);assert.equal(f.state.loading.size,0);
  await f.context.ensureParameterPreviousFrame(16);assert.equal(f.requests.length,1,'a cached block is not fetched again');
  await f.context.ensureParameterPreviousFrame(32);assert.equal(f.requests.length,1,'a retired frame cannot start a block read');
  f.state.frame=64;await f.context.ensureParameterPreviousFrame(64);assert.equal(f.requests.length,1,'an out-of-range frame cannot start a block read');
  f.state.comparison=null;f.state.frame=16;await f.context.ensureParameterPreviousFrame(16);assert.equal(f.requests.length,1,'cleared comparisons cannot start a block read');
});

test('retired block responses cannot populate caches after navigation, origin, result or card changes',async()=>{
  const mutations=[f=>f.state.pollToken++,f=>f.state.comparison={...f.state.comparison,id:'comparison-b'},
    f=>f.state.comparison.common_world_origin_source_frame=99,f=>f.card.run.result={...f.card.run.result,revision:2},
    f=>f.state.cards.set(f.card.run.id,{run:{...f.card.run}})];
  for(const change of mutations){const f=fixture(),pending=f.context.ensureParameterPreviousFrame(16);change(f);f.requests[0].resolve(block());await pending;assert.equal(f.state.cache.size,0);assert.equal(f.state.loading.size,0);}
  const f=fixture(),pending=f.context.ensureParameterPreviousFrame(16),key=f.key(),replacement=Promise.resolve();
  f.state.loading.set(key,replacement);f.state.pollToken++;f.requests[0].reject(new Error('retired read'));await pending;
  assert.equal(f.state.loading.get(key),replacement,'an old request cannot delete a newer pending entry');assert.equal(f.notices.length,0);
  const active=fixture(),failed=active.context.ensureParameterPreviousFrame(16);active.requests[0].reject(new Error('offline'));await failed;
  assert.equal(active.notices.length,0);assert.match(active.context.frameReadFailure(active.card,15).message,/offline/);assert.equal(active.state.loading.size,0);
  active.context.retryFrameRead(active.card,15);const retry=active.context.ensureParameterPreviousFrame(16);assert.equal(active.requests.length,2);active.requests[1].resolve(block());await retry;assert.equal(active.state.cache.size,1);
});

test('comparison changes during mesh loading cannot trigger an old-frame read in the next workspace',async()=>{
  for(const nextComparison of [null,{id:'comparison-b',common_world_origin_source_frame:0,sample:{timestamps_s:[0]}}]){
    const f=fixture(),mesh=deferred();f.context.frameReadCards=()=>[];f.context.ensureMeshFrame=()=>mesh.promise;
    vm.runInContext(appSource.slice(appSource.indexOf('async function ensureFrame('),appSource.indexOf('function dataFor(')),f.context);
    const pending=f.context.ensureFrame(16);await pending;const before=f.requests.length;f.state.pollToken++;f.context.resetFrameLoading();f.state.comparison=nextComparison;mesh.resolve();await Promise.resolve();
    assert.equal(f.requests.length,before,'optional mesh completion must not request old-frame geometry from a new comparison');
  }
});
