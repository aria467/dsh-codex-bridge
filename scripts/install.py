#!/usr/bin/env python3
"""Install the DSH package and the Codex plugin from this checkout on macOS."""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = '@local/dsh-bridge'
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
    if PACKAGE not in bundles:
        backup = profile_file.with_name('package.json.dsh-bridge-backup')
        shutil.copy2(profile_file, backup)
        bundles.append(PACKAGE)
        temp = profile_file.with_name('package.json.dsh-bridge-tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temp.replace(profile_file)
        print(f'Enabled DSH bundle; previous profile saved at {backup}')
    return data


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
    link = file.parent / 'node_modules' / '@local' / 'dsh-bridge'
    previous_link = os.readlink(link) if link.is_symlink() else None
    try:
        run('dsh', 'plugin', '--profile', args.profile, 'add', f'link:{ROOT}')
        ensure_bundle(file)
    except Exception:
        # DSH's pnpm wrapper can change node_modules before a policy check fails.
        if previous_link is not None and link.is_symlink():
            link.unlink()
            link.symlink_to(previous_link)
            print('Restored the previous DSH plugin link after install failure.', file=sys.stderr)
        raise
    run(sys.executable, str(ROOT / 'codex-plugin/install.py'))
    print('\nInstalled both sides from:', ROOT)
    print('Restart DeepSeek Harness manually to load the DSH bundle.')
    print('Restart Codex and review its hook trust prompt. Automatic continuation stays off until enabled after a UI pilot.')
    print('Keep this checkout in place: the DSH profile links to it.')


if __name__ == '__main__':
    try:
        main()
    except (subprocess.CalledProcessError, OSError, RuntimeError, ValueError) as exc:
        print(f'install failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
