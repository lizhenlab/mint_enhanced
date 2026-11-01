'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');

class Element {
  constructor(tag='div',text=''){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.attributes={};this._value='';this.textContent=text;this.checked=false;this.disabled=false;this.hidden=false;this.open=false;this.classList={toggle(){},add(){}};}
  get value(){return this._value;}
  set value(value){this._value=String(value);}
  get options(){return this.children.filter(child=>child.tagName==='OPTION');}
  replaceChildren(...nodes){this.children=[];if(this.tagName==='SELECT')this.value='';this.append(...nodes);}
  append(...nodes){for(const child of nodes){this.children.push(child);if(child&&typeof child==='object')child.parentElement=this;}if(this.tagName==='SELECT'&&this.value===''&&this.options.length)this.value=this.options[0].value;}
  setAttribute(name,value){this.attributes[name]=String(value);}
  querySelectorAll(){return [];}
  checkValidity(){return true;}
  reportValidity(){return true;}
  showModal(){this.open=true;}
}
function fixture(){
  const nodes={},timers=new Map(),events=[];let timer=0;
  const get=id=>nodes[id]??=(new Element(/Camera$|Episode$|Sequence$|Group$|Metric$|Version$|Dataset$/.test(id)?'select':'div'));
  const context=vm.createContext({console,URL,URLSearchParams,structuredClone,Number,Node:Element,
    document:{addEventListener(){},querySelectorAll(){return [];}},
    window:{addEventListener(){},dispatchEvent(event){events.push(event);},history:{replaceState(){},pushState(){}}},location:{href:'http://localhost/compare?hub=research',search:'?hub=research'},CustomEvent:class {constructor(type,options){this.type=type;this.detail=options.detail;}},
    setTimeout(fn){timers.set(++timer,fn);return timer;},clearTimeout(id){timers.delete(id);},
    $:get,el:(tag,text)=>new Element(tag,text),api:async()=>{throw new Error('Unexpected API request');},hubError(error){throw error;},
    state:{comparison:null},TERMINAL:new Set(['completed','failed','cancelled','interrupted']),STAGES:{},
  });
  const run=code=>vm.runInContext(code,context);
  for(const file of ['platform.js','research.js','enhancements.js'])run(fs.readFileSync(path.join(__dirname,'../mint/comparison/web',file),'utf8'));
  return {context,nodes,get,run,timers,events};
}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function dataset(root,episode=0){return {root,camera:'left',cameras:['left'],offset:0,total:1,fps:30,episodes:[{episode_index:episode,video:root+'/video.mp4',length:81,annotation:{ready:true,camera:true,mesh:false}}]};}

test('Episode browsing accepts only the latest root and blocks selection while loading',async()=>{
  const x=fixture(),a=deferred(),b=deferred();x.get('datasetRoot').value='/a';let calls=0;x.context.api=()=>++calls===1?a.promise:b.promise;
  const first=x.run('loadDatasetForm()');assert.throws(()=>x.run('datasetSelection()'),/正在读取/);
  x.get('datasetRoot').value='/b';const second=x.run('loadDatasetForm()');b.resolve(dataset('/b',7));await second;
  a.resolve(dataset('/a'));assert.equal(await first,null);
  assert.equal(x.run('datasetForm.catalog.root'),'/b');assert.equal(x.get('source').value,'/b/video.mp4');assert.equal(x.run('datasetSelection().episode'),7);
  x.get('datasetRoot').value='/edited-without-reload';assert.throws(()=>x.run('datasetSelection()'),/读取当前数据集/);
});

test('a late LeRobot response cannot re-enter dataset mode after choosing a video',async()=>{
  const x=fixture(),request=deferred();x.get('datasetRoot').value='/a';x.context.api=()=>request.promise;
  const pending=x.run('loadDatasetForm()');x.run('setDatasetMode(false)');x.get('source').value='/chosen.mp4';request.resolve(dataset('/a'));
  assert.equal(await pending,null);assert.equal(x.run('datasetForm.active'),false);assert.equal(x.get('source').value,'/chosen.mp4');
});

