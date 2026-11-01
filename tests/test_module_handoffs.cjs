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

function researchConfiguration(x){
  x.context.openPlatform=async pane=>{x.run('platform.pane='+JSON.stringify(pane)+';platform.serial++');x.get('platformDialog').open=true;};
  x.context.revealPlatformTarget=node=>{node.revealed=true;};
  x.context.updateWorkflowFields=()=>x.run('invalidateWorkflowReadiness()');
}
function catalog(x,kind='dataset'){
  x.run("platform.pane='catalog'");x.get('registryKind').value=kind;x.get('registryQuery').value='';
}
function mintComposer(x){
  const box=x.create('div');box.dataset.algorithm='mint';const check=x.create('input');check.type='checkbox';const details=x.create('details');
  for(const kind of ['checkpoint','config']){const input=x.create('input');input.dataset.selection=kind;details.append(input);}
  box.append(check,details);x.get('algorithmOptions').append(box);x.context.openTaskDialog=()=>{x.get('taskDialog').open=true;};return box;
}

test('catalog routes each actual dataset type to its available next configuration',async()=>{
  const x=fixture();catalog(x);x.context.api=async()=>({records:[
    {id:'native-val',kind:'dataset',name:'Official validation',status:'native_prepared',native:{},training_ready:false},
    {id:'native-train',kind:'dataset',name:'Official training',status:'native_prepared',native:{},training_ready:true},
    {id:'pseudo',kind:'dataset',name:'Pseudo',status:'native_pseudo_labels',training_ready:true},
    {id:'reviewed',kind:'dataset',name:'Accepted intervals',status:'reviewed_manifest',training_ready:false},
    {id:'unavailable',kind:'dataset',name:'Changed dataset',training_ready:true,availability:'missing_or_changed'}
  ],offset:0,total:5});await x.run('refreshArtifactCatalog()');
  const rows=x.get('artifactRows').querySelectorAll('tr').slice(1).map(row=>row.querySelectorAll('button').map(button=>button.textContent));
  assert.deepEqual(rows,[['来源详情','用此样本配置比较'],['来源详情','用此样本配置比较','用此版本配置训练'],['来源详情','用此版本配置训练'],['来源详情','配置复核数据导出'],['来源详情']]);
});

test('catalog protocol and native evaluation expose supported configuration entries',async()=>{
  const x=fixture();catalog(x,'protocol');x.context.api=async()=>({records:[{id:'frozen',kind:'protocol'}],offset:0,total:1});await x.run('refreshArtifactCatalog()');
  assert.deepEqual(x.get('artifactRows').querySelectorAll('button').map(button=>button.textContent),['来源详情','配置离线评测','配置固定集回归']);
  x.get('registryKind').value='evaluation';x.context.api=async()=>({records:[{id:'native',kind:'evaluation',protocol_id:'frozen',rows:[{origin:'native',model:{path:'/model'}}]},{id:'imported',kind:'evaluation',protocol_id:'frozen',rows:[{origin:'imported',model:{path:'/model'}}]}],offset:0,total:2});await x.run('refreshArtifactCatalog()');
  const rows=x.get('artifactRows').querySelectorAll('tr').slice(1).map(row=>row.querySelectorAll('button').map(button=>button.textContent));
  assert.deepEqual(rows,[['来源详情','使用所属协议','配置模型发布'],['来源详情','使用所属协议']]);
});

test('changing catalog kind removes old actions immediately and only the latest response can publish rows',async()=>{
  const x=fixture();catalog(x);x.context.api=async()=>({records:[{id:'dataset-old',kind:'dataset',name:'Old dataset',training_ready:true}],offset:30,total:90});await x.run('refreshArtifactCatalog()');assert.ok(x.get('artifactRows').querySelectorAll('button').some(button=>button.textContent==='用此版本配置训练'));
  const old=deferred(),fresh=deferred();x.context.api=url=>url.includes('/protocol?')?fresh.promise:old.promise;const outdated=x.run('refreshArtifactCatalog()');x.get('registryKind').value='protocol';const current=x.run('refreshArtifactCatalog()');
  assert.equal(x.get('artifactRows').querySelectorAll('button').length,0);assert.match(x.get('artifactRows').textContent,/正在读取/);assert.equal(x.get('artifactRows').getAttribute('aria-busy'),'true');assert.equal(x.get('registryPrevious').disabled,true);assert.equal(x.get('registryNext').disabled,true);
  fresh.resolve({records:[{id:'protocol-new',kind:'protocol',name:'New protocol'}],offset:0,total:1});await current;assert.equal(x.get('artifactRows').getAttribute('aria-busy'),'false');assert.match(x.get('artifactRows').textContent,/New protocol/);const rendered=x.get('artifactRows').textContent;
  old.resolve({records:[{id:'late-dataset',kind:'dataset',name:'Late dataset',training_ready:true}],offset:30,total:90});assert.equal(await outdated,null);assert.equal(x.get('artifactRows').textContent,rendered);assert.equal(x.get('registryPrevious').disabled,true);assert.equal(x.get('registryNext').disabled,true);
});

