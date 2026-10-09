import sys
import unittest
from pathlib import Path
import yaml
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'deploy'))
from compose_config import production_compose
from compose_release import validate_compose

class RuntimeResourcesTests(unittest.TestCase):
    def test_published_combined_worker_has_media_limits_and_no_host_mounts(self):
        images={kind:f'docker.io/yunyunjuan/treasure-up-{kind}@sha256:'+'a'*64 for kind in ('backend','web','setup')}
        config=production_compose(ROOT,images)
        config['x-treasure-release']={}
        validate_compose(config)
        worker=config['services']['worker']
        self.assertEqual(worker['cpus'],2.0)
        self.assertEqual(worker['mem_limit'],'2g')
        self.assertEqual(worker['memswap_limit'],worker['mem_limit'])
        self.assertEqual(worker['pids_limit'],256)
        self.assertIn('scratch:/data/scratch',worker['volumes'])
        self.assertEqual(worker['healthcheck']['interval'],'120s')

    def test_every_source_worker_is_bounded_and_scratch_can_leave_docker_disk(self):
        config=yaml.safe_load((ROOT/'compose.yaml').read_text(encoding='utf-8'))
        for name in ('collector','media-worker','download-worker','backup-worker'):
            worker=config['services'][name]
            with self.subTest(worker=name):
                self.assertGreater(worker['cpus'],0)
                self.assertEqual(worker['mem_limit'],worker['memswap_limit'])
                self.assertLessEqual(worker['pids_limit'],256)
                self.assertIn('${TREASURE_SCRATCH_PATH:-scratch}:/data/scratch',worker['volumes'])
        light=yaml.safe_load((ROOT/'compose.light.yaml').read_text(encoding='utf-8'))['services']['worker']
        self.assertEqual(light['mem_limit'],'2g')
        self.assertEqual(light['memswap_limit'],'2g')

if __name__=='__main__': unittest.main()
