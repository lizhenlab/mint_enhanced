'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {defaultResultSelection,normalizeResultSelection,toggleResultSelection,metricValue,metricPairScope,filterMetricRows,handPostprocessLabel,runProtocol,resultCandidateNavigation,resultStateLabel,resultReferenceRole}=require('../mint/comparison/web/result-tools.js');
const runs=[{id:'m',label:'MINT',algorithm:'mint'},{id:'h',label:'HaWoR Full',algorithm:'hawor_full'},{id:'hc',label:'HaWoR Camera',algorithm:'hawor_camera'},{id:'a',label:'ACE',algorithm:'ace_kfree'}];

test('default three-column view includes independent algorithm families',()=>{
  assert.deepEqual(defaultResultSelection(runs),{selected:['m','h','a'],reference:'m'});
});
test('a declared GT is pinned without changing prediction metadata',()=>{
  const gt={id:'gt',algorithm:'reference',result:{reference_role:'user_declared_ground_truth'}};
  assert.deepEqual(defaultResultSelection([...runs,gt]),{selected:['gt','m','h','a'],reference:'gt'});
  assert.equal(runs[0].result,undefined);
});
test('restored selections discard stale IDs and duplicates and retain their pinned baseline',()=>{
  assert.deepEqual(normalizeResultSelection(runs,{selected:['gone','a','a'],reference:'m'}),{selected:['m','a'],reference:'m'});
  assert.deepEqual(normalizeResultSelection(runs,{selected:['gone'],reference:'gone'}),defaultResultSelection(runs));
  assert.deepEqual(normalizeResultSelection([],null),{selected:[],reference:null});
});
test('the pinned result prevents an empty display while alternatives can be removed',()=>{
  const one={selected:['m'],reference:'m'};
  assert.deepEqual(toggleResultSelection(one,'m',false),one);
  assert.deepEqual(toggleResultSelection({selected:['m','a'],reference:'m'},'a',false),one);
  assert.deepEqual(toggleResultSelection(one,'a',true),{selected:['m','a'],reference:'m'});
});
test('metric selection includes only displayed pairs with the baseline, and all mode preserves every pair',()=>{
  const rows=[{first_run:'m',second_run:'h'},{first_run:'h',second_run:'a'},{first_run:'m',second_run:'a'}];
  const selection={selected:['m','a'],reference:'a'};
  assert.deepEqual(filterMetricRows(rows,selection,'selected'),[rows[2]]);
  assert.deepEqual(filterMetricRows(rows,selection,'all'),rows);
  assert.deepEqual(filterMetricRows(rows,{selected:['m'],reference:'m'},'selected'),[]);
});
test('missing, nonfinite, zero and small metre errors are formatted distinctly',()=>{
  assert.equal(metricValue(null),'—');assert.equal(metricValue(undefined),'—');assert.equal(metricValue(NaN),'—');
  assert.equal(metricValue(Infinity),'—');assert.equal(metricValue(0),'0.00');assert.equal(metricValue(.000123,4),'0.0001');
});
test('choosing a baseline never promotes an algorithm discrepancy to GT accuracy',()=>{
  assert.equal(metricPairScope({scope:'algorithm_discrepancy_not_accuracy',reference:'m'}),'算法差异');
  assert.equal(metricPairScope({scope:'error_against_declared_GT'}),'参考误差');
});
test('candidate navigation follows all alternatives with explicit boundaries and never changes overview choices',()=>{
  const view={reference:'m',candidate:'h',selected:['m','a'],layout:'overview',pairSide:'reference'},before=JSON.stringify(view);
  assert.deepEqual(resultCandidateNavigation(runs,view),{candidate:'h',index:1,total:3,previous:null,next:'hc'});
  assert.deepEqual(resultCandidateNavigation(runs,{...view,candidate:'a'}),{candidate:'a',index:3,total:3,previous:'hc',next:null});
  assert.equal(resultCandidateNavigation(runs,{...view,candidate:'m'}).candidate,'a','a newly pinned candidate falls back to a selected alternative');
  assert.deepEqual(resultCandidateNavigation([runs[0]],view),{candidate:null,index:0,total:0,previous:null,next:null});
  assert.equal(JSON.stringify(view),before);
});
test('candidate status and role labels distinguish missing geometry and immutable declared GT',()=>{
  const gt=Object.freeze({id:'gt',label:'Reference',algorithm:'reference',result:Object.freeze({reference_role:'user_declared_ground_truth'})});
  assert.equal(resultStateLabel({status:'completed'}),'已完成 · 无几何结果');
  assert.equal(resultStateLabel({status:'completed',result:{}}),'已完成');assert.equal(resultStateLabel({status:'running'}),'运行中');
  assert.equal(resultReferenceRole(gt,true).badge,'声明 GT · 基准');assert.equal(resultReferenceRole(gt,false).badge,'声明 GT');
  assert.equal(resultReferenceRole(runs[0],true).badge,'算法基准');assert.equal(resultReferenceRole(runs[0],true).declaredGT,false);
  assert.match(resultReferenceRole(runs[0],true).note,/不会.*声明为 GT/);assert.equal(gt.result.reference_role,'user_declared_ground_truth');
});
test('changing a candidate preserves the metric group contract, overview choices and source roles',async()=>{
  const vm=require('node:vm'),fs=require('node:fs'),source=fs.readFileSync(require.resolve('../mint/comparison/web/result-tools.js'),'utf8'),calls=[];
  const gt={id:'gt',label:'GT',algorithm:'reference',result:{reference_role:'user_declared_ground_truth'}},all=[gt,...runs],state={comparison:{id:'comparison',runs:all},frame:27,pollToken:1,metricsRows:[{first_run:'gt',second_run:'h'}]};
  let finishFrame;const frameWait=new Promise(resolve=>{finishFrame=resolve;}),context=vm.createContext({state});vm.runInContext(source,context);
  const selected=vm.runInContext("Object.assign(resultView,{reference:'gt',candidate:'m',selected:['gt','m','a'],layout:'pair',pairSide:'reference',expanded:'a'});resultView.selected",context),original=JSON.stringify(all);
  Object.assign(context,{saveResultPreferences:()=>calls.push('save'),applyResultVisibility:()=>calls.push('visibility'),render:()=>calls.push('render'),renderMetrics:rows=>calls.push(['metrics',rows]),ensureFrame:frame=>{calls.push(['frame',frame]);return frameWait;}});
  const pending=vm.runInContext("changeResultCandidate('h')",context);
  assert.equal(vm.runInContext('resultView.candidate',context),'h');assert.equal(vm.runInContext('resultView.reference',context),'gt');assert.equal(vm.runInContext('resultView.selected',context),selected);assert.equal(vm.runInContext('resultView.layout',context),'pair');assert.equal(vm.runInContext('resultView.pairSide',context),'candidate');assert.equal(vm.runInContext('resultView.expanded',context),null);
  assert.deepEqual(calls.find(item=>Array.isArray(item)&&item[0]==='frame'),['frame',27]);assert.equal(calls.find(item=>Array.isArray(item)&&item[0]==='metrics')[1],state.metricsRows);
  state.pollToken=2;finishFrame();await pending;assert.equal(calls.filter(item=>item==='render').length,1,'a stale frame fetch cannot render another comparison');
  const count=calls.length;await vm.runInContext("changeResultCandidate('gt');changeResultCandidate('removed')",context);assert.equal(calls.length,count);assert.equal(JSON.stringify(all),original);
});

