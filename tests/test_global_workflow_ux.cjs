'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function fixture(){
  const nodes={},requests=[],errors=[],opened=[],timers=new Map();let timer=0;
  class Element {
    constructor(tag='div',text='',classes=''){
      this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.attributes={};this._value='';this._text=String(text??'');this.checked=false;this.disabled=false;this.hidden=false;this.open=false;this.scrollTop=0;this.style={};this.className=classes;
      this.classList={remove:(...names)=>names.forEach(name=>this._classes.delete(name)),add:(...names)=>names.forEach(name=>this._classes.add(name)),contains:name=>this._classes.has(name),toggle:(name,force)=>{const on=force??!this._classes.has(name);if(on)this._classes.add(name);else this._classes.delete(name);return on;}};
    }
    set id(value){this._id=value;nodes[value]=this;}get id(){return this._id;}
    set className(value){this._classes=new Set(String(value||'').split(/\s+/).filter(Boolean));}get className(){return [...this._classes].join(' ');}
    set textContent(value){this._text=String(value??'');this.children=[];}get textContent(){return this._text+this.children.map(child=>child?.textContent??child).join('');}
    set value(value){this._value=String(value??'');}get value(){return this._value;}
    get selectedOptions(){return this.options.filter(option=>this.multiple?option.selected:option.value===this.value);}get options(){return this.children.filter(child=>child.tagName==='OPTION');}get lastChild(){return this.children.at(-1)||null;}get nextElementSibling(){return this.parentElement?.children[this.parentElement.children.indexOf(this)+1]||null;}
    append(...children){for(const child of children){child?.remove?.();this.children.push(child);if(child&&typeof child==='object')child.parentElement=this;}if(this.tagName==='SELECT'&&!this.value&&this.options.length)this.value=this.options[0].value;}
    prepend(...children){for(const child of children)child?.remove?.();this.children.unshift(...children);for(const child of children)if(child&&typeof child==='object')child.parentElement=this;}
    replaceChildren(...children){for(const child of this.children)if(child&&typeof child==='object')child.parentElement=null;this.children=[];this._text='';if(this.tagName==='SELECT')this.value='';this.append(...children);}
    remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(child=>child!==this);this.parentElement=null;}
    before(...children){if(!this.parentElement)return;const parent=this.parentElement,index=parent.children.indexOf(this);for(const child of children)child?.remove?.();parent.children.splice(index,0,...children);for(const child of children)child.parentElement=parent;}
    after(...children){if(!this.parentElement)return;const parent=this.parentElement,index=parent.children.indexOf(this);for(const child of children)child?.remove?.();parent.children.splice(index+1,0,...children);for(const child of children)child.parentElement=parent;}
    setAttribute(name,value){this.attributes[name]=String(value);if(name==='value')this.value=value;if(name==='type')this.type=value;if(['checked','disabled','hidden','open'].includes(name))this[name]=true;}getAttribute(name){return this.attributes[name]??null;}removeAttribute(name){delete this.attributes[name];}
    matches(selector){
      if(selector.includes(','))return selector.split(',').some(part=>this.matches(part.trim()));
      const parts=selector.trim().split(/\s+/);if(parts.length>1){const final=parts.pop();if(!this.matches(final))return false;let parent=this.parentElement;for(let index=parts.length-1;index>=0;index--){while(parent&&!parent.matches(parts[index]))parent=parent.parentElement;if(!parent)return false;parent=parent.parentElement;}return true;}
      if(selector.startsWith('#'))return this.id===selector.slice(1);if(selector.startsWith('.'))return this._classes.has(selector.slice(1));
      if(selector==='input[type=checkbox]')return this.tagName==='INPUT'&&this.type==='checkbox';
      const attribute=selector.match(/^\[data-([\w-]+)(?:=([^\]]+))?\]$/);if(attribute){const key=attribute[1].replace(/-([a-z])/g,(_,c)=>c.toUpperCase()),value=attribute[2]?.replace(/^['\"]|['\"]$/g,'');return Object.hasOwn(this.dataset,key)&&(value===undefined||this.dataset[key]===value);}
      return this.tagName===selector.toUpperCase();
    }
    querySelectorAll(selector){return this.children.flatMap(child=>typeof child==='object'?[...(child.matches(selector)?[child]:[]),...child.querySelectorAll(selector)]:[]);}
    querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
    closest(selector){for(let node=this;node;node=node.parentElement)if(node.matches(selector))return node;return null;}
    addEventListener(type,fn){this.listeners??={};this.listeners[type]=fn;}focus(){this.focused=true;}scrollIntoView(){this.scrolled=true;}close(){this.open=false;}show(){this.open=true;}showModal(){this.open=true;}checkValidity(){return true;}reportValidity(){return true;}pause(){this.paused=true;}load(){}
  }
  const create=(tag,text,classes)=>new Element(tag,text,classes),body=create('body');
  const get=id=>{if(nodes[id])return nodes[id];const node=create(/Setup$/.test(id)?'details':'div');node.id=id;body.append(node);return node;};
  const location={href:'http://localhost/compare?hub=research'};
  const context=vm.createContext({console,URL,URLSearchParams,structuredClone,Number,Node:Element,
    document:{body,hidden:false,addEventListener(){},createTextNode:text=>create('span',text),querySelectorAll:selector=>body.querySelectorAll(selector),querySelector:selector=>selector==='.platform-body'?body:body.querySelector(selector)},
    window:{addEventListener(){},dispatchEvent(){},history:{replaceState(_state,_unused,url){location.href=new URL(url,location.href).href;},pushState(_state,_unused,url){location.href=new URL(url,location.href).href;}}},location,
    requestAnimationFrame:fn=>fn(),CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},
    setTimeout(fn){timers.set(++timer,fn);return timer;},clearTimeout(id){timers.delete(id);},
    $:get,el:create,icon:name=>create('span',name),inputLabel(label,input){const row=create('label',label);row.append(input);return row;},
    selectInput(choices,value){const node=create('select');for(const [key,label]of choices){const option=create('option',label);option.value=key;node.append(option);}node.value=value??choices[0]?.[0]??'';return node;},
    api:async(url,payload)=>{requests.push({url,payload});throw new Error('Unexpected API: '+url);},
    state:{comparison:null},STAGES:{},TERMINAL:new Set(['completed','failed','cancelled','interrupted']),pause(){},notify:error=>errors.push(error),
    openComparison:async id=>opened.push(id),fmt:(value)=>String(value),metricValue:()=>'',statusLabel:value=>value,
  });
  const run=code=>vm.runInContext(code,context);
  for(const file of ['platform.js','research.js','enhancements.js'])run(fs.readFileSync(path.join(__dirname,'../mint/comparison/web',file),'utf8'));
  const realRefreshPlatformContext=context.refreshPlatformContext;
  context.hubError=error=>errors.push(error);context.syncPlatformNavigation=()=>{};context.platformRoute=()=>{};context.refreshPlatformContext=()=>{};context.pollPlatform=async()=>{};
  get('platformDialog').open=true;get('taskDialog').open=true;run("platform.pane='research'");
  return {nodes,get,run,context,Element,create,body,requests,errors,opened,location,realRefreshPlatformContext};
}

