#!/usr/bin/env bash
# install.sh — ssh-claude-code 插件安装
# 推荐方式：在 Claude Code 会话内使用原生插件系统安装
#   /plugin marketplace add https://github.com/weizheIP/ssh-claude-code   (或本地路径)
#   /plugin install ssh-claude-code
#
# 本脚本仅用于：手动安装 / 创建主机清单
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

HOSTS_FILE="$HOME/.claude/ssh-hosts.tsv"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo -e "${GREEN}ssh-claude-code${NC} v1.0.0"
echo "============================="
echo ""

# ── 方式一：已在插件目录中（由 Claude Code 安装） ──────────
# 此时 ${CLAUDE_PLUGIN_ROOT} 已由插件系统自动设置，无需额外操作。
# 本脚本只处理主机清单。

# ── 创建主机清单 ──────────────────────────────────────────
if [ ! -f "$HOSTS_FILE" ]; then
  if [ -f "$SCRIPT_DIR/config/hosts.example.tsv" ]; then
    cp "$SCRIPT_DIR/config/hosts.example.tsv" "$HOSTS_FILE"
    echo -e "  ${GREEN}✓${NC} 已创建主机清单: $HOSTS_FILE"
  else
    # 从远端 GitHub 下载示例
    curl -fsSL "https://raw.githubusercontent.com/weizheIP/ssh-claude-code/main/config/hosts.example.tsv" -o "$HOSTS_FILE" 2>/dev/null || {
      mkdir -p "$(dirname "$HOSTS_FILE")"
      cat > "$HOSTS_FILE" <<'EOF'
# name	ssh_target	remote_project_dir	claude_bin	jump_host	env_setup
# env_setup 用 && 连接多条命令，在 claude mcp serve 之前执行
# build-a	dev@10.0.0.21	/srv/my-app	claude
# internal-b	dev@10.0.1.50	/home/dev/backend	claude	gateway@bastion.example.com
# dev-c	dev@10.0.1.60	/home/dev/frontend	claude		source ~/.nvm/nvm.sh && export PATH=~/.local/bin:$PATH
EOF
    }
    echo -e "  ${GREEN}✓${NC} 已创建主机清单模板: $HOSTS_FILE"
  fi
else
  echo -e "  ${YELLOW}·${NC} 主机清单已存在，跳过"
fi

echo ""
echo "安装完成！重启 Claude Code 后可用："
echo ""
echo "  /ssh-connect <主机名>       连接远端机器"
echo "  /ssh-folder  <路径>         切换远端工作目录"
echo "  /ssh-disconnect             断开连接"
echo ""
echo "  （如安装了 ssh-hosts.tsv 列出的主机，可直接 /ssh-connect <名称>）"
echo ""
