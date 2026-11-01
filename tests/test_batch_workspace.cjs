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
      this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.attributes={};this._value='';this._text=String(text??'');this.checked=false;this.disabled=false;this.hidden=false;this.open=false;this.scrollTop=0;this.className=classes;
      this.classList={add:(...names)=>names.forEach(name=>this._classes.add(name)),contains:name=>this._classes.has(name),toggle:(name,force)=>{const on=force??!this._classes.has(name);if(on)this._classes.add(name);else this._classes.delete(name);return on;}};
    }
    set id(value){this._id=value;nodes[value]=this;}get id(){return this._id;}
    set className(value){this._classes=new Set(String(value||'').split(/\s+/).filter(Boolean));}get className(){return [...this._classes].join(' ');}
    set textContent(value){this._text=String(value??'');this.children=[];}get textContent(){return this._text+this.children.map(child=>child?.textContent??child).join('');}
    set value(value){this._value=String(value??'');}get value(){return this._value;}
    get options(){return this.children.filter(child=>child.tagName==='OPTION');}get lastChild(){return this.children.at(-1)||null;}
    append(...children){for(const child of children){child?.remove?.();this.children.push(child);if(child&&typeof child==='object')child.parentElement=this;}if(this.tagName==='SELECT'&&!this.value&&this.options.length)this.value=this.options[0].value;}
    prepend(...children){for(const child of children)child?.remove?.();this.children.unshift(...children);for(const child of children)if(child&&typeof child==='object')child.parentElement=this;}
    replaceChildren(...children){for(const child of this.children)if(child&&typeof child==='object')child.parentElement=null;this.children=[];this._text='';if(this.tagName==='SELECT')this.value='';this.append(...children);}
    remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(child=>child!==this);this.parentElement=null;}
    before(...children){if(!this.parentElement)return;const parent=this.parentElement,index=parent.children.indexOf(this);for(const child of children)child?.remove?.();parent.children.splice(index,0,...children);for(const child of children)child.parentElement=parent;}
    after(...children){if(!this.parentElement)return;const parent=this.parentElement,index=parent.children.indexOf(this);for(const child of children)child?.remove?.();parent.children.splice(index+1,0,...children);for(const child of children)child.parentElement=parent;}
    setAttribute(name,value){this.attributes[name]=String(value);}removeAttribute(name){delete this.attributes[name];}
    matches(selector){
      if(selector.includes(','))return selector.split(',').some(part=>this.matches(part.trim()));
      const parts=selector.trim().split(/\s+/);if(parts.length>1){const final=parts.pop();if(!this.matches(final))return false;let parent=this.parentElement;for(let index=parts.length-1;index>=0;index--){while(parent&&!parent.matches(parts[index]))parent=parent.parentElement;if(!parent)return false;parent=parent.parentElement;}return true;}
      if(selector.startsWith('#'))return this.id===selector.slice(1);if(selector.startsWith('.'))return this._classes.has(selector.slice(1));
      if(selector==='input[type=checkbox]')return this.tagName==='INPUT'&&this.type==='checkbox';
      const attribute=selector.match(/^\[data-([\w-]+)(?:=([^\]]+))?\]$/);if(attribute){const key=attribute[1].replace(/-([a-z])/g,(_,c)=>c.toUpperCase());return Object.hasOwn(this.dataset,key)&&(attribute[2]===undefined||this.dataset[key]===attribute[2]);}
      return this.tagName===selector.toUpperCase();
    }
    querySelectorAll(selector){return this.children.flatMap(child=>typeof child==='object'?[...(child.matches(selector)?[child]:[]),...child.querySelectorAll(selector)]:[]);}
    querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
    closest(selector){for(let node=this;node;node=node.parentElement)if(node.matches(selector))return node;return null;}
    focus(){this.focused=true;}scrollIntoView(){this.scrolled=true;}close(){this.open=false;}show(){this.open=true;}showModal(){this.open=true;}checkValidity(){return true;}reportValidity(){return true;}
  }
  const create=(tag,text,classes)=>new Element(tag,text,classes),body=create('body');
  const get=id=>{if(nodes[id])return nodes[id];const node=create(/Setup$/.test(id)?'details':'div');node.id=id;body.append(node);return node;};
  const location={href:'http://localhost/compare?hub=batch'};
  const context=vm.createContext({console,URL,URLSearchParams,structuredClone,Number,Node:Element,
    document:{body,hidden:false,addEventListener(){},createTextNode:text=>create('span',text),querySelectorAll:selector=>body.querySelectorAll(selector),querySelector:selector=>selector==='.platform-body'?body:body.querySelector(selector)},
    window:{addEventListener(){},dispatchEvent(){},history:{replaceState(_state,_unused,url){location.href=new URL(url,location.href).href;},pushState(_state,_unused,url){location.href=new URL(url,location.href).href;}}},location,
    requestAnimationFrame:fn=>fn(),CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},
    setTimeout(fn){timers.set(++timer,fn);return timer;},clearTimeout(id){timers.delete(id);},
    $:get,el:create,inputLabel(label,input){const row=create('label',label);row.append(input);return row;},
    selectInput(choices,value){const node=create('select');for(const [key,label]of choices){const option=create('option',label);option.value=key;node.append(option);}node.value=value??choices[0]?.[0]??'';return node;},
    api:async(url,payload)=>{requests.push({url,payload});throw new Error('Unexpected API: '+url);},
    state:{comparison:null},STAGES:{},TERMINAL:new Set(['completed','failed','cancelled','interrupted']),pause(){},notify:error=>errors.push(error),
    openComparison:async id=>opened.push(id),fmt:(value)=>String(value),metricValue:()=>'',
  });
  const run=code=>vm.runInContext(code,context);
  for(const file of ['platform.js','research.js'])run(fs.readFileSync(path.join(__dirname,'../mint/comparison/web',file),'utf8'));
  context.hubError=error=>errors.push(error);context.syncPlatformNavigation=()=>{};context.platformRoute=()=>{};context.refreshPlatformContext=()=>{};context.pollPlatform=async()=>{};
  context.cloneComposerAlgorithms=host=>{host.replaceChildren();const box=create('div',undefined,'algorithm');box.dataset.algorithm='mint';const check=create('input');check.type='checkbox';check.checked=true;const checkpoints=create('textarea');checkpoints.dataset.selection='checkpoint';box.append(check,checkpoints);host.append(box);};
  for(const kind of ['batch','evaluation'])get(kind+'Setup').append(get(kind+'Composer'));
  get('platformDialog').open=true;run("platform.pane='batch';createComposer('batch')");
  return {nodes,get,run,context,Element,create,body,requests,errors,opened,location};
}
function plain(value){return JSON.parse(JSON.stringify(value));}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function video(id='a'){return {source:'/videos/'+id+'.mp4',name:'Video '+id};}
function episode(id=0,gt=true){return {source:'/lerobot/'+id+'.mp4',name:'Episode '+id,dataset:{root:'/lerobot',episode:id,video_key:'ego',attach_gt:true,use_calibration:true},annotation:{ready:gt,camera:true}};}
function selection(x,values){x.run('platform.selection=new Map('+JSON.stringify(values.map((value,index)=>['input:'+index,value]))+');selectionSummary()');}
function preflight(inputs=1,{ready=true}={}){return {ready,errors:ready?[]:['source unavailable'],total_frames:inputs*100,inputs:Array.from({length:inputs},(_,i)=>({source:'/video/'+i,ready,frames:100,view:'full',calibration:false,gt:false,errors:ready?[]:['missing input'],warnings:i===0?['long sequence']:[]}))};}
function collection(id='batch-a',total=2){return {id,kind:'batch',title:'Batch '+id,status:'completed',done:total,total,completed:total,report_available:false,inputs:Array.from({length:total},(_,i)=>({...video(String(i)),name:'Input '+i})),items:Array.from({length:total},(_,i)=>({status:'completed',comparison_id:'comparison-'+i})),selections:[{algorithm:'mint',options:{}}],options:{view:'full',target_fps:0,start_frame:0,max_frames:null}};}
function currentCollection(x,value){x.run('platform.collection='+JSON.stringify(value.id)+';platform.collectionKind='+JSON.stringify(value.kind)+';platform.collectionPage=0;platform.pane='+JSON.stringify(value.kind)+';collectionDraft('+JSON.stringify(value.kind)+').mode="results"');}

