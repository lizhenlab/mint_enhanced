'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function fixture(){
  const nodes={},opened=[],requests=[],timers=new Map();let timer=0;
  class Element {
    constructor(tag='div',text='',className=''){
      this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.attributes={};this._value='';this._text=String(text??'');this.checked=false;this.disabled=false;this.hidden=false;this.open=false;this._classes=new Set();this.className=className;
      this.classList={add:(...names)=>names.forEach(name=>this._classes.add(name)),toggle:(name,force)=>{const on=force===undefined?!this._classes.has(name):force;if(on)this._classes.add(name);else this._classes.delete(name);return on;},contains:name=>this._classes.has(name)};
    }
    set id(value){this._id=value;nodes[value]=this;}get id(){return this._id;}
    set className(value){this._classes=new Set(String(value||'').split(/\s+/).filter(Boolean));}get className(){return [...this._classes].join(' ');}
    set textContent(value){this._text=String(value??'');this.children=[];}get textContent(){return this._text+this.children.map(value=>value?.textContent??value).join('');}
    set value(value){this._value=String(value??'');}get value(){return this._value;}
    get options(){return this.children.filter(value=>value.tagName==='OPTION');}
    append(...values){for(const value of values){this.children.push(value);if(value&&typeof value==='object')value.parentElement=this;}if(this.tagName==='SELECT'&&this.options.length&&this.value==='')this.value=this.options[0].value;}
    prepend(...values){this.children.unshift(...values);for(const value of values)if(value&&typeof value==='object')value.parentElement=this;}
    replaceChildren(...values){this.children=[];this._text='';if(this.tagName==='SELECT')this.value='';this.append(...values);}
    setAttribute(name,value){this.attributes[name]=String(value);}
    matches(selector){if(selector.startsWith('.'))return this._classes.has(selector.slice(1));if(selector==='input[type=checkbox]')return this.tagName==='INPUT'&&this.type==='checkbox';if(selector==='[data-option]')return Object.hasOwn(this.dataset,'option');if(selector==='[data-selection=checkpoint]')return this.dataset.selection==='checkpoint';if(selector==='[data-selection=config]')return this.dataset.selection==='config';return selector.toUpperCase()===this.tagName;}
    querySelectorAll(selector){return this.children.flatMap(value=>typeof value==='object'?[...(value.matches(selector)?[value]:[]),...value.querySelectorAll(selector)]:[]);}
    querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
    closest(selector){for(let node=this;node;node=node.parentElement)if(node.matches(selector))return node;return null;}
    close(){this.open=false;}focus(){this.focused=true;}
  }
  const create=(tag,text,className)=>new Element(tag,text,className);
  const get=id=>nodes[id]??=create(/Dataset$|Split$|Group$|Sequence$|Version$|Calibration$/.test(id)?'select':'div');
  const context=vm.createContext({console,URL,URLSearchParams,structuredClone,Node:Element,Number,
    document:{addEventListener(){},querySelectorAll(){return [];},querySelector(){return create('div');},createTextNode:text=>create('span',text)},
    window:{addEventListener(){}},setTimeout(fn){timers.set(++timer,fn);return timer;},clearTimeout(id){timers.delete(id);},
    $:get,el:create,inputLabel(label,input){const field=create('label',label);field.append(input);return field;},
    selectInput(choices,value){const select=create('select');for(const [key,label]of choices){const option=create('option',label);option.value=key;select.append(option);}select.value=value??choices[0]?.[0]??'';return select;},
    api:async(url,body)=>{requests.push({url,body});throw new Error('Unexpected request: '+url);},hubError(error){throw error;},
    state:{comparison:null},TERMINAL:new Set(['completed','failed','cancelled','interrupted']),STAGES:{},statusLabel:value=>value,
    openComparison:async id=>opened.push(id),
  });
  const run=code=>vm.runInContext(code,context);
  for(const name of ['platform.js','research.js','enhancements.js'])run(fs.readFileSync(path.join(__dirname,'../mint/comparison/web',name),'utf8'),{filename:name});
  context.cloneComposerAlgorithms=host=>{
    host.replaceChildren();delete host.dataset.waiting;
    for(const id of ['mint','ace_kgiven']){const box=create('div',undefined,'algorithm');box.dataset.algorithm=id;const check=create('input');check.type='checkbox';check.checked=id==='mint';box.append(check);host.append(box);}
  };
  get('hub-data');run('nativePanel()');
  return {context,nodes,get,run,opened,requests,timers,Element};
}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function json(value){return JSON.parse(JSON.stringify(value));}
function version(id='prepared',dataset='egodex',split='test'){
  return {id,name:'Prepared '+id,training_ready:split==='train',native:{plan:{dataset,split,sequence:split+'/action/0'},frames:81,native_frame_indices:Array.from({length:81},(_,i)=>i+10),details:{label_role:'declared_reference'},source:'/owned/'+id+'/video.mp4'}};
}
function source(x,{dataset='arctic',split='val',sequence='s05/box'}={}){
  x.run('nextUI.catalog='+JSON.stringify([{adapter:dataset,dataset,path:'/dataset/'+dataset,splits:['train','val','test'],training_export:true}])+';nextUI.nativeSequences=[]');
  x.run("nextOptions('nativeDataset',[['0','Dataset']])");
  x.get('nativeDataset').value='0';x.get('nativeRoot').value='/dataset/'+dataset;x.get('nativeSplit').value=split;x.get('nativeSequence').value=sequence;x.get('nativeStart').value='0';x.get('nativeMaximum').value='';
  x.run('nextUI.nativeSequenceReady=nativeSequenceContext();updateNativeDraft()');
}
function selectVersion(x,row=version()){
  x.run('nextUI.nativeVersions='+JSON.stringify([row]));x.run('nextOptions("nativeVersion",'+JSON.stringify([[row.id,row.name]])+')');x.get('nativeVersion').value=row.id;x.get('nativeVersion').onchange();
}
function chooseAlgorithm(x,id,checked){x.get('nativeAlgorithms').querySelectorAll('.algorithm').find(box=>box.dataset.algorithm===id).querySelector('input[type=checkbox]').checked=checked;x.get('nativeAlgorithms').onchange();}

