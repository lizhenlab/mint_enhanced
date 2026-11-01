'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(require.resolve('../eval/model_effect/visualization/viewer/web/app.js'),'utf8');
const ux=require('../mint/comparison/web/diagnostic-ux.js');
function routes(){
  const context=vm.createContext({URL,URLSearchParams,document:{baseURI:'https://mint.test/proxy/8011/diagnostics'}});
  vm.runInContext(source.slice(source.indexOf('// URL roots'),source.indexOf('async function getJSON')),context);
  return code=>vm.runInContext(code,context);
}
test('diagnostic returns restore local compare context and the current reverse proxy prefix',()=>{
  const run=routes();
  assert.equal(run("diagnosticWorkspaceReturnURL('https://mint.test/diagnostics','/compare?id=one&hub=tools&tools_tab=render').href"),'https://mint.test/compare?id=one&hub=tools&tools_tab=render');
  assert.equal(run("diagnosticWorkspaceReturnURL('https://mint.test/proxy/8011/diagnostics','/proxy/8011/compare/?id=two&frame=8').href"),'https://mint.test/proxy/8011/compare?id=two&frame=8');
  assert.equal(run("diagnosticSafeReturnPath('https://mint.test/diagnostics','/compare?id=one&return_to=%2Fcompare%3Fid%3Dnested')"),'/compare?id=one');
});
test('external, scheme, protocol relative and non-comparison return targets are rejected',()=>{
  const run=routes();
  for(const unsafe of ['https://other.test/compare','https://mint.test/compare','javascript:alert(1)','//other.test/compare','/settings','/compare/other','/proxy/other/compare','/compare\\@other.test']){
    assert.equal(run(`diagnosticSafeReturnPath('https://mint.test/proxy/8011/diagnostics',${JSON.stringify(unsafe)})`),null);
    assert.equal(run(`diagnosticWorkspaceReturnURL('https://mint.test/proxy/8011/diagnostics',${JSON.stringify(unsafe)}).href`),'https://mint.test/proxy/8011/compare');
  }
});
test('the tools breadcrumb keeps result association but opens its own tools directory',()=>{
  const run=routes();
  const href=run("diagnosticWorkspaceReturnURL('https://mint.test/proxy/8011/diagnostics','/proxy/8011/compare?id=one&hub=quality&collection=batch&tools_tab=render&return_to=bad','compare?hub=tools').href"),url=new URL(href);
  assert.equal(url.pathname,'/proxy/8011/compare');assert.equal(url.searchParams.get('hub'),'tools');assert.equal(url.searchParams.get('id'),'one');
  for(const key of ['collection','tools_tab','return_to'])assert.equal(url.searchParams.has(key),false);
  assert.equal(run("diagnosticWorkspaceReturnURL('https://mint.test/proxy/8011/diagnostics','/proxy/8011/compare?id=one','compare?hub=batch').href"),'https://mint.test/proxy/8011/compare?hub=batch');
});
test('opening a saved diagnostic record retains only a validated workspace return',()=>{
  const run=routes(),safe='https://mint.test/proxy/8011/diagnostics?return_to='+encodeURIComponent('/proxy/8011/compare?id=one&hub=tools');
  const result=new URL(run(`diagnosticPageURL(${JSON.stringify(safe)},{task:'task',view:'config'}).href`));
  assert.equal(result.searchParams.get('task'),'task');assert.equal(result.searchParams.get('return_to'),'/proxy/8011/compare?id=one&hub=tools');
  const unsafe='https://mint.test/diagnostics?return_to='+encodeURIComponent('/settings');
  assert.equal(new URL(run(`diagnosticPageURL(${JSON.stringify(unsafe)},{task:'task'}).href`)).searchParams.has('return_to'),false);
});
function summaryFixture(){
  class Node{constructor(){this.hidden=false;this.dataset={};this.value='';this.textContent='';this.handlers={};}addEventListener(n,f){(this.handlers[n]??=[]).push(f);}removeEventListener(){}setAttribute(n,v){this[n]=v;}}
  const names=['diagnosticPlayback','diagnosticInputLabel','diagnosticModelLabel','diagnosticConfiguredInput','diagnosticConfiguredModel','diagnosticResultState','diagnosticContextBar','diagnosticInferenceStatus','diagnosticToolContext','diagnosticBackgroundTask','diagnosticBackgroundStatus'],nodes=Object.fromEntries(names.map(id=>[id,new Node()]));
  const doc=new Node();doc.getElementById=id=>nodes[id];const state={loaded:true,sourceName:'result.mp4',resultModelTag:'result-model',hidden:new Set()},mode={view:'tools'};
  const controller=ux.mount({document:doc,window:{setInterval(){return 1;},clearInterval(){}},getState:()=>state,getVideo:()=>null,getView:()=>mode.view,getModelTag:()=> 'new-draft-model',getSelectedInput:()=>'/new/input.mp4',redraw(){}});
  return {nodes,state,mode,controller};
}
test('independent tools hide MINT input/model chrome while retaining a running diagnostic notice',()=>{
  const f=summaryFixture();assert.equal(f.nodes.diagnosticContextBar.hidden,true);assert.equal(f.nodes.diagnosticInferenceStatus.hidden,true);assert.equal(f.nodes.diagnosticToolContext.hidden,false);assert.equal(f.nodes.diagnosticBackgroundTask.hidden,true);
  f.state.loading=true;f.controller.sync();assert.equal(f.nodes.diagnosticBackgroundTask.hidden,false);assert.match(f.nodes.diagnosticBackgroundStatus.textContent,/后台运行/);
  f.state.cancelling=true;f.controller.sync();assert.match(f.nodes.diagnosticBackgroundStatus.textContent,/正在停止/);
  f.mode.view='config';f.controller.sync();assert.equal(f.nodes.diagnosticInferenceStatus.hidden,false);assert.equal(f.nodes.diagnosticToolContext.hidden,true);assert.equal(f.nodes.diagnosticBackgroundTask.hidden,true);f.controller.destroy();
});
test('loaded results keep their model tag while the configuration summary follows the current selection',()=>{
  const f=summaryFixture();assert.equal(f.nodes.diagnosticModelLabel.textContent,'result-model');assert.equal(f.nodes.diagnosticConfiguredModel.textContent,'new-draft-model');
  f.state.loaded=false;f.controller.sync();assert.equal(f.nodes.diagnosticModelLabel.textContent,'new-draft-model');
  assert.match(ux.resultContext({loaded:true,rawOnly:true,resultModelTag:'old'},{modelTag:'new'}).model,/未使用预测模型/);f.controller.destroy();
});
test('a model tag changed during a result request cannot relabel that completed result',async()=>{
  const load=source.slice(source.indexOf('async function loadEpisode('),source.indexOf('function _manualRenderBadge('));let reply;
  const nodes={'#loadBtn':{classList:{add(){},remove(){}}},'#loadRawBtn':{},'#metricsWrap':{style:{}}};
  const state={rawOnly:false,no_truth:true,camMode:'max_chunked',gtBetas:'per_frame',predBetas:'per_frame',predFov:'per_frame',views:{}};
  const context=vm.createContext({state,CKPT_TAG:'requested-model',curDatasetRel:null,EP_TOTAL:0,_lastMF:0,info:{},$:id=>nodes[id],U:path=>path,updateLoadBtn(){},setStep(){},setProg(){},hideProg(){},refreshTaskHistory(){},buildPanels(){},showDiagnosticView(){},effectiveHandMode:()=> 'smooth',_stageText:()=>'',getJSON:async()=>({stage:'load'}),setTimeout:fn=>setTimeout(fn,0),fetch:()=>new Promise(resolve=>{reply=resolve;})});
  vm.runInContext(load,context);const pending=vm.runInContext('loadEpisode(1,{explicitInference:true})',context);context.CKPT_TAG='later-selection';reply({ok:true,status:200,json:async()=>({fps:30,nframes:3,pred:{}})});await pending;
  assert.equal(state.loaded,true);assert.equal(state.resultModelTag,'requested-model');assert.equal(context.CKPT_TAG,'later-selection');
});
test('analysis menu opens from keyboard, skips unavailable tools and closes with Escape or outside click',()=>{
  let active=null,selection=null;const events={};
  const doc={querySelectorAll:selector=>selector==='[data-analysis-type]'?options:[],addEventListener:(name,fn)=>{events[name]=fn;}};
  function node(){return {dataset:{},setAttribute(n,v){this[n]=v;},focus(){active=this;}};}
  const button=node(),options=['video','intrinsics','diversity'].map(type=>({...node(),dataset:{analysisType:type}}));options[1].disabled=true;
  const launcher={opened:false,classList:{toggle(name,value){launcher.opened=value;},contains(){return launcher.opened;}},contains:target=>target===button||options.includes(target)};
  const context=vm.createContext({document:doc,$:id=>({'#datasetAnalysisBtn':button,'#datasetAnalysisLauncher':launcher}[id]||null),renderDatasetAnalysis(){},selectDatasetAnalysis:type=>{selection=type;}});
  vm.runInContext(source.slice(source.indexOf('function wireDatasetAnalysis(){'),source.indexOf('function selectDatasetAnalysis(')),context);vm.runInContext('wireDatasetAnalysis()',context);
  const key=value=>({key:value,preventDefault(){},stopPropagation(){}});button.onkeydown(key('ArrowDown'));assert.equal(launcher.opened,true);assert.equal(active,options[0]);assert.equal(button['aria-expanded'],'true');
  options[0].onkeydown(key('ArrowDown'));assert.equal(active,options[2]);options[2].onkeydown(key('Escape'));assert.equal(launcher.opened,false);assert.equal(active,button);
  button.onkeydown(key('Enter'));assert.equal(active,options[0]);events.click({target:{}});assert.equal(launcher.opened,false);button.onkeydown(key(' '));assert.equal(active,options[0]);options[2].onclick();assert.equal(selection,'diversity');assert.equal(launcher.opened,false);assert.equal(active,button);
});
test('legacy display generation requires loaded, stable diagnostic results',()=>{
  const state={loaded:false,loading:false,hidden:new Set(['mujoco_3d','wuji_retarget_3d'])},nodes=Object.fromEntries(['#mujocoBtn','#retargetBtn'].map(id=>[id,{classList:{toggle(){}},setAttribute(){}}])),calls=[];
  const context=vm.createContext({state,info:{},$:id=>nodes[id],console:{log(){}},showDiagnosticView:mode=>calls.push(mode),buildPanels:()=>calls.push('build')});
  vm.runInContext(source.slice(source.indexOf('function syncMujocoButton(){'),source.indexOf('// Diagnostic section navigation')),context);const run=code=>vm.runInContext(code,context);
  run('syncMujocoButton();toggleMujocoPanel();toggleRetargetPanel()');assert.equal(nodes['#mujocoBtn'].disabled,true);assert.equal(nodes['#retargetBtn'].disabled,true);assert.deepEqual(calls,[]);
  state.loaded=true;run('syncMujocoButton();toggleMujocoPanel()');assert.equal(nodes['#mujocoBtn'].disabled,false);assert.deepEqual(calls,['results','build']);state.loading=true;run('syncMujocoButton();toggleRetargetPanel()');assert.equal(nodes['#retargetBtn'].disabled,true);assert.deepEqual(calls,['results','build']);
});