test('selected inputs are listed with camera/GT context and removing one clears its source checkbox',async()=>{
  const x=fixture(),host=x.create('div',undefined,'collection-items');x.body.append(host);x.run('platform.selection.clear()');
  x.context.inputHost=host;x.context.inputValue=episode(3);x.run("addInputCheckbox(inputHost,'episode:3',inputValue,'Episode 3','81 frames')");
  const check=host.querySelector('input');check.checked=true;check.onchange();
  assert.equal(x.run('platform.selection.size'),1);assert.match(x.get('batch-input-list').textContent,/Episode 3/);assert.match(x.get('batch-input-list').textContent,/ego/);assert.match(x.get('batch-input-list').textContent,/GT/);
  await x.get('batch-remove-0').onclick();assert.equal(x.run('platform.selection.size'),0);assert.equal(check.checked,false);assert.equal(x.get('batch-submit').disabled,true);
});

test('mixed available-GT policy preserves every input and required-GT rejects unlabelled inputs',()=>{
  const x=fixture();selection(x,[episode(0,true),episode(1,false),video()]);
  const payload=plain(x.run("collectionPayload('batch')"));assert.equal(payload.items.length,3);assert.equal(payload.items[0].dataset.attach_gt,true);assert.equal(payload.items[1].dataset.attach_gt,false);assert.equal(Object.hasOwn(payload.items[2],'dataset'),false);assert.equal(payload.options.max_frames,null);
  x.get('batch-reference').value='required';assert.throws(()=>x.run("collectionPayload('batch')"),/每个输入都有 GT/);
  x.get('batch-reference').value='none';assert.equal(x.run("collectionPayload('batch').items[0].dataset.attach_gt"),false);
});

