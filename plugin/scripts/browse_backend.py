"""Read-only catalog, sorting, multi-axis filters for the local gallery."""
import colorsys,json,re,threading,time
from functools import lru_cache
import color_match

COLORS={'red':'红色','orange':'橙色','yellow':'黄色','green':'绿色','cyan':'青色','blue':'蓝色','purple':'紫色','pink':'粉色','white':'白色','gray':'灰色','black':'黑色'}
SHAPES={'square':'方形','landscape':'横向','portrait':'纵向','panoramic-landscape':'全景横向','panoramic-portrait':'全景纵向'}
_cache=None
_lock=threading.Lock()

def color_bucket(rgb):
 r,g,b=(max(0,min(255,float(c)))/255 for c in rgb)
 h,s,v=colorsys.rgb_to_hsv(r,g,b);h*=360
 if v<.20:return 'black'
 if s<.16:return 'white' if v>.85 else 'gray'
 if h<15 or h>=345:return 'red'
 if h<45:return 'orange'
 if h<70:return 'yellow'
 if h<165:return 'green'
 if h<195:return 'cyan'
 if h<255:return 'blue'
 if h<290:return 'purple'
 return 'pink'

def shape(i):
 w,h=i.get('width') or 0,i.get('height') or 0
 if not w or not h:return None
 ratio=w/h
 if ratio>=2:return 'panoramic-landscape'
 if ratio<=.5:return 'panoramic-portrait'
 if .95<=ratio<=1.05:return 'square'
 return 'landscape' if ratio>1 else 'portrait'

def catalog(hub):
 global _cache
 path=hub.library()['library']['path']
 with _lock:
  if _cache and _cache[0]==path and time.monotonic()-_cache[1]<60:return _cache[2]
  rows=[]
  for i in hub.all_items():
   if i.get('isDeleted'):continue
   row={k:i.get(k) for k in ('id','name','ext','tags','folders','width','height','modificationTime')}
   row['annotation']=(i.get('annotation') or '')[:240]
   row['colors']=sorted({color_bucket(p['color']) for p in i.get('palettes',[]) if len(p.get('color',[]))==3 and p.get('ratio',0)>=5})
   row['palette']=[{'lab':color_match.lab(p['color']),'ratio':float(p['ratio'])} for p in i.get('palettes',[]) if len(p.get('color',[]))==3 and p.get('ratio',0)>0]
   row['shape']=shape(i);rows.append(row)
  _cache=(path,time.monotonic(),rows)
  return rows

@lru_cache(maxsize=4096)
def namesort(s):
 return tuple((0,int(x)) if x.isdigit() else (1,x.casefold()) for x in re.split(r'(\d+)',s or ''))

def browse(hub,q):
 rows=catalog(hub);keyword=q.get('keyword','').strip().casefold()
 chosen={k:json.loads(q.get(k,'[]')) for k in ('tags','folders','colors','shapes')}
 if any(not isinstance(v,list) or len(v)>200 or any(not isinstance(x,str) for x in v) for v in chosen.values()):raise ValueError('无效筛选条件')
 sets={k:set(v) for k,v in chosen.items()}
 targets=[color_match.target(v) for v in chosen['colors']]
 tolerance=max(.02,min(.4,float(q.get('tolerance','15'))/100));minimum=max(1,min(100,float(q.get('coverage','5'))))
 scope=q.get('scope','')
 def match(i):
  if scope and scope not in (i.get('folders') or []):return False
  if targets and not color_match.matches(i['palette'],targets,tolerance,minimum):return False
  if keyword and keyword not in (i.get('name') or '').casefold() and not any(keyword in t.casefold() for t in i.get('tags') or []):return False
  for k in ('tags','folders'):
   if sets[k] and not sets[k].intersection(i.get(k) or []):return False
  return not sets['shapes'] or i['shape'] in sets['shapes']
 filtered=[i for i in rows if match(i)]
 sort=q.get('sort','date');descending=q.get('direction','desc')=='desc'
 if sort not in ('name','date','type'):raise ValueError('不支持的排序')
 key=(lambda i:(i.get('modificationTime') or 0,i['id'])) if sort=='date' else (lambda i:(namesort(i.get('ext') if sort=='type' else i.get('name')),namesort(i.get('name')),i['id']))
 filtered.sort(key=key,reverse=descending)
 if targets:filtered.sort(key=lambda i:color_match.score(i["palette"],targets,tolerance),reverse=True)
 offset=max(0,int(q.get('offset',0)))
 return {'items':[{k:v for k,v in i.items() if k!='palette'} for i in filtered[offset:offset+60]],'total':len(filtered),'offset':offset,'facets':{'tags':sorted({t for i in rows for t in i.get('tags') or []},key=str.casefold),'colors':COLORS,'shapes':SHAPES} if offset==0 else None}
