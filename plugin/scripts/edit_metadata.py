"""Explicit field saves through Eagle with read-back and stale-edit protection."""
import re,time
def save(hub,body):
 field=body.get('field');value=body.get('value')
 if field not in ('name','tags','annotation'):raise ValueError('仅支持名称、标签、提示词')
 if field=='tags':
  if not isinstance(value,list) or len(value)>200 or any(not isinstance(t,str) or not t.strip() or len(t)>200 for t in value):raise ValueError('标签格式无效')
  value=list(dict.fromkeys(t.strip() for t in value))
 else:
  if not isinstance(value,str) or len(value)>100000:raise ValueError('内容格式无效或过长')
  if field=='name':
   value=value.strip()
   if not value or len(value)>180 or re.search(r'[<>:"/\\|?*\x00-\x1f]',value) or value.endswith('.') or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',value):raise ValueError('名称为空、过长或包含文件名不支持的字符')
 with hub.LOCK:
  i=hub.item(body.get('id',''))
  if 'expected' not in body or i.get(field)!=body['expected']:raise ValueError('这项内容已在别处修改，请关闭并重新打开后编辑。')
  hub.eagle('v2/item/update' if field=='name' else 'item/update',{'id':i['id'],field:value})
  updated=hub.item(i['id'])
  for _ in range(15):
   if updated.get(field)==value:break
   time.sleep(.2)
   updated=hub.item(i['id'])
  import browse_backend
  with browse_backend._lock:browse_backend._cache=None
  if updated.get(field)!=value:raise RuntimeError('Eagle 回读与提交内容不一致，请重新打开检查。')
  return hub.brief(updated)
