import importlib.util,pathlib,unittest,tempfile,os
spec=importlib.util.spec_from_file_location('dispatcher',pathlib.Path(__file__).with_name('deepseek_queue_dispatcher.py'))
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
class Validation(unittest.TestCase):
 def test_valid(self):
  self.assertEqual(d.validate({'task':'Fix synthetic NaN test','context_files':'backend/app/estimate_import.py,backend/tests/test_estimate_import.py'})['task'],'Fix synthetic NaN test')
 def test_reject_paths(self):
  for p in ('.github/workflows/a.yml','docs/../../etc/passwd','backend/app/.env','docs/secret.md','/etc/passwd','docs/a.md,docs/a.md'):
   with self.subTest(p=p),self.assertRaises(ValueError):d.validate({'task':'x','context_files':p})
 def test_shape(self):
  for x in ({'task':'x'},{'task':'x','context_files':'docs/a.md','token':'x'},{'task':'x'*4901,'context_files':'docs/a.md'}):
   with self.assertRaises(ValueError):d.validate(x)
 def test_immutable_source_ref(self):
  data={'task':'Fix reviewed PR','context_files':'backend/tests/test_estimates.py','source_ref':'a'*40}
  self.assertEqual(d.validate(data)['source_ref'],'a'*40)
  for ref in ('main','../../etc','a'*40+';id','A'*40):
   with self.subTest(ref=ref),self.assertRaises(ValueError):d.validate(dict(data,source_ref=ref))
 def test_no_symlink(self):
  if os.name=='nt':self.skipTest('Linux queue')
  with tempfile.TemporaryDirectory() as t:
   p=pathlib.Path(t)/('a'*32+'.json');p.symlink_to('/etc/passwd')
   with self.assertRaises(OSError):d.load(p)
if __name__=='__main__':unittest.main()
