#!/usr/bin/env bash
# remote-env-diagnose.sh — 远端 Linux 开发环境全面诊断
# 用法: bash scripts/remote-env-diagnose.sh <ssh_target> [jump_host]
#
# 输出:
#   1. 系统信息（OS/内核/架构）
#   2. 开发工具链版本
#   3. claude 安装状态
#   4. Docker 状态
#   5. 磁盘/内存
#   6. 生成的 env_setup 建议（可直接填入 hosts.tsv）

set -euo pipefail

SSH_TARGET="${1:?Usage: $0 <ssh_target> [jump_host]}"
JUMP_HOST="${2:-}"

SSH_OPTS=(-T -o ConnectTimeout=10 -o BatchMode=yes)
[ -n "$JUMP_HOST" ] && SSH_OPTS+=(-J "$JUMP_HOST")

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

section() { echo -e "\n${CYAN}━━━ $1 ━━━${NC}"; }
ok()     { echo -e "  ${GREEN}✓${NC} $1"; }
bad()    { echo -e "  ${RED}✗${NC} $1"; }
info()   { echo -e "  ${YELLOW}→${NC} $1"; }

echo "============================================"
echo " 远端开发环境诊断"
echo " 主机: $SSH_TARGET"
[ -n "$JUMP_HOST" ] && echo " 跳板: $JUMP_HOST"
echo "============================================"

# ── 系统信息 ──
section "系统信息"
OS=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" 'cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d \"' 2>/dev/null || echo "unknown")
KERNEL=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" 'uname -r' 2>/dev/null || echo "unknown")
ARCH=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" 'uname -m' 2>/dev/null || echo "unknown")
UPTIME=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" 'uptime -p' 2>/dev/null || echo "unknown")
echo "  OS:      $OS"
echo "  内核:    $KERNEL"
echo "  架构:    $ARCH"
echo "  运行:    $UPTIME"

# ── CPU / 内存 ──
section "资源"
CPU=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" 'nproc' 2>/dev/null || echo "?")
MEM=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" 'free -h | awk "/^Mem:/{print \$2}"' 2>/dev/null || echo "?")
DISK=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" 'df -h / | awk "NR==2{print \$4}"' 2>/dev/null || echo "?")
echo "  CPU:     $CPU 核"
echo "  内存:    $MEM"
echo "  磁盘空闲: $DISK"

# ── Shell 环境 ──
section "Shell 环境"
SHELL_TYPE=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" 'echo $SHELL' 2>/dev/null || echo "?")
DEFAULT_PATH=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" 'echo $PATH' 2>/dev/null || echo "?")
echo "  Shell:   $SHELL_TYPE"
echo "  PATH:    $DEFAULT_PATH"

# ── 基础构建工具 ──
section "构建工具链"
for tool in gcc g++ make cmake autoconf automake pkg-config; do
  TOOL_PATH=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "command -v $tool || echo ''" 2>/dev/null)
  if [ -n "$TOOL_PATH" ]; then
    VERSION=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "$tool --version 2>&1 | head -1" 2>/dev/null || echo "?")
    ok "$tool — $VERSION"
  else
    bad "$tool"
  fi
done

# ── 脚本语言 ──
section "脚本/运行时"
for tool in python3 python node npm ruby perl; do
  TOOL_PATH=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "command -v $tool || echo ''" 2>/dev/null)
  if [ -n "$TOOL_PATH" ]; then
    VERSION=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "$tool --version 2>&1 | head -1 | tr -d '\n'" 2>/dev/null || echo "?")
    ok "$tool — $VERSION"
  else
    bad "$tool"
  fi
done

# ── Rust / Go ──
section "系统语言"
for tool in cargo go rustc java javac; do
  TOOL_PATH=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "command -v $tool || echo ''" 2>/dev/null)
  if [ -n "$TOOL_PATH" ]; then
    VERSION=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "$tool --version 2>&1 | head -1" 2>/dev/null || echo "?")
    ok "$tool — $VERSION"
  else
    bad "$tool"
  fi
done

# ── Docker ──
section "Docker"
if DOCKER_PATH=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "command -v docker" 2>/dev/null) && [ -n "$DOCKER_PATH" ]; then
  VERSION=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "docker --version 2>&1" 2>/dev/null || echo "?")
  ok "docker — $VERSION"
  # 权限
  if ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "docker ps 2>&1" 2>/dev/null | grep -qi "permission denied"; then
    bad "当前用户无 docker 权限（需 sudo 或加入 docker 组）"
  else
    ok "docker 权限正常"
  fi
  # docker compose
  if ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "docker compose version 2>&1 || docker-compose --version 2>&1" >/dev/null 2>&1; then
    ok "docker compose 可用"
  else
    bad "docker compose"
  fi