test('reference-free comparison excludes explicit references as well as Episode GT',()=>{
  const x=fixture(),withReference={...video(),reference:{path:'/gt/reference.npz',format:'normalized_npz'}};selection(x,[withReference]);
  assert.equal(x.run("collectionPayload('batch').items[0].reference.path"),'/gt/reference.npz');x.get('batch-reference').value='none';
  assert.equal(Object.hasOwn(plain(x.run("collectionPayload('batch').items[0]")),'reference'),false);
});

test('calibration modes are exclusive: stale manual JSON is ignored and Episode K stays per input',()=>{
  const x=fixture();selection(x,[episode(),video()]);x.get('batch-camera').value='not JSON from an earlier edit';
  const none=plain(x.run("collectionPayload('batch')"));assert.equal(Object.hasOwn(none.options,'camera'),false);assert.equal(none.items[0].dataset.use_calibration,false);
  x.get('batch-calibration').value='dataset';const dataset=plain(x.run("collectionPayload('batch')"));assert.equal(Object.hasOwn(dataset.options,'camera'),false);assert.equal(dataset.items[0].dataset.use_calibration,true);assert.equal(Object.hasOwn(dataset.items[1],'dataset'),false);assert.equal(Object.hasOwn(dataset.items[1],'camera'),false);
  x.get('batch-calibration').value='manual';assert.throws(()=>x.run("collectionPayload('batch')"),/JSON/);
  const camera={K:[[500,0,320],[0,500,240],[0,0,1]],width:640,height:480,source:'calibrated rig'};x.get('batch-camera').value=JSON.stringify(camera);
  assert.throws(()=>x.run("collectionPayload('batch')"),/适用于全部输入/);x.get('batch-camera-shared').checked=true;
  const manual=plain(x.run("collectionPayload('batch')"));assert.deepEqual(manual.options.camera,camera);assert.equal(manual.items[0].dataset.use_calibration,false);
  x.get('batch-calibration').value='none';x.get('batch-calibration').onchange({target:x.get('batch-calibration')});assert.equal(x.get('batch-camera-shared').checked,false);assert.equal(Object.hasOwn(plain(x.run("collectionPayload('batch')")).options,'camera'),false);
});

