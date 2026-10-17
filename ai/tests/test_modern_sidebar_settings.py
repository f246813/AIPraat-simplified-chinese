import json
from pathlib import Path
import tempfile
import unittest
from praat_ai.modern_settings import SettingsService


class SidebarSettingsTests(unittest.TestCase):
    def test_cleared_time_group_survives_reopen_and_other_preference_saves(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'config.json'
            retained=[{'mode':'project','origin':{'id':'project:local','label':'其他聊天','detail':''},'group':'today'}]
            SettingsService(path).save({'preferences':{'sidebar_retained_time_groups':retained}})
            reopened=SettingsService(path)
            self.assertEqual(reopened.get()['preferences']['sidebar_retained_time_groups'],retained)
            reopened.save({'preferences':{'theme':'dark'}})
            self.assertEqual(SettingsService(path).get()['preferences']['sidebar_retained_time_groups'],retained)

    def test_grouping_survives_reopen_and_preserves_sort_and_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'config.json'
            path.write_text(json.dumps({'api':{'api_key':'fixture-key'},'modern':{'preferences':{'sidebar_sort':'name'}}}),encoding='utf8')
            service=SettingsService(path)
            self.assertEqual(service.get()['preferences'].get('sidebar_grouping'),'project')
            for grouping in ['list','project','connection']:
                service.save({'preferences':{'sidebar_grouping':grouping}})
                restored=SettingsService(path).get()['preferences']
                self.assertEqual(restored['sidebar_grouping'],grouping)
                self.assertEqual(restored['sidebar_sort'],'name')
                self.assertEqual(json.loads(path.read_text(encoding='utf8'))['api']['api_key'],'fixture-key')
            for grouping in ['unknown',None,True,{},[]]:
                before=path.read_bytes()
                with self.assertRaises(ValueError):service.save({'preferences':{'sidebar_grouping':grouping}})
                self.assertEqual(path.read_bytes(),before)

    def test_sort_survives_service_reopen_and_preserves_other_preferences_and_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'config.json'
            path.write_text(json.dumps({'api':{'api_key':'fixture-key'},'modern':{'preferences':{'sidebar_width':332,'theme':'dark'}}}),encoding='utf8')
            service=SettingsService(path)
            self.assertEqual(service.get()['preferences']['sidebar_sort'],'recent')
            for sort in ['recent','oldest','name','created']:
                service.save({'preferences':{'sidebar_sort':sort}})
                restored=SettingsService(path).get()['preferences']
                self.assertEqual(restored['sidebar_sort'],sort)
                self.assertEqual(restored['sidebar_width'],332)
                self.assertEqual(restored['theme'],'dark')
                self.assertEqual(json.loads(path.read_text(encoding='utf8'))['api']['api_key'],'fixture-key')

    def test_invalid_sorts_are_rejected_before_any_configuration_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'config.json'
            path.write_text('{}',encoding='utf8')
            for sort in ['manual','unknown',None,True,[],{}]:
                with self.subTest(sort=sort):
                    with self.assertRaises(ValueError):
                        SettingsService(path).save({'preferences':{'sidebar_sort':sort}})
                    self.assertEqual(path.read_text(encoding='utf8'),'{}')


if __name__=='__main__': unittest.main()
