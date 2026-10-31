'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../eval/model_effect/visualization/viewer/web/app.js'),'utf8');
const nav=source.slice(source.indexOf('// Diagnostic section navigation'),source.indexOf('// 新视频帧或交互使视图失效时才重画'));
function fixture({loaded=true,width=1280,nums={}}={}){
  const events={},calls=[],frames=[];let context;
  function match(node,part){
    const id=part.match(/#([\w-]+)/),cl=part.match(/\.([\w-]+)/),tag=part.match(/^[a-z]+/i),attr=part.match(/\[data-(\w+)(?:="([^"]*)")?\]/);
    return (!id||node.id===id[1])&&(!cl||node.className.split(/\s+/).includes(cl[1]))&&(!tag||node.tagName===tag[0])&&(!attr||node.dataset[attr[1]]!==undefined&&(attr[2]===undefined||node.dataset[attr[1]]===attr[2]));
  }
  const descendants=node=>node.children.flatMap(child=>[child,...descendants(child)]);
  class Node{
    constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.style={};this.attributes={};this.className='';this.hidden=false;this.handlers={};this.rect={top:450,bottom:650,height:200};this.classList={contains:name=>this.className.split(/\s+/).includes(name),add:name=>{if(!this.classList.contains(name))this.className+=' '+name;},remove:name=>{this.className=this.className.split(/\s+/).filter(x=>x!==name).join(' ');},toggle:(name,force)=>{const on=force===undefined?!this.classList.contains(name):force;if(on)this.classList.add(name);else this.classList.remove(name);return on;}};}
    append(...items){for(const item of items){if(item.parentNode)item.parentNode.children=item.parentNode.children.filter(x=>x!==item);item.parentNode=this;this.children.push(item);}}
    appendChild(item){this.append(item);return item;}
    prepend(...items){for(const item of items.reverse()){if(item.parentNode)item.parentNode.children=item.parentNode.children.filter(x=>x!==item);item.parentNode=this;this.children.unshift(item);}}
    removeChild(item){this.children=this.children.filter(child=>child!==item);item.parentNode=null;}
    remove(){this.parentNode?.removeChild(this);}
    after(item){const parent=this.parentNode;item.parentNode=parent;parent.children.splice(parent.children.indexOf(this)+1,0,item);}
    replaceChildren(...items){for(const child of this.children)child.parentNode=null;this.children=[];this.append(...items);}
    querySelectorAll(selector){let scopes=[this];for(const part of selector.trim().split(/\s+/))scopes=scopes.flatMap(node=>descendants(node).filter(child=>match(child,part)));return scopes;}
    querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
    setAttribute(key,value){this.attributes[key]=String(value);}
    removeAttribute(key){delete this.attributes[key];}
    getBoundingClientRect(){return this.rect;}
    addEventListener(name,fn){this.handlers[name]=fn;}
    focus(){document.activeElement=this;}
  }
  const body=new Node('body'),document={body,activeElement:null,createElement:tag=>new Node(tag),querySelector:s=>body.querySelector(s),querySelectorAll:s=>body.querySelectorAll(s),addEventListener:(name,fn)=>events['document:'+name]=fn};
  const add=(id,parent=body,tag='div')=>{const node=new Node(tag);node.id=id;parent.append(node);return node;};
  const stage=add('diagnosticStage'),sidebar=add('sidebar',stage),menu=add('diagnosticSectionMenu',sidebar),list=add('orderBar',menu,'ul');sidebar.rect={top:350,bottom:410,height:60};
  add('toggleDiagnosticSections',sidebar,'button');add('diagnosticCurrentSection',sidebar);const content=add('diagnosticStageContent',stage);add('diagnostic-results',content);const blocks=add('blocks',content),wrap=add('benchWrap',content);
  const panels=[{id:'both',name:'视频',kind:'video'},{id:'world',name:'世界三维',kind:'scene'},{id:'robot',name:'MuJoCo',kind:'mujoco'},{id:'nums',name:'数值',kind:'nums'},{id:'loss',name:'损失',kind:'loss'},{id:'bench',name:'评测',kind:'tool',wrapId:'benchWrap'}],PANEL_BY=Object.fromEntries(panels.map(p=>[p.id,p]));
  function addSection(id){const section=add('diagnostic-panel-'+id,blocks,'section');section.dataset.pid=id;section.className='panel';return section;}
  const videoSection=addSection('both'),video=new Node('video');video.currentTime=12.5;video.pause=()=>calls.push('pause');videoSection.append(video);addSection('world');
  const state={loaded,nums,no_truth:false,rawOnly:false,order:panels.map(p=>p.id),hidden:new Set(['robot','bench']),panels:{both:{video}}};
  const sandbox={document,state,PANEL_BY,PERSISTENT_PANELS:{benchWrap:wrap},diagnosticView:'results',info:{},Date,
    window:{innerWidth:width,innerHeight:800,scrollY:100,addEventListener:(name,fn)=>events[name]=fn,scrollTo:value=>calls.push(['scroll',value]),matchMedia:()=>({matches:false})},
    getComputedStyle:()=>({position:'static'}),requestAnimationFrame:fn=>{frames.push(fn);return frames.length;},$:s=>document.querySelector(s),
    showDiagnosticView:mode=>{sandbox.diagnosticView=mode;calls.push(['view',mode]);},syncMujocoButton(){},updateExportButton(){},requestDraw(){},
    buildPanels:()=>{calls.push('build');if(!state.hidden.has('robot')&&!document.querySelector('#blocks section[data-pid="robot"]'))addSection('robot');},
    renderDatasetAnalysis:()=>calls.push('render-analysis')};
  for(const name of ['loadBenchModels','loadBenchCaps','loadBenchSizes','loadBenchGpus','loadBenchAliyunDefaults','loadBatchModels','loadLdRuns'])sandbox[name]=()=>calls.push(['metadata',name]);
  context=vm.createContext(sandbox);vm.runInContext(nav,context);
  const run=code=>vm.runInContext(code,context),flush=()=>{while(frames.length)frames.shift()();};
  return {run,context,sandbox,state,document,body,calls,events,list,wrap,video,videoSection,flush};
}
test('visible section navigation preserves DOM, playback time and does not rebuild',()=>{
  const f=fixture();f.run("renderOrderBar();navigateDiagnosticSection('world')");f.flush();
  assert.equal(f.calls.includes('build'),false);assert.equal(f.video.currentTime,12.5);assert.equal(f.document.querySelector('#blocks section[data-pid="both"]'),f.videoSection);
  assert.equal(f.list.querySelector('li[data-id="world"] .onm').attributes['aria-current'],'location');
  assert.ok(f.calls.some(x=>Array.isArray(x)&&x[0]==='scroll'));
});
test('hidden robot navigation requires its separate explicit generate action',()=>{
  const f=fixture();f.run("renderOrderBar();navigateDiagnosticSection('robot')");
  assert.equal(f.state.hidden.has('robot'),true);assert.equal(f.calls.includes('build'),false);
  const notice=f.document.querySelector('#diagnosticSectionNotice'),action=notice.querySelector('button');assert.equal(action.textContent,'开启并生成');
  action.onclick();assert.equal(f.calls.filter(x=>x==='build').length,1);assert.equal(f.state.hidden.has('robot'),false);
});
test('tools open without results using the original persistent node and metadata reads only',async()=>{
  const f=fixture({loaded:false});f.run("renderOrderBar();navigateDiagnosticSection('bench')");await Promise.resolve();
  assert.equal(f.sandbox.diagnosticView,'tools');assert.equal(f.state.hidden.has('bench'),false);assert.equal(f.wrap.parentNode.dataset.pid,'bench');assert.equal(f.calls.includes('build'),false);
  assert.equal(f.calls.filter(x=>Array.isArray(x)&&x[0]==='metadata').length,5);
  assert.equal(f.video.currentTime,12.5);
});
test('unloaded data and missing numeric/GT supervision have honest empty states',()=>{
  const f=fixture({loaded:false,nums:null});f.run("navigateDiagnosticSection('world')");assert.match(f.document.querySelector('#diagnosticSectionNotice p').textContent,/尚未加载/);assert.equal(f.calls.includes('build'),false);
  f.state.loaded=true;assert.equal(f.run("diagnosticSectionAvailability(PANEL_BY.nums).ready"),false);
  f.state.no_truth=true;f.run("navigateDiagnosticSection('loss')");assert.match(f.document.querySelector('#diagnosticSectionNotice p').textContent,/没有 GT/);assert.equal(f.document.querySelector('#diagnosticSectionNotice button').textContent,'查看已有结果');
});
test('keyboard and drag ordering change CSS order without rebuilding mounted media',()=>{
  const f=fixture();f.run('renderOrderBar()');const button=f.list.querySelector('li[data-id="world"] .onm'),event={altKey:true,key:'ArrowUp',preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;}};button.onkeydown(event);
  assert.equal(f.state.order[0],'world');assert.equal(event.prevented,true);assert.equal(event.stopped,true);assert.equal(f.document.querySelector('#blocks section[data-pid="world"]').style.order,0);assert.equal(f.calls.includes('build'),false);assert.equal(f.video.currentTime,12.5);
  f.run("applyDiagnosticSectionOrder('world',{before:'loss'})");assert.equal(f.state.order.indexOf('world')+1,f.state.order.indexOf('loss'));assert.equal(f.calls.includes('build'),false);
});
test('narrow menu closes on navigation and scroll offset reserves its future sticky height',()=>{
  const f=fixture({width:820});f.run('wireDiagnosticSections()');const toggle=f.document.querySelector('#toggleDiagnosticSections');toggle.onclick();assert.equal(toggle.attributes['aria-expanded'],'true');
  assert.equal(f.run('diagnosticScrollOffset()'),72,'header offset applies even before the menu has reached its sticky top');
  f.run("navigateDiagnosticSection('world')");f.flush();assert.equal(toggle.attributes['aria-expanded'],'false');const scroll=f.calls.find(x=>Array.isArray(x)&&x[0]==='scroll');assert.equal(scroll[1].top,478);
});

