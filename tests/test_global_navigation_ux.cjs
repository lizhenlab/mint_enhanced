'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function fixture(){
  const nodes={},requests=[],errors=[],opened=[],timers=new Map(),events={};let timer=0;
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
  const location={href:'http://localhost/compare?hub=tasks',get search(){return new URL(this.href).search;},get pathname(){return new URL(this.href).pathname;},get hash(){return new URL(this.href).hash;}};
  const context=vm.createContext({console,URL,URLSearchParams,structuredClone,Number,Node:Element,
    document:{body,hidden:false,addEventListener(){},createTextNode:text=>create('span',text),querySelectorAll:selector=>body.querySelectorAll(selector),querySelector:selector=>selector==='.platform-body'?body:body.querySelector(selector)},
    window:{addEventListener(name,fn){events[name]=fn;},dispatchEvent(){},history:{replaceState(_state,_unused,url){location.href=new URL(url,location.href).href;},pushState(_state,_unused,url){location.href=new URL(url,location.href).href;}}},location,
    requestAnimationFrame:fn=>fn(),CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},
    setTimeout(fn){timers.set(++timer,fn);return timer;},clearTimeout(id){timers.delete(id);},
    $:get,el:create,inputLabel(label,input){const row=create('label',label);row.append(input);return row;},
    selectInput(choices,value){const node=create('select');for(const [key,label]of choices){const option=create('option',label);option.value=key;node.append(option);}node.value=value??choices[0]?.[0]??'';return node;},
    api:async(url,payload)=>{requests.push({url,payload});throw new Error('Unexpected API: '+url);},
    state:{comparison:null},STAGES:{},TERMINAL:new Set(['completed','partial','failed','cancelled','interrupted']),pause(){},notify:error=>errors.push(error),localStorage:{getItem(){return null;}},workspaceUI:{history:[],tab:'view'},preferredComparison:rows=>rows[0],setWorkspaceTab(){},researchButton:(label,fn)=>{const button=create('button',label);button.onclick=fn;return button;},researchDetails(){},
    openComparison:async id=>opened.push(id),fmt:(value)=>String(value),metricValue:()=>'',statusLabel:value=>value,
  });
  const run=code=>vm.runInContext(code,context);
  for(const file of ['platform.js'])run(fs.readFileSync(path.join(__dirname,'../mint/comparison/web',file),'utf8'));
  const realRefreshPlatformContext=context.refreshPlatformContext;
  context.syncPlatformNavigation=()=>{};context.refreshPlatformContext=()=>{};context.pollPlatform=async()=>{};
  get('platformDialog').open=true;run("platform.pane='tasks'");
  return {nodes,get,run,context,Element,create,body,requests,errors,opened,location,realRefreshPlatformContext,events};
}

function plain(value){return JSON.parse(JSON.stringify(value));}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function options(x,id,values,selected){const field=x.get(id);field.tagName='SELECT';for(const value of values){const option=x.create('option',value);option.value=value;field.append(option);}field.value=selected;return field;}
function taskFilters(x,{kind='all',status='active',query=''}={}){options(x,'taskKindFilter',['all','inference','batch','evaluation','workflow','study','render','export','diagnostic'],kind);options(x,'taskStatusFilter',['all','active','completed','attention'],status);x.get('taskSearch').value=query;}
function tasks(rows=[],total=rows.length,offset=0){return {tasks:rows,total,offset,cached_predictions:[],resources:{},resource:{}};}

