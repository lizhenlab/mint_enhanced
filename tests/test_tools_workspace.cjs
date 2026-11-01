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
    get selectedOptions(){return this.options.filter(option=>option.value===this.value);}get options(){return this.children.filter(child=>child.tagName==='OPTION');}get lastChild(){return this.children.at(-1)||null;}get nextElementSibling(){return this.parentElement?.children[this.parentElement.children.indexOf(this)+1]||null;}
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
  const location={href:'http://localhost/compare?hub=tools'};
  const context=vm.createContext({console,URL,URLSearchParams,structuredClone,Number,Node:Element,
    document:{body,hidden:false,addEventListener(){},createTextNode:text=>create('span',text),querySelectorAll:selector=>body.querySelectorAll(selector),querySelector:selector=>selector==='.platform-body'?body:body.querySelector(selector)},
    window:{addEventListener(){},dispatchEvent(){},history:{replaceState(_state,_unused,url){location.href=new URL(url,location.href).href;},pushState(_state,_unused,url){location.href=new URL(url,location.href).href;}}},location,
    requestAnimationFrame:fn=>fn(),CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},
    setTimeout(fn){timers.set(++timer,fn);return timer;},clearTimeout(id){timers.delete(id);},
    $:get,el:create,inputLabel(label,input){const row=create('label',label);row.append(input);return row;},
    selectInput(choices,value){const node=create('select');for(const [key,label]of choices){const option=create('option',label);option.value=key;node.append(option);}node.value=value??choices[0]?.[0]??'';return node;},
    api:async(url,payload)=>{requests.push({url,payload});throw new Error('Unexpected API: '+url);},
    state:{comparison:null},STAGES:{},TERMINAL:new Set(['completed','failed','cancelled','interrupted']),pause(){},notify:error=>errors.push(error),
    openComparison:async id=>opened.push(id),fmt:(value)=>String(value),metricValue:()=>'',statusLabel:value=>value,
  });
  const run=code=>vm.runInContext(code,context);
  for(const file of ['platform.js'])run(fs.readFileSync(path.join(__dirname,'../mint/comparison/web',file),'utf8'));
  const realRefreshPlatformContext=context.refreshPlatformContext;
  context.hubError=error=>errors.push(error);context.syncPlatformNavigation=()=>{};context.platformRoute=()=>{};context.refreshPlatformContext=()=>{};context.pollPlatform=async()=>{};
  const markup=fs.readFileSync(path.join(__dirname,'../mint/comparison/web/index.html'),'utf8');
  const begin=markup.indexOf('<section id="hub-tools"'),end=markup.indexOf('<section id="hub-catalog"',begin);
  body.append(fragment({create},markup.slice(begin,end)));
  get('platformDialog').open=true;get('renderWidth').value='640';run("platform.pane='tools';initializeToolsWorkspace()");
  return {nodes,get,run,context,Element,create,body,requests,errors,opened,location,realRefreshPlatformContext};
}
function plain(value){return JSON.parse(JSON.stringify(value));}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function fragment(x,html){
  const root=x.create('div'),stack=[root];
  for(const token of html.match(/<[^>]+>|[^<]+/g)||[]){
    if(token.startsWith('</')){stack.pop();continue;}
    if(!token.startsWith('<')){stack.at(-1).append(x.create('span',token.trim()));continue;}
    const tag=token.match(/^<([\w-]+)/)?.[1];if(!tag)continue;const node=x.create(tag);
    for(const attribute of token.slice(tag.length+1,-1).matchAll(/([\w-]+)(?:="([^"]*)")?/g)){
      const [,key,value='']=attribute;if(key==='id')node.id=value;else if(key==='class')node.className=value;else if(key==='hidden'||key==='open')node[key]=true;else if(key.startsWith('data-'))node.dataset[key.slice(5).replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase())]=value;else node.setAttribute(key,value);
    }
    stack.at(-1).append(node);if(!['input','img','br','hr','link','meta'].includes(tag)&&!token.endsWith('/>'))stack.push(node);
  }
  return root;
}


