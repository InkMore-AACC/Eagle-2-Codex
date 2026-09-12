"""Confirmed image-analysis saves, with durable retry records outside the plugin."""
import base64
import hashlib
import json
import mimetypes
import re
import time
from pathlib import Path


def validate(body):
    if body.get('confirmed') is not True:
        raise ValueError('请先询问用户是否将这些图片、名称、标签和反推提示词保存到 Eagle，确认后才能写入。')
    request_id = body.get('requestId', '')
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', request_id):
        raise ValueError('需要唯一 requestId；重试同一次保存时保持不变。')
    name = body.get('name')
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 180:
        raise ValueError('需要不超过180字的图片名称。')
    name = name.strip()
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith('.') or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', name):
        raise ValueError('名称包含文件名不支持的字符。')
    prompt = body.get('prompt')
    tags = body.get('tags')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 100000:
        raise ValueError('反推提示词不能为空，最多100000字。')
    if not isinstance(tags, list) or not 1 <= len(tags) <= 200 or any(not isinstance(t, str) or not t.strip() or len(t) > 200 for t in tags):
        raise ValueError('需要有效的分类标签。')
    if bool(body.get('itemId')) == bool(body.get('imagePath')):
        raise ValueError('itemId 与 imagePath 必须提供且只能提供一个。')
    return name, prompt.strip(), list(dict.fromkeys(t.strip() for t in tags))


def _wait_item(hub, item_id):
    error = None
    for _ in range(30):
        try:
            row = hub.item(item_id)
            if row and not row.get('isDeleted'):
                return row
        except Exception as exc:
            error = exc
        time.sleep(.2)
    raise RuntimeError('Eagle 素材暂时无法回读，请保留 requestId 再试。') from error


def _rename(hub, item_id, name):
    if hub.item(item_id).get('name') != name:
        hub.eagle('v2/item/update', {'id': item_id, 'name': name})
    for _ in range(30):
        row = hub.item(item_id)
        if row.get('name') == name:
            return row
        time.sleep(.2)
    raise RuntimeError('名称保存后回读不一致，请检查 Eagle 中的素材。')


def save(hub, body):
    name, prompt, tags = validate(body)
    hub.selection_key(body.get('taskId'))
    with hub.LOCK:
        lib = hub.library()
        scope = hashlib.sha256(lib['library']['path'].encode()).hexdigest()[:20]
        key = 'analysis-requests/' + scope + '/' + body['requestId'] + '.json'
        signature = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        record = hub.read(key)
        if record and record.get('signature') != signature:
            raise ValueError('这个 requestId 已用于另一份保存内容，请勿复用。')
        if record and record.get('result'):
            result = record['result']
            rows = [hub.item(r['id'], lib) for r in result['items']]
            unchanged = all(not row.get('isDeleted') and all(row.get(k) == old.get(k) for k in ('name', 'tags', 'annotation', 'folders')) for row, old in zip(rows, result['items']))
            if unchanged and record.get('imageHash'):
                unchanged = hashlib.sha256(hub.file_for(rows[0], lib=lib).read_bytes()).hexdigest() == record['imageHash']
            return dict(result, replayed=True, verified=unchanged, items=[hub.brief(r) for r in rows],
                        message='此前保存已完成，未重复写入。' if unchanged else '此前已保存，但素材后来发生变化；本次未覆盖，请重新读取。')
        if record and not record.get('importId'):
            raise RuntimeError('上次保存中断，结果尚未确认；请先核对 Eagle，勿重复导入或覆盖。')

        image_bytes = None
        if body.get('itemId'):
            item_id = body['itemId']
            if item_id not in hub.selection_state(body['taskId'], lib).get('ids', []):
                raise ValueError('只能更新当前任务明确选中的 Eagle 素材。')
            row = hub.item(item_id, lib)
            expected = body.get('expected')
            if not isinstance(expected, dict) or any(k not in expected or expected[k] != row.get(k) for k in ('name', 'tags', 'annotation')):
                raise ValueError('名称、标签或提示词已变化，请重新读取后再确认保存。')
            if row.get('isDeleted'):
                raise ValueError('素材已移入废纸篓。')
            copy_id = hub.read('copies.json', {}).get(item_id)
            if copy_id and copy_id != item_id:
                copied = hub.item(copy_id, lib)
                if copied.get('isDeleted') or hub.folder_id() not in copied.get('folders', []):
                    raise ValueError('参考副本已删除或移出参考文件夹，请先核对 Eagle 中的副本。')
                if any(copied.get(k) != row.get(k) for k in ('annotation', 'tags')):
                    raise ValueError('参考副本与原素材的注释或标签不同，请先核对，避免覆盖独立编辑。')
                if copied.get('name') not in (row['name'], row['name']+' · 参考 '+item_id):
                    raise ValueError('参考副本名称已独立修改，请先核对，避免覆盖。')
        else:
            path = Path(body['imagePath'])
            if not path.is_absolute():
                raise ValueError('外部图片需要真实的本地绝对路径。')
            path = path.resolve(strict=True)
            mime = mimetypes.guess_type(path.name)[0] or ''
            if not path.is_file() or not mime.startswith('image/'):
                raise ValueError('请选择本地图片文件。')
            image_bytes = path.read_bytes()
            if not image_bytes:
                raise ValueError('图片文件为空。')
            digest = hashlib.sha256(image_bytes).hexdigest()
            if record and record.get('imageHash') != digest:
                raise ValueError('源图片已变化，请核对上次导入结果。')

        if not record:
            record = {'signature': signature, 'startedAt': time.time()}
            if image_bytes is not None:
                record['imageHash'] = digest
            hub.write(key, record)
        try:
            if image_bytes is None:
                result = hub.save_analysis(item_id, prompt, tags)
                ids = list(dict.fromkeys([result['sourceId'], result['referenceId']]))
                rows = [_rename(hub, x, name) for x in ids]
            else:
                fid = hub.folder_id()
                if not record.get('importId'):
                    # Record intent before submission. An ambiguous failure never imports twice.
                    added = hub.eagle('v2/item/add', {
                        'base64': 'data:' + mime + ';base64,' + base64.b64encode(image_bytes).decode(),
                        'name': name, 'tags': tags, 'folders': [fid],
                        'annotation': '【图片分析提示词｜非作者原始提示词】\n' + prompt,
                    })
                    record['importId'] = added['id']
                    hub.write(key, record)
                row = _wait_item(hub, record['importId'])
                if fid not in row.get('folders', []):
                    raise RuntimeError('导入素材的文件夹回读不一致。')
                if hashlib.sha256(hub.file_for(row).read_bytes()).hexdigest() != digest:
                    raise RuntimeError('导入后的原图与源图片不一致。')
                rows = [row]
                result = {'sourceId': row['id'], 'referenceId': row['id']}
            annotation = '【图片分析提示词｜非作者原始提示词】\n' + prompt
            if any(r.get('isDeleted') or r.get('name') != name or r.get('annotation') != annotation or set(r.get('tags', [])) != set(tags) for r in rows):
                raise RuntimeError('名称、标签或反推提示词回读不一致，请检查 Eagle。')
            if hub.library()['library']['path'] != lib['library']['path']:
                raise RuntimeError('保存期间 Eagle 切换了资源库，请核对保存结果。')
            result.update(verified=True, requestId=body['requestId'], items=[hub.brief(r) for r in rows])
            record['result'] = result
            hub.write(key, record)
            return result
        finally:
            import browse_backend
            with browse_backend._lock:
                browse_backend._cache = None