function plain(value){return JSON.parse(JSON.stringify(value));}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function fillProduction(x,run='source-a'){x.get('productionRuns').value=run;x.get('productionSplit').value='train';x.get('productionAcceleration').value='30';}
function fillStudy(x){for(const [id,value]of Object.entries({studyCheckpoint:'',studyConfig:'',suiteName:'suite',suiteSplit:'test',studyTolerance:'0.1',suiteProtocol:'protocol',studyGPUs:'1,2'}))x.get(id).value=value;x.context.nextChoices=()=>[{algorithm:'mint',options:{}}];}

test('late production preflight cannot mark edited inputs ready',async()=>{
  const x=fixture(),pending=deferred();fillProduction(x);x.context.api=()=>pending.promise;
  const request=x.run('checkProductionReadiness()');x.get('productionRuns').value='source-b';x.run('invalidateProductionReadiness()');pending.resolve({ready:true,errors:[]});assert.equal(await request,null);
  assert.equal(x.get('productionReadiness').dataset.ready,'pending');assert.equal(x.run('nextUI.productionReadySignature'),null);
});
test('production does not submit a stale preflight snapshot or duplicate a submission',async()=>{
  const x=fixture(),pending=deferred(),calls=[];fillProduction(x);x.context.api=async(url,body)=>{calls.push({url,body});return pending.promise;};
  const request=x.run('startProduction()');await x.run('startProduction()');x.get('productionSplit').value='validation';pending.resolve({ready:true,errors:[]});await assert.rejects(request,/参数已变化/);assert.equal(calls.length,1);assert.match(calls[0].url,/preflight$/);
});
test('a completed production submission preserves a later selected workflow',async()=>{
  const x=fixture(),pending=deferred(),inspected=[],notices=[];fillProduction(x);x.context.checkProductionReadiness=async()=>({ready:true});x.context.api=(_url,body)=>{x.context.sent=body;return pending.promise;};x.context.inspectWorkflow=async id=>inspected.push(id);x.context.nextUIMessage=message=>notices.push(message);
  x.run("researchUI.workflow='old'");const request=x.run('startProduction()');await Promise.resolve();x.run("researchUI.workflow='new'");pending.resolve({id:'submitted'});await request;
  assert.deepEqual(inspected,[]);assert.equal(x.run('researchUI.workflow'),'new');assert.equal(x.context.sent.parameters.run_ids[0],'source-a');assert.ok(notices.length);
});
test('training submission uses the checked snapshot and cannot replace a new inspected task',async()=>{
  const x=fixture(),pending=deferred(),posted=deferred(),inspected=[];x.context.workflowParameters=()=>({config:x.get('workflowConfig').value});x.get('workflowKind').value='train';x.get('workflowName').value='training-a';x.get('workflowConfig').value='/config-a';x.context.checkWorkflowReadiness=async()=>({ready:true});x.context.api=(_url,body)=>{x.context.sent=body;posted.resolve();return pending.promise;};x.context.inspectWorkflow=async id=>inspected.push(id);
  x.run("researchUI.workflow='old'");const request=x.run('submitWorkflow()');await posted.promise;x.get('workflowConfig').value='/config-b';x.run("researchUI.workflow='new'");pending.resolve({id:'created'});await request;
  assert.equal(x.context.sent.parameters.config,'/config-a');assert.deepEqual(inspected,[]);assert.equal(x.run('researchUI.workflow'),'new');
});
test('study submission is single-flight and late creation cannot replace a selected study',async()=>{
  const x=fixture(),pending=deferred();fillStudy(x);let posted=0,polled=0;x.context.api=()=>{posted++;return pending.promise;};x.context.pollNext=async()=>polled++;
  x.run("nextUI.study='old'");const request=x.run("startStudy('regression')");await x.run("startStudy('regression')");x.run("nextUI.study='new'");pending.resolve({id:'created'});await request;
  assert.equal(posted,1);assert.equal(polled,0);assert.equal(x.run('nextUI.study'),'new');assert.equal(x.run('nextUI.studySubmitting'),false);
});
test('study polling ignores a previous selection and does not poll from other workspaces',async()=>{
  const x=fixture(),first=deferred(),second=deferred(),shown=[];x.context.pollNativePreparation=async()=>{};x.context.showStudy=value=>shown.push(value.id);x.context.api=url=>url.endsWith('/old')?first.promise:second.promise;
  x.run("nextUI.study='old'");const old=x.run('pollNext()');await Promise.resolve();x.run("nextUI.study='new'");const current=x.run('pollNext()');await Promise.resolve();second.resolve({id:'new'});await current;first.resolve({id:'old'});await old;assert.deepEqual(shown,['new']);
  x.run("platform.pane='data'");let calls=0;x.context.api=async()=>{calls++;return {};};await x.run('pollNext()');assert.equal(calls,0);
});
test('registry refresh preserves an unavailable selection visibly instead of silently using defaults',async()=>{
  const x=fixture();x.get('workflowDataset').value='missing-dataset';x.context.api=async()=>({records:[{id:'another',name:'Another dataset'}]});await x.run("loadRegistryChoices('dataset','workflowDataset')");
  assert.equal(x.get('workflowDataset').value,'missing-dataset');const missing=x.get('workflowDataset').options.find(option=>option.value==='missing-dataset');assert.equal(missing.disabled,true);assert.match(missing.textContent,/已不可用/);
});
test('a late production comparison response cannot repopulate a newly selected source',async()=>{
  const x=fixture(),a=deferred(),b=deferred();x.get('productionRunPicker').multiple=true;x.get('productionComparisonPicker').value='a';x.context.api=url=>url.endsWith('/a')?a.promise:b.promise;
  const old=x.run('refreshProductionRunChoices()');assert.equal(x.get('productionRunPicker').disabled,true);x.get('productionComparisonPicker').value='b';const current=x.run('refreshProductionRunChoices()');b.resolve({id:'b',runs:[{id:'run-b',algorithm:'mint',status:'completed',label:'MINT',result:{frames:12}}]});await current;a.resolve({id:'a',runs:[{id:'run-a',algorithm:'mint',status:'completed',label:'MINT',result:{frames:12}}]});assert.equal(await old,null);
  assert.equal(x.run('nextUI.productionComparison.id'),'b');assert.ok(x.get('productionRunPicker').options.some(option=>option.value==='run-b'));assert.equal(x.get('productionRunPicker').options.some(option=>option.value==='run-a'),false);assert.equal(x.get('productionRunPicker').disabled,false);
});
test('research list refresh ignores older history and keeps the selected initialization model',async()=>{
  const x=fixture(),first=deferred(),second=deferred();let call=0;x.get('workflowInitPicker').value='model-b';x.context.syncTrainingWorkflowPicker=()=>{};x.context.refreshProductionRunChoices=async()=>{};x.context.api=()=>{const which=call++<3?first:second;return which.promise;};
  const old=x.run('refreshResearchRecordPickers()'),current=x.run('refreshResearchRecordPickers()');second.resolve({comparisons:[],tasks:[],records:[{id:'model-b',name:'B',path:'/b'}]});await current;first.resolve({comparisons:[],tasks:[],records:[{id:'model-a',name:'A',path:'/a'}]});assert.equal(await old,null);
  assert.equal(x.run('nextUI.researchModels[0].id'),'model-b');assert.equal(x.get('workflowInitPicker').value,'model-b');
});
test('model registration and technical output are collapsed and publishing needs complete valid choices',()=>{
  const x=fixture();x.run("initializeModelPanel($('hub-research'))");assert.equal(x.get('modelRegistrationDetails').open,false);assert.equal(x.get('modelStatus').parentElement.open,false);assert.equal(x.get('modelPromote').disabled,true);
  x.get('registeredModel').value='model';x.get('modelEvaluation').value='evaluation';x.run('updateModelActions()');assert.equal(x.get('modelPromote').disabled,false);assert.match(x.get('modelGateSummary').textContent,/95\.0%/);
  x.get('modelCoverage').value='';x.run('updateModelActions()');assert.equal(x.get('modelPromote').disabled,true);x.get('modelCoverage').value='1.1';x.run('updateModelActions()');assert.equal(x.get('modelPromote').disabled,true);
});
test('a promotion reply cannot label a different current alias as updated',async()=>{
  const x=fixture(),pending=deferred();x.run("initializeModelPanel($('hub-research'))");x.get('registeredModel').value='model-a';x.get('modelEvaluation').value='evaluation-a';x.context.api=()=>pending.promise;const request=x.run('promoteModel()');x.get('modelAlias').value='new-alias';pending.resolve({alias:'candidate'});await request;
  assert.equal(x.get('modelAlias').value,'new-alias');assert.doesNotMatch(x.get('modelActionSummary').textContent,/已更新/);assert.ok(x.errors.some(message=>String(message).includes('当前表单选择已保留')));
});
test('comparison preflight sends an immutable snapshot and edited forms cannot publish old readiness',async()=>{
  const x=fixture(),pending=deferred();x.context.comparisonDraftPayload=()=>({source:x.get('source').value,selections:[{algorithm:'mint'}]});x.context.comparisonDraftSignature=payload=>JSON.stringify(payload||x.context.comparisonDraftPayload());x.get('source').value='/a.mp4';let sent;x.context.api=(_url,body)=>{sent=body;return pending.promise;};
  const request=x.run('preflightComparison()');assert.equal(x.get('checkInputPreflight').disabled,true);x.get('source').value='/b.mp4';pending.resolve({ready:true});await assert.rejects(request,/输入或算法参数已变化/);assert.equal(sent.source,'/a.mp4');assert.equal(x.get('inputPreflight').dataset.ready,'pending');assert.equal(x.get('checkInputPreflight').disabled,false);
});
test('comparison preflight suppresses results after leaving its dialog',async()=>{
  const x=fixture(),pending=deferred();x.context.comparisonDraftPayload=()=>({source:'/a'});x.context.comparisonDraftSignature=payload=>JSON.stringify(payload||x.context.comparisonDraftPayload());x.context.api=()=>pending.promise;const request=x.run('preflightComparison()');x.get('taskDialog').open=false;pending.resolve({ready:true});const value=await request;assert.equal(value.ready,true);assert.equal(x.get('inputPreflight').dataset.ready,'pending');
});
test('training dataset handoff does not overwrite a newer manual dataset choice',async()=>{
  const x=fixture(),pending=deferred(),started=deferred();x.context.openPlatform=async()=>{};x.context.updateWorkflowFields=()=>{};x.context.refreshWorkflowDatasetChoices=()=>{started.resolve();return pending.promise;};x.get('workflowDataset').value='initial';const request=x.run("useTrainingDataset({id:'incoming',training_ready:true})");await started.promise;x.get('workflowDataset').value='manually-selected';pending.resolve({});assert.equal(await request,null);assert.equal(x.get('workflowDataset').value,'manually-selected');
});

