'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {defaultResultSelection,resultCandidate,displayResultIds,comparisonGridPlan,filterMetricRows,comparisonMetricSelection}=require('../mint/comparison/web/result-tools.js');
const {comparisonDisplayName}=require('../mint/comparison/web/workspace.js');
const runs=[{id:'m',algorithm:'mint'},{id:'h',algorithm:'hawor_full'},{id:'a',algorithm:'ace_kfree'},{id:'gt',algorithm:'reference',result:{reference_role:'user_declared_ground_truth'}}];

test('the default pair pins GT and displays one candidate without losing overview choices',()=>{
  const view=defaultResultSelection(runs),original=view.selected.slice();
  assert.deepEqual(displayResultIds(runs,view,1000),['gt','m']);
  assert.deepEqual(displayResultIds(runs,{...view,candidate:'h'},1000),['gt','h']);
  assert.deepEqual(view.selected,original);
  assert.deepEqual(displayResultIds(runs,{...view,layout:'overview'},1000),['gt','m','h','a']);
});

test('stale or newly pinned candidate IDs never create duplicate baseline cards',()=>{
  assert.equal(resultCandidate(runs,{reference:'m',candidate:'m',selected:['m','h']}),'h');
  assert.deepEqual(displayResultIds(runs,{reference:'gt',candidate:'removed',selected:['gt','a']},1000),['gt','a']);
  assert.deepEqual(displayResultIds([{id:'only'}],{reference:'only',selected:['only']},1000),['only']);
  assert.deepEqual(displayResultIds([],{selected:[]},1000),[]);
});

test('narrow display uses an explicit baseline/candidate pager while preserving selected results',()=>{
  const view={reference:'gt',candidate:'h',selected:['gt','m','h','a']};
  assert.deepEqual(displayResultIds(runs,view,500),['h']);
  assert.deepEqual(displayResultIds(runs,{...view,pairSide:'reference'},500),['gt']);
  assert.deepEqual(displayResultIds(runs,view,792),['gt','h']);
  assert.deepEqual(displayResultIds(runs,{...view,expanded:'a'},792),['a']);
});

test('a 720-high laptop pair fits one row at both normal and narrow desktop widths',()=>{
  for(const [width,height]of [[1000,410],[792,390]]){
    const plan=comparisonGridPlan(width,height,2,'pair');
    assert.equal(plan.columns,2);assert.equal(plan.rows,1);assert.equal(plan.scroll,false);
    assert.equal(plan.rowHeight,height);
  }
  const overview=comparisonGridPlan(792,390,4,'overview');
  assert.equal(overview.columns,2);assert.equal(overview.rows,2);assert.equal(overview.scroll,true);
  assert.ok(overview.rowHeight>=360);
});

test('pair metrics exclude hidden overview selections and never expand the scoring scope',()=>{
  const selected=displayResultIds(runs,{reference:'gt',candidate:'h',selected:runs.map(run=>run.id)},1000);
  const rows=[{first_run:'gt',second_run:'m'},{first_run:'gt',second_run:'h'},{first_run:'gt',second_run:'a'}];
  assert.deepEqual(filterMetricRows(rows,{selected,reference:'gt'},'selected'),[rows[1]]);
});

test('narrow paging and single-card focus never change the metric comparison group',()=>{
  const view={reference:'gt',candidate:'h',selected:['gt','m','h','a']};
  assert.deepEqual(displayResultIds(runs,view,500),['h']);
  assert.deepEqual(comparisonMetricSelection(runs,view),{selected:['gt','h'],reference:'gt'});
  assert.deepEqual(comparisonMetricSelection(runs,{...view,expanded:'h'}),{selected:['gt','h'],reference:'gt'});
  assert.deepEqual(comparisonMetricSelection(runs,{...view,layout:'overview',expanded:'a'}),{selected:['gt','m','h','a'],reference:'gt'});
});

test('numeric video filenames include their action or sequence context',()=>{
  assert.equal(comparisonDisplayName({sample_request:{source:{path:'/dataset/egodex/test/open_box/0.mp4'}}}),'test / open_box · 0.mp4');
  assert.equal(comparisonDisplayName({title:'相机回归',sample_request:{source:{path:'/dataset/egodex/test/open_box/0.mp4'}}}),'相机回归');
});

