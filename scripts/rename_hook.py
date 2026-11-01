#!/usr/bin/env python3
"""Throttle title checks and exchange Qoder title candidates through plugin data."""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import sys
import tempfile
import time
import unicodedata


INTERVAL_SECONDS = 600
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
REQUEST_ID = re.compile(r"[a-f0-9]{32}\Z")
STATE_FILE = "rename-state.json"


def read_state(path):
    try:
        with path.open(encoding="utf-8") as source:
            state = json.load(source)
    except FileNotFoundError:
        return {}
    if not isinstance(state, dict):
        raise ValueError("invalid state")
    last_check = state.get("last_check")
    if last_check is not None and (
        isinstance(last_check, bool) or not isinstance(last_check, (int, float))
        or not 0 <= last_check < 10**12
    ):
        raise ValueError("invalid check time")
    request_id = state.get("request_id")
    if request_id is not None and (
        not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id)
    ):
        raise ValueError("invalid request id")
    pending = state.get("pending")
    if pending is not None and (
        not isinstance(pending, dict)
        or not isinstance(pending.get("title"), str)
        or not isinstance(pending.get("submitted"), bool)
        or ("request_id" in pending and (
            not isinstance(pending["request_id"], str)
            or not REQUEST_ID.fullmatch(pending["request_id"])
        ))
    ):
        raise ValueError("invalid candidate")
    return state


def write_state(path, state):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".rename-state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            json.dump(state, target, ensure_ascii=False, separators=(",", ":"))
            target.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def title_instruction(platform, state_path, request_id, first_check, previous_title):
    common = (
        "会话标题检查：请在本轮最终回复前，结合整个会话及本轮已确认的信息，识别当前主要任务并检查标题。\n"
        "命名要求：写清具体工作对象和主要动作，必要时保留功能、问题或目标，"
        "让只看到标题的人能知道在做什么。围绕用户要完成的主要任务命名；"
        "查资料、打开链接、执行测试等若只是中间步骤，标题仍保留主要任务。"
        "独立的阅读或测试任务应写明文章主题或测试对象；已获取的链接、文档内容可用于补全对象。"
        "使用用户的主要语言，保留必要技术名称，在信息完整的前提下简短；"
        "只使用已确认的信息，不猜测未知主题、原因或完成状态。\n"
        "更新规则：遵守用户明确指定或要求保留标题的指令。其余情况下，"
        "未命名、过泛、缺少关键对象或动作、与主要任务不符的标题，应根据已有信息更新，即使主题没有变化。"
        "原标题已准确表达主要任务时，临时追问或中间步骤保持原题；"
        "用户明确开始新的独立任务时应更新标题。\n"
        "本次检查只适用于当前这一轮；后续轮次须等待新的标题检查指令。"
        "无需向用户报告检查过程；未成功改名时不要声称已更新。"
    )
    if platform == "codex":
        if first_check:
            common += "这是首次自动检查；除非用户要求保留标题，请为当前主要工作设置一个标题。"
        return common + (
            "若需要改名，请在最终回复前调用当前宿主提供的会话标题工具，"
            "例如 mcp__codex_tui__set_thread_title 或 mcp__codex_app__set_thread_title；"
            "工具不可用时跳过。"
        )
    if previous_title:
        common += "本插件上次生成的候选标题是 " + json.dumps(previous_title, ensure_ascii=False) + "。"
    else:
        common += "本插件尚无记录的标题；除非用户要求保留标题，请为当前主要工作生成一个候选标题。"
    command = shlex.join([
        sys.executable, str(Path(__file__).resolve()), "propose",
        "--state", str(state_path), "--request-id", request_id,
        "--title", "<替换为新标题>",
    ])
    return common + (
        "若需要改名，通过 Shell 执行以下命令，把占位内容替换为适当转义的新标题：\n"
        + command + "\n若无需改名，不执行命令。候选标题将在下一次用户输入时应用。"
    )