test('the first submit only preflights; a second explicit submit sends exactly the reviewed payload',async()=>{
  const x=fixture(),calls=[],opened=[];selection(x,[episode(),video()]);x.context.openCollection=async id=>opened.push(id);
  x.context.api=async(url,payload)=>{calls.push({url,payload:plain(payload)});return url.endsWith('/preflight')?preflight(2):{id:'submitted'};};
  await x.run("submitCollection('batch')");assert.deepEqual(calls.map(call=>call.url),['api/comparison/collections/preflight']);assert.deepEqual(opened,[]);assert.equal(x.run("collectionDraft('batch').step"),'review');assert.match(x.get('batch-preflight-rows').textContent,/long sequence/);
  const reviewed=plain(x.run("collectionDraft('batch').preflight.payload"));await x.run("submitCollection('batch')");
  assert.equal(calls[1].url,'api/comparison/collections');assert.deepEqual(calls[1].payload,reviewed);assert.deepEqual(opened,['submitted']);
});

test('changed settings invalidate a successful review and prevent a stale preflight from unlocking submission',async()=>{
  const x=fixture(),pending=deferred(),calls=[];selection(x,[video()]);x.context.api=(url,payload)=>{calls.push({url,payload:plain(payload)});return pending.promise;};
  const first=x.run("preflightCollection('batch')");await x.run("preflightCollection('batch')");assert.equal(calls.length,1);assert.equal(x.get('batch-submit').disabled,true);
  x.get('batch-max_frames').value='81';x.get('batch-max_frames').oninput({target:x.get('batch-max_frames')});pending.resolve(preflight());await assert.rejects(first,/已变化/);
  assert.equal(x.run("collectionDraft('batch').preflight"),null);assert.equal(x.run("collectionDraft('batch').busy"),false);assert.equal(calls.length,1);
  x.context.api=async()=>preflight();await x.run("preflightCollection('batch')");assert.equal(x.run("collectionDraft('batch').preflight.check.ready"),true);
  x.get('batch-reference').value='none';x.get('batch-reference').onchange({target:x.get('batch-reference')});assert.equal(x.run("collectionDraft('batch').preflight"),null);
});

test('input edits invalidate the reviewed batch and reset acknowledgement for shared manual K',async()=>{
  const x=fixture();selection(x,[video()]);x.get('batch-camera-shared').checked=true;x.context.api=async()=>preflight();await x.run("preflightCollection('batch')");
  selection(x,[video(),video('b')]);assert.equal(x.run("collectionDraft('batch').preflight"),null);assert.equal(x.get('batch-camera-shared').checked,false);assert.match(x.get('batch-input-summary').textContent,/2/);
});

test('empty lists, invalid numeric ranges and excessive checkpoint variants fail before network access',()=>{
  const x=fixture();assert.throws(()=>x.run("collectionPayload('batch')"),/先选择/);selection(x,[video()]);
  for(const [id,value]of [['target_fps',''],['target_fps','-1'],['target_fps','Infinity'],['start_frame',''],['start_frame','0.5'],['start_frame','-1'],['max_frames','0'],['max_frames','2.5']]){
    const field=x.get('batch-'+id),previous=field.value;field.value=value;assert.throws(()=>x.run("collectionPayload('batch')"),/帧范围和 FPS/);field.value=previous;
  }
  const checkpoint=x.get('batch-algorithms').querySelector('[data-selection=checkpoint]');checkpoint.value=Array.from({length:8},(_,i)=>'/weights/'+i).join('\n');assert.throws(()=>x.run("collectionPayload('batch')"),/1–7/);
  checkpoint.value='/weights/0\n/weights/1';assert.equal(x.run("collectionPayload('batch').selections.length"),2);assert.equal(x.requests.length,0);
});

