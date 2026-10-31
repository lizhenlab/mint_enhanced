'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../eval/model_effect/visualization/viewer/web/app.js'),'utf8');
const modes=source.slice(source.indexOf('// Presentation-only modes'),source.indexOf('const clampEp'));
function fixture(){
  const nodes={},buttons=['config','results','tools'].map(mode=>({dataset:{diagnosticView:mode},classList:{toggle(){}},setAttribute(name,value){this[name]=value;},focus(){this.focused=true;},addEventListener(name,fn){this[name]=fn;}}));
  for(const id of ['diagnosticConfiguration','diagnosticStage','diagnosticResultEmpty','diagnostic-results','diagnostic-tools'])nodes['#'+id]={hidden:false,setAttribute(name,value){this[name]=value;},focus(){this.focused=true;}};
  let paused=0,draws=0;
  const events={};
  const context=vm.createContext({URL,URLSearchParams,state:{loaded:false,hidden:new Set(['bench'])},location:{href:'http://localhost/?tool=benchmark',search:'',hash:''},document:{body:{dataset:{}},querySelector(){return null;},querySelectorAll(selector){return selector==='#blocks video'?[{pause(){paused++;}}]:buttons;}},window:{history:{replaceState(_a,_b,url){context.location.href='http://localhost'+url;}},scrollTo(){},addEventListener(name,fn){events[name]=fn;}},$:id=>nodes[id],requestDraw(){draws++;},buildPanels(){}});
  vm.runInContext(modes,context);
  return {context,nodes,buttons,events,run:code=>vm.runInContext(code,context),paused:()=>paused,draws:()=>draws};
}
test('diagnostic entry routes choose a view without executing inference',()=>{
  const {run}=fixture();
  assert.equal(run("initialDiagnosticView('')"),'config');
  for(const tool of ['benchmark','batch','code','analysis-video','analysis-intrinsics'])assert.equal(run(`initialDiagnosticView('?tool=${tool}')`),'tools');
  assert.equal(run("initialDiagnosticView('?view=results&tool=benchmark')"),'results');
  assert.equal(run("initialDiagnosticView('', '#diagnostic-results')"),'results');
  assert.equal(run("initialDiagnosticView('?view=invalid')"),'config');
});
test('view switches preserve mounted configuration and expose an honest empty results view',()=>{
  const {run,nodes,context,draws}=fixture();
  nodes['#diagnosticConfiguration'].parameterDraft='custom checkpoint';
  run("showDiagnosticView('results')");
  assert.equal(nodes['#diagnosticConfiguration'].hidden,true);
  assert.equal(nodes['#diagnosticResultEmpty'].hidden,false);
  assert.equal(nodes['#diagnosticStage'].hidden,false);
  assert.doesNotMatch(context.location.href,/tool=/);
  run("state.loaded=true;showDiagnosticView('results')");
  assert.equal(nodes['#diagnosticResultEmpty'].hidden,true);assert.equal(draws(),1);
  run("showDiagnosticView('config')");
  assert.equal(nodes['#diagnosticConfiguration'].hidden,false);
  assert.equal(nodes['#diagnosticConfiguration'].parameterDraft,'custom checkpoint');
  assert.equal(nodes['#diagnosticStage'].hidden,true);
});
test('leaving results pauses hidden playback and keyboard tabs select the target view',()=>{
  const {run,buttons,context,paused}=fixture();
  run('wireDiagnosticViews()');
  run("showDiagnosticView('results');showDiagnosticView('tools')");
  assert.equal(paused(),1);assert.equal(context.document.body.dataset.diagnosticView,'tools');
  const event={key:'ArrowLeft',preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;}};
  buttons[2].keydown(event);
  assert.equal(context.document.body.dataset.diagnosticView,'results');assert.equal(buttons[1].focused,true);
  assert.equal(event.stopped,true,'view navigation must not also step the video frame');
});
test('opening a legacy benchmark reveals its tool view without starting a benchmark',()=>{
  const {run,context,nodes}=fixture();
  const start=source.indexOf('function showBench(on){'),end=source.indexOf('function _benchSetOptions',start);
  vm.runInContext(source.slice(start,end),context);
  run('showBench(true)');
  assert.equal(context.document.body.dataset.diagnosticView,'tools');
  assert.equal(nodes['#diagnostic-tools'].open,true);
  assert.equal(run("state.hidden.has('bench')"),false);
});

test('successful GT or cached prediction loading focuses results only after the response completes',async()=>{
  const loadSource=source.slice(source.indexOf('async function loadEpisode('),source.indexOf('function _manualRenderBadge('));
  for(const rawOnly of [true,false]){
    const {context,nodes,run}=fixture();let resolveResponse,requested;
    nodes['#loadBtn']={classList:{add(){},remove(){}}};nodes['#loadRawBtn']={};
    Object.assign(context,{curDatasetRel:null,EP_TOTAL:0,_lastMF:0,info:{},setTimeout:fn=>setTimeout(fn,0),
      updateLoadBtn(){},setStep(){},setProg(){},hideProg(){},refreshTaskHistory(){},_stageText:()=>'',
      getJSON:async()=>({stage:'load'}),U:path=>path,effectiveHandMode:()=> 'smooth',
      fetch:url=>{requested=url;return new Promise(resolve=>{resolveResponse=resolve;});}});
    Object.assign(context.state,{rawOnly,views:{},camMode:'max_chunked'});
    vm.runInContext(loadSource,context);
    const pending=run('loadEpisode(7,{explicitInference:true})');
    assert.equal(context.document.body.dataset.diagnosticView,undefined,'loading must not hide configuration before a result exists');
    resolveResponse({status:200,ok:true,json:async()=>({nframes:2,gt:rawOnly?{}:null,pred:rawOnly?null:{},source_path:'/video.mp4'})});
    await pending;
    assert.equal(context.state.loaded,true);assert.equal(context.document.body.dataset.diagnosticView,'results');
    assert.equal(nodes['#diagnosticResultEmpty'].hidden,true);
    if(rawOnly)assert.match(requested,/raw=1/);else assert.match(requested,/cam_mode=max_chunked/);
  }
});
