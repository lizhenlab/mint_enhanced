'use strict';

const meshView={cache:new Map(),loading:new Map(),topology:null,topologyRequest:null};
const HAND_COLORS=['#6ad5f0','#ffdf80'];
function handColor(slot){return HAND_COLORS[slot];}
function geometryMode(){return $('geometryStyle')?.value||'skeleton';}
function meshKey(run,frame){const start=Math.floor(frame/4)*4;return `${run.id}:${start}:${state.comparison.common_world_origin_source_frame}`;}
function resetGeometryCache(){meshView.cache.clear();meshView.loading.clear();}
async function ensureMeshFrame(frame){
  if(geometryMode()==='skeleton'||!state.comparison?.sample)return;
  const token=state.pollToken;
  if(!meshView.topology){
    if(!meshView.topologyRequest)meshView.topologyRequest=api('api/comparison/topology').then(value=>meshView.topology=value).catch(error=>{meshView.topologyRequest=null;throw error;});
    try{await meshView.topologyRequest;}catch(error){notify('网格拓扑不可用：'+error.message);return;}
  }
  await Promise.all(visibleResultCards().filter(card=>card.run.result?.arrays.vertices_camera_m).map(async card=>{
    const key=meshKey(card.run,frame);if(meshView.cache.has(key))return;
    if(!meshView.loading.has(key)){
      const query=new URLSearchParams({start:Math.floor(frame/4)*4,count:4,vertices:1});
      const origin=state.comparison.common_world_origin_source_frame;if(origin!==null&&card.run.result.arrays.T_world_camera)query.set('origin',origin);
      const promise=api(`api/comparison/runs/${card.run.id}/frames?${query}`).then(value=>{
        if(token!==state.pollToken)return;meshView.cache.set(key,value);if(meshView.cache.size>24)meshView.cache.delete(meshView.cache.keys().next().value);
      }).finally(()=>{if(meshView.loading.get(key)===promise)meshView.loading.delete(key);});meshView.loading.set(key,promise);
    }
    try{await meshView.loading.get(key);}catch(error){if(token===state.pollToken)notify(error.message);}
  }));
}
function meshFor(card){
  const payload=meshView.cache.get(meshKey(card.run,state.frame));
  return payload?{payload,index:state.frame-Math.floor(state.frame/4)*4}:null;
}
function rgbColor(hex){return [1,3,5].map(index=>parseInt(hex.slice(index,index+2),16));}
function paintMesh(ctx,vertices,project,depth,slot,alpha){
  const faces=meshView.topology?.hands[slot?'right':'left'];if(!vertices||!faces)return false;
  const projected=vertices.map(point=>point?.every(Number.isFinite)?project(point):null),triangles=[];
  for(const face of faces){const points=face.map(index=>projected[index]);if(points.some(point=>!point))continue;triangles.push({face,points,z:face.reduce((sum,index)=>sum+depth(vertices[index]),0)});}
  triangles.sort((a,b)=>b.z-a.z);const base=rgbColor(handColor(slot));ctx.save();
  for(const {face,points}of triangles){
    const [a,b,c]=face.map(index=>vertices[index]),u=b.map((v,i)=>v-a[i]),v=c.map((n,i)=>n-a[i]);
    const normal=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]],length=Math.hypot(...normal)||1;
    const light=.45+.5*Math.abs((normal[0]*.2+normal[1]*-.4+normal[2]*.8)/length);
    ctx.fillStyle=`rgba(${base.map(value=>Math.round(value*light)).join(',')},${alpha})`;ctx.beginPath();ctx.moveTo(...points[0]);ctx.lineTo(...points[1]);ctx.lineTo(...points[2]);ctx.closePath();ctx.fill();
  }
  ctx.restore();return triangles.length>0;
}
function drawMesh2D(card,data){
  if(geometryMode()==='skeleton')return false;
  const mesh=meshFor(card);if(!mesh?.payload.vertices_camera_m)return false;
  const sample=state.comparison.sample,grid=sample.proxy_content_grid||[sample.width,sample.height],ctx=card.overlay.getContext('2d');let drawn=false;
  for(let slot=0;slot<2;slot++)if(visible(data.payload,data.index,slot)){
    const vertices=mesh.payload.vertices_camera_m[mesh.index][slot];
    drawn=paintMesh(ctx,vertices,point=>{const p=project2D(point,data.payload.K[data.index]);return p?[p[0]*grid[0]/sample.width,p[1]*grid[1]/sample.height]:null;},point=>point[2],slot,.5)||drawn;
  }
  return drawn;
}
function drawMesh3D(card,data,project){
  if(geometryMode()==='skeleton')return false;
  const mesh=meshFor(card),world=$('space').value==='world';if(!mesh)return false;
  const vertices=(world?mesh.payload.vertices_world_m:mesh.payload.vertices_camera_m)?.[mesh.index];if(!vertices)return false;
  const {yaw,pitch}=state.orbit,depth=point=>Math.sin(pitch)*point[1]+Math.cos(pitch)*(-Math.sin(yaw)*point[0]+Math.cos(yaw)*point[2]);let drawn=false;
  for(let slot=0;slot<2;slot++)if(visible(data.payload,data.index,slot))drawn=paintMesh(card.canvas.getContext('2d'),vertices[slot],project,depth,slot,1)||drawn;
  return drawn;
}
function baselineCard(){return state.cards.get(resultView.reference);}
function frameReadCards(){
  const cards=visibleResultCards(),base=baselineCard();if(($('showBaseline')?.checked||$('frameInspector')?.open)&&base&&!cards.includes(base))cards.push(base);return cards;
}
function drawBaseline(card,ctx,project,world=false){
  if(!$('showBaseline').checked||card===baselineCard())return;
  const data=dataFor(baselineCard());if(!data)return;
  const joints=(world?data.payload.joints_world_m:data.payload.joints_camera_m)?.[data.index];if(!joints)return;
  ctx.save();ctx.setLineDash([5,4]);ctx.lineWidth=1.4;ctx.strokeStyle='#edf5efaa';
  for(let slot=0;slot<2;slot++)if(visible(data.payload,data.index,slot))for(const [a,b]of data.payload.connections){if(!joints[slot][a]?.every(Number.isFinite)||!joints[slot][b]?.every(Number.isFinite))continue;const first=project(joints[slot][a],data.payload.K[data.index]),second=project(joints[slot][b],data.payload.K[data.index]);if(!first||!second)continue;ctx.beginPath();ctx.moveTo(...first);ctx.lineTo(...second);ctx.stroke();}
  ctx.restore();
}
function updateFrameInspector(){
  if(!$('frameInspector')?.open||!state.comparison?.sample)return;
  const card=state.cards.get($('inspectRun').value),data=card&&dataFor(card),base=baselineCard(),reference=base&&dataFor(base),host=$('jointValues');host.replaceChildren();
  if(!data){host.textContent='正在加载当前帧…';return;}
  const sample=state.comparison.sample;$('inspectFrameLabel').textContent=`样本帧 ${state.frame+1} · 源帧 ${sample.source_frame_indices[state.frame]} · ${fmt(sample.timestamps_s[state.frame],3)} s`;
  const points=($('space').value==='world'?data.payload.joints_world_m:data.payload.joints_camera_m)?.[data.index];
  const table=el('table'),head=el('tr');for(const label of ['关节','L · x/y/z cm','R · x/y/z cm','相机系对基准差异 · L/R mm'])head.append(el('th',label));table.append(head);
  for(let joint=0;joint<21;joint++){
    const row=el('tr');row.append(el('td',String(joint)+(joint===0?' · 腕':'')));
    for(let slot=0;slot<2;slot++){const p=points?.[slot]?.[joint];row.append(el('td',p?.every(Number.isFinite)?p.map(value=>fmt(value*100,1)).join(' / '):'—'));}
    const differences=[0,1].map(slot=>{const p=data.payload.joints_camera_m[data.index]?.[slot]?.[joint],q=reference?.payload.joints_camera_m[reference.index]?.[slot]?.[joint];return p?.every(Number.isFinite)&&q?.every(Number.isFinite)?fmt(Math.hypot(...p.map((value,i)=>value-q[i]))*1000,2):'—';});row.append(el('td',differences.join(' / ')));table.append(row);
  }
  host.append(table);$('inspectRole').textContent=base?.run.result?.reference_role==='user_declared_ground_truth'?'基准为用户声明的 GT；逐帧差异仅在有效几何上可用。':'基准为算法结果；逐帧数值表示差异。';
  const K=data.payload.K[data.index];$('inspectCamera').textContent='内参 K：'+K.map(row=>row.map(value=>fmt(value,2)).join(' / ')).join('；');
  drawInspectorTrend(card);
}
function drawInspectorTrend(card){
  const canvas=$('inspectTrend'),ctx=resizeCanvas(canvas,600,130),trajectory=card.trajectory;ctx.clearRect(0,0,600,130);ctx.font='11px sans-serif';ctx.fillStyle='#9cafaa';ctx.fillText('腕部相机深度趋势 · m（显示抽样；指标使用完整数据）',10,15);
  if(!trajectory?.camera_wrists_m)return;
  const times=trajectory.timestamps_s||trajectory.source_frame_indices.map(id=>id/state.comparison.sample.fps),valid=trajectory.camera_wrists_m.flat().map(p=>p?.[2]).filter(Number.isFinite);if(!valid.length)return;
  const min=Math.min(...valid),max=Math.max(...valid),span=Math.max(.01,max-min),duration=Math.max(.001,times.at(-1)-times[0]);
  for(let slot=0;slot<2;slot++){ctx.strokeStyle=handColor(slot);ctx.beginPath();let open=false;trajectory.camera_wrists_m.forEach((hands,index)=>{const z=hands[slot]?.[2];if(!Number.isFinite(z)){open=false;return;}const x=35+(times[index]-times[0])/duration*550,y=110-(z-min)/span*80;if(open)ctx.lineTo(x,y);else ctx.moveTo(x,y);open=true;});ctx.stroke();}
  ctx.fillStyle='#9cafaa';ctx.fillText(fmt(max,2),0,35);ctx.fillText(fmt(min,2),0,115);const x=35+(state.comparison.sample.timestamps_s[state.frame]-times[0])/duration*550;ctx.strokeStyle='#edf5ef88';ctx.beginPath();ctx.moveTo(x,25);ctx.lineTo(x,115);ctx.stroke();
}
function initializeGeometryTools(){
  $('geometryStyle').value=workspaceUI.geometry;
  $('geometryStyle').onchange=()=>{workspaceUI.geometry=geometryMode();saveLayout();ensureFrame(state.frame).then(()=>render(true)).catch(error=>notify(error.message));};
  $('showBaseline').onchange=()=>ensureFrame(state.frame).then(()=>render(true));
  $('frameInspector').ontoggle=()=>{if($('frameInspector').open){pause();ensureFrame(state.frame).then(()=>updateFrameInspector());}};
  $('inspectRun').onchange=()=>{const run=$('inspectRun').value;resultView.expanded=run;applyResultVisibility();ensureFrame(state.frame).then(()=>render(true));};
  $('inspectPrev').onclick=()=>seek(state.frame-1);$('inspectNext').onclick=()=>seek(state.frame+1);
}

if(typeof module!=='undefined')module.exports={rgbColor};
