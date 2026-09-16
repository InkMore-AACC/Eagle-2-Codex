import hashlib,shutil,subprocess,sys,tempfile,threading,unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugin/scripts'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from unittest.mock import patch
import video_media
from video_media import serve_media,byte_range,playback_file,PreviewPending
class RangeTests(unittest.TestCase):
 def test_stream_range_head_and_bad_ranges(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'video.mp4';p.write_bytes(bytes(range(256))*8192)
   class Handler(BaseHTTPRequestHandler):
    def do_GET(self):serve_media(self,p)
    do_HEAD=do_GET
    def log_message(self,*args):pass
   server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   try:
    u=f'http://127.0.0.1:{server.server_port}/video'
    for value,expected in [('bytes=10-19',p.read_bytes()[10:20]),('bytes=-16',p.read_bytes()[-16:]),('bytes=2097140-',p.read_bytes()[2097140:])]:
     with urlopen(Request(u,headers={'Range':value})) as r:self.assertEqual(r.status,206);self.assertEqual(r.read(),expected);self.assertEqual(r.headers['Accept-Ranges'],'bytes')
    with urlopen(Request(u,method='HEAD')) as r:self.assertEqual(r.read(),b'');self.assertEqual(int(r.headers['Content-Length']),p.stat().st_size)
    for value in ['bytes=99999999-','bytes=20-10','bytes=-0','bytes=0-1,4-5']:
     with self.assertRaises(HTTPError) as cm:urlopen(Request(u,headers={'Range':value}))
     self.assertEqual(cm.exception.code,416);cm.exception.close()
   finally:server.shutdown();server.server_close();thread.join()
 def test_uncached_head_never_transcodes_and_conversion_has_long_timeout(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'clip.avi';p.write_bytes(b'fixture')
   def fake(args,timeout=60):
    if 'ffprobe' in Path(args[0]).name:return b'{"streams":[{"codec_type":"video","codec_name":"mpeg4"}]}'
    self.assertEqual(timeout,3600);Path(args[-1]).write_bytes(b'preview');return b''
   with patch.object(video_media.shutil,'which',side_effect=lambda x:x),patch.object(video_media,'run',side_effect=fake) as run:
    with self.assertRaises(PreviewPending):playback_file(p,Path(t)/'cache',prepare=False)
    self.assertEqual(run.call_count,1)
    result=playback_file(p,Path(t)/'cache');self.assertTrue(result.exists())
    self.assertEqual(playback_file(p,Path(t)/'cache',prepare=False),result)
 def test_waiting_preview_request_returns_pending_without_another_conversion(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'clip.avi';p.write_bytes(b'fixture');cache=Path(t)/'cache'
   entered=threading.Event();release=threading.Event();calls=[]
   def fake(args,timeout=60):
    if 'ffprobe' in Path(args[0]).name:return b'{"streams":[{"codec_type":"video","codec_name":"mpeg4"}]}'
    calls.append(args);entered.set();release.wait(5);Path(args[-1]).write_bytes(b'preview');return b''
   with patch.object(video_media.shutil,'which',side_effect=lambda x:x),patch.object(video_media,'run',side_effect=fake):
    thread=threading.Thread(target=lambda:playback_file(p,cache));thread.start()
    try:
     self.assertTrue(entered.wait(1))
     with self.assertRaises(PreviewPending):playback_file(p,cache)
     self.assertEqual(len(calls),1)
    finally:release.set();thread.join(3)
    self.assertTrue(playback_file(p,cache,prepare=False).exists())
 def test_clamping_and_zero_file(self):
  self.assertEqual(byte_range('bytes=0-999',10),(0,9,True));self.assertEqual(byte_range(None,0),(0,-1,False))
 @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'requires FFmpeg')
 def test_browser_preview_preserves_original_and_caches(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);p=root/'clip.avi'
   subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=96x64:r=12','-t','1','-c:v','mpeg4',str(p)],check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
   before=hashlib.sha256(p.read_bytes()).hexdigest();preview=playback_file(p,root/'cache')
   self.assertEqual(preview.suffix,'.mp4');self.assertNotEqual(preview,p);self.assertTrue(preview.is_file())
   self.assertEqual(playback_file(p,root/'cache'),preview);self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),before)
   self.assertEqual(playback_file(preview,root/'other-cache'),preview)
if __name__=='__main__':unittest.main()