test('catalog failures offer retry only for the active request and late errors do not reach another page',async()=>{
  const x=fixture();catalog(x,'protocol');x.context.api=async()=>{throw new Error('Registry temporarily unavailable');};assert.equal(await x.run('refreshArtifactCatalog()'),null);
  assert.match(x.get('artifactRows').textContent,/读取失败，请重试.*Registry temporarily unavailable/);assert.equal(x.get('artifactRows').getAttribute('aria-busy'),'false');assert.equal(x.get('registryPrevious').disabled,true);assert.equal(x.get('registryNext').disabled,true);const retry=x.get('artifactRows').querySelector('button');assert.equal(retry.textContent,'重试读取');
  x.context.api=async()=>({records:[{id:'recovered',kind:'protocol',name:'Recovered protocol'}],offset:0,total:1});await retry.onclick();assert.match(x.get('artifactRows').textContent,/Recovered protocol/);assert.doesNotMatch(x.get('artifactRows').textContent,/Registry temporarily unavailable/);
  const stale=deferred(),latest=deferred();let calls=0;x.context.api=()=>calls++?latest.promise:stale.promise;const old=x.run('refreshArtifactCatalog()'),current=x.run('refreshArtifactCatalog()');stale.reject(new Error('Old request failed'));assert.equal(await old,null);assert.equal(x.get('artifactRows').getAttribute('aria-busy'),'true');assert.doesNotMatch(x.get('artifactRows').textContent,/Old request failed/);latest.resolve({records:[],offset:0,total:0});await current;
  const departed=deferred();x.context.api=()=>departed.promise;const leaving=x.run('refreshArtifactCatalog()');x.run("platform.serial++;platform.pane='tools'");x.get('platformError').textContent='';departed.reject(new Error('Late network failure'));assert.equal(await leaving,null);assert.equal(x.get('platformError').textContent,'');assert.deepEqual(x.errors,[]);
});

test('review publishing leaves a concrete export configuration action and never launches a workflow',async()=>{
  const x=fixture();catalog(x,'review');x.get('datasetVersionName').value='Accepted camera clips';x.run("researchUI.accepted.add('review-one')");const sent=[];
  x.context.api=async(url,payload)=>{sent.push({url,payload});return {id:'curation',kind:'dataset',status:'reviewed_manifest',entries:[{review_id:'review-one'}]};};
  await x.run('publishReviewedDataset()');assert.equal(sent.length,1);assert.match(sent[0].url,/registry\/dataset$/);assert.deepEqual(plain(sent[0].payload.review_ids),['review-one']);
  assert.match(x.get('datasetVersionStatus').textContent,/尚未转换为训练格式/);let selected;x.context.useReviewedDataset=async value=>{selected=value;};
  await x.get('datasetVersionStatus').querySelector('button').onclick();assert.equal(selected.id,'curation');assert.equal(sent.length,1);
});

