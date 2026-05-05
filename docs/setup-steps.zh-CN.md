# Claude Desktop 多机 Linux 开发桥接 — 安装与测试

## 前提条件

### 本地 (macOS)

- Claude Desktop 已安装
- 能通过 `ssh` 免密（或 ssh-agent）连接远端 Linux 机器
- `bash` 可用（macOS 自带）
- （可选）`rsync` 用于高效目录传输

### 远端 (每台 Linux 机器)

- SSH server 已运行
- `claude` CLI 已安装（`npm install -g @anthropic-ai/claude-code`）
- `claude --version` 可正常执行
- 项目目录已存在，且当前用户可读写
- 已在该目录下执行过 `claude` 完成初始化（生成 `.claude/` 目录和登录态）

---

## 第一步：配置 SSH 免密登录

确保本地能免密连接到每台远端机器：

```bash
ssh-keygen -t ed25519 -C "claude-desktop-bridge"
ssh-copy-id dev@your-linux-host
```

对于跳板机场景：

```bash
# 跳板机也需要免密
ssh-copy-id gateway@bastion.example.com
# ~/.ssh/config 会自动生效，无需额外配置
```

---

## 第二步：远端环境诊断

对每台主机运行诊断脚本，获取完整的远端环境信息：

```bash
# 直连
bash scripts/remote-env-diagnose.sh dev@10.0.0.21

# 跳板机
bash scripts/remote-env-diagnose.sh dev@10.0.1.50 gateway@bastion.example.com
```

诊断输出包括：
- 系统信息（OS / 内核 / 架构）
- 构建工具链（gcc / make / cmake 等）
- 脚本语言（python / node / ruby）
- Docker 安装状态和权限
- Claude Code 安装位置和版本
- **建议的 env_setup 配置**（关键！）

如果诊断提示 `claude` 不在默认 PATH 中，记下建议的 `env_setup` 值，后续填入主机清单。

---

## 第三步：填写主机清单

```bash
cp config/hosts.example.tsv config/hosts.tsv
```

TSV 格式（制表符分隔），6 列，后三列可选：

| # | 列名 | 必须 | 说明 |
|---|------|------|------|
| 1 | name | ✅ | 主机别名，用作 MCP server 名称 |
| 2 | ssh_target | ✅ | SSH 连接目标 |
| 3 | remote_project_dir | ✅ | 远端项目绝对路径 |
| 4 | claude_bin | 可选 | 远端 claude 命令，默认 `claude` |
| 5 | jump_host | 可选 | SSH 跳板机（堡垒机） |
| 6 | env_setup | 可选 | 环境初始化命令（`&&` 连接） |

示例：

```
dev-a   dev@10.0.0.21       /srv/my-app   claude
dev-b   dev@10.0.1.50       /srv/backend  claude   gateway@bastion.example.com
dev-c   dev@10.0.1.60       /srv/frontend claude           source ~/.nvm/nvm.sh && export PATH=~/.local/bin:$PATH
dev-d   deploy@172.16.0.10  /srv/api      ~/.local/bin/claude   ops@jump.corp.com   export NODE_ENV=production
```

常见 env_setup 场景：

| 场景 | env_setup 配置 |
|------|---------------|
| nvm 安装的 node/claude | `source ~/.nvm/nvm.sh` |
| 自定义安装路径 | `export PATH=~/.local/bin:$PATH` |
| Python venv | `source /srv/venv/bin/activate` |
| 组合 | `source ~/.nvm/nvm.sh && export PATH=~/.local/bin:$PATH` |

---

## 第四步：逐台预检

```bash
# 基础用法
bash scripts/check-remote-claude-code.sh dev@10.0.0.21 /srv/my-app

# 带跳板机
bash scripts/check-remote-claude-code.sh dev@10.0.1.50 /srv/backend claude gateway@bastion.example.com

# 带环境初始化
bash scripts/check-remote-claude-code.sh dev@10.0.1.60 /srv/frontend claude "" \
  "source ~/.nvm/nvm.sh && export PATH=~/.local/bin:\$PATH"
```

检查项：
1. SSH 连通性（含跳板机）
2. claude 命令是否在 PATH 中
3. claude 版本
4. 项目目录存在性
5. 目录读写权限
6. `claude mcp serve` 可用性
7. 开发工具探测（docker/git/gcc/python/node 等）
8. Docker 权限检查

全部 `[PASS]` 才能继续。

---

## 第五步：生成 Claude Desktop 配置

```bash
bash scripts/generate-claude-desktop-config.sh config/hosts.tsv build/claude_desktop_config.json
```

生成示例：

```json
{
  "mcpServers": {
    "dev-a": {
      "command": "/path/to/scripts/remote-claude-code-mcp.sh",
      "args": ["dev@10.0.0.21", "/srv/my-app", "claude"]
    },
    "dev-b": {
      "command": "/path/to/scripts/remote-claude-code-mcp.sh",
      "args": ["dev@10.0.1.50", "/srv/backend", "claude", "gateway@bastion.example.com"]
    },
    "dev-c": {
      "command": "/path/to/scripts/remote-claude-code-mcp.sh",
      "args": ["dev@10.0.1.60", "/srv/frontend", "claude", "", "source ~/.nvm/nvm.sh && export PATH=~/.local/bin:$PATH"]
    }
  }
}
```

