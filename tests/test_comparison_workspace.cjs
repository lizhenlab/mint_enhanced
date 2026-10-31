'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const {comparisonRecordKind,comparisonDisplayName,comparisonSourceSummary,comparisonReferenceSummary,comparisonSearchText,preferredComparison,comparisonJumpFrame,historyGroups,runExecutionSummary,durationLabel}=require('../mint/comparison/web/workspace.js');
const input=path=>({sample_request:{source:{path},view:'full'},runs:[{algorithm:'mint'}]});

test('system filtering is explicit and never hides a real dataset validation split',()=>{
  assert.equal(comparisonRecordKind({...input('/dataset/arctic/validation/video.mp4'),title:'validation sequence'}),'regular');
  assert.equal(comparisonRecordKind({...input('/dataset/video.mp4'),title:'验证集手部动作'}),'regular');
  assert.equal(comparisonRecordKind({...input('/data/video.mp4'),title:'验收 / native pipeline'}),'system');
  assert.equal(comparisonRecordKind({...input('/data/video.mp4'),title:'cache-lookup'}),'system');
  assert.equal(comparisonRecordKind({...input('/data/video.mp4'),runs:[{algorithm:'reference'}]}),'reference');
  assert.equal(comparisonRecordKind({...input('/data/video.mp4'),runs:[{algorithm:'mint'},{algorithm:'reference'}]}),'regular');
});
test('generic prepared names use their sequence while descriptive titles remain intact',()=>{
  assert.equal(comparisonDisplayName({...input('/dataset/subject-01/video.mp4'),title:'video.mp4'}),'subject-01 · video.mp4');
  assert.equal(comparisonDisplayName({...input('/data/real.mp4'),title:'0:None:0'}),'real.mp4');
  assert.equal(comparisonDisplayName({...input('/data/real.mp4'),title:'EgoDex 精度对比'}),'EgoDex 精度对比');
});
test('grouping combines experiments for a video but preserves eye protocol boundaries',()=>{
  const first={...input('/data/a.mp4'),id:'a'},second={...input('/data/a.mp4'),id:'b'},left={...input('/data/a.mp4'),id:'c'};left.sample_request.view='left';
  assert.deepEqual(historyGroups([first,second,left]).map(([,entries])=>entries.map(x=>x.id)),[['a','b'],['c']]);
});
test('history distinguishes episodes sharing a container video and exposes the original source context',()=>{
  const first={...input('/dataset/videos/chunk-000/file-000.mp4'),id:'a',dataset_context:{selection:{root:'/dataset/grasp',episode:12,video_key:'observation.images.left'}}};
  const same={...first,id:'b'},other={...first,id:'c',dataset_context:{selection:{...first.dataset_context.selection,episode:13}}};
  assert.deepEqual(historyGroups([first,same,other]).map(([,rows])=>rows.map(x=>x.id)),[['a','b'],['c']]);
  const source=comparisonSourceSummary(first);assert.match(source.label,/grasp · Episode 12.*left.*完整画面/);assert.match(source.description,/\/dataset\/videos\/chunk-000\/file-000.mp4/);
  assert.match(comparisonSearchText(first),/episode 12/);assert.match(comparisonSearchText(first),/observation.images.left/);
});
test('source protocol labels distinguish source bounds, sampling FPS and full remaining video',()=>{
  const value=input('/dataset/run/video.mp4');Object.assign(value.sample_request,{view:'right',start_frame:120,max_frames:null,target_fps:15});
  const source=comparisonSourceSummary(value);assert.equal(source.view,'右眼');assert.match(source.range,/源索引 120 起（0 起）.*至片尾.*15 FPS/);
  value.sample_request.max_frames=40;assert.match(comparisonSourceSummary(value).range,/最多 40 帧/);
});
test('the default comparison avoids system and reference-only records while honoring a valid remembered choice',()=>{
  const system={...input('/data/video.mp4'),id:'system',title:'验收 / cache'},reference={...input('/data/video.mp4'),id:'reference',runs:[{algorithm:'reference'}]},regular={...input('/data/video.mp4'),id:'regular'};
  const history=[system,reference,regular];assert.equal(preferredComparison(history,'missing'),regular);assert.equal(preferredComparison(history,'system'),system);assert.equal(preferredComparison([reference],null),reference);assert.equal(preferredComparison([],null),null);
});
test('a selected display baseline is not promoted to declared GT in session descriptions',()=>{
  const value=input('/data/video.mp4');value.runs[0].label='MINT';assert.match(comparisonReferenceSummary(value),/未附声明 GT.*差异.*不代表准确率/);
  value.runs.push({algorithm:'reference',label:'Imported reference',result:{}});assert.match(comparisonReferenceSummary(value),/未附声明 GT/);
  value.runs[1].result.reference_role='user_declared_ground_truth';assert.match(comparisonReferenceSummary(value),/声明 GT 参考：Imported reference.*参考误差.*其他配对只表示算法差异/);
});
test('one-based frame input ignores blanks and invalid numbers, then clamps to the selected sample',()=>{
  for(const value of ['', '  ',NaN,Infinity,'1.5','bad'])assert.equal(comparisonJumpFrame(value,450),null);
  assert.equal(comparisonJumpFrame('1',450),0);assert.equal(comparisonJumpFrame('9999',450),449);assert.equal(comparisonJumpFrame('-3',450),0);assert.equal(comparisonJumpFrame('1',0),null);
});
test('GPU summaries separate active leases, eligibility and recorded peaks without attributing work to a reuse',()=>{
  const source=fs.readFileSync(require.resolve('../mint/comparison/web/workspace.js'),'utf8');
  const context=vm.createContext({TERMINAL:new Set(['completed','failed','partial','cancelled','interrupted']),fmt:(value,digits)=>Number(value).toFixed(digits)});vm.runInContext(source,context);
  context.run={status:'running',progress:{gpu_scheduler:{eligible_devices:['cuda:0','cuda:1'],running_devices:[],max_workers:8}}};
  let summary=vm.runInContext('runGpuSummary(run)',context);assert.equal(summary.text,'运行 0 GPU');assert.match(summary.title,/可调度 2 张 · 允许上限 8/);assert.match(summary.title,/可调度不表示已分配/);
  context.run.progress.gpu_scheduler.running_devices=['cuda:1'];summary=vm.runInContext('runGpuSummary(run)',context);assert.equal(summary.text,'运行 1 GPU');assert.match(summary.title,/当前分配：cuda:1/);
  context.run={status:'completed',reuse:true,result:{parallel_stages:{infer:{peak_running_workers:7}}}};summary=vm.runInContext('runGpuSummary(run)',context);assert.equal(summary.text,'本次未推理');assert.doesNotMatch(summary.text,/7/);
  context.run={status:'completed',result:{parallel_stages:{infer:{gpu_devices:['cuda:0'],wall_seconds:20}}}};summary=vm.runInContext('runGpuSummary(run)',context);assert.equal(summary.text,'GPU 历史记录');
});
test('execution summaries preserve measured reasons and phase-only ETA',()=>{
  const text=runExecutionSummary({status:'running',progress:{stage:'slam_tracking',detail:'正在跟踪第 80 帧'},task_summary:{phase_elapsed_seconds:65,eta:{seconds:130}}});
  assert.match(text,/正在跟踪第 80 帧/);assert.match(text,/本阶段已耗时 1 分 5 秒/);assert.match(text,/本阶段预计还需 2 分 10 秒/);
  assert.doesNotMatch(runExecutionSummary({status:'running',progress:{stage:'slam_scale',done:0,total:1}}),/预计还需/);
  assert.match(runExecutionSummary({status:'running',progress:{stage:'slam_scale'}}),/尚无可靠剩余时间估算/);
});
test('finished and stale tasks do not retain live waiting or ETA claims',()=>{
  assert.equal(runExecutionSummary({status:'completed',task_summary:{waiting_reason:'等待GPU',eta:{seconds:900}}}),'');
  assert.equal(runExecutionSummary({status:'running',task_summary:{eta:{seconds:NaN}}}),'');
  assert.equal(durationLabel(3601),'1 小时 0 分');
});
