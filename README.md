# 会话标题自动更新

在对话进行时，根据当前主要工作自动更新会话标题，支持 Codex 和 Qoder CLI 的插件入口。正常顺序输入时，每个会话首次输入会检查一次，此后至少间隔 10 分钟，才在下一次输入时再次检查。标题由当前会话的模型生成。

版本更新见 [CHANGELOG.md](CHANGELOG.md)。

## 运行方式

- **Codex**：模型调用宿主提供的会话改名工具。已验证连接现有共享服务的终端会话能通过插件改名；`codex exec` 和内嵌服务的测试会话未提供该工具，工具不可用时不会改名。当前 Desktop 宿主提供该工具，但插件完整流程尚未在其中验证。
- **Qoder CLI**：模型先保存候选标题，在下一次用户输入时由 Hook 提交。如果之后没有新输入，候选标题不会生效。
- 临时追问不会单独要求改名。命名依赖当前模型执行，不能保证每次检查都更新标题。

需要 Python 3.8 或更高版本，并确保 `python3` 命令可用。Codex 和 Qoder CLI 支持 macOS 和 Linux。Codex 的 Windows Hook 已验证启动；Qoder CLI 的 Windows 支持尚未验证。

本插件尚未适配 Claude Code。

## 标题规则

标题应写清具体工作对象和主要动作，必要时补充功能、问题或目标，让人只看标题就能知道在做什么。命名围绕会话的当前主要任务；查资料、打开链接、跑测试如果只是中间步骤，标题仍保留主要任务。独立阅读或测试任务则需要写明文章主题或测试对象。

原标题未命名、过泛、缺少关键对象或动作，或与主要任务不符时，即使主题没有变化，也会要求模型重新命名。原标题已准确表达主要任务时，临时追问或中间步骤保持原题；用户明确开始新的独立任务时更新标题。用户明确指定或要求保留标题的指令优先。

标题只使用会话中已确认的信息，保留必要技术名称，不猜测未知的文章主题、问题原因或完成状态。

## 安装

选择使用的客户端，添加 GitHub 仓库中的插件市场并安装：

```bash
# Codex
codex plugin marketplace add jasonchio-cn/session-auto-rename
codex plugin add session-auto-rename@session-auto-rename

# Qoder CLI
qodercli plugins marketplace add https://github.com/jasonchio-cn/session-auto-rename.git
qodercli plugins install session-auto-rename@session-auto-rename
```

Codex 安装后，在 `/hooks` 中审核并信任本插件的 Hook。Qoder 安装后重启 CLI 或运行 `/plugins reload`。

更新插件：

```bash
# Codex
codex plugin marketplace upgrade session-auto-rename
codex plugin add session-auto-rename@session-auto-rename

# Qoder CLI
qodercli plugins marketplace update session-auto-rename
qodercli plugins update session-auto-rename@session-auto-rename
```

停用时可使用客户端的插件管理入口。

## 状态和限制

状态按会话保存在各客户端的插件数据目录中：

```text
Codex: $PLUGIN_DATA/sessions/<session-id>/rename-state.json
Qoder: $QODER_PLUGIN_DATA/sessions/<session-id>/rename-state.json
```

状态包含上次检查时间、请求标识，以及 Qoder 的候选标题和提交标记；不保存对话正文。状态文件损坏或不可写时，本次标题检查会跳过，用户的正常任务继续执行。同一会话的多个操作并发执行时，可能重复检查或覆盖状态。

Qoder 需要授权时，插件只自动允许当前会话对应的候选标题写入命令；其他命令仍按 Qoder 的权限规则处理，计划模式不会自动放行。

Qoder 会在模型读取新输入之前提交已有候选，因此新输入中的“保持当前标题”无法保证取消此前的候选。两端对界面内手动改名的识别尚未验证，首次检查可能调整已有标题。

## 验证

```bash
python3 -m unittest discover -s tests -v
qodercli plugins validate . --strict
```

## 许可证

[MIT License](LICENSE)