test('review curation and pseudo training handoffs choose the record beyond the first list page without submitting',async()=>{
  const x=fixture();researchConfiguration(x);const calls=[];x.context.api=async(url,payload)=>{calls.push({url,payload});return {records:[]};};
  await x.run("useReviewedDataset({id:'old-curation',status:'reviewed_manifest',training_ready:false})");
  assert.equal(x.get('workflowKind').value,'curate_dataset');assert.equal(x.get('workflowDataset').value,'old-curation');assert.equal(x.get('workflowSubmit').disabled,true);assert.match(x.get('workflowCheck').textContent,/先预检/);
  await x.run("useTrainingDataset({id:'pseudo-version',training_ready:true,label_role:'model_generated_pseudo_labels',requires_training_opt_in:true})");
  assert.equal(x.get('workflowKind').value,'train');assert.equal(x.get('workflowDataset').value,'pseudo-version');assert.equal(x.get('workflowPseudoOptIn').checked,false);assert.match(x.get('workflowCheck').textContent,/明确勾选/);assert.ok(calls.every(call=>call.payload===undefined));
  await x.run("useTrainingDataset({id:'pseudo-version',training_ready:true,label_role:'model_generated_pseudo_labels'})");assert.equal(x.get('workflowDataset').value,'pseudo-version');assert.equal(x.get('workflowDataset').selectedOptions[0].disabled,false);
});

test('native dataset reuse selects the compare step and updates the actual sample without inference',async()=>{
  const x=fixture();researchConfiguration(x);x.context.showDataSource=kind=>x.run('nextUI.dataSource='+JSON.stringify(kind));x.context.refreshNativeVersions=async()=>{x.run('nextUI.nativeVersions=[]');};
  const updates=[];x.context.updateNativeSample=()=>updates.push(x.get('nativeVersion').value);x.context.showNativeStep=step=>x.run('nextUI.nativeStep='+JSON.stringify(step));
  await x.run("useNativeDataset({id:'older-native',kind:'dataset',native:{source:'/prepared.mp4'},training_ready:false})");
  assert.equal(x.run('platform.pane'),'data');assert.equal(x.run('nextUI.dataSource'),'native');assert.equal(x.run('nextUI.nativeStep'),'compare');assert.equal(x.get('nativeVersion').value,'older-native');assert.equal(x.run('nativeSelectedVersion().id'),'older-native');assert.deepEqual(updates,['older-native']);assert.deepEqual(x.requests,[]);
});

test('native handoff cannot replace a later manually selected sample',async()=>{
  const x=fixture(),pending=deferred(),started=deferred();researchConfiguration(x);x.context.showDataSource=kind=>x.run('nextUI.dataSource='+JSON.stringify(kind));x.context.refreshNativeVersions=()=>{started.resolve();return pending.promise;};
  const request=x.run("useNativeDataset({id:'incoming',native:{}})");await started.promise;x.get('nativeVersion').value='manual';x.run('nextUI.nativeVersionChoice=1');pending.resolve();assert.equal(await request,null);assert.equal(x.get('nativeVersion').value,'manual');
});

test('saved protocol and report handoffs select explicit records and preserve a newer manual choice',async()=>{
  const x=fixture();researchConfiguration(x);x.context.api=async()=>({records:[]});
  await x.run("useSavedProtocol({id:'protocol-old',name:'Frozen validation',entries:[{split:'validation'}]},'regression')");assert.equal(x.run('nextUI.researchView'),'regression');assert.equal(x.get('suiteProtocol').value,'protocol-old');assert.equal(x.get('suiteSplit').value,'validation');
  await x.run("useSavedProtocol({id:'protocol-old',name:'Frozen validation',entries:[{split:'validation'}]})");assert.equal(x.run('nextUI.researchView'),'protocol');assert.equal(x.get('offlineProtocol').value,'protocol-old');assert.equal(x.get('offlineSplit').value,'validation');
  x.run("initializeModelPanel($('hub-research'))");await x.run("useEvaluationReport({id:'report-old',kind:'evaluation',rows:[]})");assert.equal(x.run('nextUI.researchView'),'models');assert.equal(x.get('modelEvaluation').value,'report-old');assert.equal(x.get('modelPromote').disabled,true);
  const pending=deferred(),started=deferred();x.context.loadRegistryChoices=()=>{started.resolve();return pending.promise;};const request=x.run("useSavedProtocol({id:'incoming'})");await started.promise;x.get('offlineProtocol').value='manual-protocol';pending.resolve();assert.equal(await request,null);assert.equal(x.get('offlineProtocol').value,'manual-protocol');assert.deepEqual(x.requests,[]);
});