test('the native source starts with an explicit unknown-intrinsics protocol and one visible step',()=>{
  const x=fixture();assert.equal(x.get('nativeCalibration').value,'unknown');
  assert.equal(x.get('nativeStep-source').hidden,false);assert.equal(x.get('nativeStep-prepare').hidden,true);assert.equal(x.get('nativeStep-compare').hidden,true);
  assert.equal(x.get('nativeCompare').disabled,true);assert.equal(x.get('nativePrepare').disabled,true);
  x.get('nativeExisting').onclick();assert.equal(x.get('nativeStep-source').hidden,true);assert.equal(x.get('nativeStep-compare').hidden,false);
});

test('editing the draft clears the selected comparison version until explicitly reselected',()=>{
  const x=fixture();source(x);selectVersion(x);assert.equal(x.get('nativeCompare').disabled,false);
  x.get('nativeMaximum').value='25';x.get('nativeMaximum').oninput();
  assert.equal(x.get('nativeVersion').value,'');assert.equal(x.get('nativeCompare').disabled,true);assert.equal(x.get('nativeVersionWarning').hidden,false);
  x.get('nativeVersion').value='prepared';x.get('nativeVersion').onchange();assert.equal(x.get('nativeVersionWarning').hidden,true);assert.equal(x.get('nativeCompare').disabled,false);
  x.get('nativeRoot').value='/other';x.get('nativeRoot').oninput();assert.equal(x.get('nativeVersion').value,'');assert.equal(x.get('nativeSequence').disabled,true);assert.equal(x.get('nativePrepare').disabled,true);
});

test('missing references cannot be selected or bypassed for preparation; ARCTIC has no irrelevant group',async()=>{
  const x=fixture();source(x);x.context.api=async()=>({groups:[],sequences:[{sequence:'valid',name:'Valid',reference_available:true},{sequence:'missing',name:'Missing',reference_available:false,reference_issue:'缺少标注'}],offset:0,total:2});
  await x.run('loadNativeSequences()');assert.equal(x.get('nativeGroup').closest('label').hidden,true);assert.equal(x.get('nativeSequencePages').hidden,true);
  assert.equal(x.get('nativeSequence').options.find(option=>option.value==='missing').disabled,true);
  x.get('nativeSequence').value='missing';assert.throws(()=>x.run('nativeImportParameters()'),/缺少标注/);
  x.get('nativeSequence').value='valid';x.get('nativeSequence').onchange();assert.equal(x.get('nativePrepare').disabled,false);
});

test('official validation data cannot enable training export; full-video selection remains unbounded',()=>{
  const x=fixture();source(x);assert.equal(x.get('nativeTraining').disabled,true);assert.equal(x.run('nativeImportParameters().max_frames'),null);
  x.get('nativeSplit').value='train';x.run('nextUI.nativeSequenceReady=nativeSequenceContext();updateNativeDraft()');assert.equal(x.get('nativeTraining').disabled,false);
  x.get('nativeMaximum').value='10428';assert.equal(x.run('nativeImportParameters().max_frames'),10428);
});

