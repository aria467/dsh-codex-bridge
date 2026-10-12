#!/usr/bin/env python3
"""Install the DSH package and the Codex plugin from this checkout on macOS."""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = '@aria467/dsh-bridge'
LEGACY_PACKAGE = '@local/dsh-bridge'
PROFILE = 'desktop'


def run(*args):
    print('+', ' '.join(map(str, args)), flush=True)
    subprocess.run(args, check=True)


def profile_path():
    return Path.home() / '.dsh/profiles' / PROFILE / 'package.json'


def ensure_bundle(profile_file):
    with profile_file.open(encoding='utf-8') as stream:
        data = json.load(stream)
    dependencies = data.get('dependencies', {})
    expected = f'link:{ROOT}'
    if dependencies.get(PACKAGE) != expected:
        raise RuntimeError(f'DSH did not install {PACKAGE} from {ROOT}; found {dependencies.get(PACKAGE)!r}')
    profile = data.setdefault('dsh', {}).setdefault('profile', {})
    bundles = profile.setdefault('bundles', [])
    if PACKAGE not in bundles or LEGACY_PACKAGE in bundles:
        backup = profile_file.with_name('package.json.dsh-bridge-backup')
        shutil.copy2(profile_file, backup)
        bundles[:] = [name for name in bundles if name != LEGACY_PACKAGE]
        if PACKAGE not in bundles:
            bundles.append(PACKAGE)
        temp = profile_file.with_name('package.json.dsh-bridge-tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temp.replace(profile_file)
        print(f'Enabled DSH bundle; previous profile saved at {backup}')
    return data


def remove_legacy_local_link(profile_file):
    """Remove only the old local-link importer entry if DSH's remove command fails."""
    lock_file = profile_file.with_name('pnpm-lock.yaml')
    data = json.loads(profile_file.read_text(encoding='utf-8'))
    old_spec = data.get('dependencies', {}).get(LEGACY_PACKAGE)
    if not isinstance(old_spec, str) or not old_spec.startswith('link:'):
        raise RuntimeError(f'cannot migrate non-local legacy dependency: {old_spec!r}')
    if data.get('dependencies', {}).get(PACKAGE) != f'link:{ROOT}':
        raise RuntimeError('new DSH package was not installed; legacy package remains active')
    lock = lock_file.read_text(encoding='utf-8')
    old_entry = re.compile(r"(?m)^      '@local/dsh-bridge':\n(?:        [^\n]*\n)+")
    if len(old_entry.findall(lock)) != 1:
        raise RuntimeError('could not identify the legacy local-link entry in pnpm-lock.yaml')
    updated_lock = old_entry.sub('', lock)
    if LEGACY_PACKAGE in updated_lock:
        raise RuntimeError('legacy package appears elsewhere in pnpm-lock.yaml')

    backup = profile_file.parent / '.dsh-bridge-migration-backup' / uuid.uuid4().hex
    backup.mkdir(parents=True)
    shutil.copy2(profile_file, backup / profile_file.name)
    shutil.copy2(lock_file, backup / lock_file.name)
    data['dependencies'].pop(LEGACY_PACKAGE)
    bundles = data.get('dsh', {}).get('profile', {}).get('bundles', [])
    bundles[:] = [name for name in bundles if name != LEGACY_PACKAGE]
    profile_tmp = profile_file.with_name('package.json.dsh-bridge-tmp')
    lock_tmp = lock_file.with_name('pnpm-lock.yaml.dsh-bridge-tmp')
    profile_tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lock_tmp.write_text(updated_lock, encoding='utf-8')
    profile_tmp.replace(profile_file)
    lock_tmp.replace(lock_file)
    old_link = profile_file.parent / 'node_modules' / '@local' / 'dsh-bridge'
    if old_link.is_symlink():
        old_link.unlink()
    print(f'Removed legacy local link after DSH remove failed; backup: {backup}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default=PROFILE, choices=[PROFILE])
    args = parser.parse_args()
    if platform.system() != 'Darwin':
        parser.error('the v1 installer supports macOS only')
    for name in ('dsh', 'codex', 'launchctl'):
        if not shutil.which(name):
            parser.error(f'{name} is required in PATH')
    file = profile_path()
    if not file.is_file():
        parser.error(f'DSH {args.profile} profile does not exist: {file}')
    link = file.parent / 'node_modules' / '@aria467' / 'dsh-bridge'
    previous_link = os.readlink(link) if link.is_symlink() else None
    policy_file = file.with_name('pnpm-workspace.yaml')
    policy_before = policy_file.read_bytes() if policy_file.is_file() else None
    try:
        try:
            run('dsh', 'plugin', '--profile', args.profile, 'add', f'link:{ROOT}')
        except Exception:
            # DSH's pnpm wrapper can change node_modules before an add policy check fails.
            if link.is_symlink():
                link.unlink()
                if previous_link is not None:
                    link.symlink_to(previous_link)
                    print('Restored the previous DSH plugin link after install failure.', file=sys.stderr)
                else:
                    print('Removed the incomplete DSH plugin link after install failure.', file=sys.stderr)
            raise
        with file.open(encoding='utf-8') as stream:
            profile = json.load(stream)
        if LEGACY_PACKAGE in profile.get('dependencies', {}):
            try:
                run('dsh', 'plugin', '--profile', args.profile, 'remove', LEGACY_PACKAGE)
            except subprocess.CalledProcessError:
                remaining = json.loads(file.read_text(encoding='utf-8')).get('dependencies', {})
                if LEGACY_PACKAGE in remaining:
                    remove_legacy_local_link(file)
        ensure_bundle(file)
    finally:
        # The DSH wrapper may auto-append unrelated minimum-release-age exceptions.
        # A local link install does not need to change the user's policy settings.
        if policy_before is not None and policy_file.is_file() and policy_file.read_bytes() != policy_before:
            policy_file.write_bytes(policy_before)
            print('Restored the original DSH supply-chain policy settings.')
    run(sys.executable, str(ROOT / 'codex-plugin/install.py'))
    print('\nInstalled both sides from:', ROOT)
    print('Restart DeepSeek Harness manually to load the DSH bundle.')
    gate = Path.home() / '.local/state/dsh-codex-bridge/auto_resume_enabled'
    print(f'Restart Codex and review its hook trust prompt. Automatic continuation is {"enabled" if gate.exists() else "disabled"}.')
    print('Keep this checkout in place: the DSH profile links to it.')


if __name__ == '__main__':
    try:
        main()
    except (subprocess.CalledProcessError, OSError, RuntimeError, ValueError) as exc:
        print(f'install failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
