'use strict';
// Share the language preference with WUJI EGO. Untranslated text is retained,
// including filenames and diagnostic evidence; it is never stripped or guessed.
(()=>{
  const $=id=>document.getElementById(id);
  const key='wuji-viewer-language';let language='zh';try{language=localStorage.getItem(key)||'zh';}catch(_){}
  const phrases={
    '数据':'Data','比较':'Compare','评测与质检':'Evaluation & review','训练与模型':'Training & models','任务':'Tasks','MINT 高级调试':'MINT advanced console','数据接入':'Data sources','任务与恢复':'Tasks & recovery','批量比较':'Batch comparison','数据集评测':'Dataset evaluation','专项工具':'Specialist tools','数据与结果目录':'Data & result catalog','当前比较':'Current comparison','逐帧质检':'Frame review','回归 / 性能':'Regression / performance','公开基准':'Public benchmarks','开源数据集 / GT':'Native dataset / GT','导入已有结果':'Import existing result','提交前协议概览':'Protocol before submission','常规比较':'Regular comparisons','全部记录':'All records','包含系统与验收记录':'Include system / acceptance records','未保存修改':'Unsaved changes','恢复本地草稿':'Restore local draft','清除本地草稿':'Discard local draft','本地草稿已保存':'Local draft saved','可用':'Ready','需配置':'Setup required','实验中':'Experimental','未实现':'Not implemented',
    '多算法比较':'Algorithm comparison','新建比较':'New comparison','结果目录':'Result catalog','质检与复核':'Quality & review','数据与训练':'Data & training','持久结果与实验目录':'Result & experiment catalog','逐帧质检与人工复核':'Frame diagnostics & review','数据、训练与模型版本':'Data, training & models','阶段记录':'Phase history','比较任务':'Comparison','读取比较结果':'Load comparison','逐帧质检与问题片段':'Frame diagnostics & issue clips','人工复核':'Manual review','分析已有几何':'Analyze geometry','保存复核记录':'Save review','发布复核数据版本':'Publish reviewed dataset version','模型版本与发布门槛':'Model versions & gates','数据生产与训练任务':'Data & training jobs','冻结协议与离线评测':'Frozen protocols & offline evaluation','提交持久任务':'Submit persistent job','预检工作流':'Preflight workflow','输入预检':'Input preflight','查看日志':'View log','任务优先级':'Task priority','结果缓存':'Result cache','交互任务':'Interactive','批量任务':'Batch','后台任务':'Background','独立重跑（性能试验）':'Fresh execution (performance trial)','当前阶段预计剩余':'Estimated phase time remaining','数据与输入':'Data & input','任务中心':'Task center','结果分析':'Results','量化评测':'Evaluation','高级工具':'Tools',
    '联动查看':'Linked views','指标分析':'Metrics','结果管理':'Manage results','比较任务':'Comparisons','全部':'All','运行中':'Running','未完整完成':'Needs attention',
    '输入与参数':'Input & parameters','比较报告':'Report','导出视频':'Export video','显示结果':'Results shown','对照基准':'Baseline','基准叠加':'Baseline overlay','逐帧诊断':'Frame inspector','逐帧分析':'Frame inspector',
    '骨架':'Skeleton','网格':'Mesh','网格 + 骨架':'Mesh & skeleton','相机系':'Camera','世界系':'World','轨迹':'Trajectories','低存在性':'Low presence',
    '上一页':'Previous page','下一页':'Next page','上一级':'Parent','前往':'Go','刷新':'Refresh','全部状态':'All states','运行中与排队':'Active & queued','已完成':'Completed','需要关注':'Needs attention',
    '多视频多算法批量':'Multi-video batch','多算法数据集评测':'Dataset evaluation','批量处理':'Batch','服务器视频':'Server video','读取目录':'Scan directory','读取数据集':'Load dataset','递归扫描':'Scan subfolders',
    '清空选择':'Clear selection','用于单次比较':'Compare this input','创建数据集评测':'Create evaluation','取消批次':'Cancel batch','恢复未完成任务':'Resume unfinished','下载报告 JSON':'Report JSON','评测 CSV':'Evaluation CSV',
    '视频质量分析':'Video analysis','内参 / FOV 分析':'Intrinsics / FOV','数据集多样性':'Dataset diversity','公开基准 / MINT 检查点评测':'Public benchmarks / MINT checkpoints','配置与代码对比':'Config & code comparison','MINT 专业调试':'MINT advanced console',
    '检查推理环境':'Check inference','检查数据生产环境':'Check data pipeline','检查训练准备':'Check training readiness','仿真与机械手重定向':'Simulation & hand retargeting','生成状态与播放':'Render status & playback',
    '结果':'Result','工具':'Tool','坐标':'Coordinates','MuJoCo 网格场景':'MuJoCo mesh scene','Wuji Hand 重定向':'Wuji Hand retargeting','原生世界系':'Native world',
    '起始样本帧（包含）':'First sample frame (inclusive)','结束样本帧（包含）':'Last sample frame (inclusive)','留空为片尾':'Empty = full interval','宽度':'Width','资源策略':'Resources','负载感知共享':'Load-aware shared','仅空闲 GPU':'Idle GPU only','生成仿真 / 重定向视频':'Render scene / retargeted hand',
    '导出结果':'Export results','视频包含结果':'Video results','全部已完成结果':'All published results','当前可见结果':'Visible results','叠加内容':'Geometry','叠加当前对照基准骨架':'Overlay baseline skeleton','生成同步视频':'Export synchronized video','保存当前可见画面 PNG':'Save visible view PNG','下载完整比较报告':'Download full report',
    '日志':'Log','几何下载':'Geometry ZIP','单列放大':'Expand result','详情':'Details','取消':'Cancel','恢复':'Resume','收起':'Collapse','运行参数':'Options','算法与资源':'Algorithms & resources','输入与协议':'Input protocol','开始比较':'Start comparison',
    '视频视角':'Video view','完整画面':'Full image','双目左眼':'Stereo left','双目右眼':'Stereo right','目标 FPS':'Target FPS','起始源帧':'First source frame','Episode 内起始帧':'First frame in Episode','最多帧数':'Maximum frames','留空 = 全部':'Empty = all',
    '输入目录':'Input directory','选择全部视频':'Select listed videos','选择本页 Episode':'Select this page','视频 / Episode 输入':'Video / Episode input','对照 / 基准':'Reference / baseline','针孔相机标定':'Pinhole calibration','已有样例':'Examples','选择一个已部署样例':'Choose a deployed example',
    'MINT 检查点（可多选）':'MINT checkpoints (one per line)','可选模型配置路径':'Optional model config','浏览服务器检查点':'Browse server checkpoints','添加当前检查点目录':'Add current checkpoint folder','手工相机标定（完整原视频网格）':'Manual calibration (full source grid)',
    '搜索任务、视频或算法':'Search tasks, videos or algorithms','搜索算法、视频、任务或来源':'Search algorithm, video, task or source','关闭工作台模块':'Close workspace module','返回结果':'Back to results',
  };
  const states=new WeakMap();let pending=false;
  function apply(){pending=false;document.documentElement.lang=language==='en'?'en':'zh-CN';$('languageSelect').value=language;
    const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let node;
    while(node=walker.nextNode()){
      if(node.parentElement?.closest('script,style,textarea,[data-i18n-ignore],pre,code'))continue;
      let saved=states.get(node);if(!saved||node.nodeValue!==saved.output)saved={source:node.nodeValue};
      const trimmed=saved.source.trim(),translated=language==='en'?phrases[trimmed]:null;
      const output=translated?saved.source.replace(trimmed,translated):saved.source;saved.output=output;states.set(node,saved);if(node.nodeValue!==output)node.nodeValue=output;
    }
  }
  const observer=new MutationObserver(()=>{if(!pending){pending=true;queueMicrotask(apply);}});
  $('languageSelect').addEventListener('change',()=>{language=$('languageSelect').value;try{localStorage.setItem(key,language);}catch(_){}apply();});
  window.addEventListener('storage',event=>{if(event.key===key){language=event.newValue||'zh';apply();}});
  observer.observe(document.body,{subtree:true,childList:true,characterData:true});apply();
})();