test('fixed protocol evaluation includes published predictions with role metadata and excludes declared GT',async()=>{
  const x=fixture();x.context.updateWorkflowFields=()=>{};x.context.loadRegistryChoices=async()=>{};x.run("initializeResearch($('hub-research'))");x.context.state.comparison={id:'comparison',runs:[{id:'prediction',algorithm:'mint',result:{reference_role:'model_prediction'}},{id:'derived',algorithm:'reference',result:{reference_role:'algorithm_derived'}},{id:'ground',algorithm:'reference',result:{reference_role:'user_declared_ground_truth'}},{id:'unfinished',algorithm:'mint',result:null}]};let submitted;x.context.api=async(url,body)=>{assert.match(url,/registry\/evaluation$/);submitted=body;return {id:'report'};};
  const button=x.body.querySelectorAll('button').find(node=>node.textContent==='评测当前比较的预测结果');await button.onclick();assert.deepEqual(plain(submitted.run_ids),['prediction','derived']);
});
test('an unavailable model stays visible but cannot be promoted',async()=>{
  const x=fixture();x.run("initializeModelPanel($('hub-research'))");x.get('registeredModel').value='missing-model';x.get('modelEvaluation').value='report';x.context.api=async()=>({records:[{id:'missing-model',name:'Missing',availability:'missing_or_changed'}]});await x.run("loadRegistryChoices('model','registeredModel')");x.run('updateModelActions()');assert.equal(x.get('registeredModel').value,'missing-model');assert.equal(x.get('registeredModel').options.find(option=>option.value==='missing-model').disabled,true);assert.equal(x.get('modelPromote').disabled,true);
});
test('choosing the current production source works even outside the first history page',async()=>{
  const x=fixture();fillProduction(x);x.context.state.comparison={id:'older-comparison',title:'Old source',runs:[{id:'old-result',label:'MINT',algorithm:'mint',status:'completed',result:{}}]};x.context.refreshProductionRunChoices=async()=>{x.run('nextUI.productionComparison=state.comparison');};await x.run('chooseCurrentProductionRuns()');assert.equal(x.get('productionComparisonPicker').value,'older-comparison');assert.ok(x.get('productionComparisonPicker').options.some(option=>option.value==='older-comparison'));assert.equal(x.get('productionRuns').value,'old-result');
});

