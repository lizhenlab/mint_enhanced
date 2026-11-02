'use strict';

function wristSummaryRows(payload,index,space='camera'){
  const world=space==='world',hands=(world?payload.joints_world_m:payload.joints_camera_m)?.[index];
  return [0,1].map(slot=>{
    const label=slot?'右腕 R':'左腕 L',geometryValid=payload.available?.[index]!==false&&payload.geometry_valid?.[index]?.[slot]===true,worldValid=!world||payload.pose_valid?.[index]===true,valid=geometryValid&&worldValid,
      point=hands?.[slot]?.[0],finite=Array.isArray(point)&&point.length===3&&point.every(Number.isFinite),
      position=valid&&finite?point.map((v,i)=>`${'XYZ'[i]} ${(v*100).toFixed(1)}`).join(' / ')+' cm':world?'世界坐标未提供':'几何缺失';
    const states=[];if(geometryValid)states.push(world&&!worldValid?'相机几何有效':'有效');if(world&&!worldValid)states.push('世界位姿缺失');
    if(payload.presence?.[index]?.[slot]===false)states.push('低存在性');
    if(payload.filled?.[index]?.[slot]===true)states.push('补全');else if(payload.tracked?.[index]?.[slot]===true)states.push('跟踪');
    const confidence=payload.confidence?.[index]?.[slot],title=[`${label} · ${world?'世界系':'相机系'} · 第0关节`,world?'世界坐标以当前公共原点表示':'相机轴：X右、Y下、Z前；Z是轴向深度',Number.isFinite(confidence)?`原生存在性分数 ${(confidence*100).toFixed(1)}%；不同算法语义不同`:null].filter(Boolean).join('；');
    return {slot,label,position,states,title,text:[label,position,...states].join(' · ')};
  });
}
function renderWristSummary(card,data){
  const rows=wristSummaryRows(data.payload,data.index,$('space').value),key=JSON.stringify(rows);
  if(card.numbers.dataset.parameterSummary===key)return;
  card.numbers.dataset.parameterSummary=key;
  card.numbers.replaceChildren(...rows.map(row=>{const node=el('span',row.text);node.title=row.title;if(typeof handColor==='function')node.style.color=handColor(row.slot);return node;}));
}
async function focusParameterResult(runId){
  if(!state.comparison||!state.cards.has(runId))return;const id=state.comparison.id,token=state.pollToken;
  resultView.expanded=runId;applyResultVisibility();render(true);await ensureFrame(state.frame);
  if(token===state.pollToken&&state.comparison?.id===id)render(true);
}
function parameterPreviousFrame(card,slot){
  if(!state.comparison?.sample||state.frame<1||![0,1].includes(slot))return null;
  const frame=state.frame-1,start=Math.floor(frame/FRAME_READ_COUNT)*FRAME_READ_COUNT,
    payload=state.cache.get(resultFrameKey(card.run,start,state.comparison.common_world_origin_source_frame)),index=frame-start;
  if(!payload||payload.available?.[index]===false||payload.sample_positions?.[index]!==frame)return null;
  return {points:payload.joints_camera_m?.[index]?.[slot],valid:payload.geometry_valid?.[index]?.[slot]===true,
    sampleIndex:frame,timestampS:state.comparison.sample.timestamps_s[frame],space:'camera'};
}
async function ensureParameterPreviousFrame(frame){
  if(typeof parameterPanelReadCards!=='function'||!$('parameterPanel')||$('parameterPanel').hidden||frame<1||frame%FRAME_READ_COUNT!==0)return;
  const comparison=state.comparison;if(!comparison?.sample||frame!==state.frame||frame>=comparison.sample.frames)return;const token=state.pollToken,origin=comparison.common_world_origin_source_frame??null,start=frame-FRAME_READ_COUNT;
  await Promise.all(parameterPanelReadCards().filter(card=>card.run.result).map(async card=>{
    await loadFrameBlock(card,start);
  }));
}
function drawParameterJoint(card,data,ctx,project,{world=false,ratio=1}={}){
  const selected=typeof parameterPanelJointSelection==='function'?parameterPanelJointSelection():null;
  if(!selected||selected.runId!==card.run.id||!data||!visible(data.payload,data.index,selected.slot)||(world&&data.payload.pose_valid?.[data.index]!==true))return;
  const point=(world?data.payload.joints_world_m:data.payload.joints_camera_m)?.[data.index]?.[selected.slot]?.[selected.joint];
  if(!point?.every(Number.isFinite))return;const xy=project(point);if(!xy?.every(Number.isFinite))return;
  ctx.save();ctx.strokeStyle='#ffffff';ctx.fillStyle=handColor(selected.slot);ctx.lineWidth=2*ratio;
  ctx.beginPath();ctx.arc(...xy,6*ratio,0,Math.PI*2);ctx.fill();ctx.stroke();
  ctx.font=`${11*ratio}px sans-serif`;ctx.fillStyle='#ffffff';ctx.fillText(`${selected.slot?'R':'L'} ${selected.joint}`,xy[0]+9*ratio,xy[1]-7*ratio);ctx.restore();
}
if(typeof module!=='undefined')module.exports={wristSummaryRows};