test('ACE K-given is blocked without known K, then sends a boolean calibrated protocol',async()=>{
  const x=fixture();selectVersion(x);chooseAlgorithm(x,'ace_kgiven',true);
  assert.equal(x.get('nativeCompare').disabled,true);await assert.rejects(x.run('compareNative()'),/需要提供已知内参/);
  assert.equal(x.requests.length,0);x.get('nativeCalibration').value='known';x.get('nativeCalibration').onchange();assert.equal(x.get('nativeCompare').disabled,false);
  const calls=[];x.context.api=async(url,body)=>{calls.push({url,body:json(body)});return {id:'new-comparison'};};
  await x.run('compareNative()');assert.equal(calls.length,1);assert.equal(calls[0].body.calibration,true);assert.equal(typeof calls[0].body.calibration,'boolean');assert.deepEqual(x.opened,['new-comparison']);
});

test('comparison uses the explicitly selected prepared sample rather than unprepared draft fields',async()=>{
  const x=fixture();source(x,{dataset:'arctic',sequence:'s05/new'});selectVersion(x,version('older-ego','egodex'));
  assert.match(x.get('nativeSampleSummary').textContent,/test\/action\/0/);assert.match(x.get('nativeSampleSummary').textContent,/81/);
  const calls=[];x.context.api=async(url,body)=>{calls.push({url,body:json(body)});return {id:'result'};};await x.run('compareNative()');
  assert.equal(calls[0].url,'api/comparison/native/older-ego/compare');assert.equal(calls[0].body.calibration,false);assert.equal(Object.hasOwn(calls[0].body,'sequence'),false);
  x.get('nativeVersion').value='not-in-registry';await assert.rejects(x.run('compareNative()'),/明确选择/);assert.equal(calls.length,1);
});

test('preparation rejects a changed frame range and never double-submits during preflight',async()=>{
  const x=fixture();source(x);const pending=deferred(),calls=[];x.context.api=(url,body)=>{calls.push({url,body:json(body)});return pending.promise;};
  const first=x.run('prepareNative()');assert.equal(x.get('nativePrepare').disabled,true);await x.run('prepareNative()');assert.equal(calls.length,1);
  x.get('nativeMaximum').value='20';x.get('nativeMaximum').oninput();pending.resolve({ready:true});await assert.rejects(first,/参数已变化/);
  assert.equal(calls.length,1);assert.equal(x.get('nativePrepare').disabled,false);assert.equal(x.run('nextUI.nativeJob'),null);
});

test('completing matching preparation selects its published sample and advances to comparison',async()=>{
  const x=fixture(),row=version('published','arctic','val');source(x);x.run("nextUI.nativeJob='job';nextUI.nativeJobDraft={fingerprint:nativeDraftFingerprint(),choice:nextUI.nativeVersionChoice||0}");
  x.context.api=async url=>url.includes('/workflows/')?{status:'completed',progress:{stage:'publish'},output:{dataset_id:row.id}}:{records:[row]};
  await x.run('pollNativePreparation()');assert.equal(x.get('nativeVersion').value,'published');assert.equal(x.run('nextUI.nativeStep'),'compare');assert.equal(x.get('nativeCompare').disabled,false);assert.equal(x.run('nextUI.nativeJob'),null);
});

test('a completed preparation survives a failed registry refresh and recovers on the next poll',async()=>{
  const x=fixture(),row=version('published','arctic','val'),calls=[];source(x);
  x.run("nextUI.nativeJob='job';nextUI.nativeJobDraft={fingerprint:nativeDraftFingerprint(),choice:nextUI.nativeVersionChoice||0};updateNativeDraft()");
  const originalDraft=json(x.run('nextUI.nativeJobDraft'));let registryCalls=0;
  x.context.api=async url=>{calls.push(url);if(url.includes('/workflows/'))return {status:'completed',progress:{stage:'publish'},output:{dataset_id:row.id}};if(++registryCalls===1)throw new Error('Temporary registry failure');return {records:[row]};};
  await assert.rejects(x.run('pollNativePreparation()'),/Temporary registry failure/);
  assert.equal(x.run('nextUI.nativeJob'),'job');assert.deepEqual(json(x.run('nextUI.nativeJobDraft')),originalDraft);
  assert.equal(x.get('nativePrepare').disabled,true);assert.equal(x.get('nativeVersion').value,'');
  await x.run('pollNativePreparation()');
  assert.equal(registryCalls,2);assert.equal(calls.filter(url=>url.includes('/workflows/')).length,2);
  assert.equal(x.get('nativeVersion').value,row.id);assert.equal(x.get('nativeCompare').disabled,false);assert.equal(x.run('nextUI.nativeStep'),'compare');
  assert.equal(x.run('nextUI.nativeJob'),null);assert.equal(x.run('nextUI.nativeJobDraft'),null);assert.equal(x.get('nativePrepare').disabled,false);
});