---

## 第六步：合并配置并重启

### 配置文件位置

| 平台 | 路径 |
|------|------|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

> 如果已有其他 `mcpServers`，只合并新增的条目，不要覆盖整个文件。

```bash
# 全新安装直接复制
cp build/claude_desktop_config.json \
   "$HOME/Library/Application Support/Claude/claude_desktop_config.json"
```

重启 Claude Desktop（`Cmd+Q` 完全退出后重新打开）。

---

## 第七步：验证

在 Claude Desktop 中发送：

> 列出所有可用的 MCP 工具

应看到以主机名命名的远端工具。

实际操作测试：

> 在 dev-a 上查看项目目录结构和最近 5 条 git commit

> 在 dev-b 上检查 Docker 容器运行状态

> 在 dev-c 上修改 README.md，添加一行测试内容，然后查看 git diff

---

## 文件传输

桥接模式的 Claude Code 本身已支持文件读写（通过 MCP 协议），但如果需要**批量传输大文件或目录**，使用独立传输脚本更快且不消耗 token：

```bash
# 上传文件
bash scripts/remote-file-transfer.sh up dev@10.0.0.21 ./localfile /srv/app/config.yaml

# 上传目录
bash scripts/remote-file-transfer.sh up dev@10.0.0.21 ./my-dir /srv/app/my-dir

# 下载文件
bash scripts/remote-file-transfer.sh down dev@10.0.0.21 /srv/app/log.txt ./log.txt

# 同步目录（rsync 增量，本地→远端）
bash scripts/remote-file-transfer.sh sync dev@10.0.0.21 ./src /srv/app/src

# 带跳板机
bash scripts/remote-file-transfer.sh up dev@10.0.1.50 ./localfile /srv/file \
  gateway@bastion.example.com
```

传输策略：
- 单文件 → `scp`
- 目录 + 本地有 rsync + 远端有 rsync → `rsync`
- 目录 + 无 rsync → `tar` 管道传输

---

## 架构总览

```
┌───────────────────────────────────────────────┐
│               Claude Desktop (macOS)          │
│                                               │
│  mcpServers:                                  │
│    dev-a → remote-claude-code-mcp.sh          │
│    dev-b → remote-claude-code-mcp.sh          │
│    dev-c → remote-claude-code-mcp.sh          │
└────────┬──────────┬──────────┬────────────────┘
         │ stdio    │ stdio    │ stdio
         ▼          ▼          ▼
    bridge.sh  bridge.sh  bridge.sh
         │          │          │
         │ SSH -J    │ SSH      │ SSH
         ▼           ▼          ▼
    ┌─────────┐ ┌─────────┐ ┌─────────┐
    │ Linux A  │ │ Linux B  │ │ Linux C  │
    │ claude   │ │ claude   │ │ claude   │
    │ mcp serve│ │ mcp serve│ │ mcp serve│
    └─────────┘ └─────────┘ └─────────┘

辅助工具:
  remote-file-transfer.sh  → scp/rsync 文件传输
  remote-env-diagnose.sh   → 远端环境诊断
  check-remote-claude-code.sh → 接入前预检
  generate-claude-desktop-config.sh → 生成配置
```

---

## 故障排查

### 重启后看不到远端工具

1. 确认配置文件 JSON 合法：`python3 -m json.tool < config.json > /dev/null`
2. 查看 Claude Desktop 日志：菜单 → Help → Open Logs Folder
3. 确认桥接脚本路径是**绝对路径**且可执行

### SSH 连接失败 / Permission denied

1. `BatchMode=yes` 只支持密钥/agent，不支持密码
2. 如需密码认证，编辑桥接脚本去掉 `-o BatchMode=yes`
3. 跳板机也需要免密：`ssh-copy-id gateway@bastion`

### 远端 claude 找不到

1. 非交互式 SSH 的 `$PATH` 与交互式登录不同
2. `bash -lc "command -v claude"` 确认实际路径
3. 使用 env_setup 注入 PATH：`export PATH=/home/dev/.local/bin:$PATH`
4. 或直接填入完整绝对路径到 claude_bin 列

### 连接空闲后断连

桥接脚本已内置 `ServerAliveInterval=60` 保活（每 60 秒发心跳）。
如果远端 SSH 超时设置更短，编辑 `~/.ssh/config`：

```
Host dev@*
  ServerAliveInterval 30
```

### Docker 权限问题

远端 Docker 需要当前用户在 `docker` 组中：

```bash
sudo usermod -aG docker $USER
newgrp docker
# 或在 Claude Code 中用 sudo docker ...
```

---

## 安全注意事项

- SSH 密钥认证优先于密码
- 保护好私钥文件（`~/.ssh/id_*`）
- 远端 `claude mcp serve` 运行在登录用户权限下
- 限制每台主机的 `remote_project_dir` 到具体项目目录
- 生产环境建议使用只读用户或受限权限的独立用户
- 跳板机本身不应运行任何 MCP server
