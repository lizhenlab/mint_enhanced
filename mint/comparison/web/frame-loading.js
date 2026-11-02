'use strict';

// Basic geometry has a small bounded queue. Optional meshes and trajectories
// must never hold the current wrist values behind another result's request.
const frameLoader={jobs:new Map(),queue:[],errors:new Map(),active:0,epoch:0,renderPending:false};
const FRAME_READ_CONCURRENCY=4,FRAME_READ_TIMEOUT_MS=15000,FRAME_CACHE_LIMIT=72;
const FRAME_ARRAY_FIELDS=['sample_positions','source_frame_indices','timestamps_s','joints_camera_m','geometry_valid','K','presence','detected','tracked','filled','confidence','exists_2d','exists_3d','available','joints_world_m','camera_world_m','T_world_camera','pose_valid'];
function splitFramePayload(payload,offset,count=FRAME_READ_COUNT){const block={...payload};for(const field of FRAME_ARRAY_FIELDS)if(Array.isArray(payload[field]))block[field]=payload[field].slice(offset,offset+count);return block;}
function frameReadAbort(message='Frame read cancelled'){const error=new Error(message);error.name='AbortError';return error;}
function frameReadAPI(path,{controller=new AbortController(),timeoutMs=FRAME_READ_TIMEOUT_MS}={}){
  return new Promise((resolve,reject)=>{
    let settled=false,timedOut=false;
    const finish=(callback,value)=>{if(settled)return;settled=true;clearTimeout(timer);controller.signal.removeEventListener('abort',onAbort);callback(value);};
    const onAbort=()=>finish(reject,timedOut?new Error(`当前帧读取超时（${Math.round(timeoutMs/1000)}秒），请重试。`):frameReadAbort());
    const timer=setTimeout(()=>{timedOut=true;controller.abort();},timeoutMs);
    controller.signal.addEventListener('abort',onAbort,{once:true});
    if(controller.signal.aborted){onAbort();return;}
    try{Promise.resolve(api(path,undefined,{signal:controller.signal})).then(value=>finish(resolve,value),error=>finish(reject,error));}catch(error){finish(reject,error);}
  });
}
function requestFrameRender(){
  if(frameLoader.renderPending)return;frameLoader.renderPending=true;const epoch=frameLoader.epoch;
  const schedule=typeof requestAnimationFrame==='function'?requestAnimationFrame:callback=>setTimeout(callback,0);
  schedule(()=>{if(epoch!==frameLoader.epoch)return;frameLoader.renderPending=false;if(state.comparison?.sample)render(true);});
}
function frameCurrentStart(){return Math.floor(state.frame/FRAME_READ_COUNT)*FRAME_READ_COUNT;}
function framePriorCards(){return typeof parameterPanelReadCards==='function'&&typeof parameterPanelIsOpen==='function'&&parameterPanelIsOpen()&&state.frame>0&&state.frame%FRAME_READ_COUNT===0?parameterPanelReadCards():[];}
function frameBlockWanted(card,start){
  if(!state.comparison?.sample||start<0||start>=state.comparison.sample.frames)return false;
  const current=frameCurrentStart();
  if((start===current||start===current+FRAME_READ_COUNT)&&frameReadCards().includes(card))return true;
  return start===current-FRAME_READ_COUNT&&framePriorCards().includes(card);
}
function protectedFrameKeys(){
  if(!state.comparison?.sample)return new Set();const origin=state.comparison.common_world_origin_source_frame??null,start=frameCurrentStart(),keys=new Set();
  for(const card of frameReadCards())if(card.run.result)keys.add(resultFrameKey(card.run,start,origin));
  for(const card of framePriorCards())if(card.run.result)keys.add(resultFrameKey(card.run,start-FRAME_READ_COUNT,origin));
  return keys;
}
function storeFrameBlock(key,value){
  state.cache.delete(key);state.cache.set(key,value);const protectedKeys=protectedFrameKeys();
  while(state.cache.size>FRAME_CACHE_LIMIT){const removable=[...state.cache.keys()].find(candidate=>!protectedKeys.has(candidate));if(removable===undefined)break;state.cache.delete(removable);}
}
function frameJobCurrent(job){return job.epoch===frameLoader.epoch&&currentResultRead(job.card,job.revision,job.token,job.comparisonId,job.origin);}
function frameJobWanted(job){return job.starts.some(start=>frameBlockWanted(job.card,start));}
function cancelFrameJob(job){
  if(job.cancelled)return;job.cancelled=true;job.controller.abort();
  if(!job.started){if(frameLoader.jobs.get(job.key)===job)frameLoader.jobs.delete(job.key);for(const key of job.keys)if(state.loading.get(key)===job.promise)state.loading.delete(key);job.resolve(null);}
}
function resetFrameLoading(){
  frameLoader.epoch++;for(const job of frameLoader.jobs.values())cancelFrameJob(job);frameLoader.jobs.clear();frameLoader.queue=[];frameLoader.errors.clear();frameLoader.renderPending=false;
}
function pruneFrameReads(){for(const job of frameLoader.jobs.values())if(!frameJobCurrent(job)||!frameJobWanted(job))cancelFrameJob(job);frameLoader.queue=frameLoader.queue.filter(job=>!job.cancelled);}
function pumpFrameReads(){
  frameLoader.queue.sort((a,b)=>Number(!a.starts.includes(frameCurrentStart()))-Number(!b.starts.includes(frameCurrentStart()))||Number(a.prefetch)-Number(b.prefetch));
  while(frameLoader.active<FRAME_READ_CONCURRENCY&&frameLoader.queue.length){
    const job=frameLoader.queue.shift();if(job.cancelled)continue;
    if(!frameJobCurrent(job)||!frameJobWanted(job)){cancelFrameJob(job);continue;}
    job.started=true;frameLoader.active++;
    (async()=>{
      let value=null;
      try{
        const query=new URLSearchParams({start:job.start,count:job.count});if(job.origin!==null&&job.card.run.result.arrays.T_world_camera)query.set('origin',job.origin);
        value=await frameReadAPI(`api/comparison/runs/${job.card.run.id}/frames?${query}`,{controller:job.controller});
        if(job.cancelled||!frameJobCurrent(job)||!frameJobWanted(job)){value=null;return;}
        let redraw=false;for(let i=0;i<job.starts.length;i++){const start=job.starts[i];if(!frameBlockWanted(job.card,start)&&(start<frameCurrentStart()||start>frameCurrentStart()+64))continue;storeFrameBlock(job.keys[i],job.count===FRAME_READ_COUNT?value:splitFramePayload(value,start-job.start));frameLoader.errors.delete(job.keys[i]);if(start===frameCurrentStart()||start===frameCurrentStart()-FRAME_READ_COUNT)redraw=true;}if(redraw)requestFrameRender();
      }catch(error){
        if(!job.cancelled&&frameJobCurrent(job)&&frameJobWanted(job)&&error.name!=='AbortError'){
          for(let i=0;i<job.keys.length;i++)frameLoader.errors.set(job.keys[i],{message:error.message||'当前帧读取失败，请重试。',prefetch:job.prefetch,at:Date.now()});
          while(frameLoader.errors.size>FRAME_CACHE_LIMIT)frameLoader.errors.delete(frameLoader.errors.keys().next().value);
          if(job.starts.includes(frameCurrentStart()))requestFrameRender();
        }
      }finally{
        frameLoader.active--;if(frameLoader.jobs.get(job.key)===job)frameLoader.jobs.delete(job.key);for(const key of job.keys)if(state.loading.get(key)===job.promise)state.loading.delete(key);job.resolve(value);pumpFrameReads();
      }
    })();
  }
}
function loadFrameBlock(card,start,{prefetch=false,count=FRAME_READ_COUNT}={}){
  const comparison=state.comparison;if(!comparison?.sample||!card?.run.result||!Number.isInteger(start)||!frameBlockWanted(card,start))return Promise.resolve(null);
  const origin=comparison.common_world_origin_source_frame??null,key=resultFrameKey(card.run,start,origin);
  if(state.cache.has(key)){const value=state.cache.get(key);state.cache.delete(key);state.cache.set(key,value);return Promise.resolve(value);}
  const previous=[...frameLoader.jobs.values()].find(job=>!job.cancelled&&job.keys.includes(key));if(previous){if(!prefetch)previous.prefetch=false;return previous.promise;}
  const error=frameLoader.errors.get(key);if(error){if(!prefetch&&error.prefetch)frameLoader.errors.delete(key);else return Promise.resolve(null);}
  count=prefetch&&count===64?64:FRAME_READ_COUNT;const starts=[];for(let position=start;position<Math.min(start+count,comparison.sample.frames);position+=FRAME_READ_COUNT)starts.push(position);
  const job={key,keys:starts.map(position=>resultFrameKey(card.run,position,origin)),starts,count,card,start,origin,token:state.pollToken,comparisonId:comparison.id,revision:resultRevision(card.run),epoch:frameLoader.epoch,prefetch,controller:new AbortController(),started:false,cancelled:false};
  job.promise=new Promise(resolve=>job.resolve=resolve);frameLoader.jobs.set(key,job);for(const alias of job.keys)state.loading.set(alias,job.promise);frameLoader.queue.push(job);pumpFrameReads();return job.promise;
}
function scheduleVisibleFrames(frame=state.frame){
  if(!state.comparison?.sample||frame!==state.frame)return;pruneFrameReads();const start=frameCurrentStart(),cards=frameReadCards().filter(card=>card.run.result);
  for(const card of cards)loadFrameBlock(card,start);
  // A low-priority contiguous read fills four ordinary 16-frame cache blocks.
  for(const card of cards)if(start+FRAME_READ_COUNT<state.comparison.sample.frames)loadFrameBlock(card,start+FRAME_READ_COUNT,{prefetch:true,count:64});
}
function frameReadFailure(card,frame=state.frame){if(!state.comparison?.sample||!card?.run.result)return null;return frameLoader.errors.get(resultFrameKey(card.run,Math.floor(frame/FRAME_READ_COUNT)*FRAME_READ_COUNT,state.comparison.common_world_origin_source_frame??null))||null;}
function retryFrameRead(card,frame=state.frame){
  if(!state.comparison?.sample||!card?.run.result)return;const start=Math.floor(frame/FRAME_READ_COUNT)*FRAME_READ_COUNT,key=resultFrameKey(card.run,start,state.comparison.common_world_origin_source_frame??null);frameLoader.errors.delete(key);loadFrameBlock(card,start);requestFrameRender();
}