test('native sequence browsing cannot pair a prior dataset sequence with a newer dataset root',async()=>{
  const x=fixture(),a=deferred(),b=deferred();x.run("nextUI.catalog=[{adapter:'egodex'},{adapter:'arctic'}]");
  x.get('nativeDataset').value='0';x.get('nativeRoot').value='/ego';x.get('nativeSplit').value='train';let calls=0;x.context.api=()=>++calls===1?a.promise:b.promise;
  const first=x.run('loadNativeSequences()');x.get('nativeDataset').value='1';x.get('nativeRoot').value='/arctic';const second=x.run('loadNativeSequences()');
  b.resolve({groups:[],sequences:[{sequence:'s05/box',name:'Box'}],offset:0,total:1});await second;x.get('nativeSequence').value='s05/box';
  a.resolve({groups:[],sequences:[{sequence:'old/episode',name:'Old'}],offset:0,total:1});await first;
  assert.equal(x.get('nativeSequence').options.some(option=>option.value==='old/episode'),false);
  x.get('nativeStart').value='0';const parameters=x.run('nativeImportParameters()');assert.equal(parameters.dataset,'arctic');assert.equal(parameters.sequence,'s05/box');
  x.get('nativeRoot').value='/edited';assert.throws(()=>x.run('nativeImportParameters()'),/先浏览当前数据集/);
});

test('editing a native import during preflight cannot start the superseded job',async()=>{
  const x=fixture(),request=deferred(),calls=[];x.run("nextUI.catalog=[{adapter:'egodex',dataset:'EgoDex'}]");
  for(const [id,value]of Object.entries({nativeDataset:'0',nativeRoot:'/ego',nativeSplit:'train',nativeSequence:'episode',nativeStart:'0',nativeMaximum:''}))x.get(id).value=value;
  x.run('nextUI.nativeSequenceReady=nativeSequenceContext()');x.context.api=(url,body)=>{calls.push({url,body});return request.promise;};
  const pending=x.run('prepareNative()');x.get('nativeMaximum').value='81';request.resolve({ready:true});await assert.rejects(pending,/参数已变化/);
  assert.equal(calls.length,1);assert.match(calls[0].url,/preflight$/);
});

test('workflow polling and log responses stay attached to the currently selected workflow',async()=>{
  const x=fixture(),a=deferred(),b=deferred();x.get('platformDialog').open=true;x.run("platform.pane='research'");
  x.context.showResearchView=()=>{};x.context.renderWorkflowNextSteps=async()=>{};
  x.context.api=url=>url.endsWith('/a')?a.promise:b.promise;
  const first=x.run("inspectWorkflow('a')"),second=x.run("inspectWorkflow('b')");b.resolve({id:'b',status:'running',progress:{stage:'train-b'}});await second;
  a.resolve({id:'a',status:'running',progress:{stage:'train-a'}});assert.equal(await first,null);
  assert.match(x.get('workflowState').textContent,/train-b/);assert.equal(x.timers.size,1);assert.equal(x.events.length,1);
  const log=deferred();x.context.api=()=>log.promise;const pendingLog=x.get('workflowLog').onclick();
  x.run("researchUI.workflow='newer'");log.resolve({text:'Old log'});await pendingLog;assert.equal(x.get('logDialog').open,false);
});

test('shared registry selectors ignore obsolete responses instead of replacing the newer workflow choices',async()=>{
  const x=fixture(),a=deferred(),b=deferred();let calls=0;x.context.api=()=>++calls===1?a.promise:b.promise;
  const first=x.run("loadRegistryChoices('dataset','workflowDataset',{filter:r=>r.training_ready})");const second=x.run("loadRegistryChoices('dataset','workflowDataset',{filter:r=>r.status==='reviewed_manifest'})");
  b.resolve({records:[{id:'review',status:'reviewed_manifest'}]});await second;a.resolve({records:[{id:'training',training_ready:true}]});assert.equal(await first,null);
  assert.deepEqual(x.get('workflowDataset').options.map(option=>option.value),['','review']);
});

test('a late training curve response cannot replace the selected training job',async()=>{
  const x=fixture(),a=deferred(),b=deferred();x.context.drawTrainingCurve=()=>{};x.context.nextTable=()=>{};x.context.statusLabel=value=>value;
  x.context.api=url=>url.includes('/a/')?a.promise:b.promise;
  x.run("nextUI.curve='a'");const first=x.run('refreshCurves()');x.run("nextUI.curve='b'");const second=x.run('refreshCurves()');
  b.resolve({status:'completed-b',points:[],available_points:2,attempts:[],checkpoints:[]});await second;
  a.resolve({status:'running-a',points:[],available_points:1,attempts:[],checkpoints:[]});await first;
  assert.equal(x.run('nextUI.curves.status'),'completed-b');assert.match(x.get('curveSummary').textContent,/completed-b/);
});

