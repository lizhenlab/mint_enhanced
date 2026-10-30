'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {defaultResultSelection,normalizeResultSelection,toggleResultSelection,metricValue,metricPairScope,filterMetricRows,handPostprocessLabel,runProtocol}=require('../mint/comparison/web/result-tools.js');
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