test('a late collection open cannot replace a newer request or navigate back after the user leaves',async()=>{
  const x=fixture(),a=deferred(),b=deferred();x.context.api=url=>url.endsWith('/a')?a.promise:b.promise;
  const first=x.run("openCollection('a')"),second=x.run("openCollection('b')");b.resolve(collection('b'));await second;
  a.resolve({invalid:'superseded result must not be inspected'});assert.equal(await first,null);
  assert.equal(x.run('platform.collection'),'b');assert.match(x.get('collectionTitle').textContent,/Batch b/);assert.equal(x.run("collectionDraft('batch').mode"),'results');
  const pending=deferred();x.context.api=()=>pending.promise;const open=x.run("openCollection('c')");x.run("switchHub('data')");pending.resolve(collection('c'));
  assert.equal(await open,null);assert.equal(x.run('platform.pane'),'data');assert.equal(x.run('platform.collection'),'b');
});

test('closing the workspace while a collection is loading cannot reopen it on a late response',async()=>{
  const x=fixture(),pending=deferred();x.context.api=()=>pending.promise;const open=x.run("openCollection('pending')");
  x.run('closePlatformWorkspace()');assert.equal(x.get('platformDialog').open,false);
  pending.resolve(collection('pending'));assert.equal(await open,null);assert.equal(x.get('platformDialog').open,false);assert.equal(x.run('platform.collection'),null);assert.equal(x.get('collectionDetails').scrolled,undefined);
});

test('a late preflight preserves the user’s current workspace and step instead of moving focus',async()=>{
  const x=fixture(),pending=deferred();selection(x,[video()]);x.run("showCollectionStep('batch','settings')");x.context.api=()=>pending.promise;
  const check=x.run("preflightCollection('batch')");x.run("showCollectionStep('batch','inputs')");pending.resolve(preflight());await check;
  assert.equal(x.run("collectionDraft('batch').step"),'inputs');assert.notEqual(x.get('batch-heading-review').focused,true);assert.equal(x.run("collectionDraft('batch').preflight.check.ready"),true);
  const again=deferred();x.context.api=()=>again.promise;const second=x.run("preflightCollection('batch')");x.run("switchHub('data')");again.resolve(preflight());await second;
  assert.equal(x.run('platform.pane'),'data');assert.equal(x.run("collectionDraft('batch').step"),'inputs');assert.notEqual(x.get('batch-heading-review').focused,true);
});

test('submission carries the reviewed backend receipt; a stale receipt forces a fresh preflight',async()=>{
  const x=fixture(),calls=[],opened=[];selection(x,[video()]);x.context.openCollection=async id=>opened.push(id);let checks=0,submits=0;
  x.context.api=async(url,payload)=>{
    calls.push({url,payload:plain(payload)});if(url.endsWith('/preflight'))return {...preflight(),preflight_id:'receipt-'+(++checks)};
    submits++;if(submits===1){const error=new Error('预检凭据已过期，请重新预检');error.status=400;throw error;}return {id:'submitted'};
  };
  await x.run("submitCollection('batch')");assert.equal(checks,1);await assert.rejects(x.run("submitCollection('batch')"),/凭据已过期/);
  assert.equal(calls[1].payload.preflight_id,'receipt-1');assert.equal(x.run("collectionDraft('batch').preflight"),null);assert.equal(x.run("collectionDraft('batch').busy"),false);assert.deepEqual(opened,[]);assert.match(x.get('batch-validation-message').textContent,/重新预检/);
  await x.run("submitCollection('batch')");assert.equal(checks,2);assert.equal(submits,1);assert.equal(calls[2].url,'api/comparison/collections/preflight');
  await x.run("submitCollection('batch')");assert.equal(calls[3].payload.preflight_id,'receipt-2');assert.equal(submits,2);assert.deepEqual(opened,['submitted']);
});