function deferred(){let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};}
function bootFixture(search,{activePane=null,taskOpen=false,source='',comparisonWait=null}={}){
  const vm=require('node:vm'),fs=require('node:fs'),history=deferred(),parent=deferred(),calls=[];
  const nodes={source:{value:source},examples:{value:'',append(){}},dependencyStatus:{},platformDialog:{open:!!activePane},taskDialog:{open:taskOpen}};
  const sandbox={URLSearchParams,location:{search},localStorage:{getItem:()=>null},state:{specs:[],comparison:null},workspaceUI:{history:[]},platform:{pane:activePane||'data'},HUB_LABELS:{data:'Data',research:'Research',tools:'Tools'},
    $:id=>nodes[id],el:()=>({}),buildAlgorithms(){},refreshComposerAlgorithms(){},updateImportFormats(){},notify:message=>calls.push(['notice',message]),
    api:path=>path==='api/algorithms'?Promise.resolve({algorithms:[]}):path==='api/comparison/examples'?Promise.resolve({examples:[{label:'Example',path:'/example.mp4'}]}):parent.promise,
    loadHistory:()=>history.promise.then(()=>{sandbox.workspaceUI.history=[{id:'remembered'}];}),
    openComparison:async(id,options)=>{calls.push(['comparison',id,options]);if(comparisonWait)await comparisonWait.promise;sandbox.state.comparison={id};},inspectResult:id=>calls.push(['inspect',id]),seek:frame=>calls.push(['seek',frame]),
    openCollection:async id=>calls.push(['collection',id]),openPlatform:async pane=>{calls.push(['platform',pane]);nodes.platformDialog.open=true;sandbox.platform.pane=pane;}};
  const sourceText=fs.readFileSync(require.resolve('../mint/comparison/web/app.js'),'utf8');
  const expression=sourceText.slice(sourceText.lastIndexOf('(async()=>{try{')).replace(/tick\(\);\s*$/,'');
  return {sandbox,nodes,history,parent,calls,promise:vm.runInNewContext(expression,sandbox)};
}
async function microtasks(){for(let i=0;i<8;i++)await Promise.resolve();}

test('an explicit comparison opens before slow history completes and preserves an active hub',async()=>{
  const boot=bootFixture('?id=direct&hub=research',{activePane:'research'});
  await microtasks();
  assert.equal(boot.calls[0][0],'comparison');assert.equal(boot.calls[0][1],'direct');
  assert.equal(boot.calls[0][2].keepPlatform,true);
  assert.equal(boot.calls.some(row=>row[0]==='platform'),false);
  boot.history.resolve();await boot.promise;
});

test('a closed default data pane opens before its slow comparison context and history finish',async()=>{
  const comparisonWait=deferred(),boot=bootFixture('?id=direct&hub=data',{comparisonWait});
  await microtasks();
  assert.equal(boot.nodes.platformDialog.open,true);
  assert.equal(boot.sandbox.platform.pane,'data');
  assert.deepEqual(boot.calls.map(row=>row[0]),['platform','comparison']);
  assert.equal(boot.calls[1][2].keepPlatform,true);
  comparisonWait.resolve();await boot.promise;
  assert.equal(boot.calls.filter(row=>row[0]==='platform').length,1);
  boot.history.resolve();
});

test('a module chosen while default history is loading cancels remembered-comparison navigation',async()=>{
  const boot=bootFixture('');await microtasks();assert.equal(boot.calls.length,0);
  boot.sandbox.location.search='?hub=research';boot.nodes.platformDialog.open=true;boot.sandbox.platform.pane='research';
  boot.history.resolve();await boot.promise;
  assert.equal(boot.calls.some(row=>row[0]==='comparison'||row[0]==='platform'),false);
});

test('a stale run-parent response cannot reopen its comparison after navigation',async()=>{
  const boot=bootFixture('?run=old-run');await microtasks();
  boot.sandbox.location.search='?hub=data';boot.nodes.platformDialog.open=true;boot.sandbox.platform.pane='data';
  boot.parent.resolve({comparisons:['old-comparison']});await boot.promise;
  assert.equal(boot.calls.some(row=>row[0]==='comparison'||row[0]==='inspect'),false);boot.history.resolve();
});

test('startup respects an open new-task form and never overwrites its selected source',async()=>{
  const boot=bootFixture('',{taskOpen:true,source:'/chosen/video.mp4'});await boot.promise;
  assert.equal(boot.nodes.source.value,'/chosen/video.mp4');assert.equal(boot.calls.length,0);boot.history.resolve();
});
