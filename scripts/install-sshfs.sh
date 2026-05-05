#!/usr/bin/env bash
# install-sshfs.sh — 安装 sshfs (macOS / Linux)
# 用于 ssh-claude-code 的文件树挂载功能。挂载是可选的, 不影响 remote_* 工具。

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { printf "${GREEN}[i]${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}[!]${NC} %s\n" "$*"; }
fail()  { printf "${RED}[x]${NC} %s\n" "$*" >&2; exit 1; }

OS="$(uname -s)"

# ── macOS ─────────────────────────────────────────────────────
install_macos() {
  info "macOS — 检查 Homebrew"
  if ! command -v brew >/dev/null 2>&1; then
    fail "未检测到 Homebrew。请先访问 https://brew.sh/ 安装,然后重新运行此脚本。"
  fi

  if [ -e /Library/Filesystems/macfuse.fs ] || [ -e /Library/Filesystems/osxfuse.fs ]; then
    info "macFUSE 已安装"
  else
    info "安装 macFUSE (需要管理员密码,且首次安装后需到 系统设置 → 隐私与安全性 允许 Benjamin Fleischer 的内核扩展,然后重启)"
    brew install --cask macfuse
    warn "macFUSE 安装完成 — 必须在 系统设置 → 隐私与安全性 中允许该扩展, 然后重启电脑, 才能挂载 sshfs。"
  fi

  if command -v sshfs >/dev/null 2>&1; then
    info "sshfs 已安装: $(command -v sshfs)"
  else
    info "安装 sshfs (gromgit/fuse/sshfs-mac)"
    # 官方 sshfs 已从 brew 主仓库移除, 使用社区维护的 tap
    brew install gromgit/fuse/sshfs-mac || brew install sshfs || \
      fail "安装 sshfs 失败。请参考 https://github.com/gromgit/homebrew-fuse"
  fi

  info "安装完成。运行 /ssh-connect 时会自动尝试挂载。"
}

# ── Linux ─────────────────────────────────────────────────────
install_linux() {
  if command -v sshfs >/dev/null 2>&1; then
    info "sshfs 已安装: $(command -v sshfs)"
    return
  fi

  if command -v apt-get >/dev/null 2>&1; then
    info "Debian/Ubuntu — apt 安装 sshfs"
    sudo apt-get update && sudo apt-get install -y sshfs
  elif command -v dnf >/dev/null 2>&1; then
    info "Fedora/RHEL — dnf 安装 fuse-sshfs"
    sudo dnf install -y fuse-sshfs
  elif command -v yum >/dev/null 2>&1; then
    info "RHEL/CentOS — yum 安装 fuse-sshfs"
    sudo yum install -y fuse-sshfs
  elif command -v pacman >/dev/null 2>&1; then
    info "Arch — pacman 安装 sshfs"
    sudo pacman -S --noconfirm sshfs
  elif command -v zypper >/dev/null 2>&1; then
    info "openSUSE — zypper 安装 sshfs"
    sudo zypper install -y sshfs
  else
    fail "未识别的发行版,请手动安装 sshfs (包名通常为 sshfs 或 fuse-sshfs)。"
  fi

  info "安装完成。"
}

case "$OS" in
  Darwin)  install_macos ;;
  Linux)   install_linux ;;
  *)       fail "不支持的系统: $OS (Windows 请使用 install-sshfs-win.ps1)" ;;
esac