test('protocol handoff explains whether the current sample belongs to its selected evaluation split',async()=>{
  const x=fixture();researchConfiguration(x);x.context.api=async()=>({records:[]});x.context.state.comparison={id:'egodex-comparison',sample_id:'egodex-sample'};
  const protocol={id:'arctic-protocol',name:'ARCTIC Validation',entries:[{sample_id:'arctic-sample',split:'validation'},{sample_id:'egodex-sample',split:'test'}]};x.context.selectedProtocol=protocol;x.get('offlineSplit').value='validation';
  await x.run('useSavedProtocol(selectedProtocol)');assert.match(x.get('fixedEvaluationStatus').textContent,/当前比较不属于此协议划分；请先打开协议对应输入，或使用固定集回归处理全部输入/);assert.deepEqual(x.opened,[]);
  x.context.state.comparison={id:'arctic-comparison',sample_id:'arctic-sample'};await x.run('useSavedProtocol(selectedProtocol)');assert.match(x.get('fixedEvaluationStatus').textContent,/仅处理当前比较已有的预测；评测协议全部输入请使用固定集回归/);assert.doesNotMatch(x.get('fixedEvaluationStatus').textContent,/不属于此协议划分/);assert.deepEqual(x.opened,[]);
});

test('a saved result opens its parent and inspects the requested run only while navigation still belongs to it',async()=>{
  const x=fixture();catalog(x,'artifact');const inspected=[];x.context.inspectResult=id=>inspected.push(id);x.context.api=async()=>({comparisons:['parent']});x.context.openComparison=async id=>{x.opened.push(id);x.context.state.comparison={id};};
  await x.run("openCatalogResult({run_id:'run'})");assert.deepEqual(x.opened,['parent']);assert.deepEqual(inspected,['run']);
  catalog(x,'artifact');x.get('platformDialog').open=true;const pending=deferred();x.context.api=()=>pending.promise;const request=x.run("openCatalogResult({run_id:'late-run'})");x.run("platform.serial++;platform.pane='tools'");pending.resolve({comparisons:['late-parent']});assert.equal(await request,null);assert.deepEqual(x.opened,['parent']);assert.deepEqual(inspected,['run']);
});

test('an imported catalog result does not pull the user back after a later page change',async()=>{
  const x=fixture(),pending=deferred();catalog(x,'artifact');x.context.api=()=>pending.promise;const request=x.run("importCatalogArtifact({id:'artifact'})");x.run("platform.serial++;platform.pane='quality'");pending.resolve({id:'new-comparison'});await request;assert.deepEqual(x.opened,[]);assert.equal(x.get('platformDialog').open,true);assert.ok(x.errors.some(message=>String(message).includes('当前页面已保留')));
});

test('a late protocol lookup cannot leave the currently selected catalog page',async()=>{
  const x=fixture(),pending=deferred();catalog(x,'evaluation');x.context.api=()=>pending.promise;let changed=false;x.context.useSavedProtocol=async()=>{changed=true;};const request=x.run("useEvaluationProtocol({protocol_id:'frozen'})");x.get('registryKind').value='dataset';pending.resolve({id:'frozen',kind:'protocol'});assert.equal(await request,null);assert.equal(changed,false);
});

test('successful alias publication explains its actual scope and offers explicit comparison configuration',async()=>{
  const x=fixture();x.run("initializeModelPanel($('hub-research'));showResearchView('models')");x.get('registeredModel').value='model';x.get('modelEvaluation').value='report';x.context.api=async()=>({alias:'candidate'});await x.run('promoteModel()');
  assert.match(x.get('modelActionSummary').textContent,/默认权重不变/);assert.match(x.get('modelActionSummary').textContent,/不会热加载/);assert.match(x.get('modelActionSummary').textContent,/新比较需明确选择/);assert.equal(x.get('modelConfigureComparison').hidden,false);
  x.get('modelAlias').value='another';x.run('updateModelActions()');assert.equal(x.get('modelConfigureComparison').hidden,true);
});

test('the published alias action resolves the current ready alias and uses its evaluated configuration',async()=>{
  const x=fixture();x.run("initializeModelPanel($('hub-research'));showResearchView('models')");const box=mintComposer(x),calls=[];x.context.api=async(url,payload)=>{calls.push({url,payload});return {aliases:[{name:'candidate',ready:true,model:{path:'/model.safetensors',config:{path:'/registered.yaml'}},effective_config:{path:'/evaluated.yaml'}}]};};
  await x.run("usePublishedModelAlias('candidate')");assert.equal(box.querySelector('[data-selection=checkpoint]').value,'/model.safetensors');assert.equal(box.querySelector('[data-selection=config]').value,'/evaluated.yaml');assert.equal(box.querySelector('details').open,true);assert.equal(box.querySelector('input[type=checkbox]').checked,true);assert.equal(x.get('inputPreflight').dataset.ready,'pending');assert.equal(x.get('platformDialog').open,false);assert.equal(x.get('taskDialog').open,true);assert.ok(calls.every(call=>call.payload===undefined));
});

