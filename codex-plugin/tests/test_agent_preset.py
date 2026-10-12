import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bridge


class AgentPresetTests(unittest.TestCase):
    def test_new_session_envelope_contains_validated_agent_preset(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bridge_root, state_root, workspace = root / "bridge", root / "state", root / "workspace"
            (bridge_root / "inbox").mkdir(parents=True)
            workspace.mkdir()
            (bridge_root / "capabilities.json").write_text(json.dumps({
                "permissionPresets": [{"value": "read-only"}],
                "agentPresets": [{"id": "standard"}, {"id": "minimal"}],
            }), encoding="utf-8")
            prompt = root / "prompt.txt"
            prompt.write_text("Inspect only", encoding="utf-8")
            args = SimpleNamespace(workspace=str(workspace), permission_preset="read-only",
                                   agent_preset="minimal", dsh_session_id=None,
                                   prompt_file=str(prompt), task_id="preset-test")
            with patch.object(bridge, "BRIDGE_ROOT", bridge_root), patch.object(bridge, "STATE_ROOT", state_root), patch.dict(os.environ, {"CODEX_THREAD_ID": "thread-test"}), contextlib.redirect_stdout(io.StringIO()):
                bridge.submit(args)
            envelope = json.loads((bridge_root / "inbox/preset-test.json").read_text(encoding="utf-8"))
            self.assertEqual(envelope["agent_preset"], "minimal")
            self.assertEqual(envelope["permission_preset"], "read-only")


if __name__ == "__main__":
    unittest.main()