function comparison(id='source'){
  return {id,title:'Comparison '+id,sample_id:'sample-'+id,sample:{frames:12,source_frame_indices:Array.from({length:12},(_,i)=>100+i*2)},runs:[{id:id+'-run',label:'Prediction '+id,sample_id:'sample-'+id,result:{arrays:{vertices_camera_m:{}}}}]};
}
function eligibility(run='source-run',values={}){return {run_id:run,sample_frames:12,result_frames:12,mesh_available:true,world_available:true,available_ranges:[[0,12]],world_ranges:[[0,12]],...values};}
function ready(x,value=comparison()){
  x.context.state.comparison=value;x.run('syncToolRuns()');x.context.allowed=eligibility(value.runs[0].id);x.run("toolsUI.options=allowed;platform.capability={render:{mujoco:{ready:true},retarget:{ready:true}}}");
  for(const [id,value]of Object.entries({renderKind:'mujoco',renderSpace:'camera',renderStart:'1',renderEnd:'',renderWidth:'640',renderPolicy:'shared'}))x.get(id).value=value;
  x.run('syncRenderSettings()');return value;
}
function job(id='job',values={}){return {id,run_id:'source-run',label:'Original source',kind:'mujoco',space:'camera',start:0,end:12,width:640,resource_policy:'shared',include_absent:false,status:'completed',url:'/api/comparison/tools/'+id+'/video',...values};}

test('tools opens a compact catalog and keeps configuration and environment in separate mounted views',()=>{
  const x=fixture();assert.equal(x.run('initialToolsView(new URLSearchParams())'),'catalog');assert.equal(x.run("initialToolsView(new URLSearchParams('tool_job=one'))"),'render');
  assert.deepEqual(x.get('hub-tools').querySelectorAll('[data-tools-tab]').map(n=>n.dataset.toolsTab),['catalog','render','environment']);
  x.run("showToolsView('catalog',{route:false})");assert.equal(x.get('toolsCatalogPanel').hidden,false);assert.equal(x.get('toolsRenderPanel').hidden,true);assert.equal(x.get('toolsEnvironmentPanel').hidden,true);
  const advanced=x.get('toolsRenderPanel').querySelector('details');assert.ok(advanced);assert.equal(advanced.open,false);assert.ok(x.nodes.renderAbsent);assert.equal(x.get('renderAbsent').checked,false);
  x.get('renderStart').value='6';x.run("showToolsView('environment',{route:false});showToolsView('catalog',{route:false})");assert.equal(x.get('renderStart').value,'6');assert.equal(x.requests.length,0);
});

test('tools does not imply the main comparison is inherited by independent catalog or environment tools',()=>{
  const x=fixture();ready(x);for(const view of ['catalog','environment','render']){x.run(`toolsUI.view=${JSON.stringify(view)}`);x.realRefreshPlatformContext();assert.equal(x.get('platformCurrentContext').hidden,true);}
  assert.match(x.get('toolsResultContext').textContent,/配置来源.*Comparison source/);assert.match(x.get('toolsCatalogPanel').textContent,/不会自动继承比较页设置/);
});

test('late capability refresh cannot rebuild a new comparison or replace a newer capability state',async()=>{
  const x=fixture(),first=deferred(),second=deferred();x.context.state.comparison=comparison('old');let calls=0;x.context.api=()=>++calls===1?first.promise:second.promise;
  const pending=x.run('refreshToolOptions()');x.context.state.comparison=comparison('new');const latest=x.run('refreshToolOptions()');
  second.resolve({render:{mujoco:{ready:true},retarget:{ready:true}},legacy:true,data_analysis:true});await latest;
  first.resolve({render:{mujoco:{ready:false}},legacy:false,data_analysis:false});assert.equal(await pending,null);
  assert.equal(x.get('renderRun').value,'new-run');assert.equal(x.run('platform.capability.render.mujoco.ready'),true);assert.equal(x.get('renderRun').options.some(n=>n.value==='old-run'),false);
});

test('old result applicability responses cannot unlock a different result or comparison',async()=>{
  const x=fixture(),first=deferred(),second=deferred();ready(x,comparison('old'));let calls=0;x.context.api=()=>++calls===1?first.promise:second.promise;
  const pending=x.run('loadRenderOptions()');x.context.state.comparison=comparison('new');x.run('syncToolRuns()');const latest=x.run('loadRenderOptions()');
  second.resolve(eligibility('new-run',{mesh_available:false}));await latest;first.resolve(eligibility('old-run'));assert.equal(await pending,null);
  assert.equal(x.run('toolsUI.options.run_id'),'new-run');assert.equal(x.get('startRenderTool').disabled,true);assert.match(x.get('renderSettingsWarning').textContent,/没有 MANO 网格/);
});

