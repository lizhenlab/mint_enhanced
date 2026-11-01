'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');

function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function fixture(){
  const nodes={},storage=new Map(),images=[],draws=[];
  class Element {
    constructor(tag='div',text=''){this.tagName=tag.toUpperCase();this.textContent=text||'';this.value='';this.checked=false;this.dataset={};this.children=[];this.attributes={};}
    append(...values){this.children.push(...values);for(const value of values)if(value&&typeof value==='object')value.parentElement=this;}
    replaceChildren(...values){this.children=[];this.append(...values);}
    setAttribute(key,value){this.attributes[key]=value;}
    closest(){return null;}
  }
  const get=id=>nodes[id]??=new Element();
  const context=vm.createContext({console,structuredClone,URLSearchParams,setTimeout,clearTimeout,queueMicrotask,Image:class {
    set src(value){this.url=value;images.push(this);if(context.autoImages)queueMicrotask(()=>this.onload());}
  },document:{addEventListener(){}},window:{addEventListener(){}},state:{comparison:{id:'comparison-a',sample_id:'sample-a'}},
    localStorage:{setItem(key,value){storage.set(key,value);},getItem(key){return storage.get(key)||null;},removeItem(key){storage.delete(key);}},
    $:get,el:(tag,text)=>new Element(tag,text),api:async()=>{throw new Error('Unexpected API call');},hubError(error){throw error;},autoImages:true});
  const run=code=>vm.runInContext(code,context);
  for(const name of ['research.js','enhancements.js'])run(fs.readFileSync(path.join(__dirname,'../mint/comparison/web',name),'utf8'));
  context.drawAnnotation=()=>draws.push('draw');context.syncAnnotationPoint=()=>{};context.refreshAnnotationDraft=()=>{};
  for(const [id,value]of Object.entries({detailRun:'prediction',annotationFrame:'8',annotationReviewer:'reviewer',annotationComment:'',annotationHand:'0',annotationJoint:'0'}))get(id).value=value;
  get('annotationCanvas').getContext=()=>({});get('annotationSwap').checked=false;get('annotationTest').checked=false;
  return {context,run,get,nodes,storage,images,draws};
}
function annotation({run='prediction',frame=7,head='v1'}={}){return {run_id:run,sample_id:'sample-a',frame,source_frame:frame+100,width:640,height:480,head,result_identity:{sha:'geometry'},source_identity:{sha:'video'},points:Array.from({length:2},()=>Array.from({length:21},()=>[10,20])),visible:Array.from({length:2},()=>Array(21).fill(true))};}
function install(x,value=annotation()){x.context.value=value;x.run('nextUI.annotation=structuredClone(value);nextUI.annotationRevision=0;nextUI.dirty=true;nextUI.image={old:true};nextUI.undo=[1]');return x.run('nextUI.annotation');}
async function tick(){await Promise.resolve();await Promise.resolve();}
function quality(issues,{offset=0,id='quality',run='prediction',limit=50}={}){return {id,run_id:run,sample_id:'sample-a',frames:30000,issue_count:issues.length,offset,issues:issues.slice(offset,offset+limit)};}
function setupIssues(x,issues,options={}){
  x.context.quality=quality(issues,options);x.run('researchUI.quality=quality');const requests=[],loaded=[];
  x.context.api=async url=>{requests.push(url);const query=new URLSearchParams(url.split('?')[1]);return quality(issues,{offset:Number(query.get('offset')),limit:Number(query.get('limit'))});};
  x.context.loadAnnotation=async()=>{const value=annotation({frame:Number(x.get('annotationFrame').value)-1});x.context.loaded=value;x.run('nextUI.annotation=loaded');loaded.push(value.frame);return value;};
  return {requests,loaded};
}

test('annotation load drops a superseded frame before fetching saved data or video',async()=>{
  const x=fixture(),request=deferred();install(x);x.context.api=()=>request.promise;
  const pending=x.run('loadAnnotation()');x.get('annotationFrame').value='9';request.resolve(annotation({head:'saved'}));
  assert.equal(await pending,null);assert.equal(x.images.length,0);assert.equal(x.run('nextUI.annotation'),null);assert.equal(x.storage.size,1,'unsaved old frame retained');
});

test('canonical quality comparison changes invalidate pending annotation loads independently of viewer state',async()=>{
  const x=fixture(),request=deferred();let canonical={id:'quality-source-a',sample_id:'sample-a'};
  x.context.qualityComparisonData=()=>canonical;x.context.qualityContextSignature=()=>canonical.id;
  x.context.api=()=>request.promise;const pending=x.run('loadAnnotation()');canonical={id:'quality-source-b',sample_id:'sample-b'};request.resolve(annotation({head:null}));
  assert.equal(await pending,null);assert.equal(x.images.length,0);assert.equal(x.context.state.comparison.id,'comparison-a');
});