function retainedMasterFixture(){
  const f=fixture();
  f.context.PANEL_BY.both_2d={id:'both_2d',name:'整体2D',kind:'video',content:'both'};
  f.videoSection.dataset.pid='both_2d';f.videoSection.id='diagnostic-panel-both_2d';f.document.querySelector('#blocks').replaceChildren(f.videoSection);
  Object.assign(f.state,{order:['both_2d'],panels:{both_2d:{id:'both_2d',kind:'video',video:f.video,displaySignature:JSON.stringify({layout:'side',rawOnly:false})}},fps:30,nframes:450,layout:'side'});
  f.video.readyState=2;f.context.masterVideo=f.video;
  Object.assign(f.context,{watchMasterVideo:()=>{},syncMujocoVideos:()=>{},syncDiagnosticView:()=>{}});
  const start=source.indexOf('function buildPanels(preserveTime){'),end=source.indexOf('// 当前帧取自主视频',start);
  vm.runInContext(source.slice(start,end),f.context);
  const frameStart=source.indexOf('function frameOf(video){'),frameEnd=source.indexOf('function requestDraw()',frameStart);
  vm.runInContext(source.slice(frameStart,frameEnd),f.context);
  return f;
}
test('seek then hide 2D then rebuild preserves the original master node and numeric frame',()=>{
  const f=retainedMasterFixture();
  f.run("setDiagnosticSectionVisibility('both_2d',false);buildPanels(true)");
  assert.equal(f.run('masterVideo'),f.video);assert.equal(f.run('frameOf(masterVideo)'),375);
  assert.equal(f.document.querySelector('#blocks section[data-pid="both_2d"]'),f.videoSection);
  assert.equal(f.videoSection.hidden,true);assert.equal(f.videoSection.dataset.clockOnly,'false');
  f.state.layout='overlay';f.run('buildPanels(true)');
  assert.equal(f.videoSection.dataset.clockOnly,'true');assert.equal(f.run('frameOf(masterVideo)'),375);
  const builds=[];f.context.buildPanels=preserveTime=>builds.push({preserveTime,time:f.run('masterVideo.currentTime'),hidden:f.state.hidden.has('both_2d'),layout:f.state.layout});
  f.run("navigateDiagnosticSection('both_2d')");assert.equal(builds.length,0,'navigation alone must not refresh hidden media');
  f.run("setDiagnosticSectionVisibility('both_2d',true)");
  assert.deepEqual(builds,[{preserveTime:true,time:12.5,hidden:false,layout:'overlay'}],'explicitly showing stale media rebuilds with current options and retained time');
});
test('a retained primary video that already failed releases render dependencies immediately',()=>{
  const f=retainedMasterFixture();f.video.readyState=0;f.video.error={code:3};
  f.context.Promise=function(executor){executor(()=>f.calls.push('primary-ready'));return {then(){}};};
  f.run("setDiagnosticSectionVisibility('both_2d',false);buildPanels(true)");
  assert.equal(f.calls.filter(x=>x==='primary-ready').length,1);
  assert.equal(f.video.handlers.loadeddata,undefined);assert.equal(f.video.handlers.error,undefined);
});

