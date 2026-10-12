import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import handoff


class FakeClient:
    def __init__(self, thread):
        self.thread = thread
        self.calls = []

    def request(self, method, params):
        self.calls.append((method, params))
        if method == "thread/read":
            return {"thread": self.thread}
        return {"turn": {"id": "pilot"}}


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.record = {"task_id": "test-1", "codex_thread_id": "thread-1"}
        self.result = Path("/tmp/test-1.result.md")
        self.status = Path("/tmp/test-1.status.json")

    def test_busy_thread_is_not_resumed(self):
        client = FakeClient({"status": {"type": "active"}, "turns": [{"status": "inProgress", "items": []}]})
        self.assertEqual(handoff.resume_one(client, self.record, "completed", self.result, self.status), "thread-busy")
        self.assertEqual([method for method, _ in client.calls], ["thread/read"])

    def test_delivers_once_to_original_thread(self):
        client = FakeClient({"status": {"type": "idle"}, "turns": [{"status": "completed", "items": []}]})
        with patch.object(handoff, "mark_task") as mark:
            self.assertEqual(handoff.resume_one(client, self.record, "completed", self.result, self.status), "delivered")
        method, params = client.calls[-1]
        self.assertEqual(method, "turn/start")
        self.assertEqual(params["threadId"], "thread-1")
        self.assertIn("DSH_HANDOFF:test-1:completed", params["input"][0]["text"])
        self.assertEqual(mark.call_args.kwargs["phase"], "finished")

    def test_existing_marker_prevents_duplicate(self):
        marker = "DSH_HANDOFF:test-1:completed"
        client = FakeClient({"status": {"type": "idle"}, "turns": [{"status": "completed", "items": [{"type": "userMessage", "content": [{"text": marker}]}]}]})
        with patch.object(handoff, "mark_task"):
            self.assertEqual(handoff.resume_one(client, self.record, "completed", self.result, self.status), "already-delivered")
        self.assertEqual([method for method, _ in client.calls], ["thread/read"])

    def test_approval_message_does_not_offer_approval(self):
        client = FakeClient({"status": {"type": "idle"}, "turns": []})
        with patch.object(handoff, "mark_task"):
            handoff.resume_one(client, self.record, "waiting_for_approval", self.result, self.status)
        prompt = client.calls[-1][1]["input"][0]["text"]
        self.assertIn("不要替人批准", prompt)
        self.assertNotIn(str(self.result), prompt)


if __name__ == "__main__":
    unittest.main()
