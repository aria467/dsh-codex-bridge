#!/usr/bin/env python3
"""Register this local marketplace, install its Codex plugin and watcher."""

import json
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent
MARKETPLACE = PLUGIN.parent
MARKETPLACE_NAME = 'local-dsh-bridge'
PLUGIN_ID = 'dsh-codex-bridge@local-dsh-bridge'
LABEL = 'com.aria.dsh-codex-bridge'


def run(*args):
    subprocess.run(args, check=True)


def listing(*args):
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def ensure_marketplace():
    entries = listing('codex', 'plugin', 'marketplace', 'list', '--json')['marketplaces']
    existing = next((entry for entry in entries if entry.get('name') == MARKETPLACE_NAME), None)
    if existing and Path(existing['root']).resolve() != MARKETPLACE:
        run('codex', 'plugin', 'remove', PLUGIN_ID)
        run('codex', 'plugin', 'marketplace', 'remove', MARKETPLACE_NAME)
        existing = None
    if not existing:
        run('codex', 'plugin', 'marketplace', 'add', str(MARKETPLACE))
    run('codex', 'plugin', 'add', PLUGIN_ID)


def main():
    ensure_marketplace()
    agent_path = Path.home() / 'Library/LaunchAgents' / f'{LABEL}.plist'
    agent_path.parent.mkdir(parents=True, exist_ok=True)
    state_dir = Path.home() / '.local/state/dsh-codex-bridge'
    state_dir.mkdir(parents=True, exist_ok=True)
    runtime = Path.home() / '.local/share/dsh-codex-bridge'
    runtime.mkdir(parents=True, exist_ok=True)
    for name in ('bridge.py', 'appserver.py', 'handoff.py'):
        shutil.copy2(PLUGIN / 'scripts' / name, runtime / name)
    config = {
        'Label': LABEL,
        'ProgramArguments': ['/usr/bin/python3', str(runtime / 'handoff.py'), 'watch'],
        'RunAtLoad': True,
        'KeepAlive': True,
        'StandardOutPath': str(state_dir / 'launchagent.out.log'),
        'StandardErrorPath': str(state_dir / 'launchagent.err.log'),
    }
    agent_path.write_bytes(plistlib.dumps(config))
    domain = f'gui/{os.getuid()}'
    subprocess.run(('launchctl', 'bootout', f'{domain}/{LABEL}'), check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run('launchctl', 'bootstrap', domain, str(agent_path))
    gate = state_dir / 'auto_resume_enabled'
    print(f'Installed {LABEL}; automatic continuation is {"enabled" if gate.exists() else "disabled"}.')


if __name__ == '__main__':
    main()