test('an overlapping registry refresh keeps completed preparation tracked until a current refresh succeeds',async()=>{
  const x=fixture(),row=version('published','arctic','val'),first=deferred(),second=deferred(),started=deferred();source(x);
  x.run("nextUI.nativeJob='job';nextUI.nativeJobDraft={fingerprint:nativeDraftFingerprint(),choice:nextUI.nativeVersionChoice||0};updateNativeDraft()");
  let registryCalls=0;x.context.api=async url=>{
    if(url.includes('/workflows/'))return {status:'completed',progress:{},output:{dataset_id:row.id}};
    registryCalls++;if(registryCalls===1){started.resolve();return first.promise;}if(registryCalls===2)return second.promise;return {records:[row]};
  };
  const poll=x.run('pollNativePreparation()');await started.promise;
  const newerRefresh=x.run('refreshNativeVersions()');second.resolve({records:[row]});await newerRefresh;
  first.resolve({invalid:'The superseded registry result must not be applied'});await poll;
  assert.equal(x.run('nextUI.nativeJob'),'job');assert.equal(x.get('nativePrepare').disabled,true);assert.equal(x.get('nativeVersion').value,'');assert.equal(x.run('nextUI.nativeStep'),'source');
  await x.run('pollNativePreparation()');assert.equal(registryCalls,3);assert.equal(x.get('nativeVersion').value,row.id);assert.equal(x.run('nextUI.nativeJob'),null);assert.equal(x.get('nativeCompare').disabled,false);
});

test('completion cannot overwrite edits made to the source while preparation runs',async()=>{
  const x=fixture(),row=version('published');source(x);x.run("nextUI.nativeJob='job';nextUI.nativeJobDraft={fingerprint:nativeDraftFingerprint(),choice:nextUI.nativeVersionChoice||0}");
  const done=deferred();x.context.api=async url=>url.includes('/workflows/')?done.promise:{records:[row]};const poll=x.run('pollNativePreparation()');
  x.get('nativeStart').value='10';x.get('nativeStart').oninput();done.resolve({status:'completed',progress:{},output:{dataset_id:row.id}});await poll;
  assert.equal(x.get('nativeVersion').value,'');assert.equal(x.run('nextUI.nativeStep'),'source');assert.match(x.get('nativeProgress').textContent,/当前选择未被替换/);
});

test('completion cannot replace an explicitly reused sample, including during registry refresh',async()=>{
  const x=fixture(),old=version('old'),fresh=version('fresh');source(x);selectVersion(x,old);x.run("nextUI.nativeJob='job';nextUI.nativeJobDraft={fingerprint:nativeDraftFingerprint(),choice:nextUI.nativeVersionChoice||0}");
  const registry=deferred();x.context.api=async url=>url.includes('/workflows/')?{status:'completed',progress:{},output:{dataset_id:fresh.id}}:registry.promise;
  const poll=x.run('pollNativePreparation()');await Promise.resolve();x.get('nativeVersion').onchange();registry.resolve({records:[old,fresh]});await poll;
  assert.equal(x.get('nativeVersion').value,'old');assert.equal(x.run('nextUI.nativeStep'),'source');assert.match(x.get('nativeSampleSummary').textContent,/Prepared old/);
});

test('late sequence responses cannot publish references from a superseded root',async()=>{
  const x=fixture();source(x);const first=deferred(),second=deferred();let calls=0;x.context.api=()=>++calls===1?first.promise:second.promise;
  const a=x.run('loadNativeSequences()');x.get('nativeRoot').value='/new/root';x.get('nativeRoot').oninput();const b=x.run('loadNativeSequences()');
  second.resolve({groups:[],sequences:[{sequence:'new',name:'New',reference_available:true}],offset:0,total:1});await b;
  first.resolve({invalid:'obsolete payload must never be read'});await a;assert.deepEqual(x.get('nativeSequence').options.map(option=>option.value),['','new']);
  x.get('nativeSequence').value='new';assert.equal(x.run('nativeImportParameters().root'),'/new/root');
});

test('late inventory and workflow responses cannot replace newer selections',async()=>{
  const x=fixture(),request=deferred();x.context.api=()=>request.promise;
  const catalog=x.run('loadNativeCatalog()');x.get('nativeCatalogRoot').value='/another';x.get('nativeCatalogRoot').oninput();request.resolve({invalid:'stale'});await catalog;assert.equal(x.run('nextUI.catalog.length'),0);
  const status=deferred();x.context.api=()=>status.promise;x.run("nextUI.nativeJob='old-job'");const poll=x.run('pollNativePreparation()');x.run("nextUI.nativeJob='new-job'");status.resolve({invalid:'stale'});await poll;
  assert.equal(x.run('nextUI.nativeJob'),'new-job');assert.equal(x.get('nativeVersion').value,'');
});
