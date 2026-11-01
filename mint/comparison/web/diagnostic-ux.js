/* Shared result playback and concise input/model context. No network operations. */
(function(factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else{window.DiagnosticUX=api;api.mount();}
})(function(){
  'use strict';
  const rates=[0.25,0.5,1,2];
  function playbackSnapshot(context,video){
    const count=Number(context.nframes),total=Number.isFinite(count)&&count>0?Math.max(1,Math.floor(count)):1;
    const rate=Number(context.fps),fps=Number.isFinite(rate)&&rate>0?rate:30;
    const time=Number.isFinite(video?.currentTime)?video.currentTime:0;
    const frame=Math.max(0,Math.min(total-1,Math.round(time*fps)));
    const ready=!!context.loaded&&!context.loading&&!!video&&video.readyState>=1&&!video.error;
    return {total,fps,frame,time,ready,playing:ready&&!video.paused&&!video.ended};
  }
  function frameTime(value,snapshot){
    const number=Number(value);
    if(!Number.isFinite(number)||String(value).trim()==='')return null;
    return Math.max(0,Math.min(snapshot.total-1,Math.round(number)))/snapshot.fps;
  }
  function shouldHandlePlaybackKey(event,view){
    if(view!=='results'||event.defaultPrevented||event.ctrlKey||event.metaKey||event.altKey||event.isComposing)return false;
    if(event.target?.closest?.('input,textarea,select,button,a,summary,video,[contenteditable],[role="tab"],[role="slider"]'))return false;
    return [' ','ArrowLeft','ArrowRight'].includes(event.key);
  }
  function resultContext(context,{selectedInput='',modelTag=''}={}){
    const loaded=!!context.loaded;
    const source=loaded?(context.sourceName||context.sourcePath||context.vidName||'当前输入'):(selectedInput||'请选择服务器视频或 Episode');
    const episode=loaded&&context.epOrdinal!=null?` · Episode ${context.epOrdinal}${context.epTotal!=null?' / '+context.epTotal:''}`:'';
    return {source:source+episode,path:loaded?context.sourcePath||source:selectedInput,
      model:loaded&&context.rawOnly?'原始 GT · 未使用预测模型':String((loaded?context.resultModelTag||modelTag:modelTag)||'尚未选择模型').replace(/^checkpoints_/,'').replace(/_([a-f0-9]{8,})$/,' · $1'),
      status:context.loading?(context.cancelling?'正在停止…':'正在处理当前输入…'):
        loaded?(context.rawOnly?'原始 GT':context.no_truth?'预测结果 · 无 GT':'GT 与预测结果'):
        context.inferenceDirty?'输入 / 参数待应用 · 未运行':'尚未加载结果',
      state:context.loading?'busy':loaded?'ready':'pending'};
  }
  function mount(options={}){
    const doc=options.document||document,win=options.window||window;
    const readState=options.getState||(()=>state),readVideo=options.getVideo||(()=>masterVideo);
    const readView=options.getView||(()=>diagnosticView),redraw=options.redraw||(()=>requestDraw());
    const readModel=options.getModelTag||(()=>typeof CKPT_TAG==='undefined'?'':CKPT_TAG);
    const readSelected=options.getSelectedInput||(()=>{
      const dataset=typeof curDatasetRel!=='undefined'&&curDatasetRel;
      if(dataset)return dataset+' · Episode '+(doc.getElementById('ep')?.value||'0');
      const directory=typeof inCur!=='undefined'?inCur:'';
      return readState().vidName?(directory.replace(/\/$/,'')+'/'+readState().vidName):directory?
        (win.ViewerI18n?.language==='en'?'Directory: '+directory+' · Select a video':'浏览目录：'+directory+' · 请选择视频'):'';
    });
    const readInputReady=options.getInputReady||(()=>typeof curDatasetRel!=='undefined'&&curDatasetRel!==null?
      typeof EP_TOTAL!=='undefined'&&EP_TOTAL>0:!!(readState().no_truth&&readState().vidName&&readState().eid!=null));
    const ids=['diagnosticPlayback','diagnosticPlay','diagnosticPrevFrame','diagnosticNextFrame','diagnosticTimeline','diagnosticFrameLabel','diagnosticFrameJump','diagnosticSpeed','diagnosticPlaybackStatus','diagnosticInputLabel','diagnosticModelLabel','diagnosticResultState','diagnosticConfiguredInput','diagnosticConfiguredModel','diagnosticInputBrowser','diagnosticRunSummary','diagnosticContextBar','diagnosticInferenceStatus','diagnosticToolContext','diagnosticBackgroundTask','diagnosticBackgroundStatus'];
    const nodes=Object.fromEntries(ids.map(id=>[id,doc.getElementById(id)]));
    if(!nodes.diagnosticPlayback)return null;
    let clock=null,detach=()=>{},speed=1,disposed=false,initialInput=true,inputBrowserTouched=false;
    const text=(node,value)=>{if(node&&node.textContent!==String(value))node.textContent=String(value);};
    function bindClock(video){
      if(video===clock)return;
      detach();clock=video;
      if(!clock){detach=()=>{};return;}
      const observed=clock;
      observed.playbackRate=speed;
      const events=['loadedmetadata','loadeddata','timeupdate','seeked','play','pause','ended','ratechange','durationchange','error','emptied'];
      const update=()=>{
        if(rates.includes(observed.playbackRate))speed=observed.playbackRate;
        sync();
      };
      for(const name of events)observed.addEventListener(name,update);
      detach=()=>{for(const name of events)observed.removeEventListener(name,update);};
    }
    function sync(){
      if(disposed)return;
      const context=readState(),view=readView(),video=readVideo();
      bindClock(video);
      if(nodes.diagnosticContextBar)nodes.diagnosticContextBar.hidden=view==='tools';
      if(nodes.diagnosticInferenceStatus)nodes.diagnosticInferenceStatus.hidden=view==='tools';
      if(nodes.diagnosticToolContext)nodes.diagnosticToolContext.hidden=view!=='tools';
      if(nodes.diagnosticBackgroundTask)nodes.diagnosticBackgroundTask.hidden=view!=='tools'||!context.loading;
      text(nodes.diagnosticBackgroundStatus,context.cancelling?'MINT 诊断正在停止…':context.rawOnly?'原始标注仍在后台读取':'MINT 诊断仍在后台运行');
      const snap=playbackSnapshot(context,video),bar=nodes.diagnosticPlayback;
      bar.hidden=view!=='results'||!context.loaded;
      const summary=resultContext(context,{selectedInput:readSelected(),modelTag:readModel()});
      text(nodes.diagnosticInputLabel,summary.source);if(nodes.diagnosticInputLabel)nodes.diagnosticInputLabel.title=summary.path||summary.source;
      text(nodes.diagnosticModelLabel,summary.model);if(nodes.diagnosticModelLabel)nodes.diagnosticModelLabel.title=summary.model;
      text(nodes.diagnosticResultState,summary.status);if(nodes.diagnosticResultState)nodes.diagnosticResultState.dataset.state=summary.state;
      const selected=readSelected(),english=win.ViewerI18n?.language==='en',configuredModel=readModel()?
        resultContext({loaded:false},{modelTag:readModel()}).model:english?'No model selected':'尚未选择模型';
      text(nodes.diagnosticConfiguredInput,selected||(english?'Select a video or Episode':'请选择视频或 Episode'));
      if(nodes.diagnosticConfiguredInput)nodes.diagnosticConfiguredInput.title=selected||'';
      text(nodes.diagnosticConfiguredModel,configuredModel);
      if(nodes.diagnosticConfiguredModel)nodes.diagnosticConfiguredModel.title=configuredModel;
      const inputBrowser=nodes.diagnosticInputBrowser;
      if(initialInput&&readInputReady()&&inputBrowser){
        if(!inputBrowserTouched)inputBrowser.open=false;
        initialInput=false;
      }
      const camera=doc.getElementById('camMode')?.selectedOptions?.[0]?.textContent;
      const hand=doc.getElementById('handModeSeg')?.querySelector?.('.on')?.textContent;
      text(nodes.diagnosticRunSummary,[camera,hand].filter(Boolean).join(' · ')||'当前设置仅在点击开始推理后应用。');
      for(const key of ['diagnosticPlay','diagnosticPrevFrame','diagnosticNextFrame','diagnosticTimeline','diagnosticFrameJump','diagnosticSpeed'])if(nodes[key])nodes[key].disabled=!snap.ready;
      const play=nodes.diagnosticPlay;
      text(play,snap.playing?'暂停':'播放');if(play){play.setAttribute('aria-label',snap.playing?'暂停联动播放':'播放联动结果');play.setAttribute('aria-pressed',String(snap.playing));}
      if(nodes.diagnosticPrevFrame)nodes.diagnosticPrevFrame.disabled=!snap.ready||snap.frame===0;
      if(nodes.diagnosticNextFrame)nodes.diagnosticNextFrame.disabled=!snap.ready||snap.frame>=snap.total-1;
      const range=nodes.diagnosticTimeline,jump=nodes.diagnosticFrameJump;
      if(range){range.max=String(snap.total-1);if(doc.activeElement!==range)range.value=String(snap.frame);range.setAttribute('aria-valuetext',`第 ${snap.frame+1} 帧，共 ${snap.total} 帧`);}
      if(jump){jump.max=String(snap.total);if(doc.activeElement!==jump)jump.value=String(snap.frame+1);}
      if(nodes.diagnosticSpeed&&doc.activeElement!==nodes.diagnosticSpeed)nodes.diagnosticSpeed.value=String(speed);
      text(nodes.diagnosticFrameLabel,`样本帧 ${snap.frame+1} / ${snap.total} · ${(snap.frame/snap.fps).toFixed(3)} s`);
      text(nodes.diagnosticPlaybackStatus,!snap.ready?(context.loading?'正在处理，完成后可联动播放':video?.error?'二维预览加载失败，暂无法播放':video?'正在准备二维预览…':'显示整体 2D 后可联动播放'):
        context.hidden?.has('both_2d')?'2D 已隐藏 · 时间轴仍联动':'← → 逐帧 · 空格播放');
    }
    function seek(frame){
      const context=readState(),video=readVideo(),snap=playbackSnapshot(context,video);
      if(!snap.ready)return false;
      const target=frameTime(frame,snap);if(target===null)return false;
      video.pause();video.currentTime=target;redraw();sync();return true;
    }
    async function toggle(){
      const video=readVideo(),snap=playbackSnapshot(readState(),video);if(!snap.ready)return;
      if(snap.playing)video.pause();
      else{
        if(video.ended)video.currentTime=0;
        try{await video.play();}catch(error){text(nodes.diagnosticPlaybackStatus,'无法播放，请重试：'+error.message);return;}
      }
      redraw();sync();
    }
    const handlers=[];
    function listen(node,name,handler){if(!node)return;node.addEventListener(name,handler);handlers.push(()=>node.removeEventListener(name,handler));}
    listen(nodes.diagnosticInputBrowser,'click',event=>{
      inputBrowserTouched=true;
      if(event.target?.closest?.('.bleaf[data-eid]')&&readInputReady()&&!readState().loading){
        nodes.diagnosticInputBrowser.open=false;
        nodes.diagnosticInputBrowser.querySelector?.('summary')?.focus();
      }
    });
    listen(nodes.diagnosticInputBrowser,'keydown',()=>{inputBrowserTouched=true;});
    listen(nodes.diagnosticPlay,'click',toggle);
    listen(nodes.diagnosticPrevFrame,'click',()=>seek(playbackSnapshot(readState(),readVideo()).frame-1));
    listen(nodes.diagnosticNextFrame,'click',()=>seek(playbackSnapshot(readState(),readVideo()).frame+1));
    listen(nodes.diagnosticTimeline,'input',event=>seek(event.target.value));
    listen(nodes.diagnosticFrameJump,'change',event=>{if(event.target.value.trim()!=='')seek(Number(event.target.value)-1);});
    listen(nodes.diagnosticFrameJump,'keydown',event=>{if(event.key==='Enter'){event.preventDefault();if(event.target.value.trim()!=='')seek(Number(event.target.value)-1);}});
    listen(nodes.diagnosticSpeed,'change',event=>{
      const next=Number(event.target.value);if(!rates.includes(next))return;speed=next;const video=readVideo();if(video)video.playbackRate=speed;sync();
    });
    listen(doc,'keydown',event=>{
      if(!shouldHandlePlaybackKey(event,readView())||!playbackSnapshot(readState(),readVideo()).ready)return;
      event.preventDefault();event.stopPropagation();
      if(event.key===' ')toggle();else seek(playbackSnapshot(readState(),readVideo()).frame+(event.key==='ArrowRight'?1:-1)*(event.shiftKey?10:1));
    });
    const interval=win.setInterval(sync,200);sync();
    return {sync,seek,toggle,destroy(){disposed=true;win.clearInterval(interval);detach();for(const remove of handlers)remove();}};
  }
  return {playbackSnapshot,frameTime,shouldHandlePlaybackKey,resultContext,mount};
});