else
  bad "docker 未安装"
fi

# ── Claude Code ──
section "Claude Code 安装"
CLAUDE_CANDIDATES=()
# 尝试常见安装位置
for candidate in claude ~/.local/bin/claude /usr/local/bin/claude /usr/bin/claude ~/.nvm/versions/node/*/bin/claude; do
  if RESULT=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "test -x '$candidate' && echo '$candidate' || test -x \$(command -v '$candidate' 2>/dev/null) && command -v '$candidate'" 2>/dev/null); then
    if [ -n "$RESULT" ]; then
      CLAUDE_CANDIDATES+=("$RESULT")
    fi
  fi
done

if [ ${#CLAUDE_CANDIDATES[@]} -gt 0 ]; then
  for c in "${CLAUDE_CANDIDATES[@]}"; do
    VER=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "'$c' --version" 2>/dev/null || echo "?")
    ok "找到 claude: $c ($VER)"
  done
else
  # 尝试用 env_setup 找
  SEARCH_RESULT=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" \
    'bash -lc "command -v claude" 2>/dev/null || echo ""' 2>/dev/null)
  if [ -n "$SEARCH_RESULT" ]; then
    info "交互式 shell 中找到 claude: $SEARCH_RESULT"
    info "非交互式 SSH 找不到，需要配置 env_setup"
  else
    bad "claude 未安装"
    info "安装: npm install -g @anthropic-ai/claude-code"
  fi
fi

# ── Git ──
section "Git"
if GIT_PATH=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "command -v git" 2>/dev/null) && [ -n "$GIT_PATH" ]; then
  VERSION=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "git --version" 2>/dev/null || echo "?")
  ok "git — $VERSION"
else
  bad "git"
fi

# ── 诊断总结和建议 ──
echo ""
echo "============================================"
echo " 诊断总结"
echo "============================================"

SUGGESTED_ENV=""
MISSING=()

# 检查是否需要特殊 env_setup
DEFAULT_CLAUDE=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "command -v claude 2>/dev/null || echo ''" 2>/dev/null || echo "")

if [ -z "$DEFAULT_CLAUDE" ]; then
  # 尝试在常见位置找
  for probe in '$HOME/.local/bin/claude' '$HOME/.nvm/versions/node/*/bin/claude' '/usr/local/bin/claude'; do
    FOUND=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "ls $probe 2>/dev/null | head -1 || echo ''" 2>/dev/null || echo "")
    if [ -n "$FOUND" ]; then
      SUGGESTED_ENV="export PATH=\$(dirname $FOUND):\$PATH"
      break
    fi
  done

  # 检查是否是 nvm 场景
  if ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "test -f ~/.nvm/nvm.sh" 2>/dev/null; then
    NVM_CLAUDE=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" 'bash -c "source ~/.nvm/nvm.sh 2>/dev/null; command -v claude"' 2>/dev/null || echo "")
    if [ -n "$NVM_CLAUDE" ]; then
      SUGGESTED_ENV='source ~/.nvm/nvm.sh'
      info "检测到 nvm 环境，需要 source ~/.nvm/nvm.sh"
    fi
  fi

  if [ -z "$SUGGESTED_ENV" ]; then
    MISSING+=("claude: 未在默认 PATH 中找到，建议安装或配置 env_setup")
  fi
fi

# 检查 docker 权限
if ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "command -v docker" >/dev/null 2>&1; then
  if ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "docker ps 2>&1" 2>/dev/null | grep -qi "permission denied"; then
    MISSING+=("docker: 需要 sudo 权限或加入 docker 组")
  fi
fi

if [ -n "$SUGGESTED_ENV" ]; then
  echo ""
  echo -e "${GREEN}建议的 env_setup:${NC}"
  echo "  $SUGGESTED_ENV"
  echo ""
  echo "将上面这行填入 config/hosts.tsv 的 env_setup 列"
fi

if [ ${#MISSING[@]} -gt 0 ]; then
  echo ""
  echo -e "${YELLOW}需要关注:${NC}"
  for m in "${MISSING[@]}"; do
    echo "  - $m"
  done
fi

echo ""
echo "诊断完成。"