test('a current comparison preflight failure releases the button and replaces the loading message',async()=>{
  const x=fixture();x.context.comparisonDraftPayload=()=>({source:'/a'});x.context.comparisonDraftSignature=payload=>JSON.stringify(payload||x.context.comparisonDraftPayload());x.context.api=async()=>{throw new Error('source unavailable');};await assert.rejects(x.run('preflightComparison()'),/source unavailable/);assert.equal(x.get('inputPreflight').dataset.ready,'false');assert.match(x.get('inputPreflight').textContent,/source unavailable/);assert.equal(x.get('checkInputPreflight').disabled,false);
});

test('explicit workflow opening records its owner while polling cannot redirect a later research tab',async()=>{
  const x=fixture(),pending=deferred();x.context.api=()=>pending.promise;x.context.renderWorkflowNextSteps=async()=>{};x.location.href='http://localhost/compare?hub=research&study=previous&research_tab=regression';const request=x.run("inspectWorkflow('task-a',{navigate:true})");let query=new URL(x.location.href).searchParams;assert.equal(query.get('workflow'),'task-a');assert.equal(query.get('research_tab'),'train');assert.equal(query.has('study'),false);
  x.run("showResearchView('models')");const newer=x.location.href;pending.resolve({id:'task-a',request:{kind:'train'},status:'completed',progress:{}});await request;assert.equal(x.location.href,newer);assert.equal(new URL(x.location.href).searchParams.get('research_tab'),'models');
});
test('train observations and reopening research do not steal an independently selected curve owner',async()=>{
  const x=fixture(),events={},synced=[];for(const name of ['nativePanel','initializeDataViews','qualityExtensions','initializeQualityWorkspace','studyPanel','curvePanel','productionPanel','researchNavigation'])x.context[name]=()=>{};x.context.window.addEventListener=(name,fn)=>{events[name]=fn;};x.context.syncTrainingWorkflowPicker=value=>synced.push(value.id);x.context.loadRegistryChoices=async()=>{};x.context.pollNext=async()=>{};
  x.run("nextUI.curve='curve-b';researchUI.workflow='task-a'");x.get('curveWorkflow').value='curve-b';x.location.href='http://localhost/compare?hub=research&workflow=curve-b&research_tab=curves';x.run('initializeEnhancements()');const href=x.location.href;events['mint-workflow-inspected']({detail:{id:'task-a',request:{kind:'train'}}});events['mint-platform-open']({detail:'research'});await Promise.resolve();
  assert.deepEqual(synced,['task-a']);assert.equal(x.run('nextUI.curve'),'curve-b');assert.equal(x.get('curveWorkflow').value,'curve-b');assert.equal(x.location.href,href);
});
test('curve selection owns a stable deep link, preserves the inspected task and respects route restoration',async()=>{
  const x=fixture(),reads=[];x.context.drawTrainingCurve=()=>{};x.context.refreshCurves=async()=>{reads.push(x.run('nextUI.curve'));return {owner:x.run('nextUI.curve')};};x.run("researchUI.workflow='task-a'");const selected=await x.run("selectTrainingCurve('curve-b')");assert.equal(selected.owner,'curve-b');assert.equal(x.run('researchUI.workflow'),'task-a');let query=new URL(x.location.href).searchParams;assert.equal(query.get('workflow'),'curve-b');assert.equal(query.get('research_tab'),'curves');assert.equal(x.get('curveWorkflowPicker').value,'curve-b');
  const href=x.location.href;x.run('platformNavigation.restoring=true');await x.run("selectTrainingCurve('curve-c',{route:false})");assert.equal(x.location.href,href);assert.deepEqual(reads,['curve-b','curve-c']);x.run('platformNavigation.restoring=false');
});
test('created studies publish their own URL once and periodic observations leave navigation untouched',async()=>{
  const x=fixture();fillStudy(x);const realPoll=x.context.pollNext;x.context.pollNext=async()=>{};x.context.api=async()=>({id:'created-study'});x.location.href='http://localhost/compare?hub=research&workflow=old-training';await x.run("startStudy('regression')");const query=new URL(x.location.href).searchParams;assert.equal(query.get('study'),'created-study');assert.equal(query.get('research_tab'),'regression');assert.equal(query.has('workflow'),false);const href=x.location.href;
  x.context.pollNext=realPoll;x.context.pollNativePreparation=async()=>{};x.context.showStudy=()=>{};x.context.api=async()=>({id:'created-study'});await x.run('pollNext()');assert.equal(x.location.href,href);
});

