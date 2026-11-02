'use strict';

// An example is a draft plus an explicit native-reference binding. Selecting it
// never submits work; the ordinary Start button remains the only submit action.
const defaultExampleUI={serial:0,loading:false,binding:null,applied:false,message:'',level:'',initialized:false,buttonLabel:''};
const DEFAULT_EXAMPLE_ALGORITHMS=['mint','hawor_full','hawor_camera','ace_kfree','ace_kgiven'];

function defaultExampleCanonical(value){
  if(Array.isArray(value))return value.map(defaultExampleCanonical);
  if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(key=>[key,defaultExampleCanonical(value[key])]));
  return value;
}
function defaultExampleProtocol(payload){
  return JSON.stringify(defaultExampleCanonical({source:payload.source,view:payload.view,target_fps:payload.target_fps,start_frame:payload.start_frame,max_frames:payload.max_frames,camera:payload.camera??null,dataset:payload.dataset??null}));
}
function defaultExampleDraftFingerprint(){
  const fields=['source','view','targetFps','startFrame','maxFrames','camera','examples','executionPriority','executionCache','datasetRoot','datasetCamera','datasetEpisode','datasetAttachGT','datasetCalibration'];
  const values=fields.map(id=>{const node=$(id);return [id,node?.value??null,node?.type==='checkbox'?node.checked:null];});
  const algorithms=[...$('algorithmOptions').querySelectorAll('.algorithm')].map(box=>[box.dataset.algorithm,...[...box.querySelectorAll('input,select,textarea')].map(node=>[node.dataset.option||node.dataset.selection||node.type,node.value,node.type==='checkbox'?node.checked:null,!!node.disabled])]);
  return JSON.stringify([values,algorithms,typeof datasetForm==='undefined'?null:[datasetForm.active,datasetForm.serial,datasetForm.loading]]);
}
function defaultExampleNavigation(){return typeof comparisonNavigation==='function'?JSON.stringify(comparisonNavigation()):JSON.stringify([typeof workspaceUI==='undefined'?null:workspaceUI.taskDialogSession,typeof state==='undefined'?null:state.pollToken]);}
function defaultExampleFeedback(message,level='ready'){
  defaultExampleUI.message=message;defaultExampleUI.level=level;const node=$('defaultExampleSummary');if(!node)return;
  const key=JSON.stringify([message,level]);if(node.dataset.feedbackKey!==key){node.textContent=message;node.dataset.feedbackKey=key;}node.dataset.state=level;node.hidden=!message;
}
function referenceInputModel(payload=null){
  let current;try{current=payload||inputBody();}catch(_){current=null;}
  const binding=defaultExampleUI.binding;
  if(binding&&current&&defaultExampleProtocol(current)===binding.protocol)return {state:'bound',title:'官方 GT 已绑定',badge:'自动附加',description:`ARCTIC Validation · ${binding.example.frames} 帧官方双手关节、网格与相机参考。用于展示和评测，无需勾选，不参与算法推理。`};
  if(defaultExampleUI.loading)return {state:'pending',title:'GT 参考标注',badge:'核对中',description:'正在核对默认示例与官方 GT，完成后将显示绑定状态。'};
  if(typeof datasetForm!=='undefined'&&datasetForm.active){
    if(!current||datasetForm.loading)return {state:'pending',title:'数据集 GT',badge:'待核对',description:'请先读取数据集并选择有效 Episode，再预检参考标注。'};
    if(current.dataset?.attach_gt)return {state:'dataset',title:'数据集 GT 将自动附加',badge:'提交前校验',description:'已选择当前 Episode 的参考标注；提交前会校验可用性、帧范围和来源。'};
  }
  return {state:'unbound',title:'GT 参考标注',badge:'未绑定',description:'使用带 GT 的默认示例、LeRobot 标注，或在下方导入参考标注。普通视频不会自动生成 GT。'};
}
function syncReferenceInputState(payload=null){
  const box=[...$('algorithmOptions').querySelectorAll('.algorithm')].find(node=>node.dataset.algorithm==='reference');if(!box)return;
  // Reference is data, not a prediction backend. It must never enter selections.
  const check=box.querySelector('input[type=checkbox]');if(check){check.checked=false;check.disabled=true;check.hidden=true;}
  const title=box.querySelector('[data-reference-title]'),badge=box.querySelector('[data-reference-badge]'),description=box.querySelector('[data-reference-description]');if(!title||!badge||!description)return;
  const model=referenceInputModel(payload),key=JSON.stringify(model);if(box.dataset.referenceModel===key)return;
  box.dataset.referenceModel=key;box.dataset.referenceState=model.state;title.textContent=model.title;badge.textContent=model.badge;description.textContent=model.description;
}
function defaultExampleReadySummary(binding,payload){
  let selections;try{selections=typeof selectedAlgorithms==='function'?selectedAlgorithms():binding.example.selections;}catch(_){selections=binding.example.selections;}
  const caps=selections.map(row=>Number(row.options?.max_gpus)).filter(Number.isFinite),maximum=caps.length?Math.max(...caps):8;
  const shared=selections.every(row=>(row.options?.resource_policy||'shared')==='shared');
  const cache=payload.execution?.cache||'exact',reuse=cache==='fresh'?'独立重跑，将重新计算。':cache==='compatible'?'数值协议兼容时复用已有结果。':'相同运行复用已有结果；修改模型或参数后按新配置计算。';
  return `完整 ${binding.example.frames} 帧 / ${(binding.example.frames/binding.example.fps).toFixed(2)} 秒 · ${selections.length} 个算法配置 + 官方双手 GT · 已知内参条件 · 上限 ${maximum} GPU${shared?'共享调度':'（资源策略见下方）'}。\n${reuse}点击“开始比较”运行。`;
}
function syncDefaultExample(payload=null){
  syncReferenceInputState(payload);
  if(defaultExampleUI.loading)return;
  const binding=defaultExampleUI.binding;
  if(binding){
    let current;try{current=payload||inputBody();}catch(_){current=null;}
    if(!current||defaultExampleProtocol(current)!==binding.protocol){
      defaultExampleUI.binding=null;defaultExampleUI.applied=false;
      defaultExampleFeedback('示例输入或相机条件已修改：官方 GT 不再自动附加。重新点击“使用默认示例”可恢复完整视频与 GT；当前草稿仍可按普通视频比较。','changed');
      return;
    }
    defaultExampleFeedback(defaultExampleReadySummary(binding,current),'ready');
    if($('defaultExampleSummary'))$('defaultExampleSummary').title=binding.example.name+'\n相机条件：HaWoR 和 ACE K-given 使用数据集内参；MINT 与 ACE K-free 仍使用自身预测。\n视频：'+binding.example.source;
    return;
  }
  if(defaultExampleUI.message)defaultExampleFeedback(defaultExampleUI.message,defaultExampleUI.level);
}
function resetDefaultExample(reason=''){
  defaultExampleUI.serial++;defaultExampleUI.loading=false;const hadExample=!!defaultExampleUI.binding||defaultExampleUI.applied;defaultExampleUI.binding=null;defaultExampleUI.applied=false;
  const button=$('useDefaultExample');if(button){button.disabled=false;button.textContent=defaultExampleUI.buttonLabel||'使用默认示例';button.removeAttribute('aria-busy');}
  if(hadExample)defaultExampleFeedback(reason==='closed'?'示例输入仍保留在草稿中；请重新点击“使用默认示例”以确认并附加官方 GT。':'默认示例已解除；重新点击“使用默认示例”可恢复完整视频与官方 GT。','changed');
  else defaultExampleFeedback('','');
  syncReferenceInputState();
}
function defaultExampleValidateField(definition,value){
  if(value===null||value==='')return '';
  if(!['string','number','boolean'].includes(typeof value))throw new Error('默认示例含有无法识别的算法参数');
  const text=String(value);
  if(definition?.choices&&!definition.choices.some(choice=>String(choice[0])===text))throw new Error('默认示例参数与当前算法选项不兼容：'+definition.name);
  if(definition&&!definition.choices){const number=Number(value);if(!Number.isFinite(number)||definition.minimum!=null&&number<definition.minimum||definition.maximum!=null&&number>definition.maximum)throw new Error('默认示例参数超出当前算法范围：'+definition.name);}
  return text;
}
function defaultExampleFormPlan(example){
  if(!example?.available)throw new Error(example?.reason||'默认示例尚未准备好，请在数据接入中准备完整 ARCTIC 样本。');
  if(typeof example.dataset_id!=='string'||!example.dataset_id||typeof example.source!=='string'||!example.source.startsWith('/')||typeof example.name!=='string'||!example.name.trim()||example.frames!==677||!Number.isFinite(example.fps)||example.fps<=0||example.view!=='full'||example.target_fps!==0||example.start_frame!==0||example.max_frames!==null||example.calibration!==true)throw new Error('默认示例不是已验证的完整677帧版本，请检查服务端示例配置。');
  const camera=example.camera;if(!camera||!Array.isArray(camera.K)||camera.K.length!==3||camera.K.some(row=>!Array.isArray(row)||row.length!==3||row.some(n=>typeof n!=='number'||!Number.isFinite(n)))||!Number.isInteger(camera.width)||camera.width<=0||!Number.isInteger(camera.height)||camera.height<=0)throw new Error('默认示例缺少有效的相机标定。');
  if(!Array.isArray(example.selections)||example.selections.length!==DEFAULT_EXAMPLE_ALGORITHMS.length||new Set(example.selections.map(row=>row.algorithm)).size!==DEFAULT_EXAMPLE_ALGORITHMS.length||DEFAULT_EXAMPLE_ALGORITHMS.some(id=>!example.selections.some(row=>row.algorithm===id)))throw new Error('默认示例需要完整的五种算法配置。');
  const selected=new Map(example.selections.map(row=>[row.algorithm,row])),boxes=[...$('algorithmOptions').querySelectorAll('.algorithm')],updates=[];
  for(const algorithm of DEFAULT_EXAMPLE_ALGORITHMS){const spec=state.specs.find(row=>row.id===algorithm),box=boxes.find(row=>row.dataset.algorithm===algorithm),check=box?.querySelector('input[type=checkbox]');if(!spec?.ready||spec.import_only||!check||check.disabled)throw new Error('默认示例所需算法未就绪：'+(spec?.label||algorithm)+'。请先检查依赖，原草稿未改动。');}
  for(const box of boxes){
    const spec=state.specs.find(row=>row.id===box.dataset.algorithm),selection=selected.get(box.dataset.algorithm),check=box.querySelector('input[type=checkbox]'),fields=[...box.querySelectorAll('[data-option]')];
    const definitions=new Map((spec?.parameters?.fields||[]).map(field=>[field.name,field]));
    const fieldValues=fields.map(field=>[field,defaultExampleValidateField(definitions.get(field.dataset.option),definitions.get(field.dataset.option)?.default??'')]);
    if(selection){
      if(!selection.options||typeof selection.options!=='object'||Array.isArray(selection.options))throw new Error('默认示例算法参数缺失。');
      for(const [name,value]of Object.entries(selection.options)){
        const field=fields.find(node=>node.dataset.option===name);
        // HaWoR's only mode is native; the form intentionally has no mode select.
        if(!field){if(box.dataset.algorithm.startsWith('hawor')&&name==='mode'&&value==='native')continue;throw new Error('默认示例参数与当前表单不兼容：'+box.dataset.algorithm+'.'+name);}
        const target=fieldValues.find(row=>row[0]===field);target[1]=defaultExampleValidateField(definitions.get(name),value);
      }
    }
    updates.push({check,checked:!!selection,fieldValues,checkpoint:box.querySelector('[data-selection=checkpoint]'),configuration:box.querySelector('[data-selection=config]')});
  }
  return {example:structuredClone(example),updates};
}
async function loadDefaultExample(trigger=null){
  if(defaultExampleUI.loading||!$('taskDialog').open)return null;
  if(state.submitting){defaultExampleFeedback('当前比较正在提交，请完成后再选择默认示例。','changed');return null;}
  const serial=++defaultExampleUI.serial,navigation=defaultExampleNavigation(),fingerprint=defaultExampleDraftFingerprint(),button=$('useDefaultExample');
  defaultExampleUI.loading=true;if(button){button.disabled=true;button.textContent='正在选择示例…';button.setAttribute('aria-busy','true');}
  syncReferenceInputState();
  defaultExampleFeedback('正在读取完整视频、官方 GT 和默认算法参数；不会启动计算。','pending');
  const sameSession=()=>serial===defaultExampleUI.serial&&$('taskDialog').open&&navigation===defaultExampleNavigation();
  const current=()=>sameSession()&&!state.submitting&&fingerprint===defaultExampleDraftFingerprint();
  try{
    const response=await api('api/comparison/default-example');
    if(!current()){if(sameSession())defaultExampleFeedback('读取示例期间设置已变化，已保留当前草稿；请再次点击“使用默认示例”。','changed');return null;}
    const plan=defaultExampleFormPlan(response),example=plan.example;
    // No field is changed before the entire response and all five algorithms pass.
    setDatasetMode(false);$('source').value=example.source;$('view').value='full';$('targetFps').value='0';$('startFrame').value='0';$('maxFrames').value='';$('camera').value=JSON.stringify(example.camera,null,2);
    $('examples').value=[...$('examples').options].some(option=>option.value===example.source)?example.source:'';
    for(const update of plan.updates){if(update.check)update.check.checked=update.checked;for(const [field,value]of update.fieldValues)field.value=value;if(update.checkpoint)update.checkpoint.value='';if(update.configuration)update.configuration.value='';}
    if($('executionPriority'))$('executionPriority').value='interactive';if($('executionCache'))$('executionCache').value='exact';
    defaultExampleUI.binding={datasetId:example.dataset_id,example,protocol:defaultExampleProtocol(inputBody())};defaultExampleUI.applied=true;defaultExampleUI.loading=false;
    if(typeof invalidateComparisonPreflight==='function')invalidateComparisonPreflight();if(typeof updateTaskProtocolSummary==='function')updateTaskProtocolSummary();if(typeof syncSourceSelection==='function')syncSourceSelection();
    syncDefaultExample();if(typeof prepareSourceBrowser==='function')prepareSourceBrowser();return example;
  }catch(error){if(sameSession())defaultExampleFeedback('默认示例选择失败：'+error.message+' 原草稿未改动。','error');return null;}
  finally{if(serial===defaultExampleUI.serial){defaultExampleUI.loading=false;if(button){button.disabled=false;button.textContent=defaultExampleUI.buttonLabel||'使用默认示例';button.removeAttribute('aria-busy');}syncReferenceInputState();}}
}
function defaultExampleSubmission(payload){
  syncDefaultExample(payload);const binding=defaultExampleUI.binding;if(!binding)return null;
  return {path:'api/comparison/native/'+encodeURIComponent(binding.datasetId)+'/compare',body:{selections:structuredClone(payload.selections),calibration:true,title:binding.example.name+' · 默认示例比较',execution:structuredClone(payload.execution||{priority:'interactive',cache:'exact'})}};
}
function initializeDefaultExample(){
  const button=$('useDefaultExample'),dialog=$('taskDialog');if(!button||!dialog||defaultExampleUI.initialized)return;
  defaultExampleUI.initialized=true;defaultExampleUI.buttonLabel=button.textContent||'使用默认示例';button.addEventListener('click',event=>loadDefaultExample(event.currentTarget));
  for(const type of ['input','change'])dialog.addEventListener(type,()=>syncDefaultExample());
  dialog.addEventListener('close',()=>resetDefaultExample('closed'));
  syncReferenceInputState();
}
