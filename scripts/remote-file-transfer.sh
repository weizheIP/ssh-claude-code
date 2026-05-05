#!/usr/bin/env bash
# remote-file-transfer.sh — 本地与远端 Linux 机器之间的文件传输
# 用法:
#   上传:   bash scripts/remote-file-transfer.sh up   <host> <local_path> <remote_path> [jump_host]
#   下载:   bash scripts/remote-file-transfer.sh down <host> <remote_path> <local_path> [jump_host]
#   同步:   bash scripts/remote-file-transfer.sh sync <host> <local_dir> <remote_dir>  [jump_host]
#
# scp 用于单文件，rsync 用于目录（保留权限/时间戳/增量传输）

set -euo pipefail

ACTION="${1:?Usage: $0 <up|down|sync> <host> <src> <dst> [jump_host]}"
HOST="${2:?}"
SRC="${3:?}"
DST="${4:?}"
JUMP_HOST="${5:-}"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

SSH_OPTS=(-o ConnectTimeout=10 -o BatchMode=yes)
[ -n "$JUMP_HOST" ] && SSH_OPTS+=(-J "$JUMP_HOST")

case "$ACTION" in
  up)
    echo -e "${GREEN}[上传]${NC} $SRC → $HOST:$DST"
    if [ -d "$SRC" ] && command -v rsync &>/dev/null; then
      rsync -avz -e "ssh ${SSH_OPTS[*]}" "$SRC/" "$HOST:$DST/"
    elif [ -d "$SRC" ]; then
      # 无 rsync，用 tar + ssh 管道（保留目录结构）
      tar czf - -C "$(dirname "$SRC")" "$(basename "$SRC")" \
        | ssh "${SSH_OPTS[@]}" "$HOST" "mkdir -p '$DST' && tar xzf - -C '$DST' --strip-components=1"
    else
      scp "${SSH_OPTS[@]}" "$SRC" "$HOST:$DST"
    fi
    echo -e "${GREEN}[完成]${NC} 上传成功"
    ;;

  down)
    echo -e "${GREEN}[下载]${NC} $HOST:$SRC → $DST"
    # 检查远端是文件还是目录
    IS_DIR=$(ssh "${SSH_OPTS[@]}" "$HOST" "test -d '$SRC' && echo yes || echo no" 2>/dev/null)
    if [ "$IS_DIR" = "yes" ] && command -v rsync &>/dev/null; then
      rsync -avz -e "ssh ${SSH_OPTS[*]}" "$HOST:$SRC/" "$DST/"
    elif [ "$IS_DIR" = "yes" ]; then
      ssh "${SSH_OPTS[@]}" "$HOST" "tar czf - -C '$SRC' ." | tar xzf - -C "$DST"
      mkdir -p "$DST"
    else
      scp "${SSH_OPTS[@]}" "$HOST:$SRC" "$DST"
    fi
    echo -e "${GREEN}[完成]${NC} 下载成功"
    ;;

  sync)
    # rsync 双向同步：以本地为主，更新远端
    # 需要 rsync（本地 + 远端都要有）
    echo -e "${GREEN}[同步]${NC} $SRC ↔ $HOST:$DST"
    if ! command -v rsync &>/dev/null; then
      echo -e "${RED}[错误]${NC} 同步需要本地安装 rsync" >&2
      exit 1
    fi
    REMOTE_RSYNC=$(ssh "${SSH_OPTS[@]}" "$HOST" "command -v rsync || echo ''" 2>/dev/null)
    if [ -z "$REMOTE_RSYNC" ]; then
      echo -e "${RED}[错误]${NC} 远端也需要 rsync，请先在 $HOST 上安装" >&2
      exit 1
    fi
    rsync -avz --progress -e "ssh ${SSH_OPTS[*]}" "$SRC/" "$HOST:$DST/"
    echo -e "${GREEN}[完成]${NC} 同步成功"
    ;;

  *)
    echo "用法: $0 <up|down|sync> <host> <src> <dst> [jump_host]" >&2
    exit 1
    ;;
esac
