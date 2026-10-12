import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bridge_install', ROOT / 'scripts/install.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class InstallTests(unittest.TestCase):
    def test_bundle_enable_preserves_existing_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'package.json'
            data = {
                'dependencies': {'@local/dsh-bridge': f'link:{ROOT}', 'other': '1.0'},
                'dsh': {'profile': {'bundles': ['other']}},
            }
            profile.write_text(json.dumps(data), encoding='utf-8')
            with patch.object(module, 'ROOT', ROOT):
                module.ensure_bundle(profile)
                module.ensure_bundle(profile)
            updated = json.loads(profile.read_text(encoding='utf-8'))
            self.assertEqual(updated['dependencies']['other'], '1.0')
            self.assertEqual(updated['dsh']['profile']['bundles'], ['other', '@local/dsh-bridge'])
            self.assertTrue(profile.with_name('package.json.dsh-bridge-backup').exists())

    def test_failed_dsh_command_restores_old_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_dir = root / 'desktop'
            profile_dir.mkdir()
            profile = profile_dir / 'package.json'
            profile.write_text('{}', encoding='utf-8')
            link = profile_dir / 'node_modules/@local/dsh-bridge'
            link.parent.mkdir(parents=True)
            link.symlink_to('/old/bridge')
            with patch.object(module, 'profile_path', return_value=profile), \
                 patch.object(module.platform, 'system', return_value='Darwin'), \
                 patch.object(module.shutil, 'which', return_value='/bin/tool'), \
                 patch.object(module, 'run', side_effect=lambda *args: (link.unlink(), link.symlink_to('/new/bridge'), (_ for _ in ()).throw(subprocess.CalledProcessError(1, args)))), \
                 patch.object(sys, 'argv', ['install.py']):
                with self.assertRaises(subprocess.CalledProcessError):
                    module.main()
            self.assertEqual(link.readlink(), Path('/old/bridge'))

    def test_reject_wrong_link_before_editing(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'package.json'
            original = '{"dependencies":{"@local/dsh-bridge":"link:/elsewhere"}}'
            profile.write_text(original, encoding='utf-8')
            with self.assertRaises(RuntimeError):
                module.ensure_bundle(profile)
            self.assertEqual(profile.read_text(encoding='utf-8'), original)


if __name__ == '__main__':
    unittest.main()
