"""Local video probing, cover extraction and bounded HTTP media streaming."""
import hashlib,io,json,mimetypes,re,shutil,subprocess,threading
from pathlib import Path

VIDEO_FORMATS={'.mp4','.webm','.mov','.mkv','.avi','.m4v','.mpeg','.mpg','.ts','.mts','.m2ts','.ogv'}
_preview_lock=threading.Lock()
_preview_locks={}
class PreviewPending(ValueError):pass
def playback_file(path,cache,prepare=True):
    path=Path(path)
    if not is_video(path):return path
    probe=shutil.which('ffprobe');ffmpeg=shutil.which('ffmpeg')
    if not probe or not ffmpeg:
        if path.suffix.lower() in {'.mp4','.webm'}:return path
        raise ValueError('此视频格式需要 FFmpeg 生成播放预览')
    info=json.loads(run([probe,'-v','error','-show_entries','stream=codec_type,codec_name','-of','json',str(path)]))
    streams=info.get('streams',[]);video=[s['codec_name'] for s in streams if s.get('codec_type')=='video'];audio=[s['codec_name'] for s in streams if s.get('codec_type')=='audio']
    ext=path.suffix.lower()
    if ext=='.mp4' and video and video[0]=='h264' and all(a in {'aac','mp3'} for a in audio):return path
    if ext=='.webm' and video and video[0] in {'vp8','vp9','av1'} and all(a in {'opus','vorbis'} for a in audio):return path
    st=path.stat();key=hashlib.sha256((str(path.resolve())+':'+str(st.st_size)+':'+str(st.st_mtime_ns)).encode()).hexdigest()
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True);out=cache/(key+'.mp4')
    if out.exists():return out
    if not prepare:raise PreviewPending("播放预览尚未生成，请用 GET 请求准备")
    with _preview_lock:lock=_preview_locks.setdefault(key,threading.Lock())
    if not lock.acquire(timeout=2):raise PreviewPending("播放预览正在生成，请稍后重试")
    try:
        if not out.exists():
            temp=out.with_suffix('.part.mp4')
            try:
                run([ffmpeg,'-v','error','-y','-i',str(path),'-map','0:v:0','-map','0:a:0?','-vf','scale=trunc(iw/2)*2:trunc(ih/2)*2','-c:v','libx264','-preset','veryfast','-crf','23','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(temp)],timeout=3600)
                temp.replace(out)
            finally:temp.unlink(missing_ok=True)
    finally:lock.release()
    return out
def is_video(path):return str(path).lower() in VIDEO_FORMATS or Path(path).suffix.lower() in VIDEO_FORMATS
def run(args,timeout=60):
    return subprocess.run(args,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout
def video_picture(path):
    from PIL import Image
    probe=shutil.which('ffprobe');ffmpeg=shutil.which('ffmpeg')
    if not probe or not ffmpeg:raise ValueError('视频索引需要 FFmpeg 和 ffprobe，请安装 FFmpeg 后重试')
    try:
        info=json.loads(run([probe,'-v','error','-select_streams','v:0','-show_entries','stream=width,height:format=duration','-of','json',str(path)]))
        stream=info['streams'][0];duration=float(info.get('format',{}).get('duration') or 0)
        cover=run([ffmpeg,'-v','error','-i',str(path),'-frames:v','1','-vf','scale=600:600:force_original_aspect_ratio=decrease','-f','image2pipe','-vcodec','mjpeg','pipe:1'])
        with Image.open(io.BytesIO(cover)) as im:picture=im.convert('RGB')
        return picture,{'width':stream['width'],'height':stream['height'],'duration':duration,'mediaType':'video'}
    except (subprocess.SubprocessError,KeyError,IndexError,TypeError) as e:raise ValueError('视频无法读取或编码不支持') from e
def byte_range(value,size):
    if not value:return 0,size-1,False
    m=re.fullmatch(r'bytes=(\d*)-(\d*)',value.strip())
    if not m or not any(m.groups()) or not size:raise ValueError('invalid range')
    a,b=m.groups()
    if not a:
        suffix=int(b)
        if suffix<=0:raise ValueError('invalid range')
        start=max(0,size-suffix);end=size-1
    else:
        start=int(a);end=min(int(b),size-1) if b else size-1
        if start>=size or start>end:raise ValueError('invalid range')
    return start,end,True
def serve_media(handler,path):
    handler.connection.settimeout(15)
    path=Path(path);size=path.stat().st_size
    try:start,end,partial=byte_range(handler.headers.get('Range'),size)
    except ValueError:
        handler.send_response(416);handler.send_header('Content-Range',f'bytes */{size}');handler.send_header('Content-Length','0');handler.end_headers();return
    length=max(0,end-start+1)
    handler.send_response(206 if partial else 200)
    handler.send_header('Content-Type',mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
    handler.send_header('Content-Length',str(length));handler.send_header('Accept-Ranges','bytes')
    handler.send_header('Cache-Control','no-store');handler.send_header('X-Content-Type-Options','nosniff')
    if partial:handler.send_header('Content-Range',f'bytes {start}-{end}/{size}')
    handler.end_headers()
    if handler.command=='HEAD':return
    try:
        with path.open('rb') as f:
            f.seek(start)
            while length:
                chunk=f.read(min(length,1024*1024))
                if not chunk:break
                handler.wfile.write(chunk);length-=len(chunk)
    except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError,TimeoutError):pass