def run_hook(platform, event, environ, now, output):
    session_id = event.get("session_id") if isinstance(event, dict) else None
    data_var = "PLUGIN_DATA" if platform == "codex" else "QODER_PLUGIN_DATA"
    data_dir = environ.get(data_var)
    if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
        return
    if not data_dir or not Path(data_dir).is_absolute():
        return
    state_path = Path(data_dir) / "sessions" / session_id / STATE_FILE
    state = read_state(state_path)
    pending = state.get("pending") if platform == "qoder" else None
    submit = pending is not None and not pending["submitted"] and bool(pending["title"])
    first_check = "last_check" not in state
    due = first_check or now - state["last_check"] >= INTERVAL_SECONDS
    result = {"hookEventName": "UserPromptSubmit"}
    if submit:
        result["sessionTitle"] = pending["title"]
    if due:
        request_id = secrets.token_hex(16)
        state["last_check"] = now
        state["request_id"] = request_id
        write_state(state_path, state)
        previous_title = pending["title"] if pending else None
        result["additionalContext"] = title_instruction(
            platform, state_path, request_id, first_check, previous_title,
        )
    if not submit and not due:
        return
    json.dump({"hookSpecificOutput": result}, output, ensure_ascii=False)
    output.write("\n")
    output.flush()
    if submit:
        pending["submitted"] = True
        write_state(state_path, state)


def propose(state_path, request_id, title):
    if not state_path.is_absolute() or state_path.name != STATE_FILE:
        raise ValueError("invalid state path")
    if not REQUEST_ID.fullmatch(request_id):
        raise ValueError("invalid request id")
    title = " ".join(
        "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in title).split()
    )
    if not title:
        raise ValueError("empty title")
    title = title[:80]
    state = read_state(state_path)
    if state.get("request_id") != request_id:
        raise ValueError("stale request")
    if (state.get("pending") or {}).get("request_id") == request_id:
        raise ValueError("candidate already recorded")
    state["pending"] = {"title": title, "submitted": False, "request_id": request_id}
    write_state(state_path, state)


def permit_qoder(event, environ, output):
    if not isinstance(event, dict) or event.get("hook_event_name") != "PermissionRequest":
        return
    mode = event.get("permission_mode")
    if not isinstance(mode, str) or mode.replace("_", "").replace("-", "").lower() not in (
        "default", "acceptedits", "auto", "bypasspermissions",
    ):
        return
    if event.get("tool_name") != "Bash":
        return
    session_id = event.get("session_id")
    data_dir = environ.get("QODER_PLUGIN_DATA")
    plugin_dir = environ.get("QODER_PLUGIN_ROOT")
    if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
        return
    if not data_dir or not Path(data_dir).is_absolute() or not plugin_dir or not Path(plugin_dir).is_absolute():
        return
    if Path(__file__).resolve() != (Path(plugin_dir) / "scripts" / "rename_hook.py").resolve():
        return
    tool_input = event.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or len(command) > 8192:
        return
    try:
        arguments = shlex.split(command)
    except ValueError:
        return
    state_path = Path(data_dir) / "sessions" / session_id / STATE_FILE
    if len(arguments) != 9 or shlex.join(arguments) != command:
        return
    if arguments[:5] != [sys.executable, str(Path(__file__).resolve()), "propose", "--state", str(state_path)]:
        return
    if arguments[5] != "--request-id" or not REQUEST_ID.fullmatch(arguments[6]) or arguments[7] != "--title":
        return
    state = read_state(state_path)
    if state.get("request_id") != arguments[6]:
        return
    if (state.get("pending") or {}).get("request_id") == arguments[6]:
        return
    json.dump({"hookSpecificOutput": {
        "hookEventName": "PermissionRequest", "decision": {"behavior": "allow"},
    }}, output)
    output.write("\n")


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="action", required=True)
    hook = commands.add_parser("hook")
    hook.add_argument("platform", choices=("codex", "qoder"))
    commands.add_parser("permit-qoder")
    candidate = commands.add_parser("propose")
    candidate.add_argument("--state", required=True, type=Path)
    candidate.add_argument("--request-id", required=True)
    candidate.add_argument("--title", required=True)
    args = parser.parse_args()
    try:
        if args.action == "hook":
            run_hook(args.platform, json.load(sys.stdin), os.environ, time.time(), sys.stdout)
        elif args.action == "permit-qoder":
            permit_qoder(json.load(sys.stdin), os.environ, sys.stdout)
        else:
            propose(args.state, args.request_id, args.title)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"session-auto-rename: {error}", file=sys.stderr)
        return 1 if args.action == "propose" else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