test('changing prediction while a saved correction is loading never publishes that correction',async()=>{
  const x=fixture(),saved=deferred();let calls=0;x.context.api=()=>++calls===1?Promise.resolve(annotation()):saved.promise;
  const pending=x.run('loadAnnotation()');await tick();assert.equal(calls,2);x.get('detailRun').value='other';saved.resolve(annotation());
  assert.equal(await pending,null);assert.equal(x.images.length,0);
});

test('an image that finishes after the selected frame changes is ignored',async()=>{
  const x=fixture();x.context.autoImages=false;x.context.api=async()=>annotation({head:null});const pending=x.run('loadAnnotation()');await tick();assert.equal(x.images.length,1);
  x.get('annotationFrame').value='12';x.images[0].onload();assert.equal(await pending,null);assert.equal(x.run('nextUI.annotation'),null);
});

test('valid image loads publish only their matching run, frame and geometry',async()=>{
  const x=fixture();x.context.api=async()=>annotation({head:null});const value=await x.run('loadAnnotation()');
  assert.equal(value.run_id,'prediction');assert.equal(value.frame,7);assert.equal(x.run('nextUI.dirty'),false);assert.equal(x.get('annotationCanvas').width,640);assert.match(x.get('annotationStatus').textContent,/源帧 107/);
});

test('frame validation rejects invalid values without erasing an existing draft',async()=>{
  for(const frame of ['','0','-2','1.5','NaN']){const x=fixture();install(x);x.get('annotationFrame').value=frame;await assert.rejects(x.run('loadAnnotation()'),/整数样本帧/);assert.equal(x.run('nextUI.annotation.run_id'),'prediction');}
});

test('invalidating selection retains a deep draft even when browser storage is unavailable',()=>{
  const x=fixture();install(x);x.context.localStorage.setItem=()=>{throw new Error('blocked');};x.run('invalidateAnnotationContext()');
  assert.equal(x.run('nextUI.annotation'),null);assert.equal(x.run('nextUI.image'),null);assert.equal(x.run('nextUI.undo.length'),0);assert.equal(x.run('nextUI.dirty'),false);
  const draft=x.run("readAnnotationDraft({run_id:'prediction',frame:7})");assert.equal(draft.points[0][0][0],10);assert.equal(draft.parent,'v1');assert.match(x.get('annotationStatus').textContent,/当前会话/);
});

test('a save for an old context never advances the head or saved version in a different active frame',async()=>{
  const x=fixture(),request=deferred();install(x);x.context.api=()=>request.promise;
  const pending=x.run('saveAnnotation()');x.run('nextUI.annotation.points[0][0][0]=99;nextUI.annotationRevision=1;invalidateAnnotationContext()');
  x.get('detailRun').value='other';x.get('annotationFrame').value='3';install(x,annotation({run:'other',frame:2,head:'other-v1'}));
  request.resolve({id:'saved-old-v2',purpose:'review'});await pending;
  assert.equal(x.run('nextUI.annotation.head'),'other-v1');assert.equal(x.run('nextUI.savedAnnotation'),null);
  const old=x.run("readAnnotationDraft({run_id:'prediction',frame:7})");assert.equal(old.parent,'saved-old-v2');assert.equal(old.points[0][0][0],99);
});

test('save replies also guard direct picker edits without relying on change handlers',async()=>{
  const x=fixture(),request=deferred();const value=install(x);x.context.api=()=>request.promise;const pending=x.run('saveAnnotation()');x.get('detailRun').value='other';request.resolve({id:'v2',purpose:'review'});await pending;
  assert.equal(value.head,'v1');assert.equal(x.run('nextUI.savedAnnotation'),undefined);
  await assert.rejects(x.run('saveAnnotation()'),/先重新读取/);
});

test('successful saves remove both session and browser drafts, retaining edits made during another save',async()=>{
  const x=fixture();install(x);x.context.api=async()=>({id:'v2',purpose:'review'});await x.run('saveAnnotation()');
  assert.equal(x.storage.size,0);assert.equal(x.run("readAnnotationDraft({run_id:'prediction',frame:7})"),null);
  const request=deferred();x.run('nextUI.dirty=true');x.context.api=()=>request.promise;const pending=x.run('saveAnnotation()');
  x.run('nextUI.annotation.points[0][0][0]=99;nextUI.annotationRevision=1');request.resolve({id:'v3',purpose:'review'});await pending;
  assert.equal(x.run('nextUI.annotation.head'),'v3');assert.equal(x.run('nextUI.dirty'),true);assert.equal(x.run("readAnnotationDraft({run_id:'prediction',frame:7}).points[0][0][0]"),99);
});

