'use strict';

const datasetForm={active:false,catalog:null,offset:0,serial:0,loading:false};
const platform={pane:'data',selection:new Map(),catalog:null,datasetOffset:0,taskOffset:0,taskSerial:0,serial:0,timer:null,collection:null,collectionKind:null,report:null,renderJob:null,capability:null};
const HUB_LABELS={data:'数据接入',tasks:'任务与恢复',batch:'批量比较',evaluation:'数据集评测',tools:'专项工具',catalog:'数据与结果目录',quality:'逐帧质检与人工复核',research:'训练与模型'};
const HUB_GROUPS={data:'data',catalog:'data',batch:'data',evaluation:'evaluation',quality:'evaluation',research:'research',tasks:'tasks',tools:'tools'};
const HUB_CONTEXT={data:'选择输入来源，再选择视频、Episode 或已准备的数据版本。',catalog:'检索不可变结果、数据版本、模型和协议。比较记录保存一组对照，任务中心管理实际执行过程。',batch:'多个输入按统一设置运行，每个输入独立保存比较。先核对输入与预检结果，再提交后台批次。',evaluation:'有 GT 的样本用于量化误差；已有预测可在逐帧质检中定位问题。',quality:'逐帧检查、区间复核和二维点修正。二维修正不会自动变成三维或 MANO 真值。',research:'准备数据 → 训练 → 检查点回归 → 模型发布。数据、模型和协议均保留独立版本。',tasks:'查看执行阶段、等待原因、资源与日志。一个比较可能包含多个算法任务。',tools:'选择工具用途；展示视频使用已有几何，分析与诊断工具各自选择输入。'};
const platformNavigation={scroll:new Map(),restoring:false};
function platformRoute(tab,replace=false){
  const url=new URL(location.href);if(tab){if(url.searchParams.get('hub')!==tab)url.searchParams.delete('section');if(tab!==platform.collectionKind)url.searchParams.delete('collection');if(tab!=='tools'){url.searchParams.delete('tool_job');url.searchParams.delete('tools_tab');}if(tab!=='research'){url.searchParams.delete('study');url.searchParams.delete('workflow');}if(tab!=='research')url.searchParams.delete('research_tab');if(tab!=='data')url.searchParams.delete('source_type');if(tab!=='tasks')for(const key of ['task_query','task_status','task_kind'])url.searchParams.delete(key);url.searchParams.set('hub',tab);}else{for(const key of ['hub','section','collection','tool_job','tools_tab','workflow','study','research_tab','source_type','task_query','task_status','task_kind'])url.searchParams.delete(key);}
  if(url.href!==location.href)window.history[replace?'replaceState':'pushState']({mintHub:tab||'compare'},'',url.pathname+url.search+url.hash);
}
function syncPlatformNavigation(tab){
  const group=HUB_GROUPS[tab];document.querySelector('#appMasthead h1').textContent=({data:'数据',evaluation:'评测与质检',research:'训练与模型',tasks:'任务',tools:'工具'})[group]||'多算法比较';document.body.classList.toggle('platform-page-open',!!tab);$('appShell').hidden=!!tab;$('appShell').inert=!!tab;
  for(const button of document.querySelectorAll('[data-platform-tab]')){const active=button.dataset.platformTab===group;button.classList.toggle('active',active);if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');}
  $('backToResults').classList.toggle('active',!tab);if(!tab)$('backToResults').setAttribute('aria-current','page');else $('backToResults').removeAttribute('aria-current');
  for(const button of document.querySelectorAll('[data-hub-pane]')){button.hidden=HUB_GROUPS[button.dataset.hubPane]!==group;const active=button.dataset.hubPane===tab;button.classList.toggle('active',active);button.setAttribute('aria-current',active?'page':'false');}
  document.querySelector('.platform-tabs').hidden=['research','tasks','tools','evaluation'].includes(group);
  $('evaluationRoutes').hidden=group!=='evaluation';for(const button of document.querySelectorAll('[data-evaluation-route]')){const active=button.dataset.evaluationRoute===(tab==='quality'?'quality':'dataset');button.classList.toggle('active',active);button.setAttribute('aria-current',active?'page':'false');}$('moduleContext').textContent=HUB_CONTEXT[tab]||'';syncPlatformPrimaryAction(tab);refreshPlatformContext();if(typeof updateEvaluationNavigation==='function')updateEvaluationNavigation();
}
function closePlatformWorkspace({history=true}={}){
  platform.serial++;platform.collectionOpenSerial=(platform.collectionOpenSerial||0)+1;
  platformNavigation.scroll.set(platform.pane,document.querySelector('.platform-body').scrollTop);
  if($('platformDialog').open)$('platformDialog').close();syncPlatformNavigation(null);if(history&&!platformNavigation.restoring)platformRoute(null);
}
function focusHubSection(tab,title){
  openPlatform(tab);const url=new URL(location.href);url.searchParams.set('section',title);window.history.replaceState(null,'',url.pathname+url.search);
  requestAnimationFrame(()=>{const section=findHubSection(tab,title);if(section){revealPlatformTarget(section);section.setAttribute('tabindex','-1');section.focus({preventScroll:true});}});
}

function datasetSelection(){
  if(!datasetForm.active)return null;
  if(datasetForm.loading)throw new Error('Episode 元数据正在读取，请稍后再提交');
  if(!datasetForm.catalog||$('datasetRoot').value.trim()!==datasetForm.catalog.root||$('datasetCamera').value!==datasetForm.catalog.camera||!datasetForm.catalog.episodes.some(row=>String(row.episode_index)===$('datasetEpisode').value))throw new Error('请先读取当前数据集并选择 Episode');
  return {root:datasetForm.catalog.root,video_key:$('datasetCamera').value,episode:Number($('datasetEpisode').value),attach_gt:$('datasetAttachGT').checked,use_calibration:$('datasetCalibration').checked};
}
function decorateInputBody(body){const dataset=datasetSelection();return dataset?{...body,dataset}:body;}
function setDatasetMode(active){
  if(!active){datasetForm.serial++;datasetForm.loading=false;}
  datasetForm.active=active;$('datasetFields').hidden=!active;$('sourceDirectory').hidden=active;
  $('chooseVideoInput').classList.toggle('active',!active);$('chooseDatasetInput').classList.toggle('active',active);
  $('view').disabled=active;if(active)$('view').value='full';$('startFrameLabel').textContent=active?'Episode 内起始帧':'起始源帧';
}
function chooseDatasetEpisode(){
  const row=datasetForm.catalog?.episodes.find(value=>value.episode_index===Number($('datasetEpisode').value));if(!row)return;
  $('source').value=row.video;$('view').value='full';$('camera').value='';$('startFrame').value=0;
  $('datasetAttachGT').disabled=!row.annotation.ready;if(!row.annotation.ready)$('datasetAttachGT').checked=false;
  $('datasetCalibration').disabled=!row.annotation.camera;if(!row.annotation.camera)$('datasetCalibration').checked=false;
  $('datasetSummary').textContent=`${datasetForm.catalog.total} 个 Episode · 当前 ${row.length} 帧 · ${datasetForm.catalog.fps} FPS · ${row.annotation.ready?(row.annotation.mesh?'GT 含 MANO 网格':'GT 含 21 点'):'GT 不可用：'+row.annotation.reason}`;
}
async function loadDatasetForm(offset=0,selection=null){
  const serial=++datasetForm.serial,root=selection?.root||$('datasetRoot').value.trim(),camera=selection?.video_key||$('datasetCamera').value,cameraBefore=$('datasetCamera').value;
  setDatasetMode(true);datasetForm.catalog=null;datasetForm.loading=true;$('datasetEpisode').replaceChildren();
  $('datasetSummary').textContent='正在读取 Episode 元数据…';$('datasetPreviousPage').disabled=true;$('datasetNextPage').disabled=true;
  const current=()=>serial===datasetForm.serial&&datasetForm.active&&$('datasetRoot').value.trim()===root&&$('datasetCamera').value===cameraBefore;
  const query=new URLSearchParams({root,offset,limit:100});if(camera)query.set('camera',camera);if(selection)query.set('episode',selection.episode);
  try{
    const value=await api('api/comparison/dataset?'+query);if(!current())return null;datasetForm.catalog=value;datasetForm.offset=value.offset;
    $('datasetRoot').value=value.root;$('datasetCamera').replaceChildren();for(const name of value.cameras){const option=el('option',name);option.value=name;$('datasetCamera').append(option);}$('datasetCamera').value=value.camera;
    for(const row of value.episodes){const option=el('option',`Episode ${row.episode_index} · ${row.length} 帧`);option.value=row.episode_index;$('datasetEpisode').append(option);}
    if(selection&&value.episodes.some(row=>row.episode_index===selection.episode))$('datasetEpisode').value=selection.episode;
    $('datasetPreviousPage').disabled=value.offset===0;$('datasetNextPage').disabled=value.offset+value.episodes.length>=value.total;
    if(value.episodes.length)chooseDatasetEpisode();else $('datasetSummary').textContent='当前数据集或相机没有可选 Episode，请检查目录和相机。';return value;
  }catch(error){if(current())throw error;return null;}
  finally{if(serial===datasetForm.serial)datasetForm.loading=false;}
}
function restoreDatasetForm(comparison){
  const context=comparison?.dataset_context;
  setDatasetMode(!!context);if(!context){datasetForm.catalog=null;return;}
  $('datasetRoot').value=context.selection.root;$('datasetAttachGT').checked=!!context.selection.attach_gt;$('datasetCalibration').checked=!!context.selection.use_calibration;
  loadDatasetForm(0,context.selection).then(value=>{if(!value||datasetForm.catalog!==value)return;$('startFrame').value=context.relative_start;$('maxFrames').value=context.requested_max_frames??'';$('camera').value=comparison.sample_request.camera&&!context.selection.use_calibration?JSON.stringify(comparison.sample_request.camera):'';}).catch(error=>{$('datasetSummary').textContent=error.message;});
}
function selectedAlgorithmsFrom(container){
  return [...container.querySelectorAll('.algorithm')].filter(box=>box.querySelector('input[type=checkbox]').checked).flatMap(box=>{
    const options={};for(const input of box.querySelectorAll('[data-option]'))if(input.value!=='')options[input.dataset.option]=input.value;
    const selection={algorithm:box.dataset.algorithm,options},field=box.querySelector('[data-selection=checkpoint]'),config=box.querySelector('[data-selection=config]')?.value.trim();
    const checkpoints=field?.value.split('\n').map(value=>value.trim()).filter(Boolean)||[];
    return checkpoints.length?checkpoints.map(checkpoint=>({...selection,checkpoint,...(config?{config}:{})})):[{...selection,...(config?{config}:{})}];
  });
}
function hubError(error){const message=error?.message||String(error);if(!$('platformDialog').open){notify(message);return;}const host=$('platformError'),feedback=$('workspaceFeedback'),changed=host.textContent!==message||feedback?.hidden;host.textContent=message;if(feedback)feedback.hidden=false;if(changed)host.focus?.({preventScroll:true});}
function selectionSummary(){
  const values=[...platform.selection.values()],episodes=values.filter(value=>value.dataset).length;
  $('collectionSelectionSummary').textContent=`批量列表：已选择 ${values.length} 项 · ${episodes} 个 Episode · ${values.length-episodes} 个视频`;
  for(const [id,disabled]of [['useSingleEpisode',values.length!==1],['prepareBatch',!values.length],['clearCollectionSelection',!values.length],['prepareEvaluation',!values.length||values.some(value=>!(value.dataset&&value.annotation?.ready)&&!value.reference)]]){const button=$(id);if(button)button.disabled=disabled;}
  for(const kind of ['batch','evaluation'])if($(kind+'Composer')?.children.length){invalidateCollectionReview(kind);$(kind+'-camera-shared').checked=false;renderCollectionInputs(kind);updateCollectionDraft(kind);}
  refreshPlatformContext();
}
function addInputCheckbox(host,key,value,label,description){
  const row=el('label',undefined,'collection-entry'),check=el('input');check.type='checkbox';check.dataset.collectionInput=key;check.checked=platform.selection.has(key);
  check.onchange=()=>{if(check.checked)platform.selection.set(key,value);else platform.selection.delete(key);selectionSummary();};
  const copy=el('span');copy.append(el('strong',label),el('small',description));row.append(check,copy);host.append(row);
}
async function scanCollectionVideos(){
  const serial=platform.scanSerial=(platform.scanSerial||0)+1,path=$('collectionDirectory').value.trim(),recursive=$('scanRecursive').checked;
  $('scanSummary').textContent='正在扫描目录…';platform.scanned=[];$('collectionVideos').replaceChildren();
  const query=new URLSearchParams({path,recursive:recursive?'1':'0',limit:1000}),current=()=>serial===platform.scanSerial&&path===$('collectionDirectory').value.trim()&&recursive===$('scanRecursive').checked;
  try{const value=await api('api/comparison/scan?'+query);if(!current())return;
    platform.scanned=value.videos;for(const row of value.videos)addInputCheckbox($('collectionVideos'),'video:'+row.source,{source:row.source,name:row.name},row.name,sourceFileSize(row.size_bytes));
    $('scanSummary').textContent=`${value.videos.length} 个视频${value.truncated?' · 已到单次扫描上限，请进入子目录分批选择':''}`;
  }catch(error){if(current()){$('scanSummary').textContent='扫描失败，请检查目录后重试。';throw error;}}
}
async function loadHubDataset(offset=0){
  const serial=platform.datasetSerial=(platform.datasetSerial||0)+1,root=$('hubDatasetRoot').value.trim(),camera=$('hubDatasetCamera').value;
  platform.catalog=null;$('hubEpisodes').replaceChildren();$('hubDatasetPrevious').disabled=true;$('hubDatasetNext').disabled=true;$('hubDatasetSummary').textContent='正在读取元数据…';
  const query=new URLSearchParams({root,offset,limit:100});if(camera)query.set('camera',camera);
  const current=()=>serial===platform.datasetSerial&&root===$('hubDatasetRoot').value.trim()&&camera===$('hubDatasetCamera').value;
  try{const value=await api('api/comparison/dataset?'+query);if(!current())return;platform.catalog=value;platform.datasetOffset=value.offset;
    $('hubDatasetRoot').value=value.root;$('hubDatasetCamera').replaceChildren();for(const name of value.cameras){const option=el('option',name);option.value=name;$('hubDatasetCamera').append(option);}$('hubDatasetCamera').value=value.camera;
    for(const row of value.episodes){const dataset={root:value.root,video_key:value.camera,episode:row.episode_index,attach_gt:row.annotation.ready,use_calibration:false},key=`episode:${value.root}:${value.camera}:${row.episode_index}`;
      addInputCheckbox($('hubEpisodes'),key,{source:row.video,dataset,name:`${value.root.split('/').pop()} · Episode ${row.episode_index}`,annotation:row.annotation},`Episode ${row.episode_index} · ${row.length} 帧`,row.annotation.ready?(row.annotation.mesh?'GT · MANO + 相机':'GT · 21 点 + 相机'):'可推理 · GT 不完整');
    }
    $('hubDatasetSummary').textContent=`${value.total} 个 Episode · 显示 ${value.episodes.length?value.offset+1:0}–${value.offset+value.episodes.length} · ${value.fps} FPS`;
    $('hubDatasetPrevious').disabled=value.offset===0;$('hubDatasetNext').disabled=value.offset+value.episodes.length>=value.total;
  }catch(error){if(current()){$('hubDatasetSummary').textContent='读取失败，请检查数据集目录和相机后重试。';throw error;}}
}
function collectionDraft(kind){platform.collectionForms??={};return platform.collectionForms[kind]??={step:'inputs',page:0,revision:0,mode:'draft',busy:false,preflight:null};}
function collectionButton(id,label,action,primary=false){const button=el('button',label,primary?'primary':undefined);button.id=id;button.onclick=()=>Promise.resolve().then(action).catch(hubError);return button;}
function createComposer(kind){
  const host=$(kind==='batch'?'batchComposer':'evaluationComposer');if(host.children.length)return;host.classList.add('collection-workflow');
  const draft=collectionDraft(kind),setup=$(kind+'Setup'),modes=el('nav',undefined,'collection-mode-nav');modes.setAttribute('aria-label','批次配置与结果');
  modes.append(collectionButton(kind+'-draft-mode','配置新批次',()=>setCollectionMode(kind,'draft')),collectionButton(kind+'-results-mode','查看已提交批次',()=>setCollectionMode(kind,'results')));setup.before(modes);
  const nav=el('nav',undefined,'collection-step-nav');nav.setAttribute('aria-label','批次创建步骤');host.append(nav);
  for(const [key,label,description]of [['inputs','1 · 核对输入清单','每个输入独立保存一组算法比较结果；跨目录勾选会累积在此清单中。'],['settings','2 · 设置统一协议','相同算法与参数应用到全部输入；实际 GPU 并发由调度器决定。'],['review','3 · 预检与提交','先核对逐输入检查结果与警告，再明确提交后台批次。']]){
    const button=el('button',label);button.id=kind+'-step-nav-'+key;button.setAttribute('aria-controls',kind+'-step-'+key);button.onclick=()=>showCollectionStep(kind,key,{focus:true});nav.append(button);
    const step=el('section',undefined,'collection-step');step.id=kind+'-step-'+key;const heading=el('div',undefined,'collection-step-heading'),title=el('h3',label);title.id=kind+'-heading-'+key;title.tabIndex=-1;step.setAttribute('aria-labelledby',title.id);heading.append(title,el('p',description));step.append(heading);host.append(step);
  }
  const inputs=$(kind+'-step-inputs'),summary=el('p',undefined,'collection-input-summary');summary.id=kind+'-input-summary';summary.setAttribute('role','status');inputs.append(summary);
  const list=el('div',undefined,'collection-input-list');list.id=kind+'-input-list';inputs.append(list);
  const pager=el('div',undefined,'collection-input-pager');pager.id=kind+'-input-pager';const page=el('span');page.id=kind+'-input-page';pager.append(collectionButton(kind+'-input-previous','上一页',()=>{draft.page=Math.max(0,draft.page-1);renderCollectionInputs(kind);}),page,collectionButton(kind+'-input-next','下一页',()=>{draft.page++;renderCollectionInputs(kind);}));inputs.append(pager);
  const choose=source=>{switchHub('data');if(typeof showDataSource==='function')showDataSource(source);};const actions=el('div',undefined,'collection-actions');actions.append(collectionButton(kind+'-add-video','添加服务器视频',()=>choose('video')),collectionButton(kind+'-add-episode','添加 LeRobot Episode',()=>choose('lerobot')),collectionButton(kind+'-clear','清空待提交清单',()=>{platform.selection.clear();syncCollectionCheckboxes();selectionSummary();}),collectionButton(kind+'-to-settings','下一步：统一设置',()=>showCollectionStep(kind,'settings',{focus:true}),true));inputs.append(actions);
  const settings=$(kind+'-step-settings'),fields=el('div',undefined,'collection-protocol-fields');
  for(const [id,label,type,value]of [['view','视频视角','select','full'],['target_fps','目标 FPS（0=源帧率）','number',0],['start_frame','起始帧（从 0 起）','number',0],['max_frames','最多帧数（留空=全部）','number','']]){
    const input=type==='select'?selectInput([['full','完整画面'],['left','双目左眼'],['right','双目右眼']],value):el('input');input.id=kind+'-'+id;if(type!=='select'){input.type=type;input.min=id==='max_frames'?1:0;input.step=id==='target_fps'?'any':1;input.required=id!=='max_frames';input.value=value;}fields.append(inputLabel(label,input));
  }
  settings.append(fields,el('p','默认处理全片。起始帧对普通视频表示源帧号，对 Episode 表示片段内位置；LeRobot 输入使用其已选择的相机流与完整画面。','hint'));
  const options=el('div',undefined,'collection-option-fields'),reference=selectInput([['available','附加各输入已有的 GT'],['none','仅比较算法结果'],['required','要求每个输入都有 GT']],kind==='evaluation'?'required':'available');reference.id=kind+'-reference';reference.disabled=kind==='evaluation';
  const calibration=selectInput([['none','不提供已知内参'],['dataset','使用各 Episode 自身标定'],['manual','全部输入共用手工标定']],'none');calibration.id=kind+'-calibration';options.append(inputLabel('参考标注策略',reference),inputLabel('算法输入的内参条件',calibration));settings.append(options);
  const cameraSettings=el('details',undefined,'collection-manual-camera');cameraSettings.id=kind+'-manual-settings';cameraSettings.append(el('summary','共用手工相机标定'));
  const camera=el('textarea');camera.id=kind+'-camera';camera.rows=3;camera.spellcheck=false;camera.placeholder='{"K":[[fx,0,cx],[0,fy,cy],[0,0,1]],"width":1920,"height":1080,"source":"标定来源"}';const shared=el('input');shared.type='checkbox';shared.id=kind+'-camera-shared';cameraSettings.append(inputLabel('标定 JSON（对应完整源视频网格）',camera),inputLabel('已核对这份标定适用于本批次全部输入',shared));settings.append(cameraSettings);
  const note=el('p',undefined,'collection-warning');note.id=kind+'-protocol-note';settings.append(note);
  const algorithms=el('div',undefined,'composer-algorithms');algorithms.id=kind+'-algorithms';const algorithmSettings=el('details',undefined,'collection-algorithm-settings');algorithmSettings.open=true;algorithmSettings.append(el('summary','选择算法、检查点与运行参数'),algorithms);settings.append(algorithmSettings);cloneComposerAlgorithms(algorithms);
  const settingsActions=el('div',undefined,'collection-actions');settingsActions.append(collectionButton(kind+'-check','校验批次并查看概览',()=>preflightCollection(kind),true),collectionButton(kind+'-back-inputs','返回输入清单',()=>showCollectionStep(kind,'inputs',{focus:true})));settings.append(settingsActions);
  const review=$(kind+'-step-review'),message=el('p',undefined,'collection-validation-message');message.id=kind+'-validation-message';message.setAttribute('role','status');const totals=el('div',undefined,'collection-review-summary');totals.id=kind+'-review-summary';const rows=el('div',undefined,'collection-preflight-table');rows.id=kind+'-preflight-rows';review.append(message,totals,rows);
  const reviewActions=el('div',undefined,'collection-actions');reviewActions.append(collectionButton(kind+'-submit','校验并查看提交概览',()=>submitCollection(kind),true),collectionButton(kind+'-back-settings','返回修改设置',()=>showCollectionStep(kind,'settings',{focus:true})));review.append(reviewActions,el('p','提交后可离开页面。每个输入生成独立比较；同一输入内的算法交给共享 GPU 调度。批量比较本身不生成精度排名，数据集评测需要逐输入参考标注。','hint'));
  const changed=event=>{if(event?.target===camera||event?.target===calibration)shared.checked=false;invalidateCollectionReview(kind);updateCollectionDraft(kind);};
  for(const field of fields.querySelectorAll('input,select')){field.oninput=changed;field.onchange=changed;}for(const field of [reference,calibration,shared,camera]){field.oninput=changed;field.onchange=changed;}algorithms.oninput=changed;algorithms.onchange=changed;
  setup.ontoggle=()=>{if(setup.open&&draft.mode!=='draft')setCollectionMode(kind,'draft');};
  if(kind==='evaluation'&&typeof configureEvaluationComposer==='function')configureEvaluationComposer();
  renderCollectionInputs(kind);showCollectionStep(kind,'inputs');setCollectionMode(kind,draft.mode,{route:false});
}
function syncCollectionCheckboxes(){for(const check of document.querySelectorAll('.collection-items input'))if(check.dataset.collectionInput)check.checked=platform.selection.has(check.dataset.collectionInput);}
function removeCollectionInput(key){platform.selection.delete(key);syncCollectionCheckboxes();selectionSummary();}
function renderCollectionInputs(kind){
  const draft=collectionDraft(kind),values=[...platform.selection.entries()],episodes=values.filter(([,value])=>value.dataset).length,gt=values.filter(([,value])=>value.annotation?.ready||value.reference).length;
  draft.page=Math.min(draft.page,Math.max(0,Math.ceil(values.length/25)-1));$(''+kind+'-input-summary').textContent=`待提交 ${values.length} 个输入 · ${episodes} 个 Episode · ${values.length-episodes} 个视频 · ${gt} 项有可用参考`;
  const host=$(kind+'-input-list');host.replaceChildren();if(!values.length)host.append(el('p','尚未选择输入。添加视频或 Episode 后，返回此处核对清单。','hint'));
  else{const table=el('table'),head=el('tr');for(const label of ['序号 / 类型','输入与相机','参考标注','操作'])head.append(el('th',label));table.append(head);
    values.slice(draft.page*25,(draft.page+1)*25).forEach(([key,value],offset)=>{const row=el('tr'),name=el('td');name.append(el('strong',value.name||value.source),el('small',value.dataset?`${value.dataset.root} · Episode ${value.dataset.episode} · ${value.dataset.video_key||'默认相机流'}`:value.source));row.append(el('td',`${draft.page*25+offset+1} · ${value.dataset?'Episode':'视频'}`),name,el('td',value.annotation?.ready?'已有 GT':value.reference?'显式参考':'无可用 GT'),el('td'));row.lastChild.append(collectionButton(kind+'-remove-'+(draft.page*25+offset),'移除',()=>removeCollectionInput(key)));table.append(row);});host.append(table);}
  $(kind+'-input-pager').hidden=values.length<=25;$(kind+'-input-page').textContent=`${draft.page+1} / ${Math.max(1,Math.ceil(values.length/25))}`;$(kind+'-input-previous').disabled=draft.page===0;$(kind+'-input-next').disabled=(draft.page+1)*25>=values.length;$(kind+'-to-settings').disabled=!values.length||(kind==='evaluation'&&gt!==values.length);
  if(kind==='evaluation'&&gt!==values.length)$(kind+'-input-summary').textContent+=` · ${values.length-gt} 项缺少 GT，请移除或改用批量比较`;
}
function collectionReferenceMode(kind){const field=$(kind+'-reference');return kind==='evaluation'?'required':['available','none','required'].includes(field.value)?field.value:field.checked?'available':'none';}
function collectionCalibrationMode(kind){const field=$(kind+'-calibration');return ['none','dataset','manual'].includes(field.value)?field.value:field.checked?'dataset':$(''+kind+'-camera').value.trim()?'manual':'none';}
function collectionPayload(kind){
  const chosen=[...platform.selection.values()];if(!chosen.length)throw new Error('请先选择视频或 Episode，并核对输入清单');if(chosen.length>1000)throw new Error('单个批次最多支持 1000 个输入，请分批提交');
  const raw=Object.fromEntries(['target_fps','start_frame','max_frames'].map(key=>[key,$(kind+'-'+key).value.trim()]));
  if(!raw.target_fps||!raw.start_frame||!Number.isFinite(Number(raw.target_fps))||Number(raw.target_fps)<0||!Number.isInteger(Number(raw.start_frame))||Number(raw.start_frame)<0||(raw.max_frames&&(!Number.isInteger(Number(raw.max_frames))||Number(raw.max_frames)<1)))throw new Error('请检查帧范围和 FPS：起始帧需为非负整数，最多帧数需为正整数或留空');
  const options={view:$(kind+'-view').value,target_fps:Number(raw.target_fps),start_frame:Number(raw.start_frame),max_frames:raw.max_frames?Number(raw.max_frames):null},reference=collectionReferenceMode(kind),calibration=collectionCalibrationMode(kind);
  if(reference==='required'&&chosen.some(value=>!(value.dataset&&value.annotation?.ready)&&!value.reference))throw new Error('当前策略要求每个输入都有 GT；请移除无参考输入，或在批量比较中改为附加可用 GT');
  if(calibration==='manual'){const text=$(kind+'-camera').value.trim();if(!text)throw new Error('请填写共用手工标定 JSON');try{options.camera=JSON.parse(text);}catch(_){throw new Error('手工标定不是有效 JSON');}if(!options.camera||Array.isArray(options.camera)||typeof options.camera!=='object')throw new Error('手工标定需为包含 K、width、height、source 的对象');if(chosen.length>1&&!$(kind+'-camera-shared').checked)throw new Error('请先核对手工标定适用于全部输入；不同相机应分批或使用各 Episode 自身标定');}
  const items=chosen.map(value=>({source:value.source,name:value.name,...(value.reference&&reference!=='none'?{reference:structuredClone(value.reference)}:{}),...(value.dataset?{dataset:{...value.dataset,attach_gt:reference!=='none'&&!!value.annotation?.ready,use_calibration:calibration==='dataset'}}:{})}));
  const selections=selectedAlgorithmsFrom($(kind+'-algorithms'));if(!selections.length||selections.length>7)throw new Error('请选择 1–7 个算法变体，MINT 多检查点分别计数');
  return {kind,items,selections,options};
}
function collectionFormSignature(kind){
  return JSON.stringify({inputs:[...platform.selection.entries()],fields:['view','target_fps','start_frame','max_frames','camera','calibration','reference','camera-shared'].map(key=>{const field=$(kind+'-'+key);return [key,field.value,field.checked];}),selections:selectedAlgorithmsFrom($(kind+'-algorithms'))});
}
function invalidateCollectionReview(kind){const draft=collectionDraft(kind);draft.revision++;draft.preflight=null;if($(kind+'-validation-message'))$(kind+'-validation-message').textContent='输入或设置已变化，请重新预检；尚未提交后台任务。';$(kind+'-review-summary').replaceChildren();$(kind+'-preflight-rows').replaceChildren();}
function updateCollectionDraft(kind){
  const draft=collectionDraft(kind),calibration=collectionCalibrationMode(kind);$(kind+'-manual-settings').hidden=calibration!=='manual';if(calibration==='manual')$(kind+'-manual-settings').open=true;
  $(kind+'-protocol-note').textContent='GT 用于参考评测，与算法输入内参分别设置。'+(calibration==='dataset'?'每个 Episode 使用自身标定；普通视频不会获得标定，ACE K-given 仍需逐输入满足内参要求。':calibration==='manual'?'本份标定将应用到全部输入，必须对应各自完整源视频网格。':'不向算法提供已知内参；ACE K-given 不能在此条件下运行。')+' MINT 保持 FoV 预测，ACE K-free 保持内参预测；HaWoR 在提供标定时使用它。';
  for(const id of ['submit','check'])$(kind+'-'+id).disabled=draft.busy||!platform.selection.size;
  $(kind+'-submit').textContent=draft.busy?'正在处理…':draft.preflight?.check.ready?(kind==='evaluation'?'提交已校验评测':'提交已校验批次'):'校验并查看提交概览';
}
function showCollectionStep(kind,key,{focus=false}={}){const draft=collectionDraft(kind);draft.step=key;draft.stepRevision=(draft.stepRevision||0)+1;for(const name of ['inputs','settings','review']){const active=key===name;$(kind+'-step-'+name).hidden=!active;$(kind+'-step-nav-'+name).classList.toggle('active',active);$(kind+'-step-nav-'+name).setAttribute('aria-current',active?'step':'false');}if(key==='review'&&!draft.preflight)$(kind+'-validation-message').textContent='尚未预检。校验会读取所选输入和算法条件，不会启动推理。';updateCollectionDraft(kind);if(focus)$(kind+'-heading-'+key).focus();}
function collectionDefinition(host,values){host.replaceChildren();const list=el('dl');for(const [label,value]of values)list.append(el('dt',label),el('dd',String(value)));host.append(list);}
function renderCollectionPreflight(kind,payload,check){
  const rows=check.inputs||[],warnings=rows.flatMap(row=>row.warnings||[]),missing=rows.filter(row=>!row.gt).length;
  $(kind+'-validation-message').dataset.ready=String(check.ready);$(kind+'-validation-message').textContent=check.ready?'预检通过。请核对下方样本范围和警告，再点击提交；当前尚未启动推理。':'预检未通过：'+(check.errors||[]).slice(0,20).join('；');
  collectionDefinition($(kind+'-review-summary'),[['输入 × 算法变体',`${payload.items.length} × ${payload.selections.length} = ${payload.items.length*payload.selections.length} 个推理任务`],['总选中帧数',check.total_frames??'待确认'],['附加参考',`${rows.length-missing} / ${rows.length} 个输入`],['检查警告',`${warnings.length} 项`],['帧范围',payload.options.max_frames===null?'从所选起始帧处理到片尾':`每个输入最多 ${payload.options.max_frames} 帧`]]);
  const host=$(kind+'-preflight-rows');host.replaceChildren();const table=el('table'),head=el('tr');for(const name of ['输入','帧 / 视角','内参 / GT','检查结果与警告'])head.append(el('th',name));table.append(head);
  rows.forEach((row,index)=>{const tr=el('tr');tr.append(el('td',payload.items[index]?.name||payload.items[index]?.source||String(index+1)),el('td',`${row.frames??0} 帧 · ${row.view||'—'}`),el('td',`${row.calibration?'提供内参':'不提供内参'} · ${row.gt?'附加 GT':'无 GT'}`),el('td',[...(row.errors||[]),...(row.warnings||[])].join('；')||(row.ready?'通过':'未通过')));table.append(tr);});host.append(table);
}
async function preflightCollection(kind){
  const draft=collectionDraft(kind);if(draft.busy)return;const payload=collectionPayload(kind),signature=collectionFormSignature(kind),revision=draft.revision,navigation=platform.serial,stepRevision=draft.stepRevision;draft.busy=true;draft.preflight=null;updateCollectionDraft(kind);
  try{const check=await api('api/comparison/collections/preflight',payload);if(signature!==collectionFormSignature(kind)||revision!==draft.revision)throw new Error('输入或参数已变化，请核对后重新提交预检');draft.preflight={payload:structuredClone(payload),signature,check};renderCollectionPreflight(kind,payload,check);if(navigation===platform.serial&&stepRevision===draft.stepRevision)showCollectionStep(kind,'review',{focus:platform.pane===kind});}
  finally{draft.busy=false;updateCollectionDraft(kind);}
}
async function submitCollection(kind){
  const draft=collectionDraft(kind);if(draft.busy||$(kind+'-submit').disabled)return;
  if(!draft.preflight||!draft.preflight.check.ready||draft.preflight.signature!==collectionFormSignature(kind))return preflightCollection(kind);
  const {payload}=draft.preflight,serial=platform.serial,revision=draft.revision,signature=collectionFormSignature(kind),stepRevision=draft.stepRevision;draft.busy=true;updateCollectionDraft(kind);
  try{const receipt=draft.preflight.check.preflight_id,value=await api('api/comparison/collections',{...structuredClone(payload),...(receipt?{preflight_id:receipt}:{})});if(serial===platform.serial&&platform.pane===kind&&revision===draft.revision&&signature===collectionFormSignature(kind)&&stepRevision===draft.stepRevision)await openCollection(value.id);else{$(kind+'-validation-message').textContent='批次已提交，后台继续运行，可到任务中心查看。';if(typeof notify==='function')notify('批次已提交，当前草稿已保留；后台进度可在任务中心查看。');}}
  catch(error){invalidateCollectionReview(kind);$(kind+'-validation-message').textContent='提交未确认：'+error.message+'；请重新预检后重试，相同批次会复用既有记录。';throw error;}
  finally{draft.busy=false;updateCollectionDraft(kind);}
}
function setCollectionMode(kind,mode,{route=true}={}){
  const draft=collectionDraft(kind);if(mode==='results'&&(!platform.collection||platform.collectionKind!==kind))mode='draft';draft.mode=mode;$(kind+'Setup').open=mode==='draft';
  $('collectionDetails').hidden=mode!=='results'||platform.pane!==kind;$(kind+'-draft-mode').classList.toggle('active',mode==='draft');$(kind+'-results-mode').classList.toggle('active',mode==='results');$(kind+'-results-mode').hidden=!platform.collection||platform.collectionKind!==kind;
  if(route&&!platformNavigation.restoring&&platform.pane===kind){const url=new URL(location.href);if(mode==='results')url.searchParams.set('collection',platform.collection);else url.searchParams.delete('collection');window.history.replaceState(null,'',url.pathname+url.search+url.hash);}refreshPlatformContext();
}
async function openPlatform(tab='data'){
  pause();clearPlatformError();if(!$('platformDialog').open)$('platformDialog').show();
  if(!$('collectionDirectory').value)$('collectionDirectory').value=sourceBrowser.path||'/data1/lizhen_dataset';
  if(!$('hubDatasetRoot').value)$('hubDatasetRoot').value=$('datasetRoot').value||'/data1/lizhen_algo/mint/data/samples/lerobot_v3';
  switchHub(tab);
}
function switchHub(tab){
  if(!HUB_LABELS[tab])return;
  const body=document.querySelector('.platform-body');platformNavigation.scroll.set(platform.pane,body.scrollTop);
  platform.pane=tab;platform.serial++;clearTimeout(platform.timer);$('platformTitle').textContent=HUB_LABELS[tab];clearPlatformError();
  for(const key of Object.keys(HUB_LABELS))$('hub-'+key).hidden=key!==tab;
  syncPlatformNavigation(tab);if(!platformNavigation.restoring)platformRoute(tab);requestAnimationFrame(()=>{if(platform.pane===tab)body.scrollTop=platformNavigation.scroll.get(tab)||0;});
  $('collectionDetails').hidden=!platform.collection||platform.collectionKind!==tab;
  if(['batch','evaluation'].includes(tab)){createComposer(tab);setCollectionMode(tab,collectionDraft(tab).mode,{route:false});}
  if(tab==='data'){const reuse=$('useCurrentDirectory'),path=typeof sourceBrowser==='undefined'?null:sourceBrowser.path;reuse.disabled=!path;reuse.title=path?'使用新建比较输入浏览器的目录：'+path:'先在新建比较的输入浏览器中浏览目录，或直接填写服务器目录';}if(tab==='tools')refreshToolOptions().catch(hubError);
  if(tab==='catalog')refreshArtifactCatalog().catch(hubError);
  if(tab==='quality')refreshQualityContext().catch(hubError);if(tab==='tasks'){restoreTaskFilters();taskFilterRoute();}
  if(tab==='research')refreshResearchChoices().catch(hubError);
  window.dispatchEvent(new CustomEvent('mint-platform-open',{detail:tab}));refreshPlatformContext();updateDiagnosticReturnLinks();requestAnimationFrame(()=>{if(platform.pane===tab&&$('platformDialog').open)focusWorkspaceContent();});
  pollPlatform();
}
async function pollPlatform(){
  clearTimeout(platform.timer);if(!$('platformDialog').open)return;const serial=platform.serial;
  try{if(!document.hidden){if(platform.pane==='tasks')await refreshWorkspaceTasks();if(platform.collection&&platform.pane===platform.collectionKind)await refreshCollection();if(platform.renderJob&&platform.pane==='tools')await refreshRenderTool();}}
  catch(error){if(serial===platform.serial)hubError(error);}
  finally{if($('platformDialog').open&&serial===platform.serial)platform.timer=setTimeout(pollPlatform,3000);}
}
function statusLabel(status){return STAGES[status]||({waiting_gpu:'等待 GPU',evicted:'缓存已释放',absent:'尚未生成'})[status]||status;}
async function refreshWorkspaceTasks(){
  const serial=++platform.taskSerial,signature=workspaceTaskFilterSignature(),query=new URLSearchParams({limit:50,offset:platform.taskOffset,query:$('taskSearch').value,status:$('taskStatusFilter').value,kind:$('taskKindFilter')?.value||'all'});
  const host=$('workspaceTasks'),changing=signature!==platform.taskDisplayedFilter;if(changing){host.setAttribute('aria-busy','true');host.replaceChildren(el('p','正在读取符合条件的任务…','hint'));$('taskPageInfo').textContent='正在读取任务…';}
  let value;try{value=await api('api/comparison/workspace?'+query);}catch(error){if(serial!==platform.taskSerial||signature!==workspaceTaskFilterSignature())return null;host.setAttribute('aria-busy','false');if(changing)host.replaceChildren(el('p','任务读取失败，请刷新重试。','task-error'));throw error;}if(serial!==platform.taskSerial||signature!==workspaceTaskFilterSignature())return null;host.setAttribute('aria-busy','false');
  renderTaskResourceSummary(value);const renderKey=JSON.stringify([signature,value.total,value.offset,value.tasks.map(task=>[task.id,task.kind,task.label,task.source,task.status,task.comparison_id,task.url,task.error,task.waiting_reason,task.eta?.seconds,...['stage','phase','done','total','unit'].map(key=>task.progress?.[key])])]);
  if(renderKey!==platform.taskRenderKey){const focused=document.activeElement?.dataset?.taskControl;host.replaceChildren();
  if(!value.tasks.length){const empty=el('div',undefined,'workspace-empty');empty.append(el('h3',value.total?'此页暂无任务':'没有符合条件的任务'),el('p',$('taskStatusFilter').value==='active'?'当前没有运行中或排队任务。可以查看全部记录，或到比较页创建任务。':'调整类别、状态或搜索条件后重试。'));const reset=el('button','查看全部任务');reset.onclick=()=>{$('taskSearch').value='';$('taskStatusFilter').value='all';$('taskKindFilter').value='all';platform.taskOffset=0;taskFilterRoute();refreshWorkspaceTasks().catch(hubError);};empty.append(reset);host.append(empty);}
  else{const table=el('table'),head=el('tr');for(const label of ['任务 / 来源','状态 / 当前阶段','完成量','操作'])head.append(el('th',label));table.append(head);
    for(const task of value.tasks){const row=el('tr'),name=el('td'),status=el('td'),actions=el('td',undefined,'task-actions'),progress=task.progress||{},phase=progress.stage||progress.phase;
      name.append(el('span',taskKindLabel(task.kind),'task-kind'),el('strong',task.label));if(task.source)name.append(el('small',task.kind==='workflow'?({train:'模型训练',native_import:'公开数据准备',prepare_training:'训练数据转换',produce_pseudo:'伪标签生产',curate_dataset:'复核数据导出',diagnostic:'环境诊断',pipeline:'参考研究流程'})[task.source]||task.source:task.source));
      status.append(el('strong',statusLabel(task.status)));if(phase&&phase!==task.status)status.append(el('small',STAGES[phase]||phase));if(task.waiting_reason&&!TERMINAL.has(task.status)&&task.status!=='evicted')status.append(el('small',task.waiting_reason,'waiting-reason'));if(task.eta)status.append(el('small',`当前阶段预计剩余 ${Math.ceil(task.eta.seconds)} 秒 · 估算`));
      const open=el('button',task.kind==='export'&&task.status==='completed'?'下载视频':'打开');open.dataset.taskControl=task.kind+':'+task.id+':open';open.onclick=()=>openWorkspaceTask(task).catch(hubError);open.disabled=!(['batch','evaluation','render','workflow','study'].includes(task.kind)||task.comparison_id||task.url);actions.append(open);
      const detail=researchButton('阶段记录',async()=>researchDetails(await api(`api/comparison/tasks/${task.kind}/${task.id}`)));detail.dataset.taskControl=task.kind+':'+task.id+':detail';actions.append(detail);
      const terminal=TERMINAL.has(task.status)||task.status==='evicted';if(['inference','batch','evaluation','render','export','workflow','study'].includes(task.kind)&&task.status!=='completed'){const button=el('button',terminal?(task.kind==='render'?'重新生成':'恢复'):'取消');button.dataset.taskAction=task.kind+':'+task.id;button.dataset.taskControl=button.dataset.taskAction+':action';button.disabled=!!platform.taskActions?.has(button.dataset.taskAction)||task.status==='cancelling';button.onclick=()=>workspaceTaskAction(task,terminal?'resume':'cancel').catch(hubError);actions.append(button);}
      if(task.error){const error=el('small',String(task.error).split('\n')[0].slice(0,200),'task-error');error.title=task.error;status.append(error);}row.append(name,status,el('td',progress.total?`${progress.done||0}/${progress.total}${progress.unit==='frames'?' 帧':progress.unit==='optimizer_steps'?' 步':''}`:'—'),actions);table.append(row);
    }host.append(table);
  }
  platform.taskRenderKey=renderKey;platform.taskDisplayedFilter=signature;if(focused)Array.from(host.querySelectorAll('[data-task-control]')).find(button=>button.dataset.taskControl===focused)?.focus?.({preventScroll:true});}
  $('taskPageInfo').textContent=`${value.total} 条 · 当前 ${value.total?value.offset+1:0}–${Math.min(value.offset+value.tasks.length,value.total)}`;$('taskPreviousPage').disabled=value.offset===0;$('taskNextPage').disabled=value.offset+value.tasks.length>=value.total;
  renderCachedPredictions(value.cached_predictions||[]);
}
async function openCollection(id,{history=true}={}){
  const serial=platform.collectionOpenSerial=(platform.collectionOpenSerial||0)+1,navigation=platform.serial,value=await api('api/comparison/collections/'+id);
  if(serial!==platform.collectionOpenSerial||navigation!==platform.serial)return null;
  platform.collection=id;platform.collectionKind=value.kind;platform.collectionValue=value;platform.collectionPage=0;platform.report=null;platform.collectionReportSerial=(platform.collectionReportSerial||0)+1;$('evaluationResults').replaceChildren();$('collectionResultFilter').value='';
  if(!$('platformDialog').open)$('platformDialog').show();const navigate=()=>{switchHub(value.kind);setCollectionMode(value.kind,'results',{route:history});};if(history)navigate();else restoringRoute(navigate);const openedNavigation=platform.serial;
  await renderCollection(value);if(id===platform.collection&&openedNavigation===platform.serial)$('collectionDetails').scrollIntoView({block:'start'});return value;
}
async function refreshCollection(){const id=platform.collection;if(!id)return;const serial=platform.collectionRefreshSerial=(platform.collectionRefreshSerial||0)+1,value=await api('api/comparison/collections/'+id);if(id===platform.collection&&serial===platform.collectionRefreshSerial)await renderCollection(value);}
function collectionErrors(item){return [...new Set([item.error,...(Array.isArray(item.errors)?item.errors:[])].filter(Boolean))].join('；');}
function collectionCounts(value){const counts={queued:0,running:0,completed:0,partial:0,failed:0,cancelled:0,interrupted:0};for(const item of value.items)if(Object.hasOwn(counts,item.status))counts[item.status]++;return value.counts||counts;}
function updateCollectionActions(value){const busy=!!platform.collectionAction;$('cancelCollection').disabled=busy||TERMINAL.has(value.status)||value.status==='cancelling';$('resumeCollection').disabled=busy||!TERMINAL.has(value.status)||value.status==='completed';$('downloadCollectionJSON').disabled=!value.report_available;}
async function renderCollection(value){
  if(value.id!==platform.collection)return;platform.collectionValue=value;$('collectionDetails').hidden=platform.pane!==value.kind||collectionDraft(value.kind).mode!=='results';$('collectionTitle').textContent='已提交批次 · '+value.title;
  const counts=collectionCounts(value);$('collectionProgress').textContent=`${statusLabel(value.status)} · 已结束 ${value.done}/${value.total}${value.error?' · '+value.error:''}`;
  $('collectionResultSummary').textContent=`完整完成 ${counts.completed} · 部分结果 ${counts.partial} · 失败 ${counts.failed} · 已取消 ${counts.cancelled} · 已中断 ${counts.interrupted} · 排队 ${counts.queued} · 运行 ${counts.running}`;updateCollectionActions(value);
  const options=value.options||{},gt=value.inputs.filter(input=>input.dataset?.attach_gt||input.reference).length;
  collectionDefinition($('collectionFrozenFields'),[['冻结输入',`${value.total} 个 · ${value.selections.length} 个算法变体 / 输入`],['算法',value.selections.map(selection=>selection.algorithm+(selection.checkpoint?' · '+selection.checkpoint.split('/').pop():'')).join(' / ')],['帧范围',`${options.start_frame||0} 起 · ${options.max_frames??'全部剩余帧'}`],['视角 / 目标 FPS',`${options.view||'full'} / ${options.target_fps||'源帧率'}`],['参考标注',`${gt} / ${value.total} 个输入`],['已知内参',options.camera?'全部输入共用已声明手工标定':value.inputs.some(input=>input.dataset?.use_calibration)?'各 Episode 使用自身标定；普通视频无标定':'未提供已知内参']]);
  const query=$('collectionResultFilter').value.trim().toLowerCase(),all=value.items.map((item,index)=>({item,index})),filtered=all.filter(({item,index})=>!query||[value.inputs[index].name,value.inputs[index].source,item.status,collectionErrors(item)].join(' ').toLowerCase().includes(query));
  platform.collectionPage=Math.min(platform.collectionPage||0,Math.max(0,Math.ceil(filtered.length/50)-1));const start=platform.collectionPage*50;
  const table=el('table'),head=el('tr');for(const label of ['序号 / 输入','状态与错误原因','结果'])head.append(el('th',label));table.append(head);
  filtered.slice(start,start+50).forEach(({item,index})=>{const row=el('tr'),name=el('td');name.append(el('strong',`${index+1} · ${value.inputs[index].name||value.inputs[index].source}`),el('small',value.inputs[index].dataset?`Episode ${value.inputs[index].dataset.episode} · ${value.inputs[index].dataset.video_key||'默认相机流'}`:value.inputs[index].source));row.append(name,el('td',statusLabel(item.status)+(collectionErrors(item)?' · '+collectionErrors(item):'')));const cell=el('td');if(item.comparison_id){const button=el('button','打开此输入的比较');button.onclick=()=>{$('platformDialog').close();openComparison(item.comparison_id).catch(error=>notify(error.message));};cell.append(button);}row.append(cell);table.append(row);});$('collectionChildren').replaceChildren(table);
  $('collectionResultPage').textContent=`${filtered.length?start+1:0}–${Math.min(start+50,filtered.length)} / ${filtered.length} 项 · 全批 ${value.total} 项`;$('collectionResultPrevious').disabled=start===0;$('collectionResultNext').disabled=start+50>=filtered.length;refreshPlatformContext();
  if(!value.report_available){platform.report=null;platform.collectionReportSerial=(platform.collectionReportSerial||0)+1;$('evaluationResults').replaceChildren();}
  if(value.report_available&&(!platform.report||platform.report._collection_id!==value.id)){
    const generation=platform.collectionReportSerial,report=await api(`api/comparison/collections/${value.id}/report`);if(platform.collection!==value.id||generation!==platform.collectionReportSerial||!platform.collectionValue?.report_available)return;platform.report={...report,_collection_id:value.id};renderEvaluationReport(platform.report);
  }
  $('downloadCollectionCSV').hidden=value.kind!=='evaluation'||!value.report_available;$('downloadCollectionCSV').href=`api/comparison/collections/${value.id}/report?format=csv`;$('downloadCollectionCSV').download='evaluation-'+value.id.slice(0,8)+'.csv';
}
async function collectionAction(action){
  const id=platform.collection;if(!id||platform.collectionAction)return;platform.collectionAction={id,action};if(action==='resume'){platform.collectionReportSerial=(platform.collectionReportSerial||0)+1;platform.report=null;}if(platform.collectionValue)updateCollectionActions(platform.collectionValue);
  try{const value=await api(`api/comparison/collections/${id}/${action}`,{});if(id!==platform.collection)return;platform.collectionRefreshSerial=(platform.collectionRefreshSerial||0)+1;if(action==='resume')platform.report=null;await renderCollection(value);}
  finally{platform.collectionAction=null;if(platform.collectionValue)updateCollectionActions(platform.collectionValue);}
}
async function downloadCollectionReport(){const id=platform.collection;if(!id)throw new Error('请先打开已提交批次');const value=await api(`api/comparison/collections/${id}/report`);download('collection-'+id.slice(0,8)+'.json',JSON.stringify(value,null,2));}
function renderEvaluationReport(report){
  const host=$('evaluationResults');host.replaceChildren();if(!report.rows)return;
  const overview=el('div',undefined,'evaluation-overview');for(const [label,value]of [['输入数',report.total_sequences],['有可评分参考',report.scorable_sequences],['算法配置',report.rows.length],['评测协议',report.protocol?.name||'标准几何评测']]){const card=el('div');card.append(el('small',label),el('strong',String(value??'—')));overview.append(card);}host.append(overview);
  const basic=[['算法',row=>row.label],['已评分 / 评测失败',row=>`${row.scored_sequences} / ${row.failed_sequences}`],['GT几何覆盖率',row=>row.gt_geometry_coverage==null?'—':fmt(row.gt_geometry_coverage*100,1)+'%'],['关节误差 mm',row=>metricValue(row.camera_absolute_joint_mm)],['腕对齐误差 mm',row=>metricValue(row.wrist_aligned_joint_mm)]];
  const extended=[['算法',row=>row.label],['完整 / 部分结果',row=>`${row.completed_sequences??'—'} / ${row.partial_sequences??'—'}`],['无可评分参考',row=>row.unscorable_sequences??'—'],['Sim3 手部 mm',row=>metricValue(row.similarity_aligned_joint_mm)],['重投影 px',row=>metricValue(row.reprojection_difference_px)],['相机 ATE SE3 m',row=>metricValue(row.camera_ate_se3_rmse_m,4)],['相机 ATE Sim3 m',row=>metricValue(row.camera_ate_sim3_rmse_m,4)],['RPE 平移 m',row=>metricValue(row.camera_rpe_translation_rmse_m,4)],['RPE 旋转 °',row=>metricValue(row.camera_rpe_rotation_rmse_deg,3)]];
  const tableFor=(columns,className)=>{const wrapper=el('div',undefined,className),table=el('table'),head=el('tr');for(const [name]of columns)head.append(el('th',name));table.append(head);for(const row of report.rows){const tr=el('tr');for(const [,format]of columns)tr.append(el('td',String(format(row))));table.append(tr);}wrapper.append(table);return wrapper;};
  host.append(tableFor(basic,'evaluation-summary-table'),el('p','覆盖率以参考手部几何为分母，包含失败预测；部分结果也可能贡献已评分帧。未提供的指标显示“—”。','hint'));
  const details=el('details',undefined,'evaluation-metric-details');details.append(el('summary','更多指标与计数说明'),tableFor(extended,'hub-table'),el('p','相机与手部的对齐方式分别计算；本平台协议不等同于公开 Benchmark 复现。评测失败包括无法评分的输入。完整报告保留逐输入原因与全部指标。','hint'));host.append(details);
}
const toolsUI={view:'catalog',capabilitySerial:0,optionsSerial:0,statusSerial:0,jobSelection:0,comparisonKey:null,options:null,optionsError:'',activeJob:null};
function toolComparisonKey(){const value=state.comparison;return JSON.stringify([value?.id,value?.sample_id,(value?.runs||[]).filter(run=>run.result).map(run=>[run.id,run.status,Object.entries(run.result.arrays||{}).map(([name,record])=>[name,record.file])])]);}
function initialToolsView(query){const chosen=query.get('tools_tab');return ['catalog','render','environment'].includes(chosen)?chosen:query.get('tool_job')?'render':'catalog';}
function showToolsView(view,{route=true,kind=null}={}){
  if(!['catalog','render','environment'].includes(view))view='catalog';toolsUI.view=view;
  for(const panel of $('hub-tools').querySelectorAll('[data-tools-view]'))panel.hidden=panel.dataset.toolsView!==view;
  for(const button of $('hub-tools').querySelectorAll('[data-tools-tab]')){const active=button.dataset.toolsTab===view;button.classList.toggle('active',active);button.setAttribute('aria-current',active?'page':'false');}
  if(kind)$('renderKind').value=kind;
  if(route&&!platformNavigation.restoring&&platform.pane==='tools'){const url=new URL(location.href);url.searchParams.set('tools_tab',view);if(platform.renderJob)url.searchParams.set('tool_job',platform.renderJob);window.history.replaceState(null,'',url.pathname+url.search+url.hash);}
  refreshPlatformContext();syncRenderSettings();if(view==='render'){if(toolsUI.options?.run_id!==$('renderRun').value)loadRenderOptions().catch(hubError);if(toolsUI.activeJob?.status==='completed')$('renderToolVideo').hidden=false;}else $('renderToolVideo').pause();
}
function syncToolRuns(){
  const comparison=state.comparison,key=JSON.stringify([comparison?.id,comparison?.sample_id]),changed=key!==toolsUI.comparisonKey,old=changed?'':$('renderRun').value,runs=(comparison?.runs||[]).filter(run=>run.result);
  toolsUI.comparisonKey=key;const select=$('renderRun');select.replaceChildren();const blank=el('option',runs.length?'请选择已有结果':'暂无已有几何结果');blank.value='';select.append(blank);
  for(const run of runs){const option=el('option',(run.result.reference_role==='user_declared_ground_truth'?'GT · ':'')+run.label+' · '+run.id.slice(0,6));option.value=run.id;select.append(option);}
  select.value=runs.some(run=>run.id===old)?old:runs.find(run=>run.result.reference_role!=='user_declared_ground_truth')?.id||runs[0]?.id||'';
  if(changed||(old&&!runs.some(run=>run.id===old))){toolsUI.optionsSerial++;toolsUI.options=null;toolsUI.optionsError='';$('renderStart').value='1';$('renderEnd').value='';}
  $('toolsResultContext').textContent=comparison?`配置来源：${comparison.title||comparison.id} · ${comparison.sample?.frames??'—'} 个样本帧`:'先选择一组含已有几何结果的比较。目录中的独立工具不需要此选择。';
  $('chooseToolComparison').textContent=comparison?'更换比较':'选择比较';syncRenderSettings();
}
function syncToolsComparison(){
  if(platform.pane!=='tools'||!$('platformDialog').open||toolsUI.publishedKey===toolComparisonKey())return;
  refreshToolOptions().catch(hubError);
}
async function refreshToolOptions(){
  syncToolRuns();const key=toolComparisonKey(),serial=++toolsUI.capabilitySerial;toolsUI.publishedKey=key;
  let capability;try{capability=await api('api/comparison/capabilities');}catch(error){if(serial!==toolsUI.capabilitySerial||key!==toolComparisonKey()||platform.pane!=='tools')return null;throw error;}if(serial!==toolsUI.capabilitySerial||key!==toolComparisonKey()||platform.pane!=='tools')return null;
  platform.capability=capability;renderToolReadiness(capability);syncRenderSettings();if(toolsUI.view==='render')await loadRenderOptions();return capability;
}
function renderToolReadiness(cap){
  const host=$('toolReadiness');host.replaceChildren();host.classList.add('tool-readiness-grid');
  const add=(label,ready,reason)=>{const card=el('div',undefined,'tool-readiness-card'),badge=el('span',ready?'环境已就绪':'需配置','readiness-badge');badge.dataset.ready=String(!!ready);card.append(el('strong',label),badge,el('p',reason,'hint'));host.append(card);};
  for(const [kind,label]of [['mujoco','MuJoCo 三维展示'],['retarget','Wuji Hand 重定向']])add(label,cap.render?.[kind]?.ready,cap.render?.[kind]?.ready?'环境已就绪；生成前还需检查所选几何、坐标和帧范围。':cap.render?.[kind]?.reason||'渲染依赖尚未就绪');
  add('数据分析入口',cap.data_analysis,cap.data_analysis?'后端已连接；打开后选择独立输入。':'数据分析后端未连接。');add('MINT 诊断与验证入口',cap.legacy,cap.legacy?'后端已连接；具体模型和数据需在工具中预检。':'诊断后端未连接。');
  add('Aliyun 云端评测',cap.cloud?.ready,cap.cloud?.ready?'配置已填写，仍需在公开基准工具中预检并明确提交。':cap.cloud?.reason||'尚未配置云端环境。');
  if(cap.benchmark){const bench=cap.benchmark,details=el('details',undefined,'tool-readiness-card');details.append(el('summary','公开基准：适配器与模型能力'),el('p','已有适配器：'+((bench.datasets||[]).filter(row=>row.implemented).map(row=>row.name).join('、')||'无'),'hint'),el('p','待实现适配器：'+((bench.datasets||[]).filter(row=>!row.implemented).map(row=>row.name).join('、')||'无'),'hint'),el('p','待实现指标：'+((bench.heads||[]).filter(row=>!row.implemented).map(row=>row.name).join('、')||'无'),'hint'),el('p','当前模型能力：'+((bench.model_capability||[]).join('、')||'未声明')+'。适配器存在不代表模型已支持全部指标。','hint'));host.append(details);}
  for(const badge of $('hub-tools').querySelectorAll('[data-tool-status]')){const type=badge.dataset.toolStatus,entry=badge.closest('a,button'),render=['mujoco','retarget'].includes(type),ready=render?!!cap.render?.[type]?.ready:type==='analysis'?!!cap.data_analysis:!!cap.legacy;
    badge.dataset.ready=String(ready);badge.textContent=ready?(render?'环境已就绪':'入口已连接'):(render?'需配置':'未连接');
    if(entry?.tagName==='A'){entry.setAttribute('aria-disabled',String(!ready));entry.title=ready?'打开后选择输入与参数；不会自动运行':'后端未连接，请先检查环境';entry.onclick=ready?null:event=>{event.preventDefault();hubError(new Error('该工具后端未连接，请先检查环境。'));};}
    // A missing rendering environment may still be inspected in its configuration view.
  }
}
function loadRenderOptions(){
  const run=$('renderRun').value,key=toolComparisonKey();
  if(toolsUI.options?.run_id===run&&toolsUI.optionsKey===key){syncRenderSettings();return Promise.resolve(toolsUI.options);}
  if(toolsUI.optionsPending?.run===run&&toolsUI.optionsPending.key===key)return toolsUI.optionsPending.promise;
  const serial=++toolsUI.optionsSerial;toolsUI.options=null;toolsUI.optionsError='';syncRenderSettings();if(!run)return Promise.resolve(null);
  const promise=(async()=>{try{const value=await api(`api/comparison/runs/${run}/tools/options`);if(serial!==toolsUI.optionsSerial||key!==toolComparisonKey()||run!==$('renderRun').value)return null;if(value.run_id!==run)throw new Error('结果适用性与所选结果不一致，请重新读取');toolsUI.options=value;toolsUI.optionsKey=key;$('renderStart').max=value.sample_frames;$('renderEnd').max=value.sample_frames;syncRenderSettings();return value;}
    catch(error){if(serial!==toolsUI.optionsSerial||key!==toolComparisonKey()||run!==$('renderRun').value)return null;toolsUI.optionsError=error.message;syncRenderSettings();throw error;}
    finally{if(toolsUI.optionsPending?.serial===serial)toolsUI.optionsPending=null;}})();
  toolsUI.optionsPending={run,key,serial,promise};return promise;
}
function renderSettingsSignature(){return JSON.stringify([toolComparisonKey(),...['renderRun','renderKind','renderSpace','renderStart','renderEnd','renderWidth','renderPolicy'].map(id=>$(id).value),$('renderAbsent').checked]);}
function renderRangeCovered(ranges,start,end){return (ranges||[]).some(range=>range[0]<=start&&range[1]>=end);}
function renderSettingsError(){
  if(!$('renderRun').value)return '请选择一组比较和已有几何结果。';const options=toolsUI.options;if(!options||options.run_id!==$('renderRun').value)return toolsUI.optionsError||'正在检查所选结果的网格、坐标和帧范围…';
  const capability=platform.capability?.render?.[$('renderKind').value];if(!capability?.ready)return capability?.reason||'正在检查工具环境，请稍后或到“环境检查”刷新。';
  const text=$('renderStart').value.trim(),endText=$('renderEnd').value.trim(),start=Number(text)-1,end=endText?Number(endText):options.sample_frames,width=Number($('renderWidth').value);
  if(!text||!Number.isInteger(start)||start<0||!Number.isInteger(end)||end<=start||end>options.sample_frames)return `请选择 1–${options.sample_frames} 内的样本帧区间，结束帧包含在内。`;
  if(!Number.isInteger(width)||width<320||width>1280||width%2)return '宽度需为 320–1280 的偶数。';
  if(!['shared','idle'].includes($('renderPolicy').value)||!['mujoco','retarget'].includes($('renderKind').value)||!['camera','world'].includes($('renderSpace').value))return '请选择有效的工具、坐标和资源策略。';
  if($('renderKind').value==='mujoco'&&!options.mesh_available)return '此结果只有关节点，没有 MANO 网格；可选择 Wuji Hand 重定向或换用网格结果。';
  if(!renderRangeCovered(options.available_ranges,start,end))return '所选区间包含缺失输出帧，请在高级设置中选择完整覆盖的片段。';
  if($('renderSpace').value==='world'&&!renderRangeCovered(options.world_ranges,start,end))return '所选区间没有完整有效的原生世界相机轨迹，请选择相机系或有效片段。';return '';
}
function syncRenderSettings(){
  const options=toolsUI.options,error=renderSettingsError(),run=$('renderRun').value;$('startRenderTool').disabled=$('startRenderTool').dataset.busy==='true'||!!error;
  $('renderSettingsWarning').textContent=error;$('renderSettingsWarning').hidden=!error;
  $('renderSourceNote').textContent=options?`${options.result_frames}/${options.sample_frames} 个样本帧有输出 · ${options.mesh_available?'有 MANO 网格':'仅关节点'} · ${options.world_available?'有原生世界相机':'无原生世界相机'}`:run?'正在核对已有结果…':'需要已有几何；此操作不会启动手部推理。';
  for(const option of $('renderSpace').options)if(option.value==='world')option.disabled=!!options&&!options.world_available;
  const end=$('renderEnd').value||'片尾';$('renderSettingsSummary').textContent=`样本帧 ${$('renderStart').value||'—'}–${end} · ${$('renderWidth').value} px · ${$('renderPolicy').value==='idle'?'仅空闲 GPU':'负载感知共享'}${$('renderAbsent').checked?' · 含低存在性预测':''}`;
  const ranges=$('renderSpace').value==='world'?options?.world_ranges:options?.available_ranges,start=Number($('renderStart').value)-1,boundEnd=$('renderEnd').value?Number($('renderEnd').value):options?.sample_frames;$('renderUseAvailableRange').hidden=!ranges?.length||renderRangeCovered(ranges,start,boundEnd);
  if(toolsUI.activeJob)renderToolJobSource(toolsUI.activeJob);
}
function useAvailableRenderRange(){const ranges=$('renderSpace').value==='world'?toolsUI.options?.world_ranges:toolsUI.options?.available_ranges;if(!ranges?.length)return;const best=ranges.reduce((a,b)=>b[1]-b[0]>a[1]-a[0]?b:a);$('renderStart').value=String(best[0]+1);$('renderEnd').value=String(best[1]);syncRenderSettings();}
function clearRenderPlayback(){const video=$('renderToolVideo');video.pause();video.hidden=true;video.removeAttribute('src');video.dataset.job='';}
function selectRenderJob(id){platform.renderJob=id;toolsUI.jobSelection++;toolsUI.statusSerial++;toolsUI.activeJob=null;clearRenderPlayback();$('renderToolStatus').replaceChildren(el('p',id?'正在读取生成任务…':'尚未选择生成任务。','tools-empty'));}
async function inspectRenderJob(id){selectRenderJob(id);switchHub('tools');showToolsView('render');await refreshRenderTool();}
async function startRenderTool(){
  const button=$('startRenderTool');if(button.dataset.busy==='true')return;const error=renderSettingsError();if(error)throw new Error(error);
  const run=$('renderRun').value,signature=renderSettingsSignature(),selection=toolsUI.jobSelection,options={kind:$('renderKind').value,space:$('renderSpace').value,start:Number($('renderStart').value)-1,end:$('renderEnd').value===''?null:Number($('renderEnd').value),width:Number($('renderWidth').value),include_absent:$('renderAbsent').checked,resource_policy:$('renderPolicy').value};
  button.dataset.busy='true';syncRenderSettings();
  try{const value=await api(`api/comparison/runs/${run}/tools`,options);if(selection!==toolsUI.jobSelection||signature!==renderSettingsSignature()){notify('展示任务已提交；当前选择已保留，可从任务中心查看。',false);return value;}selectRenderJob(value.id);showToolsView('render');await refreshRenderTool();return value;}
  finally{button.dataset.busy='false';syncRenderSettings();}
}
async function resumeRenderTool(id){
  const selection=toolsUI.jobSelection,value=await api('api/comparison/tools/'+id),options={};for(const key of ['kind','space','start','end','width','include_absent','resource_policy'])options[key]=value[key];
  const resumed=await api(`api/comparison/runs/${value.run_id}/tools`,options);if(selection===toolsUI.jobSelection){selectRenderJob(resumed.id);if(platform.pane==='tools'){showToolsView('render');await refreshRenderTool();}}return resumed;
}
function renderToolJobSource(job){const note=$('renderJobSourceNote');if(note)note.textContent=job.run_id!==$('renderRun').value?'这是另一份结果的生成任务；上方草稿与本任务分别管理。':'此任务使用固定参数；修改上方草稿不会改变已提交任务。';}
async function refreshRenderTool(){
  const id=platform.renderJob;if(!id)return null;const serial=++toolsUI.statusSerial,selection=toolsUI.jobSelection;let job;try{job=await api('api/comparison/tools/'+id);}catch(error){if(id!==platform.renderJob||selection!==toolsUI.jobSelection||serial!==toolsUI.statusSerial)return null;throw error;}if(id!==platform.renderJob||selection!==toolsUI.jobSelection||serial!==toolsUI.statusSerial)return null;
  toolsUI.activeJob=job;const host=$('renderToolStatus');host.classList.remove('tools-empty');host.replaceChildren();const source=el('div',undefined,'tools-job-summary');source.append(el('strong',`任务来源：${job.label||job.run_id}`),el('p',`${job.run_id.slice(0,10)} · ${job.kind==='mujoco'?'MuJoCo 三维展示':'Wuji Hand 重定向'} · ${job.space==='world'?'原生世界系':'相机系'} · 样本帧 ${job.start+1}–${job.end} · ${job.width} px`));const note=el('p',undefined,'hint');note.id='renderJobSourceNote';source.append(note);host.append(source);renderToolJobSource(job);
  host.append(el('p',`${statusLabel(job.status)}${job.progress?.total?' · '+job.progress.done+'/'+job.progress.total+' 帧':''}`));if(job.status==='waiting_gpu')host.append(el('p','等待符合所选资源策略的 GPU 余量；不会等待其他推理任务完成后再重新推理。','hint'));if(job.error)host.append(el('p',job.error,'task-error'));
  const actions=el('div',undefined,'tools-job-actions');
  if(['queued','waiting_gpu','running'].includes(job.status)){const button=el('button','取消此任务');button.onclick=async()=>{button.disabled=true;try{await api(`api/comparison/tools/${id}/cancel`,{});if(id===platform.renderJob)await refreshRenderTool();}catch(error){hubError(error);}finally{button.disabled=false;}};actions.append(button);}
  if(['failed','cancelled','interrupted','evicted'].includes(job.status)){const button=el('button','按原参数重新生成');button.onclick=()=>resumeRenderTool(id).catch(hubError);actions.append(button);}
  if(job.status==='completed'){const link=el('a','下载展示视频');link.href=job.url;link.download='';const mapping=el('a','下载帧映射与坐标说明');mapping.href=`api/comparison/tools/${id}/mapping`;actions.append(link,mapping);if($('renderToolVideo').dataset.job!==id){clearRenderPlayback();$('renderToolVideo').src=job.url;$('renderToolVideo').dataset.job=id;}$('renderToolVideo').hidden=toolsUI.view!=='render';}else clearRenderPlayback();host.append(actions);return job;
}
function initializeToolsWorkspace(){
  toolsUI.view=initialToolsView(new URLSearchParams(location.search));for(const button of $('hub-tools').querySelectorAll('[data-tools-tab]'))button.onclick=()=>showToolsView(button.dataset.toolsTab);
  for(const button of $('hub-tools').querySelectorAll('[data-render-entry]'))button.onclick=()=>showToolsView('render',{kind:button.dataset.renderEntry});
  $('chooseToolComparison').onclick=()=>{closePlatformWorkspace();if(window.innerWidth<=1100){workspaceUI.mobileSidebar=true;applyLayout();}$('historySearch').focus();};$('toolsOpenTasks').onclick=()=>switchHub('tasks');
  $('refreshToolsEnvironment').onclick=()=>refreshToolOptions().catch(hubError);$('renderRun').onchange=()=>loadRenderOptions().catch(hubError);
  for(const id of ['renderKind','renderSpace','renderStart','renderEnd','renderWidth','renderPolicy','renderAbsent']){$(id).onchange=syncRenderSettings;if(['renderStart','renderEnd'].includes(id))$(id).oninput=syncRenderSettings;}
  $('renderUseAvailableRange').onclick=useAvailableRenderRange;$('startRenderTool').onclick=()=>startRenderTool().catch(hubError);
  window.addEventListener('mint-platform-open',event=>{if(event.detail==='tools'){const query=new URLSearchParams(location.search);showToolsView(query.has('tools_tab')?initialToolsView(query):toolsUI.view,{route:false});}});
}
function inspectResult(id){if(!state.cards.has(id))return;if(typeof openParameterPanel==='function'){if($('comparisonOptions'))$('comparisonOptions').open=false;$('frameInspector').open=false;return openParameterPanel(id,document.activeElement);}resultView.expanded=id;$('inspectRun').value=id;applyResultVisibility();if($('comparisonOptions'))$('comparisonOptions').open=true;$('frameInspector').open=true;pause();ensureFrame(state.frame).then(()=>render(true));}
function initializePlatform(){
  for(const button of document.querySelectorAll('[data-platform-tab]'))button.onclick=()=>openPlatform(button.dataset.platformTab);
  for(const button of document.querySelectorAll('[data-hub-pane]'))button.onclick=()=>switchHub(button.dataset.hubPane);
  $('backToResults').onclick=()=>returnToComparison().catch(hubError);$('workspaceHome').onclick=event=>{if(event.button||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;event.preventDefault();returnToComparison({home:true}).catch(hubError);};$('workspaceSkip').onclick=event=>{event.preventDefault();focusWorkspaceContent();};$('dismissPlatformError').onclick=()=>{clearPlatformError();focusWorkspaceContent();};$('closePlatform').onclick=()=>closePlatformWorkspace();$('platformDialog').addEventListener('close',()=>{if($('platformDialog').open)return;platform.serial++;clearTimeout(platform.timer);$('renderToolVideo').pause();syncPlatformNavigation(null);if(!platformNavigation.restoring)platformRoute(null,true);});
  $('newComparison').addEventListener('click',interceptPlatformPrimaryAction,{capture:true});
  $('chooseNativeInput').onclick=()=>{$('taskDialog').close();focusHubSection('data','开源数据集原生接入');};
  $('chooseExistingInput').onclick=()=>{$('importSection').open=true;$('importSection').scrollIntoView({block:'nearest',behavior:'smooth'});$('importPath').focus({preventScroll:true});};
  for(const button of document.querySelectorAll('[data-evaluation-route]'))button.onclick=()=>{const route=button.dataset.evaluationRoute;if(route==='current'){openEvaluationCurrentMetrics().catch(hubError);}else if(route==='dataset')switchHub('evaluation');else if(route==='quality')switchHub('quality');else focusHubSection('research','固定集回归与性能对照');};
  window.addEventListener('popstate',()=>restoreWorkspaceRoute().catch(hubError));
  window.addEventListener('mint-platform-open',()=>{const section=new URLSearchParams(location.search).get('section');if(section)requestAnimationFrame(()=>{const target=findHubSection(platform.pane,section);if(target)revealPlatformTarget(target);});});
  $('chooseVideoInput').onclick=()=>setDatasetMode(false);$('chooseDatasetInput').onclick=()=>setDatasetMode(true);
  $('loadDataset').onclick=()=>loadDatasetForm(0).catch(error=>{$('datasetSummary').textContent=error.message;});$('datasetUseDirectory').onclick=()=>{$('datasetRoot').value=sourceBrowser.path;loadDatasetForm(0).catch(error=>{$('datasetSummary').textContent=error.message;});};
  $('datasetCamera').onchange=()=>loadDatasetForm(0).catch(error=>notify(error.message));$('datasetEpisode').onchange=chooseDatasetEpisode;
  $('datasetPreviousPage').onclick=()=>loadDatasetForm(Math.max(0,datasetForm.offset-100)).catch(error=>notify(error.message));$('datasetNextPage').onclick=()=>loadDatasetForm(datasetForm.offset+100).catch(error=>notify(error.message));
  $('useCurrentDirectory').onclick=()=>{if(sourceBrowser.path)$('collectionDirectory').value=sourceBrowser.path;};$('scanVideos').onclick=()=>scanCollectionVideos().catch(hubError);
  $('hubLoadDataset').onclick=()=>loadHubDataset(0).catch(hubError);$('hubDatasetCamera').onchange=()=>loadHubDataset(0).catch(hubError);$('hubDatasetPrevious').onclick=()=>loadHubDataset(Math.max(0,platform.datasetOffset-100)).catch(hubError);$('hubDatasetNext').onclick=()=>loadHubDataset(platform.datasetOffset+100).catch(hubError);
  $('selectAllVideos').onclick=()=>{for(const row of platform.scanned||[])platform.selection.set('video:'+row.source,{source:row.source,name:row.name});for(const check of $('collectionVideos').querySelectorAll('input'))check.checked=true;selectionSummary();};
  $('selectAllEpisodes').onclick=()=>{for(const check of $('hubEpisodes').querySelectorAll('input'))if(!check.checked){check.checked=true;check.dispatchEvent(new Event('change'));}};
  $('clearCollectionSelection').onclick=()=>{platform.selection.clear();for(const check of document.querySelectorAll('.collection-items input'))check.checked=false;selectionSummary();};
  $('useSingleEpisode').onclick=async()=>{try{const selected=[...platform.selection.values()];if(selected.length!==1)throw new Error('单次比较请选择一个视频或 Episode');const item=selected[0];$('platformDialog').close();if(item.dataset){$('datasetRoot').value=item.dataset.root;await loadDatasetForm(0,item.dataset);}else{setDatasetMode(false);$('source').value=item.source;}$('maxFrames').value='';openTaskDialog();}catch(error){hubError(error);}};
  $('prepareBatch').onclick=()=>newCollectionForm('batch');$('prepareEvaluation').onclick=()=>newCollectionForm('evaluation');
  $('refreshWorkspaceTasks').onclick=()=>refreshWorkspaceTasks().catch(hubError);let searchTimer;$('taskSearch').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{platform.taskOffset=0;taskFilterRoute();refreshWorkspaceTasks().catch(hubError);},250);};const taskFilterChanged=()=>{platform.taskOffset=0;platform.taskSerial++;taskFilterRoute();refreshWorkspaceTasks().catch(hubError);};$('taskStatusFilter').onchange=taskFilterChanged;$('taskKindFilter').onchange=taskFilterChanged;$('taskPreviousPage').onclick=()=>{platform.taskOffset=Math.max(0,platform.taskOffset-50);refreshWorkspaceTasks().catch(hubError);};$('taskNextPage').onclick=()=>{platform.taskOffset+=50;refreshWorkspaceTasks().catch(hubError);};
  $('cancelCollection').onclick=()=>collectionAction('cancel').catch(hubError);$('resumeCollection').onclick=()=>collectionAction('resume').catch(hubError);$('downloadCollectionJSON').onclick=()=>downloadCollectionReport().catch(hubError);
  $('collectionResultFilter').oninput=()=>{platform.collectionPage=0;if(platform.collectionValue)renderCollection(platform.collectionValue).catch(hubError);};
  $('collectionResultPrevious').onclick=()=>{platform.collectionPage=Math.max(0,(platform.collectionPage||0)-1);if(platform.collectionValue)renderCollection(platform.collectionValue).catch(hubError);};$('collectionResultNext').onclick=()=>{platform.collectionPage=(platform.collectionPage||0)+1;if(platform.collectionValue)renderCollection(platform.collectionValue).catch(hubError);};
  initializeToolsWorkspace();
  $('closeExportDialog').onclick=()=>$('exportDialog').close();$('exportCurrentPNG').onclick=()=>{$('exportDialog').close();$('snapshot').click();};$('exportFullReport').onclick=()=>$('downloadReport').click();
}

