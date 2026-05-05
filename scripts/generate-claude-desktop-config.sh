#!/usr/bin/env bash
# generate-claude-desktop-config.sh — 从主机清单生成 Claude Desktop 的 mcpServers 配置
# 用法: bash scripts/generate-claude-desktop-config.sh <hosts.tsv> [输出文件]
#
# 输入:  TSV 文件，6 列（后三列可选）:
#        name, ssh_target, remote_project_dir, claude_bin, jump_host, env_setup
# 输出:  JSON 片段，可合并进 claude_desktop_config.json 的 mcpServers 字段

set -euo pipefail

HOSTS_FILE="${1:?Usage: $0 <hosts.tsv> [output_file]}"
OUTPUT_FILE="${2:-}"

if [ ! -f "$HOSTS_FILE" ]; then
  echo "错误: 主机清单文件不存在: $HOSTS_FILE" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BRIDGE_SCRIPT="$SCRIPT_DIR/remote-claude-code-mcp.sh"

if [ ! -f "$BRIDGE_SCRIPT" ]; then
  echo "错误: 桥接脚本不存在: $BRIDGE_SCRIPT" >&2
  exit 1
fi
chmod +x "$BRIDGE_SCRIPT"

HOST_COUNT=0
SERVERS_JSON=""
FIRST=1

# awk 预处理：bash 的 read 会把连续 \t 折叠成一个分隔符，导致空字段丢失。
# 用 awk 将空字段替换为 __EMPTY__ 占位符，后续在循环中还原。
while IFS=$'\t' read -r name ssh_target remote_project_dir claude_bin jump_host env_setup; do
  [ -z "$name" ] && continue
  [ "$name" = "__EMPTY__" ] && continue
  [[ "$name" =~ ^# ]] && continue
  [ "$name" = "name" ] && continue

  # 还原空字段
  [ "$claude_bin" = "__EMPTY__" ] && claude_bin=""
  [ "$jump_host" = "__EMPTY__" ] && jump_host=""
  [ "$env_setup" = "__EMPTY__" ] && env_setup=""

  # 默认值
  [ -z "$claude_bin" ] && claude_bin="claude"

  HOST_COUNT=$((HOST_COUNT + 1))

  if [ "$FIRST" -eq 1 ]; then
    FIRST=0
  else
    SERVERS_JSON+=","$'\n'
  fi

  # 始终输出 5 个 args，空字段用 "" 占位保证桥接脚本的位置解析
  ARGS_JSON="\"$ssh_target\", \"$remote_project_dir\", \"$claude_bin\", \"$jump_host\", \"$env_setup\""

  SERVERS_JSON+="    \"$name\": {
      \"command\": \"$BRIDGE_SCRIPT\",
      \"args\": [$ARGS_JSON]
    }"
done < <(tr -d '\r' < "$HOSTS_FILE" | awk -F'\t' -v OFS='\t' '{
  for(i=1;i<=NF;i++) if($i=="") $i="__EMPTY__";
  NF=6; for(i=NF;i<=6;i++) if($i=="") $i="__EMPTY__";
  print
}')

if [ "$HOST_COUNT" -eq 0 ]; then
  echo "错误: 主机清单中没有有效条目" >&2
  exit 1
fi

FULL_JSON=$(cat <<EOF
{
  "mcpServers": {
${SERVERS_JSON}
  }
}
EOF
)

if [ -n "$OUTPUT_FILE" ]; then
  mkdir -p "$(dirname "$OUTPUT_FILE")"
  echo "$FULL_JSON" > "$OUTPUT_FILE"
  echo "已生成配置文件: $OUTPUT_FILE" >&2
  echo "共 $HOST_COUNT 台主机" >&2
else
  echo "$FULL_JSON"
  echo "" >&2
  echo "--- 共 $HOST_COUNT 台主机 ---" >&2
fi
