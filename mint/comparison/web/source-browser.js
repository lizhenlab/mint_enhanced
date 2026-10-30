'use strict';

const sourceBrowser={path:'',parent:'',data:null,signature:'',sequence:0,busy:false,seed:null,navigationError:false};
const joinServerPath=(base,name)=>(base==='/'?'':base.replace(/\/+$/,''))+'/'+name;
const sourceParent=path=>path.includes('/')?path.slice(0,path.lastIndexOf('/'))||'/':'';

function sourceFileSize(bytes){
  if(bytes===undefined)return '';
  const units=['B','KB','MB','GB','TB'];let index=0;
  while(bytes>=1024&&index<units.length-1){bytes/=1024;index++;}
  return `${bytes.toFixed(index?1:0)} ${units[index]}`;
}
function syncSourceSelection(){
  const path=$('source').value.trim(),name=path.slice(path.lastIndexOf('/')+1);
  const data=sourceBrowser.data,missing=!!path&&data&&sourceParent(path)===data.path&&!data.truncated&&!data.videos.some(file=>file.path===path);
  $('sourceSelectedName').textContent=path?(missing?'当前目录未找到：':'')+name:'尚未选择视频';
  $('sourceSelectedPath').textContent=path||'点击目录中的视频文件进行选择。';
  $('sourceSelectedPath').title=path;$('sourceSelected').classList.toggle('has-selection',!!path);
  $('sourceSelected').classList.toggle('missing',!!missing);
  $('sourceLocate').disabled=!path;
  for(const row of $('sourceFiles').querySelectorAll('[data-video-path]')){
    const active=row.dataset.videoPath===path;row.classList.toggle('selected',active);row.setAttribute('aria-pressed',String(active));
  }
}
function sourceBreadcrumbs(path){
  const host=$('sourceCrumbs');host.replaceChildren();
  const append=(name,value)=>{const button=el('button',name);button.type='button';button.title=value;button.onclick=()=>browseSourceDirectory(value);host.append(button);};
  append('/','/');let current='';
  for(const name of path.split('/').filter(Boolean)){current+='/'+name;append(name,current);host.append(el('span','/'));}
  [...host.querySelectorAll('button')].at(-1)?.setAttribute('aria-current','location');
}
function renderSourceFiles(){
  const host=$('sourceFiles'),data=sourceBrowser.data;if(!data)return;
  const scroll=host.scrollTop,focused=document.activeElement?.dataset?.entryPath;
  const query=$('sourceSearch').value.trim().toLocaleLowerCase();host.replaceChildren();
  const dirs=data.dirs.filter(name=>name.toLocaleLowerCase().includes(query));
  const videos=data.videos.filter(file=>file.name.toLocaleLowerCase().includes(query));
  for(const [kind,items]of [['folder',dirs],['video',videos]])for(const value of items){
    const name=kind==='folder'?value:value.name,path=kind==='folder'?joinServerPath(data.path,name):value.path;
    const row=el('button',undefined,'source-entry');row.type='button';row.dataset.entryPath=path;
    row.title=path;row.append(icon(kind),el('span',name,'source-entry-name'));
    if(kind==='folder'){
      row.setAttribute('aria-label','进入文件夹 '+name);row.append(el('small','进入 ›'));
      row.onclick=()=>browseSourceDirectory(path);
    }else{
      row.dataset.videoPath=path;row.setAttribute('aria-label','选择视频 '+name);
      row.append(el('small',sourceFileSize(value.size_bytes)));
      row.onclick=()=>{$('source').value=path;sourceBrowser.seed=path;$('source').dispatchEvent(new Event('input',{bubbles:true}));$('formError').textContent='';};
    }
    host.append(row);
  }
  if(!dirs.length&&!videos.length)host.append(el('p',query?'没有符合筛选的视频或文件夹。':'此目录没有可选视频或子文件夹。','source-empty'));
  if(data.truncated)host.append(el('p','目录最多显示 2000 项，可进入子目录或直接定位路径。','source-empty'));
  host.scrollTop=scroll;
  syncSourceSelection();
  if(focused){const row=[...host.querySelectorAll('[data-entry-path]')].find(row=>row.dataset.entryPath===focused);row?.focus({preventScroll:true});}
}
async function browseSourceDirectory(path='',{quiet=false}={}){
  if(sourceBrowser.busy&&quiet)return;
  const token=++sourceBrowser.sequence;sourceBrowser.busy=true;
  $('sourceRefresh').disabled=true;$('sourceDirectory').setAttribute('aria-busy','true');
  if(!quiet){sourceBrowser.navigationError=false;$('sourceBrowserStatus').textContent='正在读取服务器目录…';$('sourceFiles').inert=true;}
  $('sourceBrowserError').hidden=true;
  try{
    const data=await api('api/dirbrowse?videos=1&path='+encodeURIComponent(path));
    if(token!==sourceBrowser.sequence)return;
    const changed=sourceBrowser.path!==data.path;
    sourceBrowser.path=data.path;sourceBrowser.parent=data.parent;sourceBrowser.data={...data,videos:data.videos||[]};
    if(changed){$('sourceSearch').value='';$('sourceFiles').scrollTop=0;}
    if(document.activeElement!==$('sourceDirectoryPath')||!quiet)$('sourceDirectoryPath').value=data.path;
    if(changed||!$('sourceCrumbs').children.length)sourceBreadcrumbs(data.path);
    $('sourceUp').disabled=data.parent===data.path;
    const signature=JSON.stringify(sourceBrowser.data);
    if(signature!==sourceBrowser.signature||changed){sourceBrowser.signature=signature;renderSourceFiles();}
    else syncSourceSelection();
    $('sourceBrowserStatus').textContent=`${data.dirs.length} 个文件夹 · ${sourceBrowser.data.videos.length} 个视频 · ${new Date().toLocaleTimeString('zh-CN',{hour12:false})} 更新`;
  }catch(error){
    if(token!==sourceBrowser.sequence)return;
    if(!quiet)sourceBrowser.navigationError=true;
    $('sourceBrowserError').textContent=error.message;$('sourceBrowserError').hidden=false;
    $('sourceBrowserStatus').textContent=sourceBrowser.path?'上次读取未成功，可重试或定位其他目录。':'尚未读取到目录。';
  }finally{
    if(token===sourceBrowser.sequence){sourceBrowser.busy=false;$('sourceRefresh').disabled=false;$('sourceFiles').inert=false;$('sourceDirectory').setAttribute('aria-busy','false');}
  }
}
function prepareSourceBrowser(){
  const source=$('source').value.trim();syncSourceSelection();
  const path=sourceBrowser.seed===source&&sourceBrowser.path?sourceBrowser.path:sourceParent(source)||sourceBrowser.path;
  sourceBrowser.seed=source;browseSourceDirectory(path);
}
function initializeSourceBrowser(){
  $('sourceUp').onclick=()=>browseSourceDirectory(sourceBrowser.parent);
  $('sourceRefresh').onclick=()=>browseSourceDirectory(sourceBrowser.path);
  $('sourceSearch').oninput=renderSourceFiles;
  const locate=()=>{const path=$('sourceDirectoryPath').value.trim();if(path&&!path.startsWith('/')){$('sourceBrowserError').textContent='请输入以 / 开头的服务器绝对目录。';$('sourceBrowserError').hidden=false;return;}browseSourceDirectory(path);};
  $('sourceGo').onclick=locate;
  $('sourceDirectoryPath').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();locate();}};
  $('sourceLocate').onclick=()=>browseSourceDirectory(sourceParent($('source').value.trim()));
  $('source').addEventListener('input',syncSourceSelection);
  $('examples').addEventListener('change',()=>requestAnimationFrame(prepareSourceBrowser));
  setInterval(()=>{if($('taskDialog').open&&!$('sourceDirectory').hidden&&!document.hidden&&sourceBrowser.path&&!sourceBrowser.navigationError&&document.activeElement!==$('sourceDirectoryPath'))browseSourceDirectory(sourceBrowser.path,{quiet:true});},5000);
}