test('accepted submissions keep a changed draft or step visible while the frozen batch runs in background',async()=>{
  for(const change of ['input','step','unannounced-field']){
    const x=fixture(),pending=deferred(),calls=[],opened=[];selection(x,[video('original')]);x.context.openCollection=async id=>opened.push(id);
    x.context.api=async(url,payload)=>{calls.push({url,payload:plain(payload)});return url.endsWith('/preflight')?{...preflight(),preflight_id:'reviewed-receipt'}:pending.promise;};
    await x.run("preflightCollection('batch')");const submit=x.run("submitCollection('batch')");assert.equal(calls.length,2);
    if(change==='input')selection(x,[video('new-draft')]);
    else if(change==='step')x.run("showCollectionStep('batch','settings')");
    else x.get('batch-max_frames').value='81';
    pending.resolve({id:'accepted-original'});await submit;
    assert.deepEqual(opened,[],change);assert.equal(x.run('platform.pane'),'batch',change);assert.equal(x.get('batchSetup').open,true,change);assert.equal(x.run('platform.collection'),null,change);
    assert.match(x.get('batch-validation-message').textContent,/后台继续运行/,change);assert.equal(x.run("collectionDraft('batch').busy"),false,change);
    assert.equal(calls[1].payload.items[0].source,'/videos/original.mp4',change);assert.equal(calls[1].payload.options.max_frames,null,change);assert.equal(calls[1].payload.preflight_id,'reviewed-receipt',change);
    if(change==='input')assert.match(x.get('batch-input-list').textContent,/new-draft/);
    if(change==='step')assert.equal(x.run("collectionDraft('batch').step"),'settings');
    if(change==='unannounced-field')assert.equal(x.get('batch-max_frames').value,'81');
  }
});

test('result rows retain every error, distinguish terminal outcomes and expose all inputs beyond 200',async()=>{
  const x=fixture(),value=collection('large',257);value.status='partial';value.items[2]={status:'partial',error:'Detector warning',errors:['Detector warning','SLAM failed']};value.items[203]={status:'failed',errors:['Out of memory','Fallback unavailable']};value.items[1]={status:'cancelled'};value.items[3]={status:'interrupted'};
  value.counts={completed:253,partial:1,failed:1,cancelled:1,interrupted:1,queued:0,running:0};currentCollection(x,value);
  const seen=[];for(let page=0;page<6;page++){x.run('platform.collectionPage='+page);await x.context.renderCollection(value);const names=x.get('collectionChildren').querySelectorAll('strong').map(node=>node.textContent);assert.ok(names.length<=50);seen.push(...names);}
  assert.equal(seen.length,257);assert.equal(new Set(seen).size,257);assert.ok(seen.some(name=>name==='257 · Input 256'));assert.equal(x.get('collectionResultNext').disabled,true);assert.equal(x.get('collectionResultPrevious').disabled,false);
  assert.match(x.get('collectionResultSummary').textContent,/完整完成 253/);assert.match(x.get('collectionResultSummary').textContent,/部分结果 1/);assert.match(x.get('collectionResultSummary').textContent,/失败 1/);assert.match(x.get('collectionResultSummary').textContent,/已取消 1/);assert.match(x.get('collectionResultSummary').textContent,/已中断 1/);
  x.get('collectionResultFilter').value='SLAM';await x.context.renderCollection(value);assert.equal(x.get('collectionChildren').querySelectorAll('strong').length,1);assert.match(x.get('collectionChildren').textContent,/Detector warning；SLAM failed/);assert.match(x.get('collectionResultPage').textContent,/1–1 \/ 1/);
  x.get('collectionResultFilter').value='fallback';await x.context.renderCollection(value);assert.match(x.get('collectionChildren').textContent,/204 · Input 203/);assert.match(x.get('collectionChildren').textContent,/Out of memory；Fallback unavailable/);
});

test('result protocol shows the frozen submitted inputs and stays separate from an edited draft',async()=>{
  const x=fixture(),value=collection('frozen');value.inputs[0]=episode(9,true);value.inputs[0].dataset.use_calibration=true;value.options={view:'full',target_fps:24,start_frame:15,max_frames:10428};currentCollection(x,value);
  await x.context.renderCollection(value);assert.match(x.get('collectionFrozenFields').textContent,/10428/);assert.match(x.get('collectionFrozenFields').textContent,/15/);assert.match(x.get('collectionFrozenFields').textContent,/24/);assert.match(x.get('collectionFrozenFields').textContent,/Episode 使用自身标定/);
  selection(x,[video('new-input')]);x.get('batch-max_frames').value='81';x.run("setCollectionMode('batch','draft')");assert.equal(x.get('collectionDetails').hidden,true);assert.equal(x.get('batchSetup').open,true);
  x.run("setCollectionMode('batch','results')");await x.context.renderCollection(value);assert.equal(x.get('collectionDetails').hidden,false);assert.equal(x.get('batchSetup').open,false);assert.match(x.get('collectionFrozenFields').textContent,/10428/);assert.doesNotMatch(x.get('collectionChildren').textContent,/new-input/);
});

