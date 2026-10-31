'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
function node(data={}){return {dataset:{},hidden:false,attributes:{},classList:{toggle(){}},setAttribute(k,v){this.attributes[k]=v;},...data};}
function setup(){
  const ids={},researchPanels=['train','curves','regression','protocol','production','models'].map(v=>node({dataset:{researchView:v},value:'draft-'+v}));
  const researchButtons=researchPanels.map(p=>node({dataset:{researchTab:p.dataset.researchView}}));
  ids['hub-research']=node({querySelectorAll:q=>q.includes(':scope')?researchPanels:researchButtons});
  const sourcePanels=['video','lerobot','native'].map(v=>node({dataset:{dataSource:v},value:'draft-'+v}));
  const sourceButtons=sourcePanels.map(p=>node({dataset:{sourceTab:p.dataset.dataSource}}));
  const footer=node();ids['hub-data']=node({querySelectorAll:()=>sourcePanels,querySelector:()=>footer});ids.platformDialog=node({open:false});
  const sandbox={$:id=>ids[id],state:{comparison:null},document:{addEventListener(){},querySelectorAll:()=>sourceButtons},window:{addEventListener(){},history:{replaceState(){throw Error('unexpected route mutation');}}},location:{search:''},console,URL,URLSearchParams,structuredClone};
  vm.createContext(sandbox);
  for(const name of ['platform.js','enhancements.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../mint/comparison/web',name),'utf8'),sandbox);
  return {sandbox,ids,researchPanels,researchButtons,sourcePanels,sourceButtons,footer};
}
test('research switches visibility without rebuilding or losing drafts',()=>{
  const x=setup(),original=x.researchPanels.slice();x.sandbox.showResearchView('train',{route:false});
  assert.equal(x.researchPanels.filter(p=>!p.hidden).length,1);assert.equal(x.researchPanels[0].hidden,false);
  x.researchPanels[0].value='unsaved-training-config';x.sandbox.showResearchView('curves',{route:false});
  assert.equal(x.researchPanels[0].hidden,true);assert.equal(x.researchPanels[1].hidden,false);
  x.sandbox.showResearchView('train',{route:false});assert.equal(x.researchPanels[0].value,'unsaved-training-config');assert.deepEqual(x.researchPanels,original);
  assert.equal(x.researchButtons[0].attributes['aria-selected'],'true');assert.equal(x.researchButtons[1].tabIndex,-1);
});
test('research deeplinks select the owning view, with safe fallback',()=>{
  const {sandbox}=setup();assert.equal(sandbox.initialResearchView(new URLSearchParams('study=one')),'regression');
  assert.equal(sandbox.initialResearchView(new URLSearchParams('workflow=one')),'train');
  assert.equal(sandbox.initialResearchView(new URLSearchParams('research_tab=models&study=one')),'models');
  sandbox.showResearchView('unknown',{route:false});
  assert.equal(sandbox.researchViewForTarget({closest:()=>({dataset:{researchView:'production'}})}),'production');
});
test('data input kinds preserve hidden selections and native uses its own next step',()=>{
  const x=setup();vm.runInContext("platform.selection.set('video:/a',{source:'/a'})",x.sandbox);
  x.sandbox.showDataSource('native',{route:false});assert.equal(x.footer.hidden,true);assert.equal(x.sourcePanels[2].hidden,false);
  x.sourcePanels[0].value='selected-video';x.sandbox.showDataSource('lerobot',{route:false});assert.equal(x.footer.hidden,false);
  x.sandbox.showDataSource('video',{route:false});assert.equal(x.sourcePanels[0].value,'selected-video');assert.equal(vm.runInContext('platform.selection.size',x.sandbox),1);
  assert.equal(x.sourcePanels.filter(p=>!p.hidden).length,1);
});
test('context separates comparison results from training and batch data selections',()=>{
  const {sandbox}=setup(),comp={id:'a',title:'Video A',sample_request:{source:{path:'/a.mp4'},start_frame:10},sample:{frames:3,source_frame_indices:[10,11,12]},runs:[{result:{}},{}]};
  assert.match(sandbox.platformContextText(comp,'tools'),/Video A.*3 帧.*10…12.*1 个已有结果/);
  assert.match(sandbox.platformContextText(comp,'research'),/训练数据、回归协议和生产来源仍以各自选择为准/);
  assert.match(sandbox.platformContextText(comp,'batch',7),/7 项.*分别管理/);
  assert.equal(sandbox.platformContextText(comp,'data',7),'');
  assert.equal(sandbox.platformContextText(null,'data',0),'');
  assert.match(sandbox.platformContextText(null,'quality'),/尚未打开比较/);
  assert.equal(sandbox.platformContextText(comp,'tasks'),'');
});
test('module primary actions configure their own task type',()=>{
  const {sandbox}=setup();assert.equal(sandbox.platformPrimaryAction('research').kind,'train');assert.equal(sandbox.platformPrimaryAction('evaluation').kind,'evaluation');
  assert.equal(sandbox.platformPrimaryAction('data').kind,'data');assert.equal(sandbox.platformPrimaryAction('tasks'),null);assert.equal(sandbox.platformPrimaryAction('tools'),null);
});

test('contextual primary action cannot fall through to the comparison submit UI',()=>{
  const x=setup();let called=0,prevented=0,stopped=0;x.sandbox.activatePlatformPrimaryAction=()=>called++;
  const event={preventDefault:()=>prevented++,stopImmediatePropagation:()=>stopped++};
  assert.equal(x.sandbox.interceptPlatformPrimaryAction(event),false);assert.equal(called,0);
  x.ids.platformDialog.open=true;assert.equal(x.sandbox.interceptPlatformPrimaryAction(event),true);
  assert.equal(called,1);assert.equal(prevented,1);assert.equal(stopped,1);
});

test('diagnostic entries and specialist tools use the canonical diagnostic route',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../mint/comparison/web/index.html'),'utf8');
  assert.match(html,/id="diagnosticEntry"[^>]+href="\/diagnostics"[^>]*>单算法诊断<\/a>/);
  const tools=[...html.matchAll(/href="([^"]*\?tool=[^"]+)"/g)].map(match=>match[1]);
  assert.ok(tools.length>=6);assert.ok(tools.every(href=>href.startsWith('/diagnostics?tool=')));
  assert.doesNotMatch(html,/href="\/">MINT 单算法诊断/);
});
test('old diagnostic hash bookmarks migrate without hijacking explicit comparisons',()=>{
  const {sandbox}=setup();
  assert.equal(sandbox.legacyDiagnosticHashURL('/compare','','#diagnostic-results'),'/diagnostics#diagnostic-results');
  assert.equal(sandbox.legacyDiagnosticHashURL('/proxy/8011/compare','?view=results','#diagnostic-results'),'/proxy/8011/diagnostics?view=results#diagnostic-results');
  assert.equal(sandbox.legacyDiagnosticHashURL('//foreign/compare','','#diagnostic-tools'),null);
  assert.equal(sandbox.legacyDiagnosticHashURL('/compare/','?view=results','#diagnostic-tools'),'/diagnostics?view=results#diagnostic-tools');
  assert.equal(sandbox.legacyDiagnosticHashURL('/compare','?id=one','#diagnostic-results'),null);
  assert.equal(sandbox.legacyDiagnosticHashURL('/compare','?hub=research','#diagnostic-tools'),null);
  assert.equal(sandbox.legacyDiagnosticHashURL('/compare','','#ordinary-anchor'),null);
  assert.equal(sandbox.legacyDiagnosticHashURL('/diagnostics','','#diagnostic-input'),null);
});
