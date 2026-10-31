'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {comparisonRecordKind,comparisonDisplayName,historyGroups,runExecutionSummary,durationLabel}=require('../mint/comparison/web/workspace.js');
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