test('mesh, complete output range, world poses, and frame bounds gate rendering before any request',async()=>{
  const x=fixture();ready(x);x.run('toolsUI.options=null');assert.match(x.run('renderSettingsError()'),/正在检查/);
  x.run('toolsUI.options=allowed;toolsUI.options.mesh_available=false');assert.match(x.run('renderSettingsError()'),/没有 MANO 网格/);
  x.get('renderKind').value='retarget';assert.equal(x.run('renderSettingsError()'),'');
  x.run('toolsUI.options.available_ranges=[[0,4],[6,12]]');assert.match(x.run('renderSettingsError()'),/缺失输出帧/);x.run('useAvailableRenderRange()');assert.equal(x.get('renderStart').value,'7');assert.equal(x.get('renderEnd').value,'12');assert.equal(x.run('renderSettingsError()'),'');
  x.get('renderSpace').value='world';x.run('toolsUI.options.world_ranges=[[0,3]]');assert.match(x.run('renderSettingsError()'),/世界相机轨迹/);
  x.run('toolsUI.options.world_available=false;syncRenderSettings()');assert.equal(x.get('renderSpace').options.find(n=>n.value==='world').disabled,true);
  x.get('renderSpace').value='camera';for(const [start,end]of [['',''],['4','2'],['0','3'],['1','13']]){x.get('renderStart').value=start;x.get('renderEnd').value=end;await assert.rejects(x.run('startRenderTool()'),/样本帧区间/);}
  assert.equal(x.requests.length,0);
});

test('rendering uses explicit absent-hand policy and converts inclusive UI bounds exactly once',async()=>{
  const x=fixture(),pendingRequest=deferred(),calls=[];ready(x);x.get('showAbsent').checked=true;x.get('renderAbsent').checked=false;x.get('renderStart').value='2';x.get('renderEnd').value='4';
  x.context.api=(url,payload)=>{calls.push({url,payload});return pendingRequest.promise;};const pending=x.run('startRenderTool()');await x.run('startRenderTool()');assert.equal(calls.length,1);
  assert.equal(calls[0].payload.start,1);assert.equal(calls[0].payload.end,4);assert.equal(calls[0].payload.include_absent,false);x.run("selectRenderJob('newly-inspected')");pendingRequest.resolve(job('submitted'));await pending;assert.equal(x.run('platform.renderJob'),'newly-inspected');assert.equal(x.get('startRenderTool').dataset.busy,'false');
});

test('a submitted task cannot replace a later draft choice after its response arrives',async()=>{
  const x=fixture(),request=deferred();ready(x);x.context.api=()=>request.promise;const pending=x.run('startRenderTool()');x.get('renderStart').value='3';request.resolve(job('submitted'));await pending;
  assert.equal(x.run('platform.renderJob'),null);assert.equal(x.get('renderStart').value,'3');assert.match(x.errors[0],/当前选择已保留/);
});

test('the same job ignores out-of-order status responses and shows its own source independently of the draft',async()=>{
  const x=fixture(),first=deferred(),second=deferred();ready(x,comparison('draft'));x.run("toolsUI.view='render';selectRenderJob('chosen')");let calls=0;x.context.api=()=>++calls===1?first.promise:second.promise;
  const pending=x.run('refreshRenderTool()'),latest=x.run('refreshRenderTool()');second.resolve(job('chosen',{run_id:'different-run',label:'Original geometry'}));await latest;
  first.resolve(job('chosen',{status:'running'}));assert.equal(await pending,null);assert.equal(x.run('toolsUI.activeJob.status'),'completed');assert.match(x.get('renderToolStatus').textContent,/Original geometry/);assert.match(x.get('renderJobSourceNote').textContent,/另一份结果/);assert.equal(x.get('renderToolVideo').dataset.job,'chosen');assert.equal(x.get('renderToolVideo').hidden,false);assert.equal(x.get('renderRun').value,'draft-run');
  x.run("showToolsView('catalog',{route:false})");assert.equal(x.get('renderToolVideo').paused,true);
});

