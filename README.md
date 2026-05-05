# ssh-claude-code

Claude Code 插件 — 通过 `/ssh-connect` 动态连接远端 Linux 机器上的 Claude Code，实现远程开发。

## 架构

```
Claude Code 会话
  └─ /ssh-connect pc-130
       │
       ▼
  ssh-mcp-server (本地透明代理)
       │ SSH 隧道
       ▼
  远端 Linux: claude mcp serve
       │ 远端工具自动可用:
       │ remote_Bash, remote_Read, remote_Write, remote_Edit,
       │ remote_Agent, remote_WebSearch, ...
```

## 安装

### 推荐方式：插件市场安装

在 Claude Code 会话内执行：

```
/plugin marketplace add https://github.com/weizheIP/ssh-claude-code
```

或从本地目录安装：

```
/plugin marketplace add /path/to/ssh-claude-code
```

然后：

```
/plugin install ssh-claude-code
```

安装完成后运行 `bash install.sh` 创建主机清单模板（也可跳过，手动编写）。

### 手动安装

```bash
git clone https://github.com/weizheIP/ssh-claude-code.git
cd ssh-claude-code
bash install.sh
# 然后按插件市场方式注册：
#   /plugin marketplace add /path/to/ssh-claude-code
#   /plugin install ssh-claude-code
```

重启 Claude Code 后生效。

## 使用

```
/ssh-connect pc-130        # 连接远端 → 远端工具自动加载
/ssh-folder  /civi/re-algo  # 切换工作目录
/ssh-disconnect            # 断开连接
```

## 添加主机

编辑 `~/.claude/ssh-hosts.tsv`（TSV 格式）：

```
# name    ssh_target      remote_project_dir   claude_bin   jump_host   env_setup
pc-130    pc-130          /civi/re-algo        claude                   export PATH=/home/pc/.local/bin:$PATH
pc-131    dev@10.0.0.50   /srv/backend         claude       gateway@jump.example.com
```

## 卸载

在 Claude Code 会话中：

```
/plugin uninstall ssh-claude-code
```

或手动删除：

```bash
rm -rf ~/.claude/plugins/cache/ssh-claude-code
# 然后编辑 ~/.claude/plugins/installed_plugins.json 删除 ssh-claude-code 条目
```

## 要求

- 远端: Claude Code CLI 已安装 + 项目已初始化
- 本地: Python 3.8+ + SSH 密钥免密登录
- 远端 `claude` 命令在 PATH 中（或通过 `env_setup` 指定）

## License

MIT