test('module navigation removes stale owning-route parameters while keeping the comparison',()=>{
  const x=fixture();x.location.href='http://localhost/compare?id=comp&hub=research&workflow=wf&study=study&research_tab=models&collection=collection&tool_job=render&tools_tab=render&source_type=native&task_query=old&task_status=completed&task_kind=render';x.run("platformRoute('tools')");let query=new URL(x.location.href).searchParams;
  assert.equal(query.get('id'),'comp');assert.equal(query.get('hub'),'tools');assert.equal(query.get('tool_job'),'render');for(const key of ['workflow','study','research_tab','collection','source_type','task_query','task_status','task_kind'])assert.equal(query.has(key),false,key);
  x.run('platformRoute(null)');query=new URL(x.location.href).searchParams;assert.deepEqual([...query.keys()],['id']);
});
test('diagnostic links preserve local workspace source and frame without recursive or external returns',()=>{
  const x=fixture();x.location.href='http://localhost/proxy/8011/compare?hub=quality&return_to=old';x.context.state.comparison={id:'chosen'};x.context.state.frame=9;
  let result=new URL(x.context.diagnosticContextURL('/diagnostics?tool=analysis-video#target'),'http://localhost');assert.equal(result.pathname,'/proxy/8011/diagnostics');let back=new URL(result.searchParams.get('return_to'),'http://localhost');assert.equal(back.pathname,'/proxy/8011/compare');assert.equal(back.searchParams.get('hub'),'quality');assert.equal(back.searchParams.get('id'),'chosen');assert.equal(back.searchParams.has('return_to'),false);assert.equal(result.hash,'#target');
  x.get('platformDialog').open=false;back=new URL(x.context.comparisonReturnPath(),'http://localhost');assert.equal(back.searchParams.get('frame'),'10');assert.equal(x.context.diagnosticContextURL('https://elsewhere.example/diagnostics'),'https://elsewhere.example/diagnostics');
});
test('returning home is an in-page transition that keeps the selected comparison and draft',async()=>{
  const x=fixture(),tabs=[];x.context.state.comparison={id:'kept'};x.context.setWorkspaceTab=tab=>tabs.push(tab);x.run("platform.selection.set('draft-video',{source:'/draft.mp4'})");await x.run('returnToComparison({home:true})');assert.equal(x.context.state.comparison.id,'kept');assert.deepEqual(x.opened,[]);assert.equal(x.run('platform.selection.size'),1);assert.deepEqual(tabs,['view']);assert.equal(x.get('comparisonMain').focused,true);assert.equal(new URL(x.location.href).searchParams.has('hub'),false);
});
test('global errors appear at the current workspace and dismissal returns keyboard focus',()=>{
  const x=fixture();x.get('workspaceFeedback').hidden=true;x.context.hubError(new Error('Action failed'));assert.equal(x.get('workspaceFeedback').hidden,false);assert.equal(x.get('platformError').textContent,'Action failed');assert.equal(x.get('platformError').focused,true);x.context.clearPlatformError();assert.equal(x.get('workspaceFeedback').hidden,true);x.context.focusWorkspaceContent();assert.equal(x.get('platformTitle').focused,true);x.get('platformDialog').open=false;x.context.focusWorkspaceContent();assert.equal(x.get('comparisonMain').focused,true);
});
test('home, skip and error controls are wired without swallowing modified browser navigation',async()=>{
  const x=fixture(),calls=[];x.context.initializeToolsWorkspace=()=>{};x.context.returnToComparison=async options=>calls.push(options);x.context.focusWorkspaceContent=()=>calls.push('focus');x.run('initializePlatform()');let prevented=false;const event={button:0,preventDefault(){prevented=true;}};
  x.get('workspaceHome').onclick({...event,ctrlKey:true});assert.equal(prevented,false);assert.deepEqual(calls,[]);x.get('workspaceHome').onclick(event);await Promise.resolve();assert.equal(prevented,true);assert.deepEqual(plain(calls),[{home:true}]);x.get('workspaceSkip').onclick(event);assert.equal(calls.at(-1),'focus');
});
test('task filters round-trip explicit all, exact kind and literal search instead of reverting to active',()=>{
  const x=fixture();taskFilters(x,{kind:'render',status:'all',query:' needle% '});x.run('taskFilterRoute()');const query=new URL(x.location.href).searchParams;assert.equal(query.get('task_status'),'all');assert.equal(query.get('task_kind'),'render');assert.equal(query.get('task_query'),'needle%');x.get('taskStatusFilter').value='active';x.get('taskKindFilter').value='all';x.run('restoreTaskFilters()');assert.equal(x.get('taskStatusFilter').value,'all');assert.equal(x.get('taskKindFilter').value,'render');assert.equal(x.get('taskSearch').value,'needle%');assert.equal(x.run('platform.taskOffset'),0);
  x.location.href='http://localhost/compare?hub=tasks&task_status=invalid&task_kind=invalid';x.run('restoreTaskFilters()');assert.equal(x.get('taskStatusFilter').value,'active');assert.equal(x.get('taskKindFilter').value,'all');
});
test('task reads send exact filters and ignore old replies after a filter change',async()=>{
  const x=fixture(),a=deferred(),b=deferred(),queries=[];taskFilters(x,{kind:'render'});x.context.renderTaskResourceSummary=()=>{};x.context.api=url=>{queries.push(new URL(url,'http://localhost/').searchParams);return queries.length===1?a.promise:b.promise;};const old=x.run('refreshWorkspaceTasks()');x.get('taskKindFilter').value='workflow';const current=x.run('refreshWorkspaceTasks()');b.resolve(tasks([{id:'new',kind:'workflow',status:'completed',label:'New workflow',progress:{}}]));await current;a.resolve(tasks([{id:'old',kind:'render',status:'running',label:'Old render',progress:{}}]));assert.equal(await old,null);
  assert.equal(queries[0].get('kind'),'render');assert.equal(queries[1].get('kind'),'workflow');assert.equal(queries[1].get('status'),'active');assert.match(x.get('workspaceTasks').textContent,/New workflow/);assert.doesNotMatch(x.get('workspaceTasks').textContent,/Old render/);
});
test('a task filter change invalidates a reply before the next debounced request starts',async()=>{
  const x=fixture(),pending=deferred();taskFilters(x);x.context.api=()=>pending.promise;x.context.renderTaskResourceSummary=()=>{};const request=x.run('refreshWorkspaceTasks()');x.get('taskSearch').value='new text';pending.resolve(tasks([{id:'stale',kind:'inference',status:'running',label:'Stale result'}]));assert.equal(await request,null);assert.doesNotMatch(x.get('workspaceTasks').textContent,/Stale result/);
});
test('empty task results offer an explicit reset that shows all records',async()=>{
  const x=fixture();taskFilters(x,{kind:'render',status:'active',query:'missing'});x.context.renderTaskResourceSummary=()=>{};x.context.api=async()=>tasks();await x.run('refreshWorkspaceTasks()');assert.match(x.get('workspaceTasks').textContent,/没有符合条件/);const button=x.get('workspaceTasks').querySelectorAll('button').find(node=>node.textContent==='查看全部任务');let refreshed=0;x.context.refreshWorkspaceTasks=async()=>refreshed++;button.onclick();await Promise.resolve();assert.equal(x.get('taskKindFilter').value,'all');assert.equal(x.get('taskStatusFilter').value,'all');assert.equal(x.get('taskSearch').value,'');assert.equal(refreshed,1);
});
test('task actions own one pending request and disable the matching row only',async()=>{
  const x=fixture(),pending=deferred(),calls=[];taskFilters(x);const matching=x.create('button','Cancel'),other=x.create('button','Other');matching.dataset.taskAction='workflow:one';other.dataset.taskAction='workflow:two';x.get('workspaceTasks').append(matching,other);x.context.api=(url,body)=>{calls.push({url,body});return pending.promise;};let refreshes=0;x.context.refreshWorkspaceTasks=async()=>refreshes++;
  const request=x.run("workspaceTaskAction({kind:'workflow',id:'one'},'cancel')");assert.equal(matching.disabled,true);assert.equal(other.disabled,false);assert.equal(await x.run("workspaceTaskAction({kind:'workflow',id:'one'},'cancel')"),null);assert.equal(calls.length,1);assert.match(calls[0].url,/workflows\/one\/cancel$/);pending.resolve({status:'cancelled'});await request;assert.equal(matching.disabled,false);assert.equal(x.run('platform.taskActions.size'),0);assert.equal(refreshes,1);
});
test('failed task actions release pending ownership for an explicit retry',async()=>{
  const x=fixture();taskFilters(x);x.context.api=async()=>{throw new Error('temporary failure');};await assert.rejects(x.run("workspaceTaskAction({kind:'inference',id:'run'},'resume')"),/temporary failure/);assert.equal(x.run('platform.taskActions.size'),0);
});
test('opening a task cannot inspect a different comparison after a late navigation reply',async()=>{
  const x=fixture(),pending=deferred(),inspected=[];x.context.openComparison=()=>pending.promise;x.context.inspectResult=id=>inspected.push(id);const request=x.run("openWorkspaceTask({kind:'inference',id:'run-a',comparison_id:'a'})");x.context.state.comparison={id:'b'};pending.resolve();await request;assert.deepEqual(inspected,[]);
});
test('browser route restoration ignores an obsolete parent lookup',async()=>{
  const x=fixture(),pending=deferred(),panes=[];x.location.href='http://localhost/compare?run=old-run&hub=quality';x.context.api=()=>pending.promise;x.context.openPlatform=tab=>panes.push(tab);const old=x.run('restoreWorkspaceRoute()');x.location.href='http://localhost/compare?hub=data';await x.run('restoreWorkspaceRoute()');pending.resolve({comparisons:['old-comparison']});assert.equal(await old,null);assert.deepEqual(panes,['data']);assert.deepEqual(x.opened,[]);assert.equal(x.run('platformNavigation.restoring'),false);
});
test('route restoration does not suppress fresh user navigation while waiting for a workflow',async()=>{
  const x=fixture(),pending=deferred();x.location.href='http://localhost/compare?hub=research&workflow=old';x.context.openPlatform=tab=>{x.run(`platform.pane=${JSON.stringify(tab)}`);};x.context.inspectWorkflow=()=>pending.promise;x.context.showResearchView=()=>{};const old=x.run('restoreWorkspaceRoute()');assert.equal(x.run('platformNavigation.restoring'),false);x.run("platformRoute('tools')");pending.resolve({id:'old'});assert.equal(await old,null);assert.equal(new URL(x.location.href).searchParams.get('hub'),'tools');assert.equal(new URL(x.location.href).searchParams.has('workflow'),false);
});
test('collection browser restoration is read-only with respect to navigation history',async()=>{
  const x=fixture(),calls=[];x.location.href='http://localhost/compare?hub=batch&collection=batch-a';x.context.openCollection=async(id,options)=>calls.push({id,options});await x.run('restoreWorkspaceRoute()');assert.deepEqual(plain(calls),[{id:'batch-a',options:{history:false}}]);assert.equal(x.run('platformNavigation.restoring'),false);
});
test('main landmark, skip link and top-level error status exist in the actual page contract',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../mint/comparison/web/index.html'),'utf8');assert.match(html,/id="workspaceSkip"[^>]+href="#comparisonMain"/);assert.match(html,/id="workspaceHome"[^>]+href="\/compare"/);assert.match(html,/id="platformError"[^>]+role="alert"/);assert.match(html,/id="dismissPlatformError"/);assert.match(html,/id="taskKindFilter"[^>]+aria-label="任务类别"/);assert.match(html,/id="comparisonMain"[^>]+tabindex="-1"/);
});