test('task polling updates hidden tool status without reopening it or stealing the current view',async()=>{
  const cases=[
    ['startDatasetAnalysisPoll','async function loadAnalysisPage','_analysisPolling','analysis','renderDatasetAnalysis','showDatasetAnalysis'],
    ['startBatchPoll','function renderBatch','_batchPolling','batch','renderBatch','showBatch'],
    ['startBenchPoll','function benchProgressValues','_benchPolling','bench','renderBench','showBench'],
    ['startLdPoll','function renderLd','_ldPolling','logdiff','renderLd','showLd'],
  ];
  for(const [name,endMarker,flag,id,render,show]of cases){
    const f=fixture();f.state.hidden.add(id);f.state[id]={backend:'local'};let count=0;
    Object.assign(f.context,{[flag]:false,_analysisLoadedKey:'',setTimeout:fn=>fn(),loadAnalysisPage:async()=>{},loadBenchHistory:()=>{},
      getJSON:async()=>({running:++count===1,report:{},result:{},manifest:{},result_ready:true,cache_key:'cached',analysis_type:'video'}),
      [render]:()=>f.calls.push('status'),[show]:()=>f.calls.push('unexpected-open')});
    const start=source.indexOf('async function '+name+'('),end=source.indexOf(endMarker,start);
    vm.runInContext(source.slice(start,end),f.context);await f.run(name+'()');
    assert.equal(count,2,id);assert.equal(f.state.hidden.has(id),true,id);assert.equal(f.sandbox.diagnosticView,'results',id);
    assert.equal(f.calls.includes('unexpected-open'),false,id);assert.equal(f.calls.includes('build'),false,id);
  }
});
