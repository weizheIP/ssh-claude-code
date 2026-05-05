#!/usr/bin/env bash
# check-remote-claude-code.sh — 在接入前验证远端环境
# 用法: bash scripts/check-remote-claude-code.sh <ssh_target> <remote_project_dir> [claude_bin] [jump_host] [env_setup]

set -euo pipefail

SSH_TARGET="${1:?Usage: $0 <ssh_target> <project_dir> [claude_bin] [jump_host] [env_setup]}"
REMOTE_PROJECT_DIR="${2:?}"
CLAUDE_BIN="${3:-claude}"
JUMP_HOST="${4:-}"
ENV_SETUP="${5:-}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

# 构建 ssh 命令前缀
SSH_PREFIX=()
if [ -n "$JUMP_HOST" ]; then
  SSH_PREFIX=(ssh -o ConnectTimeout=5 -o BatchMode=yes -J "$JUMP_HOST" "$SSH_TARGET")
else
  SSH_PREFIX=(ssh -o ConnectTimeout=5 -o BatchMode=yes "$SSH_TARGET")
fi

# 构建远端命令前缀（注入环境初始化）
ssh_exec() {
  local cmd="$1"
  if [ -n "$ENV_SETUP" ]; then
    "${SSH_PREFIX[@]}" "{ ${ENV_SETUP}; } 2>/dev/null; ${cmd}" 2>/dev/null
  else
    "${SSH_PREFIX[@]}" "$cmd" 2>/dev/null
  fi
}

echo "============================================"
echo " 检查远端主机: $SSH_TARGET"
[ -n "$JUMP_HOST" ] && echo " 跳板机:       $JUMP_HOST"
echo " 项目目录:     $REMOTE_PROJECT_DIR"
echo " 期望 claude:  $CLAUDE_BIN"
[ -n "$ENV_SETUP" ] && echo " 环境初始化:   $ENV_SETUP"
echo "============================================"
echo ""

# 1. SSH 连通性
echo "--- 检查 1: SSH 连通性 ---"
if "${SSH_PREFIX[@]}" true 2>/dev/null; then
  pass "SSH 连接成功"
else
  # 尝试非 BatchMode
  SSH_NOBATCH=()
  if [ -n "$JUMP_HOST" ]; then
    SSH_NOBATCH=(ssh -o ConnectTimeout=5 -J "$JUMP_HOST" "$SSH_TARGET")
  else
    SSH_NOBATCH=(ssh -o ConnectTimeout=5 "$SSH_TARGET")
  fi
  if "${SSH_NOBATCH[@]}" true 2>/dev/null; then
    pass "SSH 连接成功（交互式认证）"
  else
    fail "无法 SSH 连接到 $SSH_TARGET"
    exit 1
  fi
fi

# 2. claude 命令是否存在
echo ""
echo "--- 检查 2: claude 命令 ---"
if ssh_exec "command -v '${CLAUDE_BIN}' || type '${CLAUDE_BIN}' || test -x '${CLAUDE_BIN}'"; then
  pass "找到 claude: $CLAUDE_BIN"
else
  fail "未找到 claude 命令: $CLAUDE_BIN"
  echo "  提示: 可能需要在 env_setup 中初始化 PATH"
  echo "  远端默认 PATH: $(ssh_exec 'echo $PATH')"
  exit 1
fi

# 3. claude 版本
echo ""
echo "--- 检查 3: claude 版本 ---"
CLAUD_VERSION=$(ssh_exec "'${CLAUDE_BIN}' --version" || echo "unknown")
echo "  远端版本: $CLAUD_VERSION"
[ "$CLAUD_VERSION" != "unknown" ] && pass "claude 可执行" || warn "无法获取版本"

# 4. 项目目录
echo ""
echo "--- 检查 4: 项目目录 ---"
if ssh_exec "test -d '${REMOTE_PROJECT_DIR}'"; then
  pass "目录存在: $REMOTE_PROJECT_DIR"
else
  fail "目录不存在: $REMOTE_PROJECT_DIR"
  exit 1
fi

# 5. 目录权限
echo ""
echo "--- 检查 5: 目录权限 ---"
PERMS=$(ssh_exec "ls -ld '${REMOTE_PROJECT_DIR}' | head -c 10" || echo "?")
echo "  权限: $PERMS"
if ssh_exec "test -r '${REMOTE_PROJECT_DIR}' && test -w '${REMOTE_PROJECT_DIR}'"; then
  pass "可读写"
else
  fail "不可读写"
  exit 1
fi

# 6. mcp serve 可用
echo ""
echo "--- 检查 6: claude mcp serve ---"
MCP_HELP=$(ssh_exec "cd '${REMOTE_PROJECT_DIR}' && '${CLAUDE_BIN}' mcp serve --help" || echo "")
if [ -n "$MCP_HELP" ]; then
  pass "claude mcp serve 可用"
else
  fail "claude mcp serve 不可用"
  exit 1
fi

# 7. 常用工具探测
echo ""
echo "--- 检查 7: 开发工具探测 ---"
for tool in docker git make gcc g++ cargo go python3 node npm; do
  if ssh_exec "command -v $tool" >/dev/null 2>&1; then
    VERSION=$(ssh_exec "$tool --version 2>&1 | head -1 | tr -d '\n'" || echo "?")
    echo "  ${GREEN}✓${NC} $tool — $VERSION"
  else
    echo "  ${YELLOW}✗${NC} $tool (未安装)"
  fi
done

# 8. Docker 权限检查
echo ""
echo "--- 检查 8: Docker 权限 ---"
if ssh_exec "command -v docker" >/dev/null 2>&1; then
  if ssh_exec "docker ps 2>&1" | grep -q "permission denied\|connect.*permission"; then
    warn "docker 已安装但当前用户无权限，需 sudo 或加入 docker 组"
    echo "  解决: sudo usermod -aG docker \$USER && newgrp docker"
  elif ssh_exec "docker ps 2>&1" >/dev/null 2>&1; then
    pass "Docker 可用（无需 sudo）"
  fi
else
  warn "Docker 未安装"
fi

echo ""
echo "============================================"
echo " 全部检查通过，远端主机就绪"
echo "============================================"