test('task filter loads show a busy state and identical polling preserves the existing controls',async()=>{
  const x=fixture(),pending=deferred();taskFilters(x);x.context.renderTaskResourceSummary=()=>{};x.context.api=()=>pending.promise;const loading=x.run('refreshWorkspaceTasks()');assert.equal(x.get('workspaceTasks').getAttribute('aria-busy'),'true');assert.match(x.get('workspaceTasks').textContent,/正在读取/);
  const value=tasks([{id:'stable',kind:'workflow',status:'completed',label:'Stable result',source:'train',progress:{}}]);pending.resolve(value);await loading;const table=x.get('workspaceTasks').children[0];assert.equal(x.get('workspaceTasks').getAttribute('aria-busy'),'false');x.context.api=async()=>value;await x.run('refreshWorkspaceTasks()');assert.equal(x.get('workspaceTasks').children[0],table);
});
test('task progress refresh keeps keyboard focus and does not repeat a failure as a waiting reason',async()=>{
  const x=fixture();taskFilters(x,{status:'all'});x.context.renderTaskResourceSummary=()=>{};let value=tasks([{id:'one',kind:'workflow',status:'running',label:'Job',source:'train',progress:{done:1,total:2}}]);x.context.api=async()=>value;await x.run('refreshWorkspaceTasks()');const first=x.get('workspaceTasks').querySelectorAll('[data-task-control]').find(node=>node.dataset.taskControl==='workflow:one:open');x.context.document.activeElement=first;
  value=tasks([{id:'one',kind:'workflow',status:'failed',label:'Job',source:'train',progress:{done:1,total:2},error:'Specific failure',waiting_reason:'Specific failure'}]);await x.run('refreshWorkspaceTasks()');const next=x.get('workspaceTasks').querySelectorAll('[data-task-control]').find(node=>node.dataset.taskControl==='workflow:one:open');assert.equal(next.focused,true);assert.equal((x.get('workspaceTasks').textContent.match(/Specific failure/g)||[]).length,1);assert.match(x.get('workspaceTasks').textContent,/模型训练/);
});
test('cached diagnostic imports stay single-shot and cannot navigate away from a later workspace',async()=>{
  const x=fixture(),pending=deferred(),entry={id:'cached',source:'/a.mp4',frames:3,camera_mode:'camera',hand_mode:'smooth'};taskFilters(x);x.context.state.pollToken=1;x.context.api=(url,payload)=>{x.requests.push({url,payload});return pending.promise;};x.context.cached=[entry];x.run('renderCachedPredictions(cached)');const original=x.get('legacyResults').children[0];x.run('renderCachedPredictions(cached)');assert.equal(x.get('legacyResults').children[0],original);
  const first=x.run('importLegacyPrediction(cached[0])');assert.equal(await x.run('importLegacyPrediction(cached[0])'),null);assert.equal(x.requests.length,1);x.run("platform.pane='research';platform.serial++");pending.resolve({id:'saved'});await first;assert.equal(x.opened.length,0);assert.match(x.errors.at(-1),/当前页面已保留/);assert.equal(x.run('platform.legacyImports.size'),0);
});
