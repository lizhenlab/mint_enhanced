'use strict';

// Navigation only: this guide never submits inference, training or publishing.
const WORKFLOW_GUIDE_ROUTES={
  data:new Set(['']),catalog:new Set(['']),compare:new Set(['']),
  evaluation:new Set(['']),quality:new Set(['']),tasks:new Set(['']),tools:new Set(['']),
  research:new Set(['train','curves','regression','protocol','production','models']),
};
async function openWorkflowGuideRoute(route,view=''){
  if(!WORKFLOW_GUIDE_ROUTES[route]?.has(view))throw new Error('未知的工作流程入口');
  $('workflowGuideDialog').close();
  if(route==='compare')return returnToComparison();
  await openPlatform(route);
  if(route==='research')showResearchView(view);
}
function initializeWorkflowGuide(){
  const dialog=$('workflowGuideDialog'),button=$('openWorkflowGuide');
  button.onclick=()=>{if(!dialog.open)dialog.showModal();};
  $('closeWorkflowGuide').onclick=()=>dialog.close();
  dialog.addEventListener('click',event=>{const target=event.target.closest('[data-guide-route]');if(target)openWorkflowGuideRoute(target.dataset.guideRoute,target.dataset.guideView||'').catch(hubError);});
}
initializeWorkflowGuide();
