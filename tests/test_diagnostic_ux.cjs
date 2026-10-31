'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const ux=require('../mint/comparison/web/diagnostic-ux.js');
function fixture(){
  class Node{
    constructor(){this.handlers={};this.textContent='';this.value='';this.dataset={};this.attributes={};this.hidden=false;}
    addEventListener(name,handler){(this.handlers[name]??=[]).push(handler);}
    removeEventListener(name,handler){this.handlers[name]=(this.handlers[name]||[]).filter(x=>x!==handler);}
    setAttribute(key,value){this.attributes[key]=value;}
    fire(name,event={}){for(const fn of this.handlers[name]||[])fn({target:this,...event});}
  }
  const ids=['diagnosticPlayback','diagnosticPlay','diagnosticPrevFrame','diagnosticNextFrame','diagnosticTimeline','diagnosticFrameLabel','diagnosticFrameJump','diagnosticSpeed','diagnosticPlaybackStatus','diagnosticInputLabel','diagnosticModelLabel','diagnosticResultState'];
  const nodes=Object.fromEntries(ids.map(id=>[id,new Node()])),document=new Node();document.getElementById=id=>nodes[id];document.activeElement=null;
  const video=new Node();Object.assign(video,{currentTime:12.5,readyState:2,paused:true,ended:false,playbackRate:1,pause(){this.paused=true;},async play(){this.paused=false;}});
  const state={loaded:true,fps:30,nframes:450,sourceName:'sample.mp4',sourcePath:'/dataset/sample.mp4',hidden:new Set()};
  let clock=video,view='results',redraws=0,interval;
  const controller=ux.mount({document,window:{setInterval(fn){interval=fn;return 1;},clearInterval(){}},getState:()=>state,getVideo:()=>clock,getView:()=>view,redraw:()=>redraws++,getModelTag:()=> 'model-v1',getSelectedInput:()=>'/dataset/draft.mp4'});
  return {nodes,document,video,state,controller,tick:()=>interval(),setClock:value=>{clock=value;},setView:value=>{view=value;},redraws:()=>redraws,Node};
}
test('playback frames are one-based in UI and clamp seeks to the real last sample frame',()=>{
  const f=fixture();assert.match(f.nodes.diagnosticFrameLabel.textContent,/376 \/ 450/);
  f.controller.seek(900);assert.equal(f.video.currentTime,449/30);assert.equal(f.nodes.diagnosticNextFrame.disabled,true);
  f.controller.seek(-4);assert.equal(f.video.currentTime,0);assert.equal(f.nodes.diagnosticPrevFrame.disabled,true);
  assert.equal(ux.frameTime('',{total:450,fps:30}),null);assert.equal(ux.frameTime(Infinity,{total:450,fps:30}),null);
  assert.equal(ux.playbackSnapshot({loaded:true,nframes:Infinity,fps:Infinity},f.video).fps,30);
});
test('persistent playback controls the hidden master clock and preserves speed across replacement',async()=>{
  const f=fixture();f.state.hidden.add('both_2d');f.tick();assert.match(f.nodes.diagnosticPlaybackStatus.textContent,/2D 已隐藏/);
  await f.controller.toggle();assert.equal(f.video.paused,false);
  f.nodes.diagnosticSpeed.value='2';f.nodes.diagnosticSpeed.fire('change');assert.equal(f.video.playbackRate,2);
  const next=new f.Node();Object.assign(next,{currentTime:12.5,readyState:2,paused:true,playbackRate:1,pause(){this.paused=true;},async play(){this.paused=false;}});
  f.setClock(next);f.tick();assert.equal(next.playbackRate,2);assert.equal((f.video.handlers.timeupdate||[]).length,0);
  f.controller.seek(20);assert.equal(next.currentTime,20/30);assert.equal(f.video.currentTime,12.5);
});
test('empty jump fields do not rewind and unready or failed media cannot claim to play',async()=>{
  const f=fixture();f.nodes.diagnosticFrameJump.value='';f.nodes.diagnosticFrameJump.fire('change');assert.equal(f.video.currentTime,12.5);
  f.video.readyState=0;f.tick();assert.equal(f.nodes.diagnosticPlay.disabled,true);await f.controller.toggle();assert.equal(f.video.paused,true);
  f.video.error={code:3};f.tick();assert.match(f.nodes.diagnosticPlaybackStatus.textContent,/加载失败/);
  f.state.loaded=false;f.tick();assert.equal(f.nodes.diagnosticPlayback.hidden,true);
});
test('playback shortcuts leave form fields, navigation, native video and other views alone',()=>{
  const event={key:'ArrowRight',target:{closest:()=>null}};
  assert.equal(ux.shouldHandlePlaybackKey(event,'results'),true);
  for(const view of ['config','tools'])assert.equal(ux.shouldHandlePlaybackKey(event,view),false);
  for(const flag of ['ctrlKey','metaKey','altKey','isComposing'])assert.equal(ux.shouldHandlePlaybackKey({...event,[flag]:true},'results'),false);
  assert.equal(ux.shouldHandlePlaybackKey({...event,target:{closest:()=>({})}},'results'),false);
  const f=fixture();let prevented=false;f.document.fire('keydown',{...event,preventDefault(){prevented=true;},stopPropagation(){}});
  assert.equal(prevented,true);assert.equal(f.video.currentTime,376/30);assert.equal(f.video.paused,true);
});
test('context uses the loaded source, distinguishes original GT and never promotes a draft to a result',()=>{
  const loaded=ux.resultContext({loaded:true,sourceName:'loaded.mp4',sourcePath:'/real/loaded.mp4',rawOnly:true},{selectedInput:'/draft/new.mp4',modelTag:'model'});
  assert.equal(loaded.source,'loaded.mp4');assert.equal(loaded.path,'/real/loaded.mp4');assert.match(loaded.model,/未使用预测/);
  const pending=ux.resultContext({loaded:false,inferenceDirty:true},{selectedInput:'/draft/new.mp4',modelTag:'model'});
  assert.equal(pending.source,'/draft/new.mp4');assert.match(pending.status,/待应用.*未运行/);assert.equal(pending.state,'pending');
});
const app=fs.readFileSync(require.resolve('../eval/model_effect/visualization/viewer/web/app.js'),'utf8');
test('input browsing cannot change an in-flight request, including a late directory response',async()=>{
  const source=app.slice(app.indexOf('async function inGo(abs){'),app.indexOf('const _joinAbs ='));
  const prior={},list={childNodes:[prior],innerHTML:'old',replaceChildren(...nodes){this.restored=nodes;}};
  let requests=0,resolve;const context=vm.createContext({state:{loading:true},info:{},_inBrowseToken:0,$:()=>list,_enc:encodeURIComponent,getJSON(){requests++;return new Promise(done=>resolve=done);}});
  vm.runInContext(source,context);await vm.runInContext("inGo('/new')",context);assert.equal(requests,0);
  context.state.loading=false;const pending=vm.runInContext("inGo('/new')",context);assert.equal(requests,1);context.state.loading=true;resolve({path:'/new'});await pending;
  assert.deepEqual(list.restored,[prior]);
});
test('changing a browsed directory clears an unexecuted old video selection and context',async()=>{
  const source=app.slice(app.indexOf('async function inGo(abs){'),app.indexOf('const _joinAbs ='));
  const element={childNodes:[],querySelectorAll:()=>[],classList:{remove(){}}},state={loading:false,loaded:false,eid:4,vidName:'old.mp4',mediaContext:{source:'old'},inferenceDirty:false};let builds=0;
  const context=vm.createContext({state,info:{},_inBrowseToken:0,inCur:'/old',$:()=>element,_enc:encodeURIComponent,getJSON:async()=>({path:'/new',dirs:[],videos:[]}),setRawOnly(){},buildPanels(){builds++;},onInputDir(){},updateLoadBtn(){}});
  vm.runInContext(source,context);await vm.runInContext("inGo('/new')",context);
  assert.equal(state.eid,null);assert.equal(state.vidName,null);assert.equal(state.mediaContext,null);assert.equal(state.inferenceDirty,true);assert.equal(builds,1);
});
test('GPU readiness refresh preserves pending settings and labels eligible GPUs separately from running ones',async()=>{
  const source=app.slice(app.indexOf('async function pollModelReady(){'),app.indexOf("window.addEventListener('viewer:languagechange'"));
  const context=vm.createContext({modelReadyPolling:false,modelReadyTimer:null,clearTimeout(){},setTimeout(){},syncFullLimit(){},updateLoadBtn(){},hideProg(){},state:{loaded:false,loading:false,inferenceDirty:true},window:{},$:()=>({style:{}}),getJSON:async()=>({ready:true,devices:['cuda:0','cuda:1'],gpu_scheduler:{policy:'shared',eligible_devices:['cuda:0','cuda:1'],running_devices:[],max_workers:2,queued:0,gpus:[]}}),setStep(text){context.step=text;}});
  vm.runInContext(source,context);await vm.runInContext('pollModelReady()',context);assert.match(context.step,/设置待应用/);assert.doesNotMatch(context.step,/2卡/);
});
test('run actions require a selected input and freeze input controls while a request is active',()=>{
  const source=app.slice(app.indexOf('function updateLoadBtn(){'),app.indexOf('let modelReadyTimer'));
  const nodes=Object.fromEntries(['#loadModelBtn','#loadBtn','#loadRawBtn','#inBrow','#diagnosticModelBrowser'].map(id=>[id,{}]));
  const state={modelReady:true,modelLoading:false,loading:false,no_truth:false};
  const context=vm.createContext({state,curDatasetRel:null,EP_TOTAL:0,$:id=>nodes[id],document:{querySelectorAll:()=>[]}});
  vm.runInContext(source,context);vm.runInContext('updateLoadBtn()',context);assert.equal(nodes['#loadBtn'].disabled,true);assert.match(nodes['#loadBtn'].title,/先选择/);
  Object.assign(state,{no_truth:true,vidName:'selected.mp4',eid:4});vm.runInContext('updateLoadBtn()',context);assert.equal(nodes['#loadBtn'].disabled,false);
  state.loading=true;vm.runInContext('updateLoadBtn()',context);assert.equal(nodes['#loadBtn'].disabled,false,'running action must remain available to stop');assert.equal(nodes['#inBrow'].inert,true);assert.equal(nodes['#diagnosticModelBrowser'].inert,true);
});
