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
                'dependencies': {'@aria467/dsh-bridge': f'link:{ROOT}', 'other': '1.0'},
                'dsh': {'profile': {'bundles': ['other', '@local/dsh-bridge']}},
            }
            profile.write_text(json.dumps(data), encoding='utf-8')
            with patch.object(module, 'ROOT', ROOT):
                module.ensure_bundle(profile)
                module.ensure_bundle(profile)
            updated = json.loads(profile.read_text(encoding='utf-8'))
            self.assertEqual(updated['dependencies']['other'], '1.0')
            self.assertEqual(updated['dsh']['profile']['bundles'], ['other', '@aria467/dsh-bridge'])
            self.assertTrue(profile.with_name('package.json.dsh-bridge-backup').exists())

    def test_failed_dsh_command_restores_old_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_dir = root / 'desktop'
            profile_dir.mkdir()
            profile = profile_dir / 'package.json'
            profile.write_text('{}', encoding='utf-8')
            link = profile_dir / 'node_modules/@aria467/dsh-bridge'
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

    def test_existing_local_package_is_removed_before_enabling_new_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'package.json'
            data = {
                'dependencies': {'@local/dsh-bridge': 'link:/old/bridge'},
                'dsh': {'profile': {'bundles': ['@local/dsh-bridge', 'other']}},
            }
            profile.write_text(json.dumps(data), encoding='utf-8')
            calls = []

            def fake_run(*args):
                calls.append(args)
                if args[:4] == ('dsh', 'plugin', '--profile', 'desktop'):
                    current = json.loads(profile.read_text(encoding='utf-8'))
                    if args[4] == 'add':
                        current['dependencies'][module.PACKAGE] = f'link:{ROOT}'
                    elif args[4] == 'remove':
                        current['dependencies'].pop(module.LEGACY_PACKAGE)
                    profile.write_text(json.dumps(current), encoding='utf-8')

            with patch.object(module, 'profile_path', return_value=profile), \
                 patch.object(module.platform, 'system', return_value='Darwin'), \
                 patch.object(module.shutil, 'which', return_value='/bin/tool'), \
                 patch.object(module, 'run', side_effect=fake_run), \
                 patch.object(sys, 'argv', ['install.py']):
                module.main()

            updated = json.loads(profile.read_text(encoding='utf-8'))
            self.assertEqual([args[4] for args in calls[:2]], ['add', 'remove'])
            self.assertNotIn(module.LEGACY_PACKAGE, updated['dependencies'])
            self.assertEqual(updated['dsh']['profile']['bundles'], ['other', module.PACKAGE])

    def test_reject_wrong_link_before_editing(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'package.json'
            original = '{"dependencies":{"@aria467/dsh-bridge":"link:/elsewhere"}}'
            profile.write_text(original, encoding='utf-8')
            with self.assertRaises(RuntimeError):
                module.ensure_bundle(profile)
            self.assertEqual(profile.read_text(encoding='utf-8'), original)

    def test_fallback_removes_only_legacy_local_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / 'package.json'
            lock = root / 'pnpm-lock.yaml'
            data = {
                'dependencies': {
                    module.PACKAGE: f'link:{ROOT}',
                    module.LEGACY_PACKAGE: 'link:/old/bridge',
                    'other': '1.0',
                },
                'dsh': {'profile': {'bundles': [module.LEGACY_PACKAGE, 'other']}},
            }
            profile.write_text(json.dumps(data), encoding='utf-8')
            lock.write_text(
                "importers:\n  .:\n    dependencies:\n"
                "      '@local/dsh-bridge':\n"
                "        specifier: link:/old/bridge\n"
                "        version: link:/old/bridge\n"
                "      other:\n        specifier: 1.0\n        version: 1.0\n",
                encoding='utf-8',
            )
            old_link = root / 'node_modules/@local/dsh-bridge'
            old_link.parent.mkdir(parents=True)
            old_link.symlink_to('/old/bridge')

            module.remove_legacy_local_link(profile)

            updated = json.loads(profile.read_text(encoding='utf-8'))
            self.assertEqual(updated['dependencies'], {module.PACKAGE: f'link:{ROOT}', 'other': '1.0'})
            self.assertEqual(updated['dsh']['profile']['bundles'], ['other'])
            self.assertNotIn(module.LEGACY_PACKAGE, lock.read_text(encoding='utf-8'))
            self.assertFalse(old_link.is_symlink())
            self.assertEqual(len(list((root / '.dsh-bridge-migration-backup').glob('*/package.json'))), 1)

    def test_dsh_policy_change_is_restored_after_install(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'package.json'
            policy = Path(directory) / 'pnpm-workspace.yaml'
            policy.write_text('minimumReleaseAge: 1440\n', encoding='utf-8')
            profile.write_text(json.dumps({
                'dependencies': {module.PACKAGE: f'link:{ROOT}'},
                'dsh': {'profile': {'bundles': [module.PACKAGE]}},
            }), encoding='utf-8')

            def fake_run(*args):
                if args[:5] == ('dsh', 'plugin', '--profile', 'desktop', 'add'):
                    policy.write_text('minimumReleaseAge: 1440\nminimumReleaseAgeExclude:\n  - another-package\n', encoding='utf-8')

            with patch.object(module, 'profile_path', return_value=profile), \
                 patch.object(module.platform, 'system', return_value='Darwin'), \
                 patch.object(module.shutil, 'which', return_value='/bin/tool'), \
                 patch.object(module, 'run', side_effect=fake_run), \
                 patch.object(sys, 'argv', ['install.py']):
                module.main()

            self.assertEqual(policy.read_text(encoding='utf-8'), 'minimumReleaseAge: 1440\n')


if __name__ == '__main__':
    unittest.main()
