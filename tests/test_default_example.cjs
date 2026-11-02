'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../mint/comparison/web/default-example.js'),'utf8');
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const clone=value=>JSON.parse(JSON.stringify(value));
function node(value='',type='text'){
  return {value,type,checked:false,disabled:false,hidden:false,textContent:'',dataset:{},options:[],listeners:{},attributes:{},addEventListener(name,callback){(this.listeners[name]??=[]).push(callback);},dispatch(name){for(const callback of this.listeners[name]||[])callback({currentTarget:this,target:this});},setAttribute(name,value){this.attributes[name]=String(value);},removeAttribute(name){delete this.attributes[name];},getAttribute(name){return this.attributes[name]??null;}};
}
function definition(name,value,choices){return {name,default:value,...(choices?{choices:choices.map(v=>[String(v),String(v)])}:{minimum:1,maximum:8})};}
function fixture(){
  const nodes={},specs=[],boxes=[],calls=[],effects=[];
  for(const [id,value]of Object.entries({source:'/existing/video.mp4',view:'left',targetFps:'10',startFrame:'4',maxFrames:'81',camera:'',examples:'',executionPriority:'background',executionCache:'fresh',datasetRoot:'/existing/dataset',datasetCamera:'ego',datasetEpisode:'3'}))nodes[id]=node(value);
  for(const id of ['datasetAttachGT','datasetCalibration'])nodes[id]=node('', 'checkbox');
  nodes.taskDialog=node();nodes.taskDialog.open=true;nodes.useDefaultExample=node();nodes.useDefaultExample.textContent='使用默认示例';nodes.defaultExampleSummary=node();nodes.defaultExampleSummary.hidden=true;
  const definitions={mint:[definition('mode','max_chunked',['max_chunked','chunked']),definition('hand_mode','smooth',['smooth','blend'])],hawor_full:[{name:'analysis_width',default:832,minimum:0,maximum:1920},{name:'focal',default:'',minimum:.0001}],hawor_camera:[{name:'analysis_width',default:832,minimum:0,maximum:1920},{name:'focal',default:'',minimum:.0001}],ace_kfree:[definition('mode','independent81',['independent81','native_tiled']),definition('encode_width',832,[832,640,512]),definition('stride',80,[80,40]),definition('merge','center',['center','keep','blend'])],ace_kgiven:[definition('mode','independent81',['independent81','native_tiled']),definition('encode_width',832,[832,640,512]),definition('stride',80,[80,40]),definition('merge','center',['center','keep','blend'])],reference:[]};
  for(const [id,fields]of Object.entries(definitions)){
    if(id!=='reference')fields.push(definition('max_gpus',8),definition('resource_policy','shared',['shared','idle','dedicated']));
    const check=node(id,'checkbox');check.checked=id==='mint';check.disabled=id==='reference';
    const inputs=fields.map(field=>{const input=node(String(field.default),field.choices?'select-one':'number');input.dataset.option=field.name;return input;});
    const checkpoint=id==='mint'?node('/old/checkpoint'):null,config=id==='mint'?node('/old/config'):null;
    if(checkpoint)checkpoint.dataset.selection='checkpoint';if(config)config.dataset.selection='config';
    const box={dataset:{algorithm:id},check,inputs,checkpoint,config,querySelector(selector){if(selector==='input[type=checkbox]')return check;if(selector==='[data-selection=checkpoint]')return checkpoint;if(selector==='[data-selection=config]')return config;return null;},querySelectorAll(selector){if(selector==='[data-option]')return inputs;if(selector==='input,select,textarea')return [check,...inputs,checkpoint,config].filter(Boolean);throw new Error('Unexpected box query '+selector);}};
    boxes.push(box);specs.push({id,label:id,ready:true,import_only:id==='reference',parameters:{fields}});
  }
  nodes.algorithmOptions={querySelectorAll:selector=>{assert.equal(selector,'.algorithm');return boxes;}};
  const datasetForm={active:true,serial:1,loading:false},workspaceUI={taskDialogSession:1},state={specs,pollToken:1,submitting:false};
  const inputBody=()=>({source:nodes.source.value.trim(),view:nodes.view.value,target_fps:Number(nodes.targetFps.value),start_frame:Number(nodes.startFrame.value),max_frames:nodes.maxFrames.value===''?null:Number(nodes.maxFrames.value),camera:nodes.camera.value?JSON.parse(nodes.camera.value):null,execution:{priority:nodes.executionPriority.value,cache:nodes.executionCache.value},...(datasetForm.active?{dataset:{root:nodes.datasetRoot.value,episode:Number(nodes.datasetEpisode.value)}}:{})});
  const selectedAlgorithms=()=>boxes.filter(box=>box.check.checked).map(box=>({algorithm:box.dataset.algorithm,options:Object.fromEntries(box.inputs.filter(field=>field.value!=='').map(field=>[field.dataset.option,field.value])),...(box.checkpoint?.value?{checkpoint:box.checkpoint.value}:{}),...(box.config?.value?{config:box.config.value}:{})}));
  const context=vm.createContext({console,JSON,Map,Set,Number,Math,Object,Array,String,Error,structuredClone,$:id=>nodes[id],state,workspaceUI,datasetForm,inputBody,selectedAlgorithms,comparisonNavigation:()=>[state.pollToken,workspaceUI.taskDialogSession],setDatasetMode:active=>{datasetForm.active=active;datasetForm.serial++;},invalidateComparisonPreflight:()=>effects.push('invalidated'),updateTaskProtocolSummary:()=>effects.push('summary'),syncSourceSelection:()=>effects.push('selection'),prepareSourceBrowser:()=>effects.push('browse'),api:async(...args)=>{calls.push(args);return example();}});
  vm.runInContext(source,context);const run=script=>vm.runInContext(script,context);run('initializeDefaultExample()');
  function example(){return {available:true,id:'arctic-full-gt',dataset_id:'dataset-complete',name:'ARCTIC Validation · 完整677帧',frames:677,fps:30,source:'/prepared/full/video.mp4',camera:{K:[[578,0,336],[0,578,240],[0,0,1]],width:672,height:480,source:'rectified dataset camera'},view:'full',target_fps:0,start_frame:0,max_frames:null,calibration:true,selections:boxes.filter(box=>box.dataset.algorithm!=='reference').map(box=>({algorithm:box.dataset.algorithm,options:{...Object.fromEntries(specs.find(spec=>spec.id===box.dataset.algorithm).parameters.fields.map(field=>[field.name,field.name==='focal'?null:field.default])),...(box.dataset.algorithm.startsWith('hawor')?{mode:'native'}:{})}})),execution:{cache:'exact',priority:'interactive'}};}
  return {nodes,boxes,specs,calls,effects,state,datasetForm,workspaceUI,context,run,example,inputBody,selectedAlgorithms,draft:()=>run('defaultExampleDraftFingerprint()'),payload:()=>({...inputBody(),selections:selectedAlgorithms()})};
}

