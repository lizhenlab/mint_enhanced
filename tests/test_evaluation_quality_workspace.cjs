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
    get options(){return this.children.filter(child=>child.tagName==='OPTION');}get lastChild(){return this.children.at(-1)||null;}get nextElementSibling(){return this.parentElement?.children[this.parentElement.children.indexOf(this)+1]||null;}
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
    addEventListener(type,fn){this.listeners??={};this.listeners[type]=fn;}focus(){this.focused=true;}scrollIntoView(){this.scrolled=true;}close(){this.open=false;}show(){this.open=true;}showModal(){this.open=true;}checkValidity(){return true;}reportValidity(){return true;}
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
  for(const file of ['platform.js','research.js','enhancements.js'])run(fs.readFileSync(path.join(__dirname,'../mint/comparison/web',file),'utf8'));
  context.hubError=error=>errors.push(error);context.syncPlatformNavigation=()=>{};context.platformRoute=()=>{};context.refreshPlatformContext=()=>{};context.pollPlatform=async()=>{};
  context.cloneComposerAlgorithms=host=>{host.replaceChildren();const box=create('div',undefined,'algorithm');box.dataset.algorithm='mint';const check=create('input');check.type='checkbox';check.checked=true;const checkpoints=create('textarea');checkpoints.dataset.selection='checkpoint';box.append(check,checkpoints);host.append(box);};
  for(const kind of ['batch','evaluation'])get(kind+'Setup').append(get(kind+'Composer'));
  context.drawQualityCurve=()=>{};context.drawAnnotation=()=>{};context.refreshAnnotationDraft=()=>{};context.persistAnnotationDraft=()=>{};
  get('platformDialog').open=true;run("platform.pane='quality';initializeQuality($('hub-quality'));qualityExtensions();initializeQualityWorkspace()");
  return {nodes,get,run,context,Element,create,body,requests,errors,opened,location};
}
function plain(value){return JSON.parse(JSON.stringify(value));}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function comparison(id='quality-source'){
  return {id,title:'Comparison '+id,sample_id:'sample-'+id,runs:[
    {id:id+'-pred',label:'MINT prediction',algorithm:'mint',result:{reference_role:'model_prediction'}},
    {id:id+'-derived',label:'Derived camera result',algorithm:'reference',result:{reference_role:'algorithm_derived'}},
    {id:id+'-gt',label:'Declared GT',algorithm:'reference',result:{reference_role:'user_declared_ground_truth'}},
    {id:id+'-unfinished',label:'No output',algorithm:'hawor_full'},
  ]};
}
function selectComparison(x,value=comparison()){x.context.selectedComparison=value;x.run('setQualityComparison(selectedComparison)');return value;}
function quality(value,{id='analysis',run=value.id+'-pred',reference=value.id+'-gt',offset=0}={}){
  return {id,run_id:run,identity:{reference_run:reference},offset,frames:120,issue_count:80,paired_frames:100,missing_output_frames:0,comparison_role:'declared_GT_error',curves:[],issues:[{start:offset,end:offset+3,reason:'joint_error_against_declared_GT'}]};
}
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

test('quality source selection loads an independent comparison without changing the main viewer',async()=>{
  const x=fixture(),main=comparison('main'),chosen=comparison('independent');x.context.state.comparison=main;x.context.state.frame=37;
  selectComparison(x,chosen);assert.equal(x.context.state.comparison,main);assert.equal(x.context.state.frame,37);assert.equal(x.run('qualityComparisonData().id'),'independent');assert.equal(x.get('annotationFrame').value,'1');
  assert.equal(x.get('annotationReadPlaying').disabled,true);assert.equal(x.get('annotationReadFrame').disabled,false);assert.match(x.get('qualityContextSummary').textContent,/independent/);
  x.get('qualityComparison').value='new-source';x.get('qualityComparison').onchange();assert.equal(x.run('qualityComparisonData()'),null);assert.equal(x.get('qualityAnalyze').disabled,true);assert.equal(x.context.state.comparison,main);
  x.context.api=async url=>{assert.equal(url,'api/comparisons/new-source');return comparison('new-source');};await x.run('loadQualityComparison()');assert.equal(x.run('qualityComparisonData().id'),'new-source');assert.equal(x.context.state.comparison,main);
});

