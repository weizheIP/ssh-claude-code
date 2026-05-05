#!/usr/bin/env bash
# remote-claude-code-mcp.sh — Claude Desktop MCP 的 stdio→SSH 桥接入口
# 由 Claude Desktop 作为 mcpServer command 调用，不要手动执行
#
# 参数:
#   $1: ssh_target          SSH 目标，如 dev@10.0.0.21
#   $2: remote_project_dir  远端项目目录绝对路径
#   $3: claude_bin          远端 claude 命令，默认 claude
#   $4: jump_host           可选，SSH 跳板机，如 gateway@bastion.example.com
#   $5: env_setup           可选，远端环境初始化命令（&& 连接），在 claude mcp serve 前执行
#
# stdio 通道:
#   stdin/stdout → MCP 协议流量（绝对不能混杂其他输出）
#   stderr       → 日志和错误信息

set -euo pipefail

SSH_TARGET="${1:?Usage: $0 <ssh_target> <project_dir> [claude_bin] [jump_host] [env_setup]}"
REMOTE_PROJECT_DIR="${2:?}"
CLAUDE_BIN="${3:-claude}"
JUMP_HOST="${4:-}"
ENV_SETUP="${5:-}"

# ── 构建 SSH 选项 ──────────────────────────────────────────────
SSH_OPTS=(
  -T                          # 不分配伪终端（MCP 走 stdio）
  -o BatchMode=yes            # 禁用交互式密码提示（依赖 SSH key/agent）
  -o ServerAliveInterval=60   # 每 60s 保活
  -o ServerAliveCountMax=5    # 5 次保活失败后断连（5 分钟容错）
  -o ExitOnForwardFailure=yes
  -o ConnectTimeout=10
)

# 跳板机
if [ -n "$JUMP_HOST" ]; then
  SSH_OPTS+=(-J "$JUMP_HOST")
  echo "[remote-claude-code-mcp] 使用跳板机: $JUMP_HOST → $SSH_TARGET" >&2
fi

# ── 构建远端命令 ────────────────────────────────────────────────
REMOTE_CMD=""
# 环境初始化
if [ -n "$ENV_SETUP" ]; then
  REMOTE_CMD+="{ ${ENV_SETUP}; } && "
fi
# 进入项目目录并启动 MCP server
REMOTE_CMD+="cd '${REMOTE_PROJECT_DIR}' && exec '${CLAUDE_BIN}' mcp serve"

echo "[remote-claude-code-mcp] 连接 $SSH_TARGET, 项目 $REMOTE_PROJECT_DIR" >&2
echo "[remote-claude-code-mcp] 远端命令: $REMOTE_CMD" >&2

# ── 启动 ────────────────────────────────────────────────────────
exec ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "$REMOTE_CMD"