// Checkpoint browsing is read-only. Selecting a path does not load a model.
function checkpointFields(host){
  const checkpoint=el('textarea');checkpoint.rows=2;checkpoint.dataset.selection='checkpoint';checkpoint.placeholder='留空使用默认模型；每行一个检查点绝对路径';checkpoint.spellcheck=false;
  const config=el('input');config.dataset.selection='config';config.placeholder='留空优先使用检查点配置快照';config.spellcheck=false;
  host.append(inputLabel('MINT 检查点（可多选）',checkpoint),inputLabel('可选模型配置路径',config));
  const aliases=el('details',undefined,'checkpoint-browser');aliases.dataset.modelAliases='true';aliases.append(el('summary','选择已评测模型别名'));host.append(aliases);wireModelAliasBrowser(aliases);
  const picker=el('details',undefined,'checkpoint-browser');picker.append(el('summary','浏览服务器检查点'));host.append(picker);wireCheckpointBrowser(picker);
}
function wireCheckpointBrowser(picker){
  picker.ontoggle=()=>{if(picker.open&&!picker.querySelector('.checkpoint-browser-body'))browseCheckpoint(picker,'').catch(error=>notify(error.message));};
}
function wireModelAliasBrowser(picker){
  picker.ontoggle=async()=>{if(!picker.open)return;try{
    const value=await api('api/comparison/models/aliases');picker.querySelector('.alias-options')?.remove();const options=el('div',undefined,'alias-options');
    for(const alias of value.aliases){const button=el('button',`${alias.name} · ${alias.model.name}${alias.ready?'':' · 文件变化'}`);button.disabled=!alias.ready;
      button.onclick=()=>{const box=picker.closest('.algorithm');box.querySelector('[data-selection=checkpoint]').value=alias.model.path;box.querySelector('[data-selection=config]').value=alias.effective_config?.path||alias.model.config?.path||'';box.querySelector('input[type=checkbox]').checked=true;};options.append(button);}
    if(!value.aliases.length)options.append(el('p','尚无已通过门槛的模型别名。可在数据与训练中登记、评测后发布。','hint'));picker.append(options);
  }catch(error){notify(error.message);}};
}
async function browseCheckpoint(picker,path){
  const value=await api('/api/ckpt/browse?'+new URLSearchParams({path}));
  let body=picker.querySelector('.checkpoint-browser-body');if(!body){body=el('div',undefined,'checkpoint-browser-body');picker.append(body);}body.replaceChildren();
  const row=el('div',undefined,'source-path-row'),input=el('input');input.value=value.path;input.setAttribute('aria-label','检查点目录');input.spellcheck=false;
  const go=el('button','前往'),up=el('button','上一级');go.onclick=()=>browseCheckpoint(picker,input.value).catch(error=>notify(error.message));up.onclick=()=>browseCheckpoint(picker,value.parent).catch(error=>notify(error.message));input.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();go.click();}};row.append(input,go,up);body.append(row);
  const append=path=>{const field=picker.closest('.algorithm').querySelector('[data-selection=checkpoint]');const paths=field.value.split('\n').map(v=>v.trim()).filter(Boolean);if(!paths.includes(path))paths.push(path);field.value=paths.join('\n');picker.closest('.algorithm').querySelector('input[type=checkbox]').checked=true;};
  if(value.selectable){const choose=el('button','添加当前检查点目录');choose.onclick=()=>append(value.path);body.append(choose);}
  const list=el('div',undefined,'checkpoint-files');for(const directory of value.dirs){const button=el('button','📁 '+directory);button.onclick=()=>browseCheckpoint(picker,value.path+'/'+directory).catch(error=>notify(error.message));list.append(button);}for(const file of value.files){const button=el('button','＋ '+file);button.onclick=()=>append(value.path+'/'+file);list.append(button);}body.append(list);
  if(value.truncated)body.append(el('p','目录超过 2000 项，请定位更小目录。','hint'));
}
function cloneComposerAlgorithms(host){
  if(!$('algorithmOptions').querySelector('.algorithm')){host.dataset.waiting='true';host.textContent='正在读取算法…';return;}
  host.replaceChildren();delete host.dataset.waiting;
  for(const box of $('algorithmOptions').children)if(box.dataset.algorithm!=='reference'){
    const clone=box.cloneNode(true),from=box.querySelectorAll('input,textarea,select'),to=clone.querySelectorAll('input,textarea,select');
    from.forEach((field,index)=>{to[index].value=field.value;if(field.type==='checkbox')to[index].checked=field.checked;});
    if(host.dataset.onlyMint==='true')clone.querySelector('input[type=checkbox]').checked=clone.dataset.algorithm==='mint';
    for(const picker of clone.querySelectorAll('.checkpoint-browser')){picker.querySelector('.checkpoint-browser-body')?.remove();picker.querySelector('.alias-options')?.remove();picker.open=false;if(picker.dataset.modelAliases)wireModelAliasBrowser(picker);else wireCheckpointBrowser(picker);}host.append(clone);
  }
}
function refreshComposerAlgorithms(){for(const id of ['batch-algorithms','evaluation-algorithms','nativeAlgorithms','studyAlgorithms']){const host=$(id);if(host?.dataset.waiting)cloneComposerAlgorithms(host);}}
async function runDiagnostic(profile){
  const buttons=[...document.querySelectorAll('[data-diagnostic]')];for(const button of buttons)button.disabled=true;$('diagnosticStatus').textContent='正在独立进程检查…';$('diagnosticResults').replaceChildren();
  try{const value=await api('api/comparison/diagnostics',{profile}),table=el('table');
    for(const check of value.checks){const row=el('tr');row.append(el('td',check.ok?'✓ 通过':check.required?'✗ 缺失':'○ 可选'),el('td',check.name),el('td',check.detail));table.append(row);}
    $('diagnosticResults').append(table);$('diagnosticStatus').textContent=value.ready?'必需项已就绪':value.environment_ready&&!value.inputs_ready?'环境就绪；默认生产配方的数据 / 初始化仍需配置，可在数据与训练中选择已转换版本。':'仍有必需环境或资产缺失';
  }catch(error){hubError(error);$('diagnosticStatus').textContent='检查失败';}finally{for(const button of buttons)button.disabled=false;}
}
for(const button of document.querySelectorAll('[data-diagnostic]'))button.onclick=()=>runDiagnostic(button.dataset.diagnostic);

