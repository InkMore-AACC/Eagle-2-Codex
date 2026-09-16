import base64
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE=Path(os.environ.get('EAGLE_TEST_SOURCE',str(Path(__file__).resolve().parents[1]/'plugin')))
sys.path.insert(0,str(SOURCE/'scripts'))
_state=tempfile.TemporaryDirectory()
os.environ['LIBRARY_HUB_DATA']=_state.name
import hub, analysis_store, browse_backend


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)
        self.source=self.path/'outside.png';self.source.write_bytes(b'example image bytes')
        self.row={'id':'one','name':'Original','ext':'png','tags':['old'],'annotation':'old','folders':['ref']}
        self.rows={'one':copy.deepcopy(self.row)};self.calls=[]
        def api(endpoint,body=None,**params):
            self.calls.append(endpoint)
            if endpoint=='library/info':return {'library':{'path':self.tmp.name}}
            if endpoint=='folder/list':return [{'id':'ref','name':'image2.5参考','children':[]}]
            if endpoint=='item/info':return copy.deepcopy(self.rows[params['id']])
            if endpoint in ('item/update','v2/item/update'):
                self.rows[body['id']].update(body);return None
            if endpoint=='v2/item/add':
                id='import'+str(len(self.rows))
                self.rows[id]=dict(self.row,**{k:v for k,v in body.items() if k!='base64'},id=id)
                (self.path/(id+'.png')).write_bytes(base64.b64decode(body['base64'].split(',',1)[1]))
                return {'id':id}
            raise AssertionError(endpoint)
        for p in (patch.object(hub,'STATE',self.path/'state'),patch.object(hub,'eagle',side_effect=api),
                  patch.object(hub,'file_for',side_effect=lambda row,**_: self.path/(row['id']+'.png'))):
            p.start();self.addCleanup(p.stop)
        hub.set_selection('analysis-task-123',['one']);self.calls.clear()
        self.body={'taskId':'analysis-task-123','requestId':'request-test-123','confirmed':True,
                   'name':'蓝色运动海报','prompt':'反推提示词','tags':['海报','运动'],
                   'itemId':'one','expected':{k:self.row[k] for k in ('name','tags','annotation')}}

    def external(self):
        body=dict(self.body,imagePath=str(self.source));body.pop('itemId');body.pop('expected');return body

    def test_unconfirmed_rejected_before_any_write(self):
        for value in (None,False,'true',1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):analysis_store.save(hub,dict(self.body,confirmed=value))
        self.assertEqual(self.calls,[])
        self.assertFalse((hub.STATE/'analysis-requests').exists())

    def test_updates_name_tags_and_long_prompt(self):
        self.body['prompt']='长'*75000
        result=analysis_store.save(hub,self.body)
        self.assertTrue(result['verified'])
        self.assertEqual(self.rows['one']['name'],self.body['name'])
        self.assertEqual(self.rows['one']['tags'],self.body['tags'])
        self.assertTrue(self.rows['one']['annotation'].endswith(self.body['prompt']))

    def test_import_preserves_source_and_replay_is_not_duplicate(self):
        body=self.external();before=self.source.read_bytes()
        result=analysis_store.save(hub,body)
        self.assertTrue(result['verified']);self.assertEqual(self.source.read_bytes(),before)
        self.rows[result['sourceId']]['annotation']='later manual edit'
        replay=analysis_store.save(hub,body)
        self.assertTrue(replay['replayed']);self.assertEqual(self.calls.count('v2/item/add'),1)
        self.assertFalse(replay['verified'])
        self.assertEqual(self.rows[result['sourceId']]['annotation'],'later manual edit')

    def test_conflicting_metadata_or_selection_prevents_write(self):
        self.rows['one']['name']='changed elsewhere'
        with self.assertRaises(ValueError):analysis_store.save(hub,self.body)
        self.assertNotIn('item/update',self.calls)
        self.rows['one']['name']=self.row['name'];hub.set_selection('analysis-task-123',[])
        with self.assertRaises(ValueError):analysis_store.save(hub,self.body)
        self.assertNotIn('item/update',self.calls)

    def test_same_request_id_different_content_rejected(self):
        analysis_store.save(hub,self.body)
        with self.assertRaises(ValueError):analysis_store.save(hub,dict(self.body,prompt='different'))

    def test_deleted_or_independently_edited_copy_prevents_all_writes(self):
        self.rows['one']['folders']=['outside']
        hub.write('copies.json',{'one':'copy'})
        for change in ({'isDeleted':True},{'annotation':'edited copy'},{'folders':['moved']},{'name':'independent name'}):
            with self.subTest(change=change):
                self.rows['copy']=dict(self.row,**change,id='copy')
                with self.assertRaises(ValueError):analysis_store.save(hub,self.body)
                self.assertNotIn('item/update',self.calls)
                self.assertNotIn('v2/item/update',self.calls)

    def test_ambiguous_import_failure_does_not_import_again(self):
        body=self.external()
        real=hub.eagle
        def fail(endpoint,*args,**kwargs):
            if endpoint=='v2/item/add':raise OSError('response lost')
            return real(endpoint,*args,**kwargs)
        with patch.object(hub,'eagle',side_effect=fail) as api:
            with self.assertRaises(OSError):analysis_store.save(hub,body)
            with self.assertRaisesRegex(RuntimeError,'上次保存中断'):analysis_store.save(hub,body)
            self.assertEqual(sum(c.args[0]=='v2/item/add' for c in api.call_args_list),1)

    def test_pending_import_can_finish_readback_without_reimport(self):
        body=self.external()
        with patch.object(analysis_store,'_wait_item',side_effect=OSError('not ready')):
            with self.assertRaises(OSError):analysis_store.save(hub,body)
        result=analysis_store.save(hub,body)
        self.assertTrue(result['verified']);self.assertEqual(self.calls.count('v2/item/add'),1)

    def test_video_rejected_without_metadata_or_receipt_write(self):
        self.rows['one']['ext']='mp4';before=copy.deepcopy(self.rows)
        with self.assertRaisesRegex(ValueError,'不接受视频'):analysis_store.save(hub,self.body)
        self.assertEqual(before,self.rows);self.assertNotIn('item/update',self.calls)
        self.assertFalse((hub.STATE/'analysis-requests').exists())

    def test_rename_failure_rolls_back_prompt_tags_and_name(self):
        before=copy.deepcopy(self.rows)
        with patch.object(analysis_store,'_rename',side_effect=OSError('rename failed')):
            with self.assertRaises(OSError):analysis_store.save(hub,self.body)
        self.assertEqual(self.rows,before)

    def test_final_receipt_failure_restores_metadata(self):
        before=copy.deepcopy(self.rows);real=hub.write
        def fail(key,value):
            if key.startswith('analysis-requests/') and value.get('result'):raise OSError('receipt failed')
            return real(key,value)
        with patch.object(hub,'write',side_effect=fail):
            with self.assertRaises(OSError):analysis_store.save(hub,self.body)
        self.assertEqual(self.rows,before)

    def test_rollback_preserves_independent_edit(self):
        before=copy.deepcopy(self.rows)
        def fail(*args):
            self.rows['one']['annotation']='concurrent manual edit'
            raise OSError('rename failed')
        with patch.object(analysis_store,'_rename',side_effect=fail):
            with self.assertRaises(OSError):analysis_store.save(hub,self.body)
        self.assertEqual(self.rows['one']['annotation'],'concurrent manual edit')
        self.assertEqual(self.rows['one']['tags'],before['one']['tags'])

    def test_reference_copy_failure_restores_source(self):
        self.rows['one']['folders']=['outside'];before=copy.deepcopy(self.rows)
        with patch.object(hub,'all_items',return_value=[]),patch.object(hub,'file_for',side_effect=OSError('copy failed')):
            with self.assertRaises(OSError):analysis_store.save(hub,self.body)
        self.assertEqual(self.rows,before)

    def test_unregistered_edited_copy_rejected_before_source_write(self):
        self.rows['one']['folders']=['outside']
        self.rows['copy']=dict(self.row,id='copy',name='Original · 参考 one',annotation='manual copy edit')
        before=copy.deepcopy(self.rows)
        with patch.object(hub,'all_items',return_value=[self.rows['copy']]):
            with self.assertRaisesRegex(ValueError,'拒绝自动覆盖'):analysis_store.save(hub,self.body)
        self.assertEqual(self.rows,before);self.assertNotIn('item/update',self.calls)

    def test_unregistered_matching_copy_never_reaches_rename(self):
        self.rows['one']['folders']=['outside']
        self.rows['copy']=dict(self.row,id='copy',name='Original · 参考 one')
        before=copy.deepcopy(self.rows)
        with patch.object(hub,'all_items',return_value=[self.rows['copy']]),patch.object(analysis_store,'_rename') as rename:
            with self.assertRaisesRegex(ValueError,'拒绝自动覆盖'):analysis_store.save(hub,self.body)
            rename.assert_not_called()
        self.assertEqual(self.rows,before)

    def test_invalid_inputs_do_not_write(self):
        for extra in ({'name':'bad/name'},{'tags':[]},{'imagePath':str(self.source)}, {'prompt':''}):
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):analysis_store.save(hub,dict(self.body,**extra))
        self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