test('late or unavailable alias reads cannot overwrite the current comparison draft',async()=>{
  const x=fixture(),pending=deferred();x.run("initializeModelPanel($('hub-research'));showResearchView('models')");const box=mintComposer(x);box.querySelector('[data-selection=checkpoint]').value='/manual.safetensors';x.context.api=()=>pending.promise;
  const request=x.run("usePublishedModelAlias('candidate')");x.run("platform.serial++;platform.pane='tools'");pending.resolve({aliases:[{name:'candidate',ready:true,model:{path:'/old'}}]});assert.equal(await request,null);assert.equal(box.querySelector('[data-selection=checkpoint]').value,'/manual.safetensors');
  x.run("platform.pane='research';showResearchView('models')");x.context.api=async()=>({aliases:[{name:'candidate',ready:false,model:{path:'/changed'}}]});await assert.rejects(x.run("usePublishedModelAlias('candidate')"),/别名当前不可用/);assert.equal(box.querySelector('[data-selection=checkpoint]').value,'/manual.safetensors');
});

test('workflow next steps contain actions without repeating task status or expanding the raw error',async()=>{
  const x=fixture();await x.run("renderWorkflowNextSteps({id:'failed',request:{kind:'train',name:'Training'},status:'failed',error:'failed\\nraw technical traceback'})");
  assert.equal(x.get('workflowNextSteps').textContent,'查看训练曲线与检查点');assert.doesNotMatch(x.get('workflowNextSteps').textContent,/raw technical traceback|Training|failed/);
});

test('offline protocol evaluation submits the explicitly chosen validation split',async()=>{
  const x=fixture();x.context.updateWorkflowFields=()=>{};x.context.loadRegistryChoices=async()=>{};x.run("initializeResearch($('hub-research'))");x.get('offlineProtocol').value='frozen-validation';x.get('offlineSplit').value='validation';x.context.state.comparison={id:'validation-comparison',runs:[{id:'prediction',result:{reference_role:'model_prediction'}}]};let sent;
  x.context.api=async(url,payload)=>{assert.match(url,/registry\/evaluation$/);sent=payload;return {id:'report'};};await x.get('hub-research').querySelectorAll('button').find(button=>button.textContent==='评测当前比较的预测结果').onclick();
  assert.equal(sent.protocol_id,'frozen-validation');assert.equal(sent.split,'validation');assert.deepEqual(plain(sent.run_ids),['prediction']);
});

test('manually selecting a pseudo-label dataset shows its role, partition and origin before preflight',async()=>{
  const x=fixture();x.context.updateWorkflowFields=()=>{};x.run("initializeResearch($('hub-research'))");x.get('workflowKind').value='train';x.context.api=async()=>({records:[{id:'pseudo',name:'Native pseudo training',training_ready:true,label_role:'model_generated_pseudo_labels',requires_training_opt_in:true,split:'train',provenance:{label_origin:{plan:[{run_id:'first'},{run_id:'second'}]}}}]});
  await x.run('refreshWorkflowDatasetChoices()');x.get('workflowDataset').value='pseudo';x.get('workflowPseudoOptIn').checked=true;x.get('workflowDataset').onchange();
  assert.match(x.get('workflowDatasetSummary').textContent,/模型生成伪标签.*Train.*2 个原生推理结果/);assert.equal(x.get('workflowPseudoOptIn').checked,false);assert.equal(x.get('workflowSubmit').disabled,true);
});

test('production states that its full native source belongs to the comparison workspace',()=>{
  const x=fixture();x.run('productionPanel()');const section=x.get('nativeProductionSection');assert.match(section.textContent,/质检中的独立选择不改变比较工作区/);assert.match(section.textContent,/不自动应用复核区间或二维修正/);assert.ok(section.querySelectorAll('button').some(button=>button.textContent==='选择比较工作区的 MINT 结果'));assert.equal(section.querySelectorAll('button').some(button=>button.textContent==='选择当前比较的 MINT 结果'),false);
});