test('a new HaWoR inference is never labelled as an existing numerical result',()=>{
  assert.match(runProtocol({algorithm:'hawor_full',options:{analysis_width:832}}).short,/原生流程/);
  assert.doesNotMatch(runProtocol({algorithm:'hawor_camera',options:{}}).short,/已有数值/);
});
test('MINT production and Viewer smoothing are explicit and preserve actual input protocol',()=>{
  assert.match(handPostprocessLabel('smooth'),/0\.6 \/ 0\.6 \/ 2/);
  const sample={frames:10428,fps:36.0025,request:{view:'left'}};
  const protocol=runProtocol({...runs[0],options:{mode:'max_chunked',hand_mode:'smooth@0.7,0.5,0.3'}},sample);
  assert.match(protocol.short,/Viewer/);assert.match(protocol.short,/0\.7 \/ 0\.5 \/ 0\.3/);
  assert.match(protocol.rows.find(([label])=>label==='输入协议')[1],/左眼 · 10428 帧 · 36\.0025 FPS/);
});
test('import and camera combinations do not advertise inherited GPU settings as new inference',()=>{
  const run={...runs[0],imported:true,options:{mode:'max_chunked',max_gpus:8,resource_policy:'shared'}};
  assert.match(runProtocol(run).short,/数值结果导入/);
  assert.equal(runProtocol(run).rows.find(([label])=>label==='资源策略')[1],'已有数值处理 · 不执行模型');
  const combined={...run,imported:false,result:{component_sources:{hands:'a',camera:'h'}}};
  assert.match(runProtocol(combined).short,/相机外参组合/);
  assert.deepEqual(runProtocol(combined).rows.find(([label])=>label==='手部 / 相机组件'),['手部 / 相机组件','a / h']);
});

test('language module starts independently, shares preference and preserves untranslated evidence',()=>{
  const vm=require('node:vm'),fs=require('node:fs');
  const nodes=['数据与输入','/data1/中文目录/video.mp4','未翻译诊断原文'].map(nodeValue=>({nodeValue,parentElement:{closest:()=>false}}));
  const storage=new Map([['wuji-viewer-language','en']]),listeners={};
  const select={value:'',addEventListener:(name,fn)=>listeners[name]=fn};
  const document={body:{},documentElement:{},getElementById:()=>select,createTreeWalker:()=>{let i=0;return{nextNode:()=>nodes[i++]||null};}};
  const sandbox={document,NodeFilter:{SHOW_TEXT:4},MutationObserver:class{observe(){}},localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},window:{addEventListener(){}},queueMicrotask};
  vm.runInNewContext(fs.readFileSync(require.resolve('../mint/comparison/web/locale.js'),'utf8'),sandbox);
  assert.equal(nodes[0].nodeValue,'Data & input');assert.equal(nodes[1].nodeValue,'/data1/中文目录/video.mp4');assert.equal(nodes[2].nodeValue,'未翻译诊断原文');
  select.value='zh';listeners.change();assert.equal(nodes[0].nodeValue,'数据与输入');assert.equal(storage.get('wuji-viewer-language'),'zh');
});

test('compatible result reuse keeps original resource evidence and declares no new forward',()=>{
  const protocol=runProtocol({...runs[0],reuse:{source_run:'original'},options:{mode:'max_chunked',max_gpus:2,resource_policy:'shared'}});
  assert.match(protocol.short,/复用结果/);
  assert.match(protocol.rows.find(([label])=>label==='结果复用')[1],/本次未执行网络/);
});