test('switching inspected jobs clears old playback and ignores the previous job response',async()=>{
  const x=fixture(),old=deferred();ready(x);x.run("toolsUI.view='render';selectRenderJob('old')");x.context.api=()=>old.promise;const pending=x.run('refreshRenderTool()');x.run("selectRenderJob('new')");old.resolve(job('old'));assert.equal(await pending,null);assert.equal(x.get('renderToolVideo').hidden,true);assert.equal(x.get('renderToolVideo').dataset.job,'');assert.match(x.get('renderToolStatus').textContent,/正在读取生成任务/);
});

test('resuming uses the original task range and source without rewriting a later selected task or draft',async()=>{
  const x=fixture(),submitted=deferred(),calls=[];ready(x,comparison('draft'));x.run("selectRenderJob('original')");const original=job('original',{run_id:'original-result',start:7,end:11,width:960,space:'world',resource_policy:'idle',include_absent:true,status:'cancelled'});
  x.context.api=(url,payload)=>{calls.push({url,payload});return payload?submitted.promise:Promise.resolve(original);};const pending=x.run("resumeRenderTool('original')");await new Promise(resolve=>setImmediate(resolve));assert.equal(calls.length,2);assert.equal(calls[1].url,'api/comparison/runs/original-result/tools');assert.deepEqual(plain(calls[1].payload),{kind:'mujoco',space:'world',start:7,end:11,width:960,include_absent:true,resource_policy:'idle'});
  x.run("selectRenderJob('newer-selected')");submitted.resolve(job('resumed'));await pending;assert.equal(x.run('platform.renderJob'),'newer-selected');assert.equal(x.get('renderRun').value,'draft-run');assert.equal(x.get('renderStart').value,'1');assert.equal(x.get('renderEnd').value,'');
});

test('newly published results do not reset an existing result selection and frame range',()=>{
  const x=fixture(),source=comparison();source.runs.push({id:'source-other',label:'Other prediction',sample_id:source.sample_id,result:{}});ready(x,source);
  x.get('renderRun').value='source-other';x.get('renderStart').value='3';x.get('renderEnd').value='7';
  source.runs.push({id:'newly-completed',label:'New result',sample_id:source.sample_id,result:{}});x.run('syncToolRuns()');
  assert.equal(x.get('renderRun').value,'source-other');assert.equal(x.get('renderStart').value,'3');assert.equal(x.get('renderEnd').value,'7');assert.equal(x.get('renderRun').options.some(option=>option.value==='newly-completed'),true);
  source.runs=source.runs.filter(run=>run.id!=='source-other');x.run('syncToolRuns()');assert.equal(x.get('renderRun').value,'source-run');assert.equal(x.get('renderStart').value,'1');assert.equal(x.get('renderEnd').value,'');
});

test('late comparison hydration refreshes the open tools source once and leaves other workspaces untouched',async()=>{
  const x=fixture(),calls=[];x.run('toolsUI.publishedKey=toolComparisonKey()');
  x.context.refreshToolOptions=async()=>{calls.push(x.context.state.comparison?.id);x.run('toolsUI.publishedKey=toolComparisonKey();syncToolRuns()');};
  x.context.state.comparison=comparison('late-main');await x.run('syncToolsComparison()');assert.deepEqual(calls,['late-main']);assert.equal(x.get('renderRun').value,'late-main-run');
  await x.run('syncToolsComparison()');assert.deepEqual(calls,['late-main'],'repeated main-viewer polls do not duplicate capability refreshes');
  x.run("platform.pane='data'");x.context.state.comparison=comparison('not-tools');await x.run('syncToolsComparison()');assert.equal(calls.length,1);
  x.run("platform.pane='tools'");x.get('platformDialog').open=false;await x.run('syncToolsComparison()');assert.equal(calls.length,1);
  x.get('platformDialog').open=true;await x.run('syncToolsComparison()');assert.deepEqual(calls,['late-main','not-tools']);
  const app=fs.readFileSync(path.join(__dirname,'../mint/comparison/web/app.js'),'utf8'),start=app.indexOf('async function refresh(id,token)'),end=app.indexOf('function openRunDetails',start),refresh=app.slice(start,end),assignment=refresh.indexOf('state.comparison=value'),sync=refresh.indexOf('syncToolsComparison(');
  assert.ok(assignment>=0&&sync>assignment,'comparison hydration must notify tools after assigning the current comparison');assert.match(refresh,/typeof\s+syncToolsComparison\s*===\s*['"]function['"]/);
});
