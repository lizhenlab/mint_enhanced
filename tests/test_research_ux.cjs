'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function fixture(){
  const nodes={},storage=new Map();
  const context=vm.createContext({structuredClone,setTimeout,clearTimeout,
    document:{addEventListener(){}},window:{addEventListener(){}},
    localStorage:{setItem(k,v){storage.set(k,v);},getItem(k){return storage.get(k)||null;},removeItem(k){storage.delete(k);}},
    $:id=>nodes[id]||={value:'',dataset:{}},el:()=>({dataset:{}}),
    api:async()=>({ready:true}),hubError:()=>{}});
  const root=path.resolve(__dirname,'../mint/comparison/web');
  for(const name of ['research.js','enhancements.js'])vm.runInContext(fs.readFileSync(path.join(root,name),'utf8'),context,{filename:name});
  const run=code=>vm.runInContext(code,context);
  run(`nextUI.annotation={run_id:'r',frame:7,head:'v1',width:640,height:480,result_identity:{hash:'a'},source_identity:{hash:'video'},points:Array.from({length:2},()=>Array.from({length:21},()=>[10,20])),visible:Array.from({length:2},()=>Array(21).fill(true))};`);
  for(const id of ['annotationReviewer','annotationComment','annotationSwap','annotationTest','annotationHand','annotationJoint'])nodes[id]={value:'',checked:false};
  nodes.annotationReviewer.value='reviewer';nodes.annotationHand.value='0';nodes.annotationJoint.value='0';
  nodes.workflowKind={value:'pipeline'};nodes.workflowSource={value:'/video.mp4'};nodes.workflowGpus={value:'1'};
  nodes.workflowSubmit={dataset:{},disabled:false};nodes.workflowCheck={dataset:{},textContent:''};
  return {context,nodes,storage,run};
}

test('annotation draft requires the same server parent, source, result and point schema',()=>{
  const {run,storage}=fixture();run(`nextUI.dirty=true;persistAnnotationDraft();`);
  assert.equal(storage.size,1);
  assert.equal(run(`validAnnotationDraft(JSON.parse(localStorage.getItem(annotationDraftKey(nextUI.annotation))),nextUI.annotation)`),true);
  for(const mutation of ["parent:'stale'","source_identity:{hash:'changed'}","result_identity:{hash:'changed'}","width:1280","visible:[[true]]","points:[[[Infinity,0]]]"])
    assert.equal(run(`validAnnotationDraft({...annotationDraftSnapshot(),${mutation}},nextUI.annotation)`),false,mutation);
});

test('annotation keyboard nudges clamp to image bounds and do not move playback',()=>{
  const {context,run}=fixture();context.drawAnnotation=()=>{};context.syncAnnotationPoint=()=>{};context.annotationChanged=()=>{};context.pushAnnotationUndo=()=>{};
  context.event={key:'ArrowRight',shiftKey:true,preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;}};
  run(`annotationKeyboard(event)`);assert.equal(run(`nextUI.annotation.points[0][0][0]`),20);assert.equal(context.event.stopped,true);
  context.event={key:'ArrowUp',shiftKey:true,preventDefault(){},stopPropagation(){}};
  run(`nextUI.annotation.points[0][0][1]=2;annotationKeyboard(event)`);assert.equal(run(`nextUI.annotation.points[0][0][1]`),0);
});

test('workflow submission remains locked after failed preflight and after parameter changes',async()=>{
  const {context,run,nodes}=fixture();context.api=async()=>({ready:false,errors:['missing dependency']});
  await run(`checkWorkflowReadiness()`);assert.equal(nodes.workflowSubmit.disabled,true);assert.match(nodes.workflowCheck.textContent,/missing dependency/);
  context.api=async()=>({ready:true,errors:[]});await run(`checkWorkflowReadiness()`);assert.equal(nodes.workflowSubmit.disabled,false);
  run(`invalidateWorkflowReadiness()`);assert.equal(nodes.workflowSubmit.disabled,true);
  nodes.workflowSubmit.dataset.busy='true';await run(`checkWorkflowReadiness()`);assert.equal(nodes.workflowSubmit.disabled,true,'a successful recheck must not enable a submission already in flight');
});

test('a stale successful preflight cannot unlock a changed form',async()=>{
  const {context,run,nodes}=fixture();let resolve;context.api=()=>new Promise(r=>resolve=r);
  const pending=run(`checkWorkflowReadiness()`);nodes.workflowGpus.value='2';resolve({ready:true,errors:[]});await pending;
  assert.equal(nodes.workflowSubmit.disabled,true);
});

test('saving an annotation snapshots the request and retains edits made while saving',async()=>{
  const {context,run,storage}=fixture();let resolve,request;context.refreshAnnotationDraft=()=>{};
  context.api=async(_url,body)=>{request=body;return new Promise(r=>resolve=r);};
  run(`nextUI.annotationRevision=0;nextUI.dirty=true`);const pending=run(`saveAnnotation()`);
  run(`nextUI.annotation.points[0][0][0]=99;nextUI.annotationRevision=1`);resolve({id:'v2',purpose:'review'});await pending;
  assert.equal(request.points[0][0][0],10,'in-flight request must retain its original coordinate');
  assert.equal(run(`nextUI.dirty`),true);assert.equal(run(`nextUI.annotation.head`),'v2');
  const draft=JSON.parse([...storage.values()][0]);assert.equal(draft.parent,'v2');assert.equal(draft.points[0][0][0],99);
});

test('quality panel mounts annotation controls before resolving document IDs',()=>{
  const {context,run}=fixture();
  // IDs are discoverable only in the attached tree, matching browser getElementById.
  class Element {
    constructor(tag,text){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.attributes={};this.textContent=text||'';this.value='';}
    append(...children){for(const child of children){this.children.push(child);if(child&&typeof child==='object')child.parentElement=this;}}
    prepend(...children){for(const child of children.reverse()){this.children.unshift(child);if(child&&typeof child==='object')child.parentElement=this;}}
    setAttribute(key,value){this.attributes[key]=value;}
    addEventListener(){}
    get options(){return this.children.filter(child=>child.tagName==='OPTION');}
  }
  const root=new Element('main'),quality=new Element('section');quality.id='hub-quality';root.append(quality);
  const find=(node,id)=>node.id===id?node:node.children?.map(child=>find(child,id)).find(Boolean);
  context.el=(tag,text,className)=>{const element=new Element(tag,text);element.className=className||'';return element;};
  context.$=id=>find(root,id)||null;
  context.inputLabel=(label,input)=>{const node=context.el('label',label);node.append(input);return node;};
  context.selectInput=(choices,value)=>{const select=context.el('select');for(const [key,label]of choices){const option=context.el('option',label);option.value=key;select.append(option);}select.value=value;return select;};
  run('qualityExtensions()');
  assert.ok(context.$('annotationCanvas'));
  assert.equal(context.$('annotationDraftStatus').attributes.role,'status');
  assert.equal(typeof context.$('annotationHand').onchange,'function');
  assert.ok(context.$('annotationTest'),'developer-only marker remains available inside its mounted details');
});
