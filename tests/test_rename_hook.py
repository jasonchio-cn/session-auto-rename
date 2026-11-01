import concurrent.futures
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rename_hook.py"


class RenameHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = os.environ.copy()
        self.env["PLUGIN_DATA"] = str(self.root / "codex")
        self.env["QODER_PLUGIN_DATA"] = str(self.root / "qoder")

    def run_command(self, *args, event=None, env=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            input=json.dumps(event or {}), text=True, capture_output=True,
            env=env or self.env, check=False,
        )

    def hook(self, platform="qoder", session="session-1"):
        return self.run_command(
            "hook", platform,
            event={"session_id": session, "prompt": "PRIVATE_CONTEXT_SENTINEL"},
        )

    def state_path(self, platform="qoder", session="session-1"):
        return self.root / platform / "sessions" / session / "rename-state.json"

    def state(self, platform="qoder", session="session-1"):
        return json.loads(self.state_path(platform, session).read_text())

    def propose(self, request_id, title, session="session-1"):
        return self.run_command(
            "propose", "--state", str(self.state_path(session=session)),
            "--request-id", request_id, "--title", title,
        )

    def permission(self, command, session="session-1", tool="Bash", mode="default"):
        env = self.env.copy()
        env["QODER_PLUGIN_ROOT"] = str(SCRIPT.parent.parent)
        return self.run_command(
            "permit-qoder", env=env,
            event={"session_id": session, "tool_name": tool,
                   "hook_event_name": "PermissionRequest", "tool_input": {"command": command},
                   "permission_mode": mode},
        )

    def candidate_command(self, request_id, title="测试标题", session="session-1"):
        return shlex.join([
            sys.executable, str(SCRIPT), "propose", "--state", str(self.state_path(session=session)),
            "--request-id", request_id, "--title", title,
        ])

    def test_first_check_throttling_and_state_privacy(self):
        first = self.hook("codex")
        self.assertEqual(first.returncode, 0)
        context = json.loads(first.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("mcp__codex_tui__set_thread_title", context)
        self.assertIn("只适用于当前这一轮", context)
        self.assertNotIn("PRIVATE_CONTEXT_SENTINEL", self.state_path("codex").read_text())
        self.assertEqual(self.hook("codex").stdout, "")
        path = self.state_path("codex")
        state = self.state("codex")
        previous_request = state["request_id"]
        state["last_check"] = time.time() - 601
        path.write_text(json.dumps(state))
        self.assertIn("additionalContext", json.loads(self.hook("codex").stdout)["hookSpecificOutput"])
        self.assertNotEqual(self.state("codex")["request_id"], previous_request)

    def test_qoder_candidate_is_submitted_on_next_input_even_during_cooldown(self):
        first = self.hook()
        context = json.loads(first.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn(str(self.state_path()), context)
        self.assertIn("尚无记录的标题", context)
        request_id = self.state()["request_id"]
        result = self.propose(request_id, "  会话\n自动\x01命名\u0085方案  ")
        self.assertEqual(result.returncode, 0, result.stderr)
        next_output = json.loads(self.hook().stdout)["hookSpecificOutput"]
        self.assertEqual(next_output["sessionTitle"], "会话 自动 命名 方案")
        self.assertNotIn("additionalContext", next_output)
        self.assertTrue(self.state()["pending"]["submitted"])
        self.assertEqual(self.hook().stdout, "")

    def test_pending_title_is_submitted_before_new_check(self):
        self.hook()
        old_request = self.state()["request_id"]
        self.assertEqual(self.propose(old_request, "旧候选").returncode, 0)
        path = self.state_path()
        state = self.state()
        state["last_check"] = time.time() - 601
        path.write_text(json.dumps(state))
        result = json.loads(self.hook().stdout)["hookSpecificOutput"]
        self.assertEqual(result["sessionTitle"], "旧候选")
        self.assertIn("additionalContext", result)
        self.assertIn("旧候选", result["additionalContext"])
        self.assertNotEqual(self.state()["request_id"], old_request)
        self.assertTrue(self.state()["pending"]["submitted"])
        stale = self.propose(old_request, "迟到候选")
        self.assertNotEqual(stale.returncode, 0)
        self.assertEqual(self.state()["pending"]["title"], "旧候选")

    def test_sessions_are_isolated(self):
        self.hook(session="session-1")
        self.hook(session="session-2")
        self.assertEqual(self.propose(self.state()["request_id"], "标题一").returncode, 0)
        self.assertEqual(self.hook(session="session-2").stdout, "")
        self.assertEqual(json.loads(self.hook().stdout)["hookSpecificOutput"]["sessionTitle"], "标题一")

    def test_bad_input_and_corrupt_state_do_not_block_user_prompt(self):
        env = self.env.copy()
        env.pop("QODER_PLUGIN_DATA")
        self.assertEqual(self.run_command("hook", "qoder", event={"session_id": "s"}, env=env).stdout, "")
        self.assertEqual(self.hook(session="../outside").stdout, "")
        self.hook()
        path = self.state_path()
        path.write_text("{broken")
        result = self.hook()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(path.read_text(), "{broken")
        self.assertIn("session-auto-rename", result.stderr)

    def test_empty_or_stale_candidate_is_rejected(self):
        self.hook()
        request_id = self.state()["request_id"]
        self.assertNotEqual(self.propose(request_id, "\n\x01  ").returncode, 0)
        self.assertNotEqual(self.propose("0" * 32, "stale").returncode, 0)
        self.assertNotIn("pending", self.state())

    def test_candidate_cannot_be_requeued_by_an_old_instruction(self):
        self.hook()
        request_id = self.state()["request_id"]
        self.assertEqual(self.propose(request_id, "初始标题").returncode, 0)
        self.hook()
        self.assertNotEqual(self.propose(request_id, "迟到的重复候选").returncode, 0)
        self.assertTrue(self.state()["pending"]["submitted"])
        self.assertEqual(self.hook().stdout, "")

    def test_simultaneous_inputs_issue_at_most_one_check(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.hook(), range(8)))
        self.assertTrue(all(result.returncode == 0 for result in results))
        self.assertEqual(sum(bool(result.stdout) for result in results), 1)

    def test_permission_hook_allows_only_current_candidate_command(self):
        self.hook()
        request_id = self.state()["request_id"]
        command = self.candidate_command(request_id)
        result = self.permission(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PermissionRequest")
        self.assertEqual(output["decision"]["behavior"], "allow")
        self.assertEqual(self.propose(request_id, "已记录").returncode, 0)
        self.assertEqual(self.permission(command).stdout, "")

    def test_permission_hook_never_approves_other_commands(self):
        self.hook()
        request_id = self.state()["request_id"]
        valid = self.candidate_command(request_id)
        unsafe = [
            valid + "; touch /tmp/unrelated",
            valid + " && echo unrelated",
            valid.replace("'测试标题'", "$(touch /tmp/unrelated)"),
            self.candidate_command("0" * 32),
            self.candidate_command(request_id, session="another-session"),
            "env PYTHONPATH=/tmp " + valid,
        ]
        for command in unsafe:
            with self.subTest(command=command):
                result = self.permission(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
        self.assertEqual(self.permission(valid, tool="Write").stdout, "")
        self.assertEqual(self.permission(valid, mode="plan").stdout, "")
        self.assertEqual(self.permission(valid, mode="dont_ask").stdout, "")
        self.assertEqual(self.permission(valid, mode="").stdout, "")

    def test_approved_quoted_title_does_not_execute_shell_content(self):
        self.hook()
        request_id = self.state()["request_id"]
        sentinel = self.root / "unexpected-file"
        title = "标题 $(touch " + str(sentinel) + ")"
        command = self.candidate_command(request_id, title)
        self.assertEqual(json.loads(self.permission(command).stdout)["hookSpecificOutput"]["decision"]["behavior"], "allow")
        result = subprocess.run(["/bin/sh", "-c", command], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(sentinel.exists())
        self.assertEqual(self.state()["pending"]["title"], title[:80])


if __name__ == "__main__":
    unittest.main()