test('next problem searches across distant pages and never jumps backwards',async()=>{
  const x=fixture(),issues=Array.from({length:700},(_,i)=>({start:i*30,end:i*30+4})),{requests,loaded}=setupIssues(x,issues);
  x.get('annotationFrame').value='10001';const issue=await x.run('nextAnnotationIssue(1)');
  assert.equal(issue.start,10020);assert.deepEqual(loaded,[10020]);assert.equal(x.get('reviewStart').value,10021);assert.ok(requests.length<=6,'binary page search remains bounded');
});

test('previous problem searches earlier pages instead of selecting a later loaded-page item',async()=>{
  const x=fixture(),issues=Array.from({length:700},(_,i)=>({start:i*30,end:i*30+4})),{loaded}=setupIssues(x,issues,{offset:500});
  x.get('annotationFrame').value='1001';const issue=await x.run('nextAnnotationIssue(-1)');assert.equal(issue.start,990);assert.deepEqual(loaded,[990]);
});

test('problem navigation boundaries do not change the current frame',async()=>{
  const x=fixture(),issues=[{start:0,end:1},{start:20,end:22}];setupIssues(x,issues);x.get('annotationFrame').value='21';await assert.rejects(x.run('nextAnnotationIssue(1)'),/最后/);assert.equal(x.get('annotationFrame').value,'21');
  x.get('annotationFrame').value='1';await assert.rejects(x.run('nextAnnotationIssue(-1)'),/第一个/);assert.equal(x.get('annotationFrame').value,'1');
});

test('a late issue page never navigates after another analysis or picker takes over',async()=>{
  const x=fixture(),issues=Array.from({length:700},(_,i)=>({start:i*30,end:i*30+4})),{loaded}=setupIssues(x,issues),request=deferred();x.context.api=()=>request.promise;x.get('annotationFrame').value='10001';
  const pending=x.run('nextAnnotationIssue(1)');x.run('researchUI.quality={id:"new",run_id:"prediction"}');request.resolve(quality(issues,{offset:200,limit:200}));
  assert.equal(await pending,null);assert.deepEqual(loaded,[]);assert.equal(x.get('annotationFrame').value,'10001');
});

test('malformed or changed pagination is rejected before loading any annotation frame',async()=>{
  const x=fixture(),issues=Array.from({length:700},(_,i)=>({start:i*30,end:i*30+4})),{loaded}=setupIssues(x,issues);x.context.api=async()=>({...quality(issues,{offset:200,limit:200}),id:'wrong'});
  await assert.rejects(x.run('nextAnnotationIssue(1)'),/分页已变化/);assert.deepEqual(loaded,[]);
});

test('returning to a correction uses its canonical quality source without changing the viewer',async()=>{
  const x=fixture(),calls=[];install(x);const canonical={id:'quality-a',sample_id:'sample-a',runs:[{id:'prediction'}]};let source=canonical;
  x.context.qualityComparisonData=()=>source;x.context.qualityContextSignature=()=>source.id;
  x.context.setQualityComparison=(value,options)=>{source=value;calls.push(['source',options]);x.get('detailRun').value=options.preferredRun;};
  x.context.openPlatform=async view=>calls.push(['platform',view]);x.context.showQualityView=view=>calls.push(['view',view]);
  x.context.openComparison=async()=>assert.fail('Returning to the editor must not replace the independent viewer');
  const result=await x.run('returnToAnnotation()');assert.equal(result.run_id,'prediction');assert.equal(x.context.state.comparison.id,'comparison-a');assert.equal(x.run('nextUI.dirty'),true);assert.equal(x.get('annotationReviewer').value,'reviewer');
  assert.deepEqual(calls.map(row=>row[0]),['source','platform','view']);assert.equal(calls[0][1].preserveAnnotation,true);assert.equal(calls[0][1].preferredRun,'prediction');assert.equal(calls[2][1],'annotation');
});

test('returning to a stored correction ignores a late parent lookup after the user changes frames',async()=>{
  const x=fixture(),request=deferred();install(x);x.context.api=()=>request.promise;x.context.setQualityComparison=()=>assert.fail('An old return request cannot switch the current source');x.context.openPlatform=async()=>assert.fail('An old return request cannot reopen the editor');
  const pending=x.run('returnToAnnotation()');x.get('annotationFrame').value='9';request.resolve({comparisons:['old-parent']});assert.equal(await pending,null);assert.equal(x.context.state.comparison.id,'comparison-a');
});