test('declared GT cannot be a prediction, while derived results remain selectable predictions',()=>{
  const x=fixture(),value=selectComparison(x),predictions=x.get('qualityRun').options.map(option=>option.value);
  assert.deepEqual(predictions,['',value.id+'-pred',value.id+'-derived']);assert.equal(x.get('qualityReference').value,value.id+'-gt');
  assert.equal(x.get('qualityReference').options.some(option=>option.value===x.get('qualityRun').value),false);
  x.get('qualityRun').value=value.id+'-derived';x.get('qualityRun').onchange();assert.equal(x.get('qualityAnalyze').disabled,false);assert.equal(x.get('detailRun').value,value.id+'-derived');assert.equal(x.get('qualityReference').options.some(option=>option.value===value.id+'-derived'),false);
});

test('explicitly choosing no reference survives refresh and prediction changes, and self-reference is rejected',async()=>{
  const x=fixture(),value=selectComparison(x);x.get('qualityReference').value='';x.get('qualityReference').onchange();
  x.run('qualityRunChoices()');assert.equal(x.get('qualityReference').value,'');assert.equal(x.get('detailReference').value,'');assert.equal(x.get('qualityBreakdownAnalyze').disabled,true);
  x.get('qualityRun').value=value.id+'-derived';x.get('qualityRun').onchange();assert.equal(x.get('qualityReference').value,'');
  x.get('qualityReference').value=value.id+'-derived';x.run('updateQualityActions()');assert.equal(x.get('qualityAnalyze').disabled,true);await assert.rejects(x.run('analyzeQuality()'),/不同结果/);assert.equal(x.requests.length,0);
  selectComparison(x,comparison('another'));assert.equal(x.get('qualityReference').value,'another-gt');
});

test('changing the quality context clears prior analysis and disables old review and download actions',async()=>{
  const x=fixture(),value=selectComparison(x);x.context.analysis=quality(value);x.run('researchUI.qualitySignature=qualityAnalysisSignature()');await x.run('showQuality(analysis)');
  x.run("nextUI.breakdown={groups:{}};updateQualityActions()");x.get('breakdownRows').textContent='Old detailed error';x.get('reviewComment').value='Old review';
  assert.equal(x.get('qualitySaveReview').disabled,false);assert.equal(x.get('qualityBreakdownDownload').disabled,false);assert.equal(x.get('qualityDownload').hidden,false);
  x.get('qualityRun').value=value.id+'-derived';x.get('qualityRun').onchange();
  assert.equal(x.run('researchUI.quality'),null);assert.equal(x.run('nextUI.breakdown'),null);assert.equal(x.get('qualityIssues').textContent,'');assert.equal(x.get('breakdownRows').textContent,'');assert.equal(x.get('qualityDownload').hidden,true);assert.equal(x.get('qualityCanvas').hidden,true);assert.equal(x.get('qualitySaveReview').disabled,true);assert.equal(x.get('qualityBreakdownDownload').disabled,true);assert.equal(x.get('reviewComment').value,'');
  await assert.rejects(x.run('saveQualityReview()'),/先分析当前/);assert.equal(x.requests.length,0);
});

test('late source-loading and analysis responses cannot replace a newer quality context',async()=>{
  const x=fixture(),main=comparison('main'),a=deferred(),b=deferred();x.context.state.comparison=main;
  x.get('qualityComparison').value='a';x.get('qualityComparison').onchange();x.context.api=url=>url.endsWith('/a')?a.promise:b.promise;
  const first=x.run('loadQualityComparison()');x.get('qualityComparison').value='b';x.get('qualityComparison').onchange();const second=x.run('loadQualityComparison()');
  b.resolve(comparison('b'));await second;a.resolve({invalid:'old response'});assert.equal(await first,null);assert.equal(x.run('qualityComparisonData().id'),'b');assert.equal(x.context.state.comparison,main);
  const old=deferred(),current=deferred();let requests=0;x.context.api=()=>++requests===1?old.promise:current.promise;
  const oldAnalysis=x.run('analyzeQuality()');x.get('qualityRun').value='b-derived';x.get('qualityRun').onchange();const freshAnalysis=x.run('analyzeQuality()');
  current.resolve(quality(comparison('b'),{id:'current-analysis',run:'b-derived'}));await freshAnalysis;old.resolve({invalid:'old analysis'});assert.equal(await oldAnalysis,null);assert.equal(x.run('researchUI.quality.id'),'current-analysis');assert.equal(x.get('qualitySaveReview').disabled,false);
});

