import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('codex_bridge_install', PLUGIN / 'install.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MarketplaceTests(unittest.TestCase):
    def test_migrate_existing_marketplace_path(self):
        entries = {'marketplaces': [{'name': 'local-dsh-bridge', 'root': '/old/bridge'}]}
        with patch.object(module, 'listing', return_value=entries), patch.object(module, 'run') as run:
            module.ensure_marketplace()
        self.assertEqual([call.args[2] for call in run.call_args_list], ['remove', 'marketplace', 'marketplace', 'add'])

    def test_same_marketplace_path_is_idempotent(self):
        entries = {'marketplaces': [{'name': 'local-dsh-bridge', 'root': str(module.MARKETPLACE)}]}
        with patch.object(module, 'listing', return_value=entries), patch.object(module, 'run') as run:
            module.ensure_marketplace()
        run.assert_called_once_with('codex', 'plugin', 'add', module.PLUGIN_ID)


if __name__ == '__main__':
    unittest.main()
