# ssh-claude-code

Claude Code 插件 — 通过 `/ssh-connect` 动态连接远端 Linux 机器上的 Claude Code，实现远程开发。
连接后远端项目目录会通过 sshfs **覆盖挂载**到 Claude Code 选中的本地项目目录，
本地可直接浏览远端文件树（只用于查看），真实编辑/构建仍走远端 `remote_*` 工具。

## 架构

```
Claude Code 会话 (cwd = 选中的项目目录)
  └─ /ssh-connect pc-130
       │
       ▼
  ssh-mcp-server (本地透明代理)
       │ ① SSH 隧道 → 远端 claude mcp serve
       │     远端工具自动可用 (remote_ 前缀):
       │     remote_Bash, remote_Read, remote_Write, remote_Edit,
       │     remote_Agent, remote_WebSearch, ...
       │
       └ ② sshfs 覆盖挂载远端 project_dir → 本地 cwd
             本地文件树即时反映远端内容(只用于查看)
```

## 文件树挂载（可选）

挂载是可选能力，缺失依赖只会返回提示，不会阻塞 `remote_*` 工具。

| 平台 | 依赖 | 一键安装 |
|------|------|----------|
| macOS | macFUSE + sshfs (gromgit/fuse 仓库) | `bash <plugin>/scripts/install-sshfs.sh` |
| Linux | sshfs (apt/dnf/yum/pacman/zypper) | `bash <plugin>/scripts/install-sshfs.sh` |
| Windows | WinFsp + sshfs-win | 管理员 PowerShell：`powershell -ExecutionPolicy Bypass -File <plugin>\scripts\install-sshfs-win.ps1` |

> macOS 首次安装 macFUSE 后，需到 `系统设置 → 隐私与安全性` 允许 Benjamin Fleischer 的内核扩展并重启电脑。

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