test('old issue pages and breakdown replies are ignored after selecting a different prediction',async()=>{
  const x=fixture(),value=selectComparison(x);x.context.analysis=quality(value);x.run('researchUI.qualitySignature=qualityAnalysisSignature()');await x.run('showQuality(analysis)');
  const page=deferred();x.context.api=()=>page.promise;const paging=x.run('loadQualityPage(1)');
  x.get('qualityRun').value=value.id+'-derived';x.get('qualityRun').onchange();page.resolve({invalid:'retired quality page'});await paging;assert.equal(x.run('researchUI.quality'),null);assert.equal(x.get('qualityIssues').textContent,'');
  const breakdown=deferred();x.context.api=()=>breakdown.promise;const loading=x.run('showBreakdown()');x.get('qualityReference').value='';x.get('qualityReference').onchange();
  breakdown.resolve({invalid:'retired breakdown'});assert.equal(await loading,null);assert.equal(x.run('nextUI.breakdown'),null);assert.equal(x.get('breakdownRows').textContent,'');assert.equal(x.get('qualityBreakdownDownload').disabled,true);
});

test('threshold changes invalidate an otherwise current quality analysis without launching inference',async()=>{
  const x=fixture(),value=selectComparison(x);x.context.analysis=quality(value);x.run('researchUI.qualitySignature=qualityAnalysisSignature()');await x.run('showQuality(analysis)');
  x.get('qualityThreshold').value='75';x.get('qualityThreshold').oninput();assert.equal(x.run('researchUI.quality'),null);assert.equal(x.get('qualitySaveReview').disabled,true);assert.equal(x.get('qualityNext').disabled,true);assert.equal(x.requests.length,0);
  for(const text of ['', '0', '-1', 'Infinity']){x.get('qualityThreshold').value=text;await assert.rejects(x.run('analyzeQuality()'),/有限正数/);}assert.equal(x.requests.length,0);
});

test('evaluation and quality expose two primary entries while specialist tools and thresholds start collapsed',()=>{
  const x=fixture(),html=fs.readFileSync(path.join(__dirname,'../mint/comparison/web/index.html'),'utf8'),nav=html.match(/<nav id="evaluationRoutes"[\s\S]*?<\/nav>/)?.[0];assert.ok(nav);
  const tree=fragment(x,nav),routes=tree.querySelector('#evaluationRoutes');x.body.append(tree);
  assert.deepEqual(routes.children.filter(node=>node.tagName==='BUTTON'&&!node.hidden).map(node=>node.dataset.evaluationRoute),['dataset','quality']);
  const more=routes.querySelector('details');assert.equal(more.open,false);assert.equal(more.querySelector('button').dataset.evaluationRoute,'regression');
  for(const details of x.get('hub-quality').querySelectorAll('.quality-advanced'))assert.equal(details.open,false);assert.equal(x.get('qualityCurveMetric').closest('details').open,false);
});

test('evaluation requires GT before advancing, with frame and calibration options collapsed by default',()=>{
  const x=fixture();x.run("createComposer('evaluation')");
  assert.equal(x.get('evaluation-advanced-settings').open,false);assert.equal(x.get('evaluation-add-video').hidden,true);assert.equal(x.get('evaluation-reference').value,'required');
  x.run("platform.selection=new Map([['gt',{source:'/with-gt.mp4',dataset:{root:'/data',episode:0},annotation:{ready:true}}],['no-gt',{source:'/without-gt.mp4'}]]);selectionSummary()");
  assert.equal(x.get('evaluation-to-settings').disabled,true);assert.match(x.get('evaluation-input-summary').textContent,/缺少 GT/);assert.throws(()=>x.run("collectionPayload('evaluation')"),/每个输入都有 GT/);
  x.run("platform.selection.delete('no-gt');selectionSummary()");assert.equal(x.get('evaluation-to-settings').disabled,false);assert.equal(x.run("collectionPayload('evaluation').items[0].dataset.attach_gt"),true);assert.equal(x.run("collectionPayload('evaluation').items[0].dataset.use_calibration"),false);assert.equal(x.requests.length,0);
});

test('evaluation reports show five core columns and retain additional metrics in collapsed details',()=>{
  const x=fixture();x.context.report={protocol:{name:'Frozen protocol'},total_sequences:5,scorable_sequences:4,rows:[{label:'MINT',scored_sequences:3,failed_sequences:2,gt_geometry_coverage:.8,completed_sequences:2,partial_sequences:1,unscorable_sequences:1}]};
  x.run('renderEvaluationReport(report)');const host=x.get('evaluationResults'),core=host.querySelector('.evaluation-summary-table').querySelector('table'),details=host.querySelector('.evaluation-metric-details');
  assert.equal(core.querySelectorAll('th').length,5);assert.equal(core.querySelectorAll('tr').length,2);assert.match(core.textContent,/MINT/);assert.match(core.textContent,/80%/);assert.equal(details.open,false);assert.ok(details.querySelectorAll('th').length>5);assert.match(details.textContent,/相机 ATE/);assert.match(host.querySelector('.evaluation-overview').textContent,/Frozen protocol/);
});