function newCollectionForm(kind){platform.collectionOpenSerial=(platform.collectionOpenSerial||0)+1;switchHub(kind);setCollectionMode(kind,'draft');showCollectionStep(kind,'inputs',{focus:true});}


// Module views preserve their original controls; switching never creates jobs or resets drafts.
function renderCachedPredictions(entries){
  const host=$('legacyResults'),signature=JSON.stringify(entries.map(entry=>[entry.id,entry.source,entry.frames,entry.camera_mode,entry.hand_mode]));
  if(signature!==platform.legacyRenderKey){platform.legacyRenderKey=signature;host.replaceChildren();for(const entry of entries){const row=el('div',undefined,'legacy-result');row.append(el('span',`${entry.source.split('/').pop()} · ${entry.frames} 帧 · ${entry.camera_mode} · ${entry.hand_mode}`));const button=el('button','导入到比较');button.dataset.legacyImport=entry.id;button.onclick=()=>importLegacyPrediction(entry).catch(hubError);row.append(button);host.append(row);}if(!entries.length)host.textContent='当前没有诊断页的内存预测；可在单算法诊断中加载已有结果，或导入保存的 NPZ。';}
  for(const button of host.querySelectorAll('[data-legacy-import]'))button.disabled=!!platform.legacyImports?.has(button.dataset.legacyImport);
}
async function importLegacyPrediction(entry){
  platform.legacyImports??=new Set();if(platform.legacyImports.has(entry.id))return null;platform.legacyImports.add(entry.id);for(const button of $('legacyResults').querySelectorAll('[data-legacy-import]'))if(button.dataset.legacyImport===entry.id)button.disabled=true;
  const navigation=platform.serial,token=state.pollToken,filter=workspaceTaskFilterSignature();
  try{const comparison=await api('api/comparison/legacy-results/import',{id:entry.id});if(navigation!==platform.serial||token!==state.pollToken||platform.pane!=='tasks'||!$('platformDialog').open||filter!==workspaceTaskFilterSignature()){notify('已有预测已导入比较记录，当前页面已保留。',false);return comparison;}closePlatformWorkspace();await openComparison(comparison.id);return comparison;}
  finally{platform.legacyImports.delete(entry.id);for(const button of $('legacyResults').querySelectorAll('[data-legacy-import]'))if(button.dataset.legacyImport===entry.id)button.disabled=false;}
}
function clearPlatformError(){const host=$('platformError');if(host)host.textContent='';const feedback=$('workspaceFeedback');if(feedback)feedback.hidden=true;}
function focusWorkspaceContent(){const target=$('platformDialog').open?$('platformTitle'):$('comparisonMain');target?.focus?.({preventScroll:true});}
function comparisonReturnPath(){
  const url=new URL(location.href),match=url.pathname.match(/^(.*)\/compare\/?$/);if(!match)return '/compare';
  url.searchParams.delete('return_to');if(state.comparison?.id&&!url.searchParams.has('id'))url.searchParams.set('id',state.comparison.id);
  if(!$('platformDialog').open&&state.comparison)url.searchParams.set('frame',String((state.frame||0)+1));return url.pathname+url.search;
}
function diagnosticContextURL(href){
  const url=new URL(href,location.href),current=new URL(location.href);if(url.origin!==current.origin||!url.pathname.endsWith('/diagnostics'))return href;
  const prefix=current.pathname.match(/^(.*)\/compare\/?$/)?.[1]||'';url.pathname=prefix+'/diagnostics';url.searchParams.set('return_to',comparisonReturnPath());return url.pathname+url.search+url.hash;
}
function updateDiagnosticReturnLinks(){for(const link of document.querySelectorAll('a[href]')){const href=link.getAttribute?.('href');if(href&&new URL(href,location.href).pathname.endsWith('/diagnostics'))link.setAttribute('href',diagnosticContextURL(href));}}
async function returnToComparison({home=false}={}){
  closePlatformWorkspace();if(!state.comparison){let remembered;try{remembered=localStorage.getItem('mint.compare.last');}catch(_){}const preferred=preferredComparison(workspaceUI.history,remembered);if(preferred)await openComparison(preferred.id);}
  if($('platformDialog').open)return;if(home)setWorkspaceTab('view');focusWorkspaceContent();updateDiagnosticReturnLinks();
}
function restoringRoute(fn){const before=platformNavigation.restoring;platformNavigation.restoring=true;try{return fn();}finally{platformNavigation.restoring=before;}}
async function restoreWorkspaceRoute(){
  const serial=platformNavigation.routeSerial=(platformNavigation.routeSerial||0)+1,href=location.href,query=new URLSearchParams(location.search),current=()=>serial===platformNavigation.routeSerial&&href===location.href;
  let id=query.get('id');if(!id&&query.get('run')){const parents=await api('api/comparison/runs/'+encodeURIComponent(query.get('run'))+'/parent');if(!current())return null;id=parents.comparisons[0];}
  if(id&&state.comparison?.id!==id){await openComparison(id,{history:false,keepPlatform:true});if(!current())return null;}
  const hub=query.get('hub');
  if(query.get('collection')&&(!hub||['batch','evaluation'].includes(hub))){await openCollection(query.get('collection'),{history:false});if(!current())return null;}
  else if(query.get('tool_job')&&(!hub||hub==='tools')){restoringRoute(()=>openPlatform('tools'));selectRenderJob(query.get('tool_job'));showToolsView('render',{route:false});await refreshRenderTool();if(!current())return null;}
  else if(query.get('workflow')&&(!hub||hub==='research')){restoringRoute(()=>openPlatform('research'));if(typeof showResearchView==='function')showResearchView(query.get('research_tab')||'train',{route:false});if(query.get('research_tab')==='curves'&&typeof selectTrainingCurve==='function')await selectTrainingCurve(query.get('workflow'),{route:false});else await inspectWorkflow(query.get('workflow'),{route:false});if(!current())return null;}
  else if(query.get('study')&&(!hub||hub==='research')){nextUI.study=query.get('study');restoringRoute(()=>openPlatform('research'));if(typeof showResearchView==='function')showResearchView('regression',{route:false});await pollNext();if(!current())return null;}
  else if(hub&&HUB_LABELS[hub])restoringRoute(()=>openPlatform(hub));
  else{restoringRoute(()=>closePlatformWorkspace({history:false}));if(query.get('run'))inspectResult(query.get('run'));if(query.get('frame')&&state.comparison?.sample){const frame=comparisonJumpFrame(query.get('frame'),state.comparison.sample.frames);if(frame!==null)seek(frame);}}
  if(!current())return null;focusWorkspaceContent();updateDiagnosticReturnLinks();return state.comparison;
}
function workspaceTaskFilterSignature(){return JSON.stringify([$('taskSearch').value,$('taskStatusFilter').value,$('taskKindFilter')?.value||'all',platform.taskOffset]);}
function taskFilterRoute(){if(platform.pane!=='tasks'||platformNavigation.restoring)return;const url=new URL(location.href);for(const [key,value,defaultValue]of [['task_query',$('taskSearch').value.trim(),''],['task_status',$('taskStatusFilter').value,'active'],['task_kind',$('taskKindFilter').value,'all']])if(value!==defaultValue)url.searchParams.set(key,value);else url.searchParams.delete(key);window.history.replaceState(null,'',url.pathname+url.search+url.hash);}
function restoreTaskFilters(){const query=new URLSearchParams(location.search);if(query.get('hub')!=='tasks')return;const explicit=['task_query','task_status','task_kind'].some(key=>query.has(key));if(platform.tasksVisited&&!platformNavigation.restoring&&!explicit)return;platform.tasksVisited=true;const allowed=(select,value)=>Array.from(select.options).some(option=>option.value===value);$('taskSearch').value=query.get('task_query')||'';const status=query.get('task_status')||'active',kind=query.get('task_kind')||'all';$('taskStatusFilter').value=allowed($('taskStatusFilter'),status)?status:'active';$('taskKindFilter').value=allowed($('taskKindFilter'),kind)?kind:'all';platform.taskOffset=0;}
function taskKindLabel(kind){return ({inference:'算法推理',batch:'批量比较',evaluation:'数据集评测',workflow:'数据生产与训练',study:'回归与性能',render:'展示视频',export:'比较视频导出',mint_workspace:'单算法诊断',mint_batch:'诊断批量推理',mint_benchmark:'专项基准',code_diff:'代码差异',dataset_analysis:'数据分析'})[kind]||kind;}
async function openWorkspaceTask(task){
  if(['batch','evaluation'].includes(task.kind))return openCollection(task.id);
  if(task.kind==='render')return inspectRenderJob(task.id);
  if(task.kind==='workflow'){switchHub('research');return inspectWorkflow(task.id,{navigate:true});}
  if(task.kind==='study'){nextUI.study=task.id;await openPlatform('research');showResearchView('regression');return;}
  if(task.kind==='export'&&task.status==='completed'&&task.url){location.href=task.url;return;}
  if(task.comparison_id){closePlatformWorkspace();const serial=platform.serial;await openComparison(task.comparison_id);if(serial===platform.serial&&state.comparison?.id===task.comparison_id&&!$('platformDialog').open)inspectResult(task.id);return;}
  if(task.url)location.href=diagnosticContextURL(task.url);
}
async function workspaceTaskAction(task,action){
  platform.taskActions??=new Map();const key=task.kind+':'+task.id;if(platform.taskActions.has(key))return null;platform.taskActions.set(key,action);platform.taskSerial++;
  const update=()=>{for(const button of $('workspaceTasks').querySelectorAll('[data-task-action]'))if(button.dataset.taskAction===key)button.disabled=platform.taskActions.has(key);};update();
  try{let value;if(task.kind==='inference')value=await api(`api/comparison/runs/${task.id}/${action}`,{});else if(task.kind==='render')value=action==='resume'?await resumeRenderTool(task.id):await api(`api/comparison/tools/${task.id}/cancel`,{});else{const endpoint=({export:'exports',workflow:'workflows',study:'studies',batch:'collections',evaluation:'collections'})[task.kind];if(!endpoint)throw new Error('此任务需要回到原工具继续操作');value=await api(`api/comparison/${endpoint}/${task.id}/${action}`,{});}if(platform.pane==='tasks'&&$('platformDialog').open)await refreshWorkspaceTasks();return value;}
  finally{platform.taskActions.delete(key);update();}
}
function platformPrimaryAction(tab){
  if(tab==='research'){const view=$('hub-research')?.dataset.researchTab||'train';if(view==='curves')return null;const action=({models:['登记检查点','modelName'],regression:['配置回归','suiteProtocol'],protocol:['配置固定评测','offlineProtocol'],production:['配置伪标签生产','productionComparisonPicker']})[view];if(action)return {label:action[0],kind:'research-view',view,target:action[1]};return {label:'新建训练',kind:'train'};}
  return ({data:{label:'选择输入',kind:'data'},catalog:{label:'准备数据',kind:'data'},batch:{label:'新建批次',kind:'batch'},evaluation:{label:'新建评测',kind:'evaluation'},quality:{label:'选择比较',kind:'quality'}})[tab]||null;
}
function syncPlatformPrimaryAction(tab){const button=$('newComparison'),action=platformPrimaryAction(tab);button.hidden=!!tab&&!action;const label=tab?action?.label:'新建比较';if(label){button.replaceChildren(icon('plus'),document.createTextNode(label));button.setAttribute('aria-label',label);button.title=tab?'配置当前模块，不会立即提交计算任务':'配置算法与输入';}}
function interceptPlatformPrimaryAction(event){if(!$('platformDialog').open)return false;event.preventDefault();event.stopImmediatePropagation();activatePlatformPrimaryAction();return true;}
function activatePlatformPrimaryAction(){
  const action=platformPrimaryAction(platform.pane),kind=action?.kind;
  if(kind==='train'){showResearchView('train');$('workflowKind').value='train';updateWorkflowFields();revealPlatformTarget($('workflowKind'));$('workflowName').focus({preventScroll:true});}
  else if(kind==='research-view'){showResearchView(action.view);if(action.view==='models')$('modelRegistrationDetails').open=true;const target=$(action.target);if(target){revealPlatformTarget(target);target.focus({preventScroll:true});}}
  else if(kind==='data'){openPlatform('data');requestAnimationFrame(()=>{$('dataSourceTabs')?.focus();});}
  else if(kind==='evaluation'||kind==='batch'){newCollectionForm(kind);revealPlatformTarget($(kind+'Composer'));}
  else if(kind==='quality'){revealPlatformTarget($('qualityComparison'));$('qualityComparison').focus({preventScroll:true});}
}
function findHubSection(tab,title){
  const ids={'开源数据集原生接入':'nativeDataPanel','固定集回归与性能对照':'suiteProtocol','训练曲线与完整检查点':'research-pane-curves','训练曲线与检查点':'research-pane-curves','原生后端伪标签生产':'nativeProductionSection','模型版本与发布门槛':'modelName','数据生产与训练任务':'workflowKind','冻结协议与离线评测':'offlineProtocol'};
  const node=ids[title]?$(ids[title]):null;return node?.closest('section')||[...$('hub-'+tab).querySelectorAll('section')].find(row=>row.querySelector('h3')?.textContent.includes(title));
}
function revealPlatformTarget(target,{scroll=true}={}){
  if(!target)return;
  if(target.closest('#hub-research')&&typeof revealResearchTarget==='function')revealResearchTarget(target);
  if(target.closest('#hub-data')&&typeof showDataSource==='function'){const region=target.closest('[data-data-source]');if(region)showDataSource(region.dataset.dataSource);}
  if(scroll)target.scrollIntoView({block:'start',behavior:'auto'});
}
function platformContextText(comparison,pane,selectionCount=0){
  if(['batch','evaluation'].includes(pane))return `待提交输入清单：${selectionCount} 项；与当前打开的比较分别管理。`;
  if(!['quality','tools','research'].includes(pane))return '';
  if(!comparison)return pane==='research'?'尚未打开比较；训练使用所选数据版本，回归和生产按各自来源选择。':'尚未打开比较；需要已有结果的操作请先选择一组比较。';
  const request=comparison.sample_request||{},path=request.source?.path||'',sample=comparison.sample;
  const frames=sample?`${sample.frames} 帧 · 源索引 ${sample.source_frame_indices?.[0]??request.start_frame??0}…${sample.source_frame_indices?.at(-1)??'—'}`:`源索引 ${request.start_frame??0} 起`;
  return `当前比较：${comparison.title||path.split('/').pop()||comparison.id} · ${frames} · ${(comparison.runs||[]).filter(r=>r.result).length} 个已有结果。${pane==='research'?'训练数据、回归协议和生产来源仍以各自选择为准。':''}`;
}
function refreshPlatformContext(){
  let host=$('platformCurrentContext');if(!host){const parent=$('moduleContext');if(!parent)return;host=el('div',undefined,'platform-current-context');host.id='platformCurrentContext';host.setAttribute('role','status');parent.after(host);}
  const frozen=['batch','evaluation'].includes(platform.pane)&&collectionDraft(platform.pane).mode==='results'&&platform.collectionValue?.kind===platform.pane?platform.collectionValue:null;
  const text=frozen?`已提交批次：${frozen.title} · ${frozen.total} 个冻结输入；待提交清单 ${platform.selection.size} 项另行管理。`:platformContextText(state.comparison,platform.pane,platform.selection.size);host.hidden=!$('platformDialog').open||!text||platform.pane==='quality'||platform.pane==='tools'||(platform.pane==='research'&&!(typeof nextUI!=='undefined'&&['regression','protocol'].includes(nextUI.researchView)))||(platform.pane==='evaluation'&&!frozen);host.title=state.comparison?.sample_request?.source?.path||'';host.replaceChildren();if(!text)return;host.append(el('span',text));
  if(['quality','tools','research'].includes(platform.pane)){const change=el('button',state.comparison?'更换比较':'选择比较','text-button');change.onclick=()=>{closePlatformWorkspace();if(window.innerWidth<=1100){workspaceUI.mobileSidebar=true;applyLayout();}$('historySearch').focus();};host.append(change);}
}
function renderTaskResourceSummary(value){
  const host=$('resourceSummary'),scheduler=value.resource||{},resources=value.resources||{},gpus=resources.gpus||[];
  let summary=$('taskResourcePrimary'),details=$('taskResourceDetails'),body=$('taskResourceBody');
  if(!summary){summary=el('p',undefined,'task-resource-primary');summary.id='taskResourcePrimary';details=el('details');details.id='taskResourceDetails';details.append(el('summary','主机与 GPU 资源详情'));body=el('p',undefined,'hint');body.id='taskResourceBody';details.append(body);host.replaceChildren(summary,details);}
  summary.textContent=`MINT 当前使用 ${scheduler.running_devices?.length||0} 张 GPU · ${scheduler.queued||0} 个推理窗口排队 · 当前筛选 ${value.total} 条任务`;
  body.textContent=`物理 GPU ${gpus.length} 张 · 显存空余 ${(gpus.reduce((sum,gpu)=>sum+(gpu.free_mib||0),0)/1024).toFixed(1)} GiB · CPU ${resources.cpu_count||'—'} 核 / 1 分钟负载 ${Number(resources.load_average?.[0]||0).toFixed(1)} · 主机可用内存 ${((resources.memory?.MemAvailable||0)/2**30).toFixed(1)} GiB · 磁盘余量 ${((resources.disk?.free_bytes||0)/2**30).toFixed(1)} GiB。资源数值包含其他用户任务。`;
}


// HTTP redirects cannot see fragments from old diagnostic bookmarks.
function legacyDiagnosticHashURL(pathname,search,hash){
  const match=String(pathname||'').match(/^((?:\/[^/]+)*)\/compare\/?$/);if(!match||!['#diagnostic-input','#diagnostic-results','#diagnostic-tools'].includes(hash))return null;
  const query=new URLSearchParams(search||'');if(['id','run','hub','collection','tool_job','workflow','study'].some(key=>query.has(key)))return null;
  return match[1]+'/diagnostics'+(search||'')+hash;
}
