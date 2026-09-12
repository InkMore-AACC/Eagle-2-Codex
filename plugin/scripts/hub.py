"""Eagle library bridge. Standard-library only. Persistent data lives outside plugin cache."""
import argparse, base64, hashlib, json, mimetypes, os, re, secrets, subprocess, sys, tempfile, threading, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse, parse_qs

ROOT=Path(__file__).resolve().parents[1]
STATE=Path(os.environ.get('LIBRARY_HUB_DATA',str(Path.home()/'Documents/Codex/LibraryData')))
STATE.mkdir(parents=True,exist_ok=True)
PORT=18765
BASE=f'http://127.0.0.1:{PORT}'
LOCK=threading.RLock()
API_LOCK=threading.BoundedSemaphore(3)
def read(name,default=None):
 p=STATE/name
 return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default
def write(name,value):
 with LOCK:
  p=STATE/name; p.parent.mkdir(parents=True,exist_ok=True)
  tmp=None
  try:
   with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=p.parent,delete=False) as f:
    tmp=Path(f.name);json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
   tmp.replace(p)
  finally:
   if tmp is not None:tmp.unlink(missing_ok=True)
def eagle(endpoint,body=None,**params):
 url='http://127.0.0.1:41595/api/'+endpoint
 token=os.environ.get('EAGLE_API_TOKEN')
 if token: params['token']=token
 if params: url+='?'+urlencode(params)
 req=Request(url,data=None if body is None else json.dumps(body,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
 with API_LOCK:
  with urlopen(req,timeout=120) as r: value=json.load(r)
 if value.get('status')!='success': raise RuntimeError('Eagle API: '+str(value.get('message',value.get('status'))))
 return value.get('data')
def library():
 value=eagle('library/info'); expected=read('config.json',{}).get('libraryPath')
 if expected and os.path.normcase(value['library']['path'])!=os.path.normcase(expected):
  raise RuntimeError('Eagle 已切换资源库。请先切回已连接资源库，或明确要求重新连接。')
 return value
def item(item_id,lib=None):
 if not re.fullmatch(r'[A-Za-z0-9_-]+',item_id): raise ValueError('无效素材编号')
 if lib is None:library()
 return eagle('item/info',id=item_id)
def file_for(i,thumb=False,lib=None):
 root=Path((lib if lib is not None else library())['library']['path']).resolve()
 folder=root/'images'/(i['id']+'.info')
 original=folder/(i['name']+'.'+i['ext'])
 p=original
 if thumb:
  thumbs=list(folder.glob('*_thumbnail.*'))
  if thumbs:p=thumbs[0]
 if not thumb and not p.is_file():
  candidates=[f for f in folder.glob('*.'+i['ext']) if not f.name.endswith('_thumbnail.'+i['ext'])]
  if len(candidates)==1:p=candidates[0]
 if not p.is_file(): p=Path(eagle('item/thumbnail',id=i['id']))
 p=p.resolve()
 if not p.is_relative_to(root) or not p.is_file(): raise ValueError('素材文件不可用')
 if not thumb and p!=original.resolve():
  candidates=[f for f in folder.glob('*.'+i['ext']) if not f.name.endswith('_thumbnail.'+i['ext'])]
  if len(candidates)==1: p=candidates[0].resolve()
  else: raise ValueError('无法确定原图，不使用缩略图冒充')
 return p
def folder_id():
 library(); fs=eagle('folder/list');cfg=read('config.json',{})
 def walk(fs):
  for f in fs:
   if f['name']=='image2.5参考': return f['id']
   found=walk(f.get('children',[]))
   if found:return found
 def ids(nodes):
  return [f['id'] for f in nodes]+[x for f in nodes for x in ids(f.get('children',[]))]
 if cfg.get('folderId') in ids(fs):return cfg['folderId']
 found=walk(fs) or eagle('folder/create',{'folderName':'image2.5参考'})['id']
 cfg['folderId']=found;write('config.json',cfg)
 return found
def all_items(**filters):
 out=[]
 # Installed Eagle 4.0 implements offset as page index (source verified).
 for offset in range(5000):
  batch=eagle('item/list',limit=200,offset=offset,**filters)
  out.extend(batch)
  if len(batch)<200:return out
 raise RuntimeError('结果过多，请缩小范围')
def brief(i):return {k:i.get(k) for k in ['id','name','ext','tags','folders','width','height','annotation','url','modificationTime','size']}
def selection_key(task_id):
 if not isinstance(task_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',task_id):raise ValueError('请从当前任务重新打开图库，缺少有效任务绑定')
 return 'selections/'+task_id+'.json'
def selection_state(task_id,lib=None):
 key=selection_key(task_id);path=(lib if lib is not None else library())['library']['path'];s=read(key,{'ids':[]})
 if s.get('libraryPath',path)!=path:raise ValueError('参考列表属于另一个 Eagle 库')
 return s
def set_selection(task_id,ids):
 key=selection_key(task_id)
 if not isinstance(ids,list) or not all(isinstance(x,str) and x for x in ids) or len(set(ids))!=len(ids):raise ValueError('请选择不重复的有效素材')
 with LOCK:
  lib=library();previous=set(selection_state(task_id,lib).get('ids',[]));added=[]
  for x in ids:
   if x in previous:continue
   i=item(x,lib)
   if i.get('isDeleted'):raise ValueError('素材已移入废纸篓')
   added.append(brief(i))
  if library()['library']['path']!=lib['library']['path']:raise ValueError('Eagle 资源库已切换，请重新选择')
  if ids:write(key,{'ids':ids,'selectedAt':time.time(),'libraryPath':lib['library']['path']})
  else:(STATE/key).unlink(missing_ok=True)
 return {'ids':ids,'saved':True,'items':added}
def selected(task_id=None):
 task_id=task_id or os.environ.get('CODEX_THREAD_ID')
 lib=library();s=selection_state(task_id,lib);out=[];errors=[]
 for x in s.get('ids',[]):
  try:
   i=item(x,lib)
   if i.get('isDeleted'):raise ValueError('素材已移入废纸篓')
   out.append(dict(brief(i),localImage=str(file_for(i,lib=lib))))
  except Exception as e:errors.append({'id':x,'error':str(e)})
 if library()['library']['path']!=lib['library']['path']:raise ValueError('Eagle 资源库已切换，请重新读取')
 return {'taskId':task_id,'items':out,'errors':errors,'message':'请先在图库点击用作参考' if not out else '当前任务选中素材，可按用户要求读取、分析或创作；选中不触发任务'}
def save_analysis(item_id,prompt,tags):
 # All MCP writers are routed through the HTTP owner; serialize the full operation.
 with LOCK:
  try:return _save_analysis(item_id,prompt,tags)
  finally:
   import browse_backend
   with browse_backend._lock:browse_backend._cache=None

def _save_analysis(item_id,prompt,tags):
 if not prompt.strip() or not tags or not all(isinstance(t,str) and t.strip() for t in tags):raise ValueError('需要分析提示词和分类标签')
 i=item(item_id); fid=folder_id(); annotation='【图片分析提示词｜非作者原始提示词】\n'+prompt.strip()
 tags=list(dict.fromkeys(t.strip() for t in tags))
 # User explicitly authorized replacement, not annotation append.
 eagle('item/update',{'id':item_id,'annotation':annotation,'tags':tags})
 updated=item(item_id)
 if updated['annotation']!=annotation or set(updated['tags'])!=set(tags):raise RuntimeError('注释或标签写入回读不一致')
 copies=read('copies.json',{})
 copy_id=copies.get(item_id)
 if fid in i.get('folders',[]):copy_id=item_id
 elif copy_id:
  eagle('item/update',{'id':copy_id,'annotation':annotation,'tags':tags})
 else:
  name=i['name']+' · 参考 '+item_id
  # Reconcile a previous partial import before submitting a duplicate.
  existing=[x for x in all_items(folders=fid) if x['name']==name]
  if not existing:
   p=file_for(i)
   result=eagle('v2/item/add',{'base64':'data:'+(mimetypes.guess_type(p.name)[0] or 'application/octet-stream')+';base64,'+base64.b64encode(p.read_bytes()).decode(),'name':name,'website':i.get('url',''),'annotation':annotation,'tags':tags,'folders':[fid]})
   copy_id=result['id'];copies[item_id]=copy_id;write('copies.json',copies)
   for _ in range(60):
    existing=[x for x in all_items(folders=fid) if x['name']==name]
    if existing:break
    time.sleep(.5)
  if len(existing)!=1:raise RuntimeError('复制结果需要检查，不重复提交')
  copy_id=existing[0]['id'];copies[item_id]=copy_id;write('copies.json',copies)
  eagle('item/update',{'id':copy_id,'annotation':annotation,'tags':tags})
 copied=item(copy_id)
 if fid not in copied['folders'] or copied['annotation']!=annotation or set(copied['tags'])!=set(tags):raise RuntimeError('参考副本回读验证失败')
 write('analysis/'+item_id+'.json',{'sourceId':item_id,'referenceId':copy_id,'prompt':prompt,'tags':tags,'savedAt':time.time()})
 return {'sourceId':item_id,'referenceId':copy_id,'verified':True}
def configure():
 lib=eagle('library/info'); old=read('config.json',{})
 if old.get('libraryPath') and old['libraryPath']!=lib['library']['path']:raise RuntimeError('已连接其他资源库，拒绝隐式改绑')
 write('config.json',dict(old,libraryPath=lib['library']['path'],folderName='image2.5参考'))
 return {'libraryPath':lib['library']['path'],'folderId':folder_id()}
def migrate():
 configure(); fid=folder_id(); data=json.loads((ROOT/'assets/cases.json').read_text(encoding='utf-8'))
 cats={'Architecture & Spaces':'建筑与空间','Brand & Logos':'品牌与标志','Characters & People':'角色与人物','Charts & Infographics':'图表与信息图','Documents & Publishing':'文档与出版','History & Classical Themes':'历史与国风','Illustration & Art':'插画与艺术','Other Use Cases':'其他','Photography & Realism':'摄影与写实','Posters & Typography':'海报与字体','Products & E-commerce':'商品与电商','Scenes & Storytelling':'场景与叙事','UI & Interfaces':'界面设计'}
 existing={x['name']:x for x in all_items(folders=fid)}
 def name(c):return re.sub(r'[<>:"/\\|?*]','',f"#{c['id']} {c['title']}")
 def annotation(c):return '【案例原始提示词】\n'+c['prompt']
 def tags(c):return list(dict.fromkeys([cats.get(c['category'],c['category']),*c.get('styles',[]),*c.get('scenes',[])]))
 submitted=set(read('import-submitted.json',[]))
 pending=[c for c in data['cases'] if name(c) not in existing and c['id'] not in submitted]
 for start in range(0,len(pending),25):
  batch=pending[start:start+25]
  submitted.update(c['id'] for c in batch);write('import-submitted.json',sorted(submitted))
  # The v2 base64 path preserves independent entries even for identical source images.
  for c in batch:
   p=ROOT/'assets'/c['image'].lstrip('/')
   eagle('v2/item/add',{'folders':[fid],'base64':'data:'+(mimetypes.guess_type(p.name)[0] or 'image/jpeg')+';base64,'+base64.b64encode(p.read_bytes()).decode(),'name':name(c),'annotation':annotation(c),'tags':tags(c),'website':c.get('sourceUrl') or c.get('githubUrl','')})
  print(json.dumps({'queued':min(start+25,len(pending)),'newTotal':len(pending)}),flush=True)
 for attempt in range(120):
  existing={x['name']:x for x in all_items(folders=fid)}
  complete=sum(name(c) in existing for c in data['cases'])
  if complete==len(data['cases']):break
  if attempt%10==0:print(json.dumps({'available':complete,'waitingForEagle':True}),flush=True)
  time.sleep(1)
 existing={x['name']:x for x in all_items(folders=fid)}; mappings={};fail=[]
 for c in data['cases']:
  i=existing.get(name(c))
  if not i:fail.append({'id':c['id'],'error':'missing'});continue
  if i['annotation']!=annotation(c) or set(i['tags'])!=set(tags(c)):
   fail.append({'id':c['id'],'error':'metadata mismatch'});continue
  p=file_for(i); src=ROOT/'assets'/c['image'].lstrip('/')
  if hashlib.sha256(p.read_bytes()).digest()!=hashlib.sha256(src.read_bytes()).digest():fail.append({'id':c['id'],'error':'image mismatch'});continue
  mappings[str(c['id'])]=i['id']
 write('case-map.json',mappings)
 report={'cases':len(data['cases']),'verified':len(mappings),'failures':fail,'folderId':fid,'libraryPath':library()['library']['path']}
 write('migration-report.json',report);print(json.dumps(report,ensure_ascii=False),flush=True)
 if fail:raise RuntimeError('迁移未完全验证')

class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,value,status=200,ctype='application/json; charset=utf-8'):
  raw=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode()
  self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw)
 def valid_host(self):return self.headers.get('Host')==f'127.0.0.1:{PORT}'
 def do_GET(self):
  try:
   if not self.valid_host():return self.send({'error':'Host rejected'},403)
   u=urlparse(self.path);q={k:v[0] for k,v in parse_qs(u.query).items()}
   if u.path=='/':return self.send((ROOT/'web/index.html').read_bytes(),ctype='text/html; charset=utf-8')
   if u.path=='/health':return self.send({'app':'library-hub','version':'1.0.0'})
   if u.path=='/api/status':
    lib=library();return self.send({'library':lib['library'],'folders':eagle('folder/list'),'referenceFolderId':folder_id(),'selection':read('selection.json',{}),'csrf':self.server.csrf})
   if u.path=='/api/references':return self.send(selected(q.get('task')))
   if u.path=='/api/browse':
    import browse_backend
    return self.send(browse_backend.browse(sys.modules[__name__],q))
   if u.path=='/api/items':
    library(); filters={'folders':q['folders']} if q.get('folders') else {}
    keyword=q.get('keyword','').strip().casefold(); offset=max(0,int(q.get('offset',0)))
    if keyword:
     rows=[i for i in all_items(**filters) if keyword in i.get('name','').casefold() or any(keyword in t.casefold() for t in i.get('tags',[]))]
     rows.sort(key=lambda i:i.get('modificationTime',0),reverse=True)
     return self.send([brief(i) for i in rows[offset:offset+60]])
    return self.send([brief(i) for i in eagle('item/list',limit=60,offset=offset//60,orderBy='-CREATEDATE',**filters)])
   if u.path=='/api/item':return self.send(brief(item(q['id'])))
   if u.path=='/media':
    i=item(q['id']);p=file_for(i,q.get('thumb')=='1')
    return self.send(p.read_bytes(),ctype=mimetypes.guess_type(p.name)[0] or 'application/octet-stream')
   return self.send({'error':'not found'},404)
  except Exception as e:self.send({'error':str(e)},400)
 def do_POST(self):
  try:
   if not self.valid_host() or self.headers.get('Origin') not in [None,BASE] or self.headers.get('X-Library-Token')!=self.server.csrf:return self.send({'error':'请求来源无效'},403)
   n=int(self.headers.get('Content-Length','0'))
   limit=64*1024*1024 if self.path=='/api/save-analysis' else 1048576
   if n<0 or n>limit:return self.send({'error':'请求过大'},413)
   body=json.loads(self.rfile.read(n))
   if self.path=='/api/edit':
    import edit_metadata
    return self.send(edit_metadata.save(sys.modules[__name__],body))
   if self.path=='/api/select':
    return self.send(set_selection(body.get('taskId'),body['ids']))
   if self.path=='/api/save-analysis':
    import analysis_store
    return self.send(analysis_store.save(sys.modules[__name__],body))
   return self.send({'error':'not found'},404)
  except Exception as e:self.send({'error':str(e)},400)
def serve():
 server=ThreadingHTTPServer(('127.0.0.1',PORT),Handler);server.csrf=secrets.token_urlsafe(32);server.serve_forever()
def ensure_server():
 try:
  with urlopen(BASE+'/health',timeout=2) as r:
   if json.load(r).get('app')=='library-hub':return BASE
  raise RuntimeError('端口被其他服务占用')
 except OSError:pass
 log=(STATE/'server.log').open('ab')
 flags=(subprocess.CREATE_NO_WINDOW|subprocess.DETACHED_PROCESS) if os.name=='nt' else 0
 subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'serve'],stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=flags,start_new_session=os.name!='nt')
 for _ in range(30):
  time.sleep(.1)
  try:
   with urlopen(BASE+'/health',timeout=1) as r:
    if json.load(r).get('app')=='library-hub':return BASE
  except OSError:pass
 raise RuntimeError('素材面板未启动，请检查本地日志')

TOOLS=[
 {'name':'library_open','description':'启动本地 Eagle 素材面板并返回 URL；随后用 Codex open_in_codex 在右侧打开。','inputSchema':{'type':'object','properties':{'taskId':{'type':'string','description':'当前 Codex 任务真实 ID，不得使用其他任务或猜测'}},'required':['taskId']}},
 {'name':'library_selected','description':'读取用户在库面板已选择的图片、原图路径、注释和分类标签。不会生成或修改素材。','inputSchema':{'type':'object','properties':{'taskId':{'type':'string','description':'当前 Codex 任务真实 ID，不得使用其他任务或猜测'}},'required':['taskId']}},
 {'name':'library_search','description':'按关键词和可选文件夹搜索 Eagle 素材，返回简要信息。','inputSchema':{'type':'object','properties':{'keyword':{'type':'string'},'folderId':{'type':'string'}},'required':['keyword']}},
 {'name':'library_save_analysis','description':'反推提示词后先询问是否存入 Eagle；仅明确确认后调用。按图片内容拟定名称和分类标签，更新选中素材并维护 image2.5参考 副本，或导入外部本地图片。不会自动反推或生成。','inputSchema':{'type':'object','properties':{'taskId':{'type':'string'},'requestId':{'type':'string','description':'本次保存的唯一ID；同一次重试保持不变'},'confirmed':{'type':'boolean','const':True},'itemId':{'type':'string'},'imagePath':{'type':'string','description':'外部图片真实绝对路径，与itemId二选一'},'expected':{'type':'object','description':'更新已有素材时传最新读取的name/tags/annotation'},'name':{'type':'string'},'prompt':{'type':'string'},'tags':{'type':'array','items':{'type':'string'},'minItems':1}},'required':['taskId','requestId','confirmed','name','prompt','tags']}}
]

def post(path,body):
 try:
  ensure_server()
  with urlopen(BASE+'/api/status',timeout=120) as response:token=json.load(response)['csrf']
  req=Request(BASE+path,data=json.dumps(body,ensure_ascii=False).encode(),headers={'Content-Type':'application/json','X-Library-Token':token})
  with urlopen(req,timeout=120) as response:return json.load(response)
 except HTTPError as e:
  message=f'HTTP {e.code}: {e.reason}'
  try:
   payload=json.load(e)
   if isinstance(payload,dict) and isinstance(payload.get('error'),str):message=payload['error']
  except (ValueError,OSError):pass
  finally:e.close()
  raise RuntimeError(message) from e
def call(name,a):
 if name=='library_open':
  task_id=a.get('taskId') or os.environ.get('CODEX_THREAD_ID');selection_key(task_id)
  return {'url':ensure_server()+'/?'+urlencode({'task':task_id}),'library':library()['library'],'taskId':task_id}
 if name=='library_selected':return selected(a.get('taskId'))
 if name=='library_search':
  library();return [{'id':i['id'],'name':i['name'],'tags':i['tags']} for i in eagle('item/list',keyword=a['keyword'],folders=a.get('folderId',''),limit=12)]
 if name=='library_save_analysis':
  import analysis_store
  analysis_store.validate(a)
  return post('/api/save-analysis',dict(a,taskId=a.get('taskId') or os.environ.get('CODEX_THREAD_ID')))
 raise ValueError('未知工具')
def mcp():
 for line in sys.stdin:
  try:
   m=json.loads(line);method=m.get('method');rid=m.get('id');params=m.get('params',{})
   if rid is None:continue
   if method=='initialize':result={'protocolVersion':params.get('protocolVersion','2024-11-05'),'capabilities':{'tools':{}},'serverInfo':{'name':'library-hub','version':'1.0.0'}}
   elif method=='tools/list':result={'tools':TOOLS}
   elif method=='ping':result={}
   elif method=='tools/call':
    try:result={'content':[{'type':'text','text':json.dumps(call(params['name'],params.get('arguments',{})),ensure_ascii=False)}]}
    except Exception as e:result={'content':[{'type':'text','text':str(e)}],'isError':True}
   else:
    print(json.dumps({'jsonrpc':'2.0','id':rid,'error':{'code':-32601,'message':'Method not found'}}),flush=True);continue
   print(json.dumps({'jsonrpc':'2.0','id':rid,'result':result},ensure_ascii=False),flush=True)
  except Exception as e:print(str(e),file=sys.stderr,flush=True)
def main():
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['serve','mcp','open','configure','migrate','selected']);a=p.parse_args()
 if a.mode=='serve':serve()
 elif a.mode=='mcp':mcp()
 elif a.mode=='migrate':migrate()
 elif a.mode=='open':print(ensure_server())
 elif a.mode=='configure':print(json.dumps(configure(),ensure_ascii=False))
 else:print(json.dumps(selected(),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