test('batch preflight refuses parameter edits and cannot double-submit while in flight',async()=>{
  const x=fixture(),request=deferred(),calls=[];x.run("platform.selection.set('video:/a.mp4',{source:'/a.mp4',name:'A'})");
  for(const [id,value]of Object.entries({view:'full',target_fps:'0',start_frame:'0',max_frames:'',camera:''}))x.get('batch-'+id).value=value;
  x.context.selectedAlgorithmsFrom=()=>[{algorithm:'mint',options:{}}];x.context.api=(url,body)=>{calls.push({url,body});return request.promise;};
  const pending=x.run("submitCollection('batch')");await x.run("submitCollection('batch')");assert.equal(calls.length,1);
  x.get('batch-target_fps').value='15';request.resolve({ready:true});await assert.rejects(pending,/输入或参数已变化/);assert.equal(calls.length,1);assert.equal(x.get('batch-submit').disabled,false);
});

test('render submission rejects blank or reversed ranges and owns one request at a time',async()=>{
  const x=fixture(),request=deferred(),calls=[];x.get('renderRun').value='run';x.get('renderWidth').value='832';x.get('renderEnd').value='';x.get('renderKind').value='mujoco';x.get('renderSpace').value='camera';x.get('renderPolicy').value='shared';x.context.refreshRenderTool=async()=>{};x.context.clearRenderPlayback=()=>{};x.context.showToolsView=()=>{};
  x.run("toolsUI.options={run_id:'run',sample_frames:81,result_frames:81,mesh_available:true,world_available:false,available_ranges:[[0,81]],world_ranges:[]};platform.capability={render:{mujoco:{ready:true}}}");
  x.context.api=(url,body)=>{calls.push({url,body});return request.promise;};await assert.rejects(x.run('startRenderTool()'),/样本帧区间/);
  x.get('renderStart').value='4';x.get('renderEnd').value='2';await assert.rejects(x.run('startRenderTool()'),/结束帧/);assert.equal(calls.length,0);
  x.get('renderStart').value='1';x.get('renderEnd').value='81';
  const pending=x.run('startRenderTool()');await x.run('startRenderTool()');assert.equal(calls.length,1);request.resolve({id:'render-job'});await pending;
  assert.equal(calls[0].body.start,0);assert.equal(calls[0].body.end,81);assert.equal(x.get('startRenderTool').disabled,false);
});

test('workflow deep links and late observations preserve the explicitly selected research view',async()=>{
  const x=fixture(),request=deferred();x.run("platform.pane='research';nextUI.researchView='curves'");x.get('platformDialog').open=true;
  const navigations=[];x.context.showResearchView=view=>{navigations.push(view);x.run(`nextUI.researchView=${JSON.stringify(view)}`);};
  x.context.renderWorkflowNextSteps=async()=>{};x.context.api=()=>request.promise;
  const pending=x.run("inspectWorkflow('training-deeplink')");
  assert.equal(x.run('nextUI.researchView'),'curves');x.run("nextUI.researchView='models'");
  request.resolve({id:'training-deeplink',status:'completed',progress:{},request:{kind:'train'}});await pending;
  assert.deepEqual(navigations,[]);assert.equal(x.run('nextUI.researchView'),'models');
});

test('explicit workflow opening navigates before loading and cannot hijack a later tab click',async()=>{
  const x=fixture(),request=deferred();x.run("platform.pane='research';nextUI.researchView='models'");x.get('platformDialog').open=true;
  x.context.showResearchView=view=>x.run(`nextUI.researchView=${JSON.stringify(view)}`);x.context.renderWorkflowNextSteps=async()=>{};x.context.api=()=>request.promise;
  const pending=x.run("inspectWorkflow('selected-from-tasks',{navigate:true})");assert.equal(x.run('nextUI.researchView'),'train');
  x.run("nextUI.researchView='curves'");request.resolve({id:'selected-from-tasks',status:'completed',progress:{}});await pending;
  assert.equal(x.run('nextUI.researchView'),'curves');
});

test('the curve picker retains the named deep-linked training outside the current task page',()=>{
  const x=fixture();x.context.statusLabel=status=>status;x.run("nextUI.curve='older-training';nextUI.researchTasks=[{id:'render',kind:'render',source:'train',label:'Unrelated'}]");
  x.context.workflow={id:'older-training',status:'completed',request:{kind:'train',name:'ARCTIC MANO 10 steps'}};
  x.run('syncTrainingWorkflowPicker(workflow)');
  assert.equal(x.get('curveWorkflowPicker').value,'older-training');
  assert.match(x.get('curveWorkflowPicker').options.find(option=>option.value==='older-training').textContent,/ARCTIC MANO 10 steps/);
  x.run("nextUI.researchTasks=[{id:'recent-training',kind:'workflow',source:'train',label:'Recent',status:'running'}];syncTrainingWorkflowPicker()");
  assert.equal(x.get('curveWorkflowPicker').value,'older-training');
  assert.deepEqual(x.get('curveWorkflowPicker').options.map(option=>option.value),['','recent-training','older-training']);
});