test('a stale collection refresh cannot replace a newer update or a newer collection',async()=>{
  const x=fixture(),original=collection('a'),a=deferred(),b=deferred();currentCollection(x,original);let calls=0;x.context.api=()=>++calls===1?a.promise:b.promise;
  const first=x.run('refreshCollection()'),second=x.run('refreshCollection()');const latest=collection('a');latest.title='Latest status';b.resolve(latest);await second;
  a.resolve({invalid:'old refresh ignored'});await first;assert.match(x.get('collectionTitle').textContent,/Latest status/);
  const pending=deferred();x.context.api=()=>pending.promise;const third=x.run('refreshCollection()');x.run("platform.collection='other'");pending.resolve({invalid:'old collection ignored'});await third;assert.match(x.get('collectionTitle').textContent,/Latest status/);
});

test('a late report cannot replace the selected collection or scroll the user back to old results',async()=>{
  const x=fixture(),value=collection('a'),report=deferred(),started=deferred();value.report_available=true;
  x.context.api=url=>{if(url.endsWith('/report')){started.resolve();return report.promise;}return Promise.resolve(value);};
  const opening=x.run("openCollection('a')");await started.promise;x.run("platform.collection='b';switchHub('data')");
  report.resolve({rows:[],protocol:{name:'Old protocol'}});await opening;
  assert.equal(x.run('platform.collection'),'b');assert.equal(x.run('platform.report'),null);assert.equal(x.get('collectionDetails').scrolled,undefined);assert.equal(x.run('platform.pane'),'data');
});

test('resuming the same batch invalidates its pending old report and clears previous metrics',async()=>{
  const x=fixture(),finished=collection('same'),pendingReport=deferred(),reportStarted=deferred();finished.status='partial';finished.report_available=true;currentCollection(x,finished);
  const running={...finished,status:'running',report_available:false};x.get('evaluationResults').textContent='Previously displayed metrics';
  x.context.api=async url=>{if(url.endsWith('/report')){reportStarted.resolve();return pendingReport.promise;}assert.ok(url.endsWith('/same/resume'));return running;};
  const render=x.context.renderCollection(finished);await reportStarted.promise;await x.run("collectionAction('resume')");
  assert.equal(x.get('evaluationResults').textContent,'');assert.equal(x.run('platform.report'),null);assert.equal(x.run('platform.collectionValue.status'),'running');
  pendingReport.resolve({protocol:{name:'Old protocol'},total_sequences:2,scorable_sequences:2,rows:[{label:'Obsolete metrics',scored_sequences:2,failed_sequences:0,gt_geometry_coverage:.5}]});await render;
  assert.equal(x.run('platform.report'),null);assert.doesNotMatch(x.get('evaluationResults').textContent,/Obsolete metrics/);assert.equal(x.get('resumeCollection').disabled,true);assert.equal(x.get('cancelCollection').disabled,false);
});

test('collection actions are single-flight and a late reply cannot replace a different batch',async()=>{
  const x=fixture(),value=collection('running');value.status='running';currentCollection(x,value);await x.context.renderCollection(value);
  const pending=deferred(),calls=[];x.context.api=(url,payload)=>{calls.push({url,payload});return pending.promise;};
  const action=x.run("collectionAction('cancel')");await x.run("collectionAction('cancel')");assert.equal(calls.length,1);assert.equal(x.get('cancelCollection').disabled,true);
  x.run("platform.collection='newer'");pending.resolve({invalid:'reply for old collection'});await action;assert.equal(x.run('platform.collection'),'newer');assert.equal(x.run('platform.collectionAction'),null);
});