test('selecting the example fills complete-video defaults and five algorithms without a POST',async()=>{
  const x=fixture();await x.run('loadDefaultExample()');assert.deepEqual(x.calls.map(args=>args[0]),['api/comparison/default-example']);assert.ok(x.calls.every(args=>args.length===1));
  assert.equal(x.nodes.source.value,'/prepared/full/video.mp4');assert.equal(x.nodes.view.value,'full');assert.equal(x.nodes.targetFps.value,'0');assert.equal(x.nodes.startFrame.value,'0');assert.equal(x.nodes.maxFrames.value,'');assert.equal(x.datasetForm.active,false);
  assert.deepEqual(x.selectedAlgorithms().map(row=>row.algorithm),['mint','hawor_full','hawor_camera','ace_kfree','ace_kgiven']);assert.equal(x.boxes[0].checkpoint.value,'');assert.equal(x.boxes[0].config.value,'');
  assert.equal(x.nodes.executionCache.value,'exact');assert.equal(x.nodes.executionPriority.value,'interactive');assert.match(x.nodes.defaultExampleSummary.textContent,/677.*22\.57/);assert.match(x.nodes.defaultExampleSummary.textContent,/官方双手 GT/);assert.match(x.nodes.defaultExampleSummary.textContent,/上限 8 GPU共享/);assert.match(x.nodes.defaultExampleSummary.textContent,/复用已有结果/);assert.equal(x.nodes.useDefaultExample.disabled,false);
});
test('the submission binds the exact native dataset and preserves modified algorithm/execution choices',async()=>{
  const x=fixture();await x.run('loadDefaultExample()');x.boxes[0].inputs.find(n=>n.dataset.option==='max_gpus').value='2';x.boxes[2].check.checked=false;x.nodes.executionCache.value='fresh';
  x.context.payload=x.payload();const submission=clone(x.run('defaultExampleSubmission(payload)'));assert.equal(submission.path,'api/comparison/native/dataset-complete/compare');assert.equal(submission.body.calibration,true);assert.equal(submission.body.selections.length,4);assert.equal(submission.body.selections[0].options.max_gpus,'2');assert.equal(submission.body.execution.cache,'fresh');assert.match(x.nodes.defaultExampleSummary.textContent,/独立重跑/);assert.equal(x.calls.length,1);
});
test('source, frame range, view, camera, FPS and Episode changes clear the GT binding',async()=>{
  for(const field of ['source','startFrame','maxFrames','view','camera','targetFps','dataset']){
    const x=fixture();await x.run('loadDefaultExample()');if(field==='dataset')x.datasetForm.active=true;else if(field==='camera')x.nodes.camera.value='{"K":[[579,0,336],[0,578,240],[0,0,1]],"width":672,"height":480}';else x.nodes[field].value=({source:'/different.mp4',startFrame:'1',maxFrames:'81',view:'left',targetFps:'15'})[field];
    x.context.payload=x.payload();assert.equal(x.run('defaultExampleSubmission(payload)'),null,field);assert.equal(x.nodes.defaultExampleSummary.dataset.state,'changed');assert.match(x.nodes.defaultExampleSummary.textContent,/GT 不再自动附加/);
  }
});
test('formatting camera JSON without changing its values keeps the reference binding',async()=>{
  const x=fixture();await x.run('loadDefaultExample()');const camera=JSON.parse(x.nodes.camera.value);x.nodes.camera.value=JSON.stringify({source:camera.source,height:camera.height,width:camera.width,K:camera.K});x.context.payload=x.payload();assert.ok(x.run('defaultExampleSubmission(payload)'));
});
test('a missing dependency never silently deselects one algorithm or changes the original draft',async()=>{
  const x=fixture();x.specs.find(spec=>spec.id==='ace_kgiven').ready=false;const draft=x.draft();assert.equal(await x.run('loadDefaultExample()'),null);assert.equal(x.draft(),draft);assert.match(x.nodes.defaultExampleSummary.textContent,/未就绪.*ace_kgiven/);assert.equal(x.run('defaultExampleUI.binding'),null);
});
test('a disabled checkbox is also treated as an unavailable required algorithm',async()=>{
  const x=fixture();x.boxes.find(box=>box.dataset.algorithm==='ace_kfree').check.disabled=true;const draft=x.draft();await x.run('loadDefaultExample()');assert.equal(x.draft(),draft);assert.match(x.nodes.defaultExampleSummary.textContent,/未就绪/);
});
test('unavailable, malformed, old short and incompatible presets leave the draft untouched',async()=>{
  const changes=[e=>({available:false,reason:'完整样本尚未准备'}),e=>({...e,frames:81}),e=>({...e,max_frames:81}),e=>({...e,camera:null}),e=>({...e,selections:e.selections.slice(0,4)}),e=>{e.selections[0].options.max_gpus=9;return e;},e=>{e.selections[0].options.mode='unknown';return e;},e=>{e.selections[1].options.missing_field=3;return e;}];
  for(const change of changes){const x=fixture(),draft=x.draft();x.context.api=async()=>change(x.example());await x.run('loadDefaultExample()');assert.equal(x.draft(),draft);assert.equal(x.nodes.defaultExampleSummary.dataset.state,'error');assert.match(x.nodes.defaultExampleSummary.textContent,/原草稿未改动/);assert.equal(x.run('defaultExampleUI.binding'),null);}
});
test('a transport error retains the current form and does not create a job',async()=>{
  const x=fixture(),draft=x.draft();x.context.api=async()=>{throw new Error('network unavailable');};await x.run('loadDefaultExample()');assert.equal(x.draft(),draft);assert.match(x.nodes.defaultExampleSummary.textContent,/network unavailable/);assert.equal(x.nodes.useDefaultExample.disabled,false);
});
test('a late response cannot overwrite edits made while it is loading',async()=>{
  for(const edit of ['source','algorithm','option','checkpoint','execution']){
    const x=fixture(),pending=deferred();x.context.api=()=>pending.promise;const loading=x.run('loadDefaultExample()');assert.equal(x.nodes.useDefaultExample.disabled,true);
    if(edit==='source')x.nodes.source.value='/new/source.mp4';if(edit==='algorithm')x.boxes[1].check.checked=true;if(edit==='option')x.boxes[0].inputs[0].value='chunked';if(edit==='checkpoint')x.boxes[0].checkpoint.value='/new/model';if(edit==='execution')x.nodes.executionCache.value='compatible';
    const draft=x.draft();pending.resolve(x.example());await loading;assert.equal(x.draft(),draft,edit);assert.equal(x.run('defaultExampleUI.binding'),null);assert.match(x.nodes.defaultExampleSummary.textContent,/设置已变化/);assert.equal(x.nodes.useDefaultExample.disabled,false);
  }
});
test('closing/reopening or navigating while loading cannot fill a later dialog session',async()=>{
  for(const action of ['close','reopen','navigation','submit']){
    const x=fixture(),pending=deferred();x.context.api=()=>pending.promise;const draft=x.draft(),loading=x.run('loadDefaultExample()');
    if(action==='close'||action==='reopen'){x.nodes.taskDialog.open=false;x.workspaceUI.taskDialogSession++;x.nodes.taskDialog.dispatch('close');if(action==='reopen'){x.nodes.taskDialog.open=true;x.workspaceUI.taskDialogSession++;}}if(action==='navigation')x.state.pollToken++;if(action==='submit')x.state.submitting=true;
    pending.resolve(x.example());await loading;assert.equal(x.draft(),draft,action);assert.equal(x.run('defaultExampleUI.binding'),null);
  }
});
test('an old request finally block does not unlock or overwrite a newer request',async()=>{
  const x=fixture(),first=deferred(),second=deferred();let count=0;x.context.api=()=>++count===1?first.promise:second.promise;const a=x.run('loadDefaultExample()');x.nodes.taskDialog.open=false;x.nodes.taskDialog.dispatch('close');x.nodes.taskDialog.open=true;x.workspaceUI.taskDialogSession++;const b=x.run('loadDefaultExample()');first.resolve(x.example());await a;assert.equal(x.nodes.useDefaultExample.disabled,true);assert.equal(x.run('defaultExampleUI.loading'),true);second.resolve(x.example());await b;assert.equal(x.nodes.useDefaultExample.disabled,false);assert.ok(x.run('defaultExampleUI.binding'));
});
test('closing a selected example keeps visible instructions to reconfirm the GT on reopening',async()=>{
  const x=fixture();await x.run('loadDefaultExample()');x.nodes.taskDialog.open=false;x.nodes.taskDialog.dispatch('close');x.nodes.taskDialog.open=true;x.run('syncDefaultExample()');assert.equal(x.run('defaultExampleUI.binding'),null);assert.match(x.nodes.defaultExampleSummary.textContent,/重新点击.*官方 GT/);assert.equal(x.nodes.source.value,'/prepared/full/video.mp4');
});
test('an unrelated draft does not claim that it still contains example input after closing',async()=>{
  const x=fixture();await x.run('loadDefaultExample()');x.nodes.source.value='/my/own/video.mp4';x.run('syncDefaultExample()');x.nodes.taskDialog.open=false;x.nodes.taskDialog.dispatch('close');x.nodes.taskDialog.open=true;x.run('syncDefaultExample()');assert.equal(x.nodes.defaultExampleSummary.hidden,true);assert.equal(x.nodes.source.value,'/my/own/video.mp4');
});
test('the initialized button only loads defaults and duplicate clicks do not start another request',async()=>{
  const x=fixture(),pending=deferred();x.context.api=(...args)=>{x.calls.push(args);return pending.promise;};x.nodes.useDefaultExample.dispatch('click');await Promise.resolve();assert.equal(x.calls.length,1);await x.run('loadDefaultExample()');assert.equal(x.calls.length,1);pending.resolve(x.example());await new Promise(resolve=>setImmediate(resolve));assert.ok(x.run('defaultExampleUI.binding'));assert.ok(x.calls.every(args=>args.length===1));
});
