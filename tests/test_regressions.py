import copy, json, os, sys, tempfile, threading, time, unittest
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

SOURCE=Path(os.environ.get('EAGLE_TEST_SOURCE',str(Path(__file__).resolve().parents[1]/'plugin')))
sys.path.insert(0,str(SOURCE/'scripts'))
_state=tempfile.TemporaryDirectory()
os.environ['LIBRARY_HUB_DATA']=_state.name
import hub, browse_backend, edit_metadata

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.state=patch.object(hub,'STATE',Path(self.tmp.name));self.state.start();self.addCleanup(self.state.stop)
        self.row={'id':'one','name':'one','ext':'png','tags':['old'],'annotation':'old','folders':['ref'],'isDeleted':False}
        self.api_calls=[]
        def eagle(endpoint,body=None,**params):
            self.api_calls.append(endpoint)
            if endpoint=='library/info':return {'library':{'path':self.tmp.name}}
            if endpoint=='item/info':return dict(copy.deepcopy(self.row),id=params['id'])
            if endpoint=='folder/list':return [{'id':'ref','name':'image2.5参考','children':[]}]
            if endpoint in ('item/update','v2/item/update'):self.row.update(body);return None
            raise AssertionError(endpoint)
        self.eagle=patch.object(hub,'eagle',side_effect=eagle);self.eagle.start();self.addCleanup(self.eagle.stop)
        browse_backend._cache=None

    def test_analysis_invalidates_catalog(self):
        browse_backend._cache=('stale',0,[])
        hub.save_analysis('one','new analysis',['new'])
        self.assertIsNone(browse_backend._cache)
        self.assertEqual(self.row['tags'],['new'])

    def test_analysis_failure_invalidates_catalog(self):
        browse_backend._cache=('stale',0,[])
        with patch.object(hub,'write',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):hub.save_analysis('one','new analysis',['new'])
        self.assertIsNone(browse_backend._cache)

    def test_long_chinese_prompt_http(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),hub.Handler);server.csrf='test-token'
        with patch.object(hub,'PORT',server.server_port),patch.object(hub,'BASE',f'http://127.0.0.1:{server.server_port}'):
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                prompt='中'*75000
                data=json.dumps({'id':'one','field':'annotation','expected':'old','value':prompt},ensure_ascii=False).encode()
                request=Request(hub.BASE+'/api/edit',data=data,headers={'Content-Type':'application/json','X-Library-Token':'test-token'})
                try:
                    with urlopen(request) as response:self.assertEqual(json.load(response)['annotation'],prompt)
                except HTTPError as e:
                    code=e.code;e.close();self.fail(f'valid 75000-character prompt rejected: {code}')
            finally:server.shutdown();server.server_close();thread.join()

    def test_stale_edit_does_not_write(self):
        with self.assertRaises(ValueError):edit_metadata.save(hub,{'id':'one','field':'annotation','expected':'outdated','value':'new'})
        self.assertEqual(self.row['annotation'],'old')
        self.assertNotIn('item/update',self.api_calls)

    def test_edit_partial_failure_invalidates_cache(self):
        browse_backend._cache=('stale',0,[])
        with patch.object(hub,'item',side_effect=[copy.deepcopy(self.row),OSError('lost response')]):
            with self.assertRaises(OSError):edit_metadata.save(hub,{'id':'one','field':'annotation','expected':'old','value':'new'})
        self.assertIsNone(browse_backend._cache)
        self.assertEqual(self.row['annotation'],'new')

    def test_selection_only_validates_additions(self):
        hub.set_selection('test-task-123',['one']);self.api_calls.clear()
        hub.set_selection('test-task-123',['one','two'])
        self.assertEqual(self.api_calls.count('item/info'),1)
        self.assertEqual(self.api_calls.count('library/info'),2)
        self.api_calls.clear();hub.set_selection('test-task-123',['two'])
        self.assertNotIn('item/info',self.api_calls)

    def test_selected_reads_latest_with_bounded_library_checks(self):
        ids=['item'+str(n) for n in range(20)]
        hub.set_selection('test-task-123',ids);self.api_calls.clear();self.row['annotation']='latest'
        with patch.object(hub,'file_for',return_value=Path(self.tmp.name)/'image.png'):
            result=hub.selected('test-task-123')
        self.assertEqual([i['id'] for i in result['items']],ids)
        self.assertTrue(all(i['annotation']=='latest' for i in result['items']))
        self.assertEqual(self.api_calls.count('item/info'),20)
        self.assertEqual(self.api_calls.count('library/info'),2)

    def test_analysis_mcp_uses_single_service_owner(self):
        with patch.object(hub,'post',return_value={'verified':True}) as post:
            result=hub.call('library_save_analysis',{'taskId':'test-task-123','itemId':'one','prompt':'new','tags':['x'],'name':'name','confirmed':True,'requestId':'request-test-123'})
        self.assertTrue(result['verified']);self.assertEqual(post.call_args.args[0],'/api/save-analysis')

    def test_mcp_preserves_business_error(self):
        import io
        error=HTTPError(hub.BASE,400,'Bad Request',{},io.BytesIO(json.dumps({'error':'只能保存面板当前选中素材的分析'},ensure_ascii=False).encode()))
        with patch.object(hub,'ensure_server'),patch.object(hub,'urlopen',side_effect=[io.BytesIO(b'{"csrf":"test"}'),error]):
            with self.assertRaisesRegex(RuntimeError,'只能保存面板'):hub.post('/api/save-analysis',{})

    def test_mcp_non_json_error_retains_status(self):
        import io
        error=HTTPError(hub.BASE,502,'Bad Gateway',{},io.BytesIO(b'upstream failed'))
        with patch.object(hub,'ensure_server'),patch.object(hub,'urlopen',side_effect=[io.BytesIO(b'{"csrf":"test"}'),error]):
            with self.assertRaisesRegex(RuntimeError,'HTTP 502'):hub.post('/api/save-analysis',{})

    def test_mcp_preserves_status_get_error(self):
        import io
        error=HTTPError(hub.BASE,400,'Bad Request',{},io.BytesIO(json.dumps({'error':'Eagle 已切换资源库'},ensure_ascii=False).encode()))
        with patch.object(hub,'ensure_server'),patch.object(hub,'urlopen',side_effect=error):
            with self.assertRaisesRegex(RuntimeError,'Eagle 已切换资源库'):hub.post('/api/save-analysis',{})

    def test_concurrent_analysis_creates_one_copy(self):
        from concurrent.futures import ThreadPoolExecutor
        records={'one':dict(self.row,folders=['outside'])};copies=[]
        def api(endpoint,body=None,**params):
            if endpoint=='library/info':return {'library':{'path':self.tmp.name}}
            if endpoint=='item/info':return copy.deepcopy(records[params['id']])
            if endpoint=='folder/list':return [{'id':'ref','name':'image2.5参考','children':[]}]
            if endpoint=='item/update':records[body['id']].update(body);return None
            if endpoint=='v2/item/add':
                time.sleep(.03);id='copy'+str(len(copies));row=dict(self.row,**{k:v for k,v in body.items() if k in self.row});row['id']=id
                records[id]=row;copies.append(id);return {'id':id}
            raise AssertionError(endpoint)
        image=Path(self.tmp.name)/'source.png';image.write_bytes(b'test bytes')
        with patch.object(hub,'eagle',side_effect=api),patch.object(hub,'all_items',side_effect=lambda **_: [copy.deepcopy(records[i]) for i in copies]),patch.object(hub,'file_for',return_value=image):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:hub.save_analysis('one','new',['x']),range(2)))
        self.assertEqual(len(copies),1);self.assertEqual(results[0]['referenceId'],results[1]['referenceId'])
        self.assertEqual(hub.read('copies.json')['one'],copies[0])

    def test_recovered_copy_gets_current_analysis(self):
        records={'one':dict(self.row,folders=['outside']),'copy':dict(self.row,id='copy',name='one · 参考 one')}
        def api(endpoint,body=None,**params):
            if endpoint=='library/info':return {'library':{'path':self.tmp.name}}
            if endpoint=='folder/list':return [{'id':'ref','name':'image2.5参考','children':[]}]
            if endpoint=='item/info':return copy.deepcopy(records[params['id']])
            if endpoint=='item/update':records[body['id']].update(body);return None
            raise AssertionError(endpoint)
        with patch.object(hub,'eagle',side_effect=api),patch.object(hub,'all_items',return_value=[copy.deepcopy(records['copy'])]):
            result=hub.save_analysis('one','current',['current'])
        self.assertTrue(result['verified']);self.assertIn('current',records['copy']['annotation'])

    def test_selection_isolation_and_no_six_limit(self):
        ids=['item'+str(n) for n in range(20)]
        hub.set_selection('test-task-123',ids)
        self.assertEqual(hub.selection_state('test-task-123')['ids'],ids)
        self.assertEqual(hub.selection_state('other-task-123')['ids'],[])
        hub.set_selection('test-task-123',[])
        self.assertEqual(hub.selection_state('test-task-123')['ids'],[])

if __name__=='__main__':unittest.main()