test('the visible workflow summary identifies the selected submitted task and ignores an old reply',async()=>{
  const x=fixture(),old=deferred(),current=deferred();x.context.renderWorkflowNextSteps=async()=>{};x.context.api=url=>url.endsWith('/task-a')?old.promise:current.promise;const first=x.run("inspectWorkflow('task-a')"),second=x.run("inspectWorkflow('task-b')");current.resolve({id:'task-b',request:{kind:'train',name:'Training B'},status:'failed',progress:{stage:'training',done:4,total:10,unit:'optimizer_steps'},error:'GPU allocation unavailable\nprivate technical trace'});await second;assert.match(x.get('workflowSummary').textContent,/Training B.*failed.*4\/10 步.*task-b.*GPU allocation unavailable/);assert.doesNotMatch(x.get('workflowSummary').textContent,/private technical trace/);assert.match(x.get('workflowSummary').textContent,/配置草稿与此任务分别管理/);const summary=x.get('workflowSummary').textContent;old.resolve({id:'task-a',request:{kind:'train',name:'Training A'},status:'running',progress:{}});assert.equal(await first,null);assert.equal(x.get('workflowSummary').textContent,summary);
});

test('research subviews bind their own object URL and show comparison context only where it is needed',()=>{
  const x=fixture();x.context.refreshPlatformContext=x.realRefreshPlatformContext;x.context.state.comparison={id:'main',title:'Main comparison',runs:[]};x.run("researchUI.workflow='train-owner';nextUI.curve='curve-owner';nextUI.study='study-owner';showResearchView('train')");let query=new URL(x.location.href).searchParams;assert.equal(query.get('workflow'),'train-owner');assert.equal(x.get('platformCurrentContext').hidden,true);
  x.run("showResearchView('curves')");query=new URL(x.location.href).searchParams;assert.equal(query.get('workflow'),'curve-owner');assert.equal(x.get('platformCurrentContext').hidden,true);
  x.run("showResearchView('regression')");query=new URL(x.location.href).searchParams;assert.equal(query.get('study'),'study-owner');assert.equal(query.has('workflow'),false);assert.equal(x.get('platformCurrentContext').hidden,false);
  x.run("showResearchView('models')");query=new URL(x.location.href).searchParams;assert.equal(query.has('workflow')||query.has('study'),false);assert.equal(x.get('platformCurrentContext').hidden,true);
});
test('research section navigation retains its bookmark while explicit tab changes clear it',()=>{
  const x=fixture();x.location.href='http://localhost/compare?hub=research&section=fixed-protocol';x.run("showResearchView('protocol',{clearSection:false})");assert.equal(new URL(x.location.href).searchParams.get('section'),'fixed-protocol');x.run("showResearchView('models',{clearSection:true})");assert.equal(new URL(x.location.href).searchParams.has('section'),false);
});

test('research header action follows its selected view and only opens configuration',()=>{
  const x=fixture();for(const [view,label,target] of [['models','登记检查点','modelName'],['regression','配置回归','suiteProtocol'],['protocol','配置固定评测','offlineProtocol'],['production','配置伪标签生产','productionComparisonPicker']]){
    x.run('showResearchView('+JSON.stringify(view)+')');assert.equal(x.get('newComparison').getAttribute('aria-label'),label);x.run('activatePlatformPrimaryAction()');assert.equal(x.get(target).focused,true);assert.equal(x.run('nextUI.researchView'),view);
  }
  x.run("showResearchView('curves')");assert.equal(x.get('newComparison').hidden,true);
  assert.equal(x.get('modelRegistrationDetails').open,true);assert.deepEqual(x.requests,[]);
});
