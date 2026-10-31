'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../mint/comparison/web/app.js'),'utf8');
const helpers=source.slice(source.indexOf('// Media readiness is separate'),source.indexOf('function masterVideo(){'));
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function fixture(){
  const nodes=Object.fromEntries(['play','prev','next','timeline','frameJump','snapshot','playbackStatus'].map(id=>[id,{dataset:{}}]));
  const calls=[],video={readyState:2,currentTime:0,seeking:false,error:null,playbackRate:1,paused:true,pause(){this.paused=true;calls.push('pause-video');},async play(){this.paused=false;}};
  const state={comparison:{sample:{frames:10,fps:30}},pollToken:1,cards:new Map([['first',{video}]]),frame:0,rate:1,playing:false};
  let clock=video;
  const sandbox={state,$:id=>nodes[id],masterVideo:()=>clock,updatePlayControl:value=>calls.push(['play-control',value]),notify:text=>calls.push(['notice',text]),ensureFrame:async()=>{},captureVideo(){},render:()=>calls.push('render')};
  const context=vm.createContext(sandbox);vm.runInContext(helpers,context);
  vm.runInContext(source.slice(source.indexOf('function pause(){'),source.indexOf('function tick(){')),context);
  return {state,video,nodes,calls,context,run:code=>vm.runInContext(code,context),setClock:value=>{clock=value;}};
}
test('media preparation never disables geometry stepping but does disable play',()=>{
  const f=fixture();f.video.readyState=0;f.state.frame=2;f.run('syncPlaybackReadiness()');
  assert.equal(f.nodes.play.disabled,true);assert.equal(f.nodes.prev.disabled,false);assert.equal(f.nodes.next.disabled,false);assert.equal(f.nodes.timeline.disabled,false);assert.match(f.nodes.playbackStatus.textContent,/准备中/);
  assert.equal(f.nodes.snapshot.disabled,false,'ready cards can be captured without waiting for the source video');
  const card=f.state.cards.get('first');f.state.cards.clear();f.run('syncPlaybackReadiness()');assert.equal(f.nodes.snapshot.disabled,true);f.state.cards.set('first',card);
  f.video.error={code:3};f.run('syncPlaybackReadiness()');assert.equal(f.nodes.playbackStatus.dataset.state,'error');assert.match(f.nodes.playbackStatus.textContent,/逐帧查看三维/);
  f.video.error=null;f.video.readyState=2;f.run('syncPlaybackReadiness()');assert.equal(f.nodes.play.disabled,false);
});
test('play readiness and frame boundaries are independent and missing samples are honest',()=>{
  const f=fixture();assert.equal(f.run('comparisonPlaybackState(state.comparison.sample,masterVideo(),0).previous'),false);
  assert.equal(f.run('comparisonPlaybackState(state.comparison.sample,masterVideo(),9).next'),false);
  assert.equal(f.run('comparisonPlaybackState(null,masterVideo(),0).seek'),false);
  f.video.seeking=true;assert.equal(f.run('comparisonPlaybackState(state.comparison.sample,masterVideo(),3).ready'),false);
});
test('stale play rejection cannot pause a newly selected comparison',async()=>{
  const f=fixture(),old=deferred();f.video.play=()=>old.promise;const pending=f.run('play()');
  const current={...f.video,pause(){throw new Error('Must not pause new media');}};
  f.state.pollToken++;f.state.playing=true;f.state.cards=new Map([['new',{video:current}]]);f.setClock(current);
  old.reject(new Error('old decoder retired'));await pending;
  assert.equal(f.state.playing,true);assert.equal(f.calls.some(item=>Array.isArray(item)&&item[0]==='notice'),false);
});
test('invalid seeks do nothing and a retired frame request does not redraw a new workspace',async()=>{
  const f=fixture(),read=deferred();f.context.ensureFrame=()=>read.promise;
  await f.run('seek(NaN)');assert.equal(f.calls.length,0);
  const pending=f.run('seek(999)');assert.equal(f.state.frame,9);f.state.pollToken++;f.state.frame=0;read.resolve();await pending;
  assert.equal(f.calls.includes('render'),false);assert.equal(f.state.frame,0);
});
test('comparison shortcuts respect fields, native video controls, IME and handled events',()=>{
  const f=fixture(),event={key:'ArrowRight',target:{closest:()=>null}};f.context.event=event;
  assert.equal(f.run('comparisonShortcutAllowed(event)'),true);
  for(const key of ['defaultPrevented','isComposing','ctrlKey','metaKey','altKey']){f.context.event={...event,[key]:true};assert.equal(f.run('comparisonShortcutAllowed(event)'),false);}
  f.context.event={...event,target:{closest:()=>({})}};assert.equal(f.run('comparisonShortcutAllowed(event)'),false);
});
test('metadata dialog copies the latest evidence without moving or exposing the hidden card panel',()=>{
  let pauses=0;const child={cloneNode:()=>({copy:true})},meta={children:[child],hidden:true};
  const body={children:[],replaceChildren(...nodes){this.children=nodes;},append(node){this.children.push(node);}},dialog={open:false,showModal(){this.open=true;}},title={};
  const context=vm.createContext({state:{comparison:{sample_request:{source:{path:'/dataset/video.mp4'}}}},$:id=>({runDetailsDialog:dialog,runDetailsBody:body,runDetailsTitle:title})[id],pause(){pauses++;},el:(tag,text,className)=>({tag,textContent:text,className})});
  vm.runInContext(source.slice(source.indexOf('function openRunDetails(card){'),source.indexOf('function createCard(run,index){')),context);
  context.card={run:{label:'Reference',result:{reference_role:'user_declared_ground_truth'}},meta};vm.runInContext('openRunDetails(card)',context);
  assert.equal(dialog.open,true);assert.equal(pauses,1);assert.match(title.textContent,/Reference/);assert.match(body.children[0].textContent,/用户声明/);assert.equal(meta.hidden,true);assert.equal(meta.children[0],child);assert.notEqual(body.children.at(-1),child);
});
test('an aborted earlier play request cannot cancel a newer play intent on the same video',async()=>{
  const f=fixture(),first=deferred(),second=deferred();let calls=0;
  f.video.play=()=>++calls===1?first.promise:second.promise;
  const old=f.run('play()');f.run('pause()');const current=f.run('play()');
  first.reject(Object.assign(new Error('paused earlier request'),{name:'AbortError'}));await old;
  assert.equal(f.state.playing,true);second.resolve();await current;assert.equal(f.state.playing,true);
});
