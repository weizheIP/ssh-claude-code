#!/usr/bin/env python3
"""
ssh-mcp-server.py — SSH 远端 Claude Code 透明代理
=================================================
Claude Code 会话内通过 /ssh-connect 动态连接到远端机器上的 claude mcp serve。
连接后，远端的全部工具（Bash, Read, Write, Edit, Agent, WebSearch...）都会
在本地可用，远程执行。

协议: MCP over stdio (JSON-RPC 2.0)
状态: .claude/ssh-state.json
主机: config/hosts.tsv

架构:
  Claude Code ←→ ssh-mcp-server.py (代理) ←→ SSH隧道 ←→ 远端 claude mcp serve
  · 本地管理工具: ssh_connect, ssh_disconnect, ssh_folder, ssh_status
  · 远端工具:    由 claude mcp serve 动态提供(连接后自动发现)
"""

import sys
import json
import os
import subprocess
import threading
import time
import argparse
from pathlib import Path

# ── 配置 ───────────────────────────────────────────────────────
HOSTS_FILE = Path.home() / ".claude" / "ssh-hosts.tsv"
STATE_FILE = Path.home() / ".claude" / "ssh-state.json"

# 本地挂载点：MCP 进程的 cwd（即 Claude Code 选中的项目目录）
LOCAL_MOUNT = os.path.realpath(os.getcwd())

# ── 状态管理 ──────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, IOError):
            pass
    return {"connected": False}

def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))

def load_hosts() -> list:
    hosts = []
    if not HOSTS_FILE.exists():
        return hosts
    with open(HOSTS_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 3 or parts[0] == "name":
                continue
            while len(parts) < 6:
                parts.append("")
            name, target, proj, cbin, jump, env = parts[:6]
            if not name or not target or not proj:
                continue
            hosts.append({
                "name": name.strip(), "ssh_target": target.strip(),
                "project_dir": proj.strip(),
                "claude_bin": cbin.strip() or "claude",
                "jump_host": jump.strip(),
                "env_setup": env.strip(),
            })
    return hosts

def find_host(name_or_target: str) -> dict | None:
    for h in load_hosts():
        if h["name"] == name_or_target or h["ssh_target"] == name_or_target:
            return h
    return None

# ── 挂载管理 (sshfs / sshfs-win) ──────────────────────────────
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# Windows: sshfs-win 不会 daemonize, 需要长驻进程引用
_mount_proc = None

def _which(cmd: str) -> str:
    finder = "where" if IS_WIN else "which"
    try:
        r = subprocess.run([finder, cmd], capture_output=True, text=True)
    except Exception:
        return ""
    if r.returncode != 0:
        return ""
    out = r.stdout.strip().splitlines()
    return out[0].strip() if out else ""

def _macfuse_installed() -> bool:
    return any(os.path.exists(p) for p in (
        "/Library/Filesystems/macfuse.fs",
        "/Library/Filesystems/osxfuse.fs",
    ))

def _winfsp_installed() -> bool:
    return any(os.path.exists(p) for p in (
        r"C:\Program Files\WinFsp",
        r"C:\Program Files (x86)\WinFsp",
    ))

def _sshfs_bin() -> str:
    """定位 sshfs 可执行文件 (Windows 上是 sshfs-win)."""
    if IS_WIN:
        for c in (
            r"C:\Program Files\SSHFS-Win\bin\sshfs.exe",
            r"C:\Program Files (x86)\SSHFS-Win\bin\sshfs.exe",
        ):
            if os.path.exists(c):
                return c
    return _which("sshfs")

def _install_hint() -> str:
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if IS_WIN:
        script = (f"{plugin_root}\\scripts\\install-sshfs-win.ps1"
                  if plugin_root else "scripts\\install-sshfs-win.ps1")
        return (
            "缺少 WinFsp / sshfs-win,文件树挂载未启用 (remote_* 工具仍可正常使用)。\n"
            f"  PowerShell 安装:  powershell -ExecutionPolicy Bypass -File \"{script}\"\n"
            "  或 Chocolatey:    choco install winfsp sshfs-win\n"
            "  或下载安装包:     https://github.com/winfsp/sshfs-win/releases"
        )
    if IS_MAC:
        script = (f"{plugin_root}/scripts/install-sshfs.sh"
                  if plugin_root else "scripts/install-sshfs.sh")
        return (
            "缺少 sshfs / macFUSE,文件树挂载未启用 (remote_* 工具仍可正常使用)。\n"
            f"  运行安装脚本:  bash {script}\n"
            "  或手动安装:    brew install --cask macfuse && brew install gromgit/fuse/sshfs-mac"
        )
    return (
        "缺少 sshfs,文件树挂载未启用 (remote_* 工具仍可正常使用)。\n"
        "  Ubuntu/Debian: sudo apt install sshfs\n"
        "  RHEL/CentOS:   sudo yum install fuse-sshfs"
    )

def check_sshfs() -> tuple[bool, str]:
    if not _sshfs_bin():
        return False, _install_hint()
    if IS_MAC and not _macfuse_installed():
        return False, _install_hint()
    if IS_WIN and not _winfsp_installed():
        return False, _install_hint()
    return True, ""

def is_mount(path: str) -> bool:
    """检测 path 是否为当前挂载点."""
    if IS_WIN:
        # Windows/WinFsp: 进程存活 + os.path.ismount 双重确认
        if _mount_proc is None or _mount_proc.poll() is not None:
            return False
        try:
            return os.path.ismount(path)
        except Exception:
            return True  # 进程在则大概率挂载中
    try:
        r = subprocess.run(["mount"], capture_output=True, text=True, timeout=5)
        target = os.path.realpath(path)
        for line in r.stdout.splitlines():
            # 兼容 "... on /path (type...)" 和 "... on /path\n" 两种格式
            if f" on {target} " in line or f" on {target}(" in line:
                return True
    except Exception:
        pass
    return False

def unmount_path(path: str) -> bool:
    """解除 sshfs 挂载."""
    global _mount_proc
    if IS_WIN:
        if _mount_proc is not None:
            try:
                _mount_proc.terminate()
                _mount_proc.wait(timeout=5)
            except Exception:
                try:
                    _mount_proc.kill()
                except Exception:
                    pass
            _mount_proc = None
        return True

    if not is_mount(path):
        return True
    try:
        if IS_MAC:
            r = subprocess.run(["umount", path], capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                subprocess.run(["diskutil", "unmount", "force", path],
                               capture_output=True, text=True, timeout=15)
        else:
            subprocess.run(["fusermount", "-u", path],
                           capture_output=True, text=True, timeout=10)
        time.sleep(0.3)
        return not is_mount(path)
    except Exception:
        return False

def mount_remote(state: dict) -> tuple[bool, str]:
    """sshfs 挂载远端 project_dir 到本地 LOCAL_MOUNT."""
    global _mount_proc
    available, hint = check_sshfs()
    if not available:
        return False, hint

    ssh_target = state["ssh_target"]
    project_dir = state["project_dir"]
    jump_host = state.get("jump_host", "")
    host_label = (state.get("host", "remote") or "remote").replace(",", "_").replace(" ", "_")

    unmount_path(LOCAL_MOUNT)

    opts = [
        "ServerAliveInterval=60",
        "ServerAliveCountMax=5",
        "reconnect",
        "ConnectTimeout=10",
    ]
    if IS_MAC:
        opts.extend([
            "defer_permissions",
            "noappledouble",
            "follow_symlinks",
            f"volname={host_label}",
        ])
    elif IS_WIN:
        # sshfs-win: 把远端 uid/gid 映射到当前用户, 避免权限错乱
        opts.extend([
            "idmap=user",
            "uid=-1",
            "gid=-1",
            "umask=000",
            "create_file_umask=0644",
            "create_dir_umask=0755",
            f"volname={host_label}",
        ])
    else:
        opts.append("follow_symlinks")

    if jump_host:
        opts.append(f"ProxyJump={jump_host}")

    sshfs_bin = _sshfs_bin()
    cmd = [sshfs_bin, "-o", ",".join(opts),
           f"{ssh_target}:{project_dir}", LOCAL_MOUNT]

    if IS_WIN:
        # Windows: sshfs-win 前台运行, 用 Popen 后台启动, 进程存活即挂载存活
        try:
            creationflags = 0
            if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            _mount_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
            )
        except Exception as e:
            return False, f"sshfs 挂载异常: {e}"

        # 等待挂载完成或失败
        # os.path.ismount() 在 WinFsp 挂载完成前返回 False，完成后返回 True
        deadline = time.time() + 15
        while time.time() < deadline:
            if _mount_proc.poll() is not None:
                err = b""
                try:
                    err = _mount_proc.stderr.read() or b""
                except Exception:
                    pass
                msg = err.decode(errors="replace").strip() or "进程已退出"
                _mount_proc = None
                return False, f"sshfs 挂载失败: {msg}"
            try:
                if os.path.ismount(LOCAL_MOUNT):
                    state["mount_point"] = LOCAL_MOUNT
                    return True, f"已挂载 {ssh_target}:{project_dir} → {LOCAL_MOUNT}"
            except OSError:
                pass
            time.sleep(0.3)
        return False, "sshfs 挂载超时"

    # macOS / Linux: sshfs 默认 daemonize, run 即可
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return False, "sshfs 挂载超时"
    except Exception as e:
        return False, f"sshfs 挂载异常: {e}"

    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip() or "未知错误"
        return False, f"sshfs 挂载失败: {msg}"

    state["mount_point"] = LOCAL_MOUNT
    return True, f"已挂载 {ssh_target}:{project_dir} → {LOCAL_MOUNT}"

# ── 远端进程管理器 ─────────────────────────────────────────────
class RemoteProxy:
    """管理到远端 claude mcp serve 的 SSH 连接和 MCP 代理."""

    def __init__(self):
        self.proc = None          # subprocess.Popen
        self.reader_thread = None # 读取远端 stdout 的后台线程
        self.lock = threading.Lock()
        self.pending = {}         # id → response (用于同步等待)
        self.notifications = []   # 收到的通知消息
        self.next_local_id = 100000  # 转发时用的本地 ID
        self._stop = False

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, state: dict):
        """启动远端 claude mcp serve 进程并完成 MCP 握手."""
        self.stop()

        ssh_target = state["ssh_target"]
        project_dir = state["project_dir"]
        claude_bin = state.get("claude_bin", "claude")
        jump_host = state.get("jump_host", "")
        env_setup = state.get("env_setup", "")

        # 构建 SSH 命令
        ssh_cmd = ["ssh", "-T", "-o", "BatchMode=yes",
                   "-o", "ConnectTimeout=10",
                   "-o", "ServerAliveInterval=60",
                   "-o", "ServerAliveCountMax=5"]
        if jump_host:
            ssh_cmd += ["-J", jump_host]
        ssh_cmd.append(ssh_target)

        # 远端命令
        remote_cmd = ""
        if env_setup:
            remote_cmd += f"{{ {env_setup}; }} && "
        remote_cmd += f"cd '{project_dir}' && exec '{claude_bin}' mcp serve"
        ssh_cmd.append(remote_cmd)

        # 启动进程
        self.proc = subprocess.Popen(
            ssh_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._stop = False
        self.pending = {}
        self.notifications = []

        # 后台线程读取远端 stdout
        self.reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self.reader_thread.start()

        # 后台线程读取远端 stderr（日志）
        threading.Thread(target=self._stderr_loop, daemon=True).start()

        # MCP 握手
        init_resp = self._rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                               "params": {"protocolVersion": "2024-11-05",
                                          "capabilities": {},
                                          "clientInfo": {"name": "ssh-bridge", "version": "1.0"}}})
        if "error" in init_resp:
            raise RuntimeError(f"远端 MCP 握手失败: {init_resp['error']}")

        self._rpc({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        return init_resp.get("result", {})

    def stop(self):
        """停止远端进程."""
        self._stop = True
        if self.proc:
            try:
                self.proc.stdin.close()
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        self.reader_thread = None

    def _read_loop(self):
        """后台线程：持续读取远端 stdout，分发响应和通知."""
        while not self._stop and self.proc and self.proc.poll() is None:
            try:
                line = self.proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                msg = json.loads(line)
                msg_id = msg.get("id")
                if msg_id is not None and msg_id in self.pending:
                    # 这是对某个请求的响应
                    with self.lock:
                        self.pending[msg_id] = msg
                elif "method" in msg:
                    # 通知消息
                    with self.lock:
                        self.notifications.append(msg)
                # 否则忽略（未知响应）
            except json.JSONDecodeError:
                pass
            except Exception:
                break

    def _stderr_loop(self):
        """后台线程：读取远端 stderr 输出到本地 stderr."""
        while not self._stop and self.proc and self.proc.poll() is None:
            try:
                line = self.proc.stderr.readline()
                if not line:
                    break
                sys.stderr.write(f"[remote] {line}")
                sys.stderr.flush()
            except Exception:
                break

    def _rpc(self, request: dict, timeout: float = 30) -> dict:
        """发送 JSON-RPC 请求到远端，同步等待响应."""
        if not self.is_alive():
            return {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -1, "message": "远端未连接"}}

        # 有 id 的请求才等待响应
        msg_id = request.get("id")
        if msg_id is not None:
            with self.lock:
                self.pending[msg_id] = None

        try:
            req_str = json.dumps(request, ensure_ascii=False) + "\n"
            self.proc.stdin.write(req_str)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            self.stop()
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -1, "message": f"SSH 连接断开: {e}"}}

        if msg_id is None:
            return {}  # 通知类消息，不等待响应

        # 轮询等待响应
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                if self.pending.get(msg_id) is not None:
                    resp = self.pending.pop(msg_id)
                    return resp
            if not self.is_alive():
                return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -1, "message": "SSH 进程已退出"}}
            time.sleep(0.05)

        with self.lock:
            self.pending.pop(msg_id, None)
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -1, "message": "远端响应超时"}}

    def drain_notifications(self) -> list:
        """取出并清空通知消息."""
        with self.lock:
            msgs = self.notifications[:]
            self.notifications.clear()
            return msgs

    def forward(self, request: dict, timeout: float = 60) -> dict:
        """转发请求到远端并返回响应."""
        # 为转发请求分配新 ID（避免与本地 ID 冲突）
        local_id = request.get("id")
        new_id = self.next_local_id
        self.next_local_id += 1
        if self.next_local_id > 999999:
            self.next_local_id = 100000

        fwd = dict(request)
        fwd["id"] = new_id
        resp = self._rpc(fwd, timeout=timeout)
        # 恢复原始 ID
        if "id" in resp:
            resp["id"] = local_id
        return resp


# ── 全局代理实例 ───────────────────────────────────────────────
remote = RemoteProxy()

# ── 管理工具 (本地处理) ────────────────────────────────────────

MANAGEMENT_TOOLS = {
    "ssh_connect": {
        "name": "ssh_connect",
        "description": "连接/切换到远端 Linux 机器上的 Claude Code。成功后会自动发现远端全部工具。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "主机名称(hosts.tsv)或 user@host"},
                "project_dir": {"type": "string", "description": "远端项目目录(清单中已配置可省略)"},
            },
            "required": ["host"]
        }
    },
    "ssh_disconnect": {
        "name": "ssh_disconnect",
        "description": "断开当前 SSH 连接。",
        "inputSchema": {"type": "object", "properties": {}}
    },
    "ssh_folder": {
        "name": "ssh_folder",
        "description": "切换远端工作目录（断开重连到新目录）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "远端目录绝对路径"},
            },
            "required": ["path"]
        }
    },
    "ssh_status": {
        "name": "ssh_status",
        "description": "查看当前连接状态和主机清单。",
        "inputSchema": {"type": "object", "properties": {}}
    },
}

def handle_ssh_connect(args: dict) -> str:
    host_ref = args.get("host", "").strip()
    project_dir = args.get("project_dir", "").strip()

    host_info = find_host(host_ref)
    if host_info:
        ssh_target = host_info["ssh_target"]
        project_dir = project_dir or host_info["project_dir"]
        claude_bin = host_info.get("claude_bin", "claude")
        jump_host = host_info.get("jump_host", "")
        env_setup = host_info.get("env_setup", "")
    else:
        if not host_ref or not project_dir:
            return "错误: 主机不在清单中，需同时指定 host 和 project_dir"
        ssh_target = host_ref
        claude_bin = "claude"
        jump_host = ""
        env_setup = ""

    # 快速连通性检查
    ssh_check = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if jump_host:
        ssh_check += ["-J", jump_host]
    ssh_check += [ssh_target, "true"]
    try:
        r = subprocess.run(ssh_check, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return f"SSH 连接失败: {ssh_target}\n{r.stderr}"
    except subprocess.TimeoutExpired:
        return f"连接超时: {ssh_target}"
    except Exception as e:
        return f"错误: {e}"

    # 检查目录
    dir_check = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if jump_host:
        dir_check += ["-J", jump_host]
    dir_check += [ssh_target, f"test -d '{project_dir}' && test -r '{project_dir}'"]
    r = subprocess.run(dir_check, capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        return f"目录不存在或不可读: {project_dir}"

    # 断开旧连接
    if remote.is_alive():
        remote.stop()

    # 保存状态
    state = {
        "connected": True,
        "host": host_ref,
        "ssh_target": ssh_target,
        "project_dir": project_dir,
        "claude_bin": claude_bin,
        "jump_host": jump_host,
        "env_setup": env_setup,
    }
    save_state(state)

    # 启动远端 claude mcp serve
    try:
        remote.start(state)
    except Exception as e:
        save_state({"connected": False})
        return f"启动远端 Claude Code 失败: {e}"

    # sshfs 挂载远端目录到本地 cwd (失败不阻塞,只提示)
    mount_ok, mount_msg = mount_remote(state)
    save_state(state)

    jump_info = f", 跳板: {jump_host}" if jump_host else ""
    lines = [
        f"已连接 {ssh_target} → 远端 Claude Code 就绪 (项目: {project_dir}{jump_info})",
        "远端工具已自动加载,现在可以直接在远端机器上操作。",
    ]
    prefix = "[挂载 ✓]" if mount_ok else "[挂载 ✗]"
    lines.append(f"{prefix} {mount_msg}")
    return "\n".join(lines)

def handle_ssh_disconnect(args: dict) -> str:
    state = load_state()
    host = state.get("ssh_target", "unknown")
    mount_point = state.get("mount_point", "")
    remote.stop()
    unmount_msg = ""
    if mount_point:
        if unmount_path(mount_point):
            unmount_msg = f"\n已卸载本地挂载: {mount_point}"
        else:
            unmount_msg = f"\n警告: 卸载 {mount_point} 失败,可能需要手动 umount"
    save_state({"connected": False})
    return f"已断开 {host}{unmount_msg}"

def handle_ssh_folder(args: dict) -> str:
    new_dir = args.get("path", "").strip()
    if not new_dir:
        return "错误: 请指定 path"

    state = load_state()
    if not state.get("connected"):
        return "未连接。请先 /ssh-connect"

    # 卸载旧挂载
    old_mount = state.get("mount_point", "")
    if old_mount:
        unmount_path(old_mount)
        state.pop("mount_point", None)

    state["project_dir"] = new_dir

    # 重启远端进程到新目录
    remote.stop()
    try:
        remote.start(state)
    except Exception as e:
        save_state(state)
        return f"切换失败: {e}"

    # 重新挂载到新目录
    mount_ok, mount_msg = mount_remote(state)
    save_state(state)

    lines = [f"已切换到 {new_dir}", "(远端 Claude Code 已在新目录重启)"]
    lines.append(f"[挂载] {mount_msg}")
    return "\n".join(lines)

def handle_ssh_status(args: dict) -> str:
    state = load_state()
    hosts = load_hosts()

    lines = ["=== SSH 连接 ==="]
    if remote.is_alive():
        lines.append(f"状态:   已连接")
        lines.append(f"主机:   {state.get('ssh_target', '?')}")
        lines.append(f"目录:   {state.get('project_dir', '?')}")
        j = state.get("jump_host")
        if j:
            lines.append(f"跳板:   {j}")
        e = state.get("env_setup")
        if e:
            lines.append(f"环境:   {e}")
        lines.append(f"远端工具: 已加载")
        mp = state.get("mount_point")
        if mp and is_mount(mp):
            lines.append(f"挂载点: {mp} (sshfs)")
        else:
            ok, hint = check_sshfs()
            lines.append(f"挂载点: 未挂载{' — ' + hint.splitlines()[0] if not ok else ''}")
    else:
        lines.append("状态:   未连接")

    lines.append("")
    lines.append("=== 主机清单 ===")
    for h in hosts:
        mark = " ← 当前" if state.get("ssh_target") == h["ssh_target"] and remote.is_alive() else ""
        lines.append(f"  {h['name']} → {h['ssh_target']} ({h['project_dir']}){mark}")
    if not hosts:
        lines.append("  (无)")

    return "\n".join(lines)

MANAGEMENT_HANDLERS = {
    "ssh_connect": handle_ssh_connect,
    "ssh_disconnect": handle_ssh_disconnect,
    "ssh_folder": handle_ssh_folder,
    "ssh_status": handle_ssh_status,
}

# ── MCP 协议处理 ────────────────────────────────────────────────
def build_local_tools() -> list:
    """构建本地工具列表（始终可用的管理工具 + 远端工具如果已连接）."""
    tools = list(MANAGEMENT_TOOLS.values())

    if remote.is_alive():
        # 从远端获取工具列表
        resp = remote.forward(
            {"jsonrpc": "2.0", "id": 999001, "method": "tools/list", "params": {}},
            timeout=10
        )
        if "result" in resp:
            remote_tools = resp["result"].get("tools", [])
            # 远端工具重命名为 remote_<name> 避免与本地工具冲突
            for t in remote_tools:
                t = dict(t)
                t["name"] = f"remote_{t['name']}"
                t["description"] = f"[远端] {t.get('description', '')}"
                tools.append(t)

    return tools

def send_list_changed():
    """通知客户端工具列表已变更."""
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}) + "\n")
    sys.stdout.flush()

def is_local_tool(name: str) -> bool:
    return name in MANAGEMENT_HANDLERS

def handle_request(req: dict) -> dict | None:
    msg_id = req.get("id")
    method = req.get("method", "")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ssh-bridge", "version": "2.0.0"}
            }
        }

    elif method == "notifications/initialized":
        return None

    elif method == "tools/list":
        tools = build_local_tools()
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}}

    elif method == "tools/call":
        tool_name = req.get("params", {}).get("name", "")
        tool_args = req.get("params", {}).get("arguments", {})

        # 本地管理工具
        if is_local_tool(tool_name):
            try:
                handler = MANAGEMENT_HANDLERS[tool_name]
                text = handler(tool_args)

                # connect/disconnect/folder 会改变工具列表
                if tool_name in ("ssh_connect", "ssh_disconnect", "ssh_folder"):
                    send_list_changed()

                return {
                    "jsonrpc": "2.0", "id": msg_id,
                    "result": {"content": [{"type": "text", "text": text}]}
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0", "id": msg_id,
                    "result": {"content": [{"type": "text", "text": f"错误: {e}"}], "isError": True}
                }

        # 远端工具 (remote_XXX)
        if tool_name.startswith("remote_"):
            if not remote.is_alive():
                return {
                    "jsonrpc": "2.0", "id": msg_id,
                    "result": {"content": [{"type": "text", "text": "未连接远端。请先 /ssh-connect"}], "isError": True}
                }
            # 转发到远端 (去掉 remote_ 前缀)
            remote_name = tool_name[7:]  # remove "remote_"
            fwd_req = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "method": "tools/call",
                "params": {"name": remote_name, "arguments": tool_args}
            }
            resp = remote.forward(fwd_req, timeout=120)

            # 转发过程中产生的通知消息，直接输出
            for notif in remote.drain_notifications():
                sys.stdout.write(json.dumps(notif, ensure_ascii=False) + "\n")
                sys.stdout.flush()

            return resp

        # 未知工具
        return {
            "jsonrpc": "2.0", "id": msg_id,
            "error": {"code": -32601, "message": f"未知工具: {tool_name}"}
        }

    elif method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    else:
        # 未知方法 → 如果已连接，尝试转发到远端
        if remote.is_alive() and msg_id is not None:
            return remote.forward(req)
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"未知: {method}"}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    debug = args.debug

    # 检查是否需要恢复上次连接
    state = load_state()
    if state.get("connected"):
        if debug:
            sys.stderr.write(f"[ssh-mcp] 恢复连接: {state.get('ssh_target')}\n")
            sys.stderr.flush()
        try:
            remote.start(state)
            # 恢复挂载 (失败不阻塞)
            mount_ok, mount_msg = mount_remote(state)
            save_state(state)
            if debug:
                sys.stderr.write(f"[ssh-mcp] 恢复成功 | 挂载: {mount_msg}\n")
                sys.stderr.flush()
        except Exception as e:
            if debug:
                sys.stderr.write(f"[ssh-mcp] 恢复失败: {e}\n")
                sys.stderr.flush()
            save_state({"connected": False})

    if debug:
        sys.stderr.write(f"[ssh-mcp] 就绪\n")
        sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            if debug:
                sys.stderr.write(f"[ssh-mcp] JSON错误\n")
                sys.stderr.flush()
            continue

        if debug:
            method = req.get("method", "?")
            tid = req.get("id", "?")
            pname = req.get("params", {}).get("name", "")
            sys.stderr.write(f"[ssh-mcp] ← {method} id={tid} {pname}\n")
            sys.stderr.flush()

        resp = handle_request(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()

            if debug:
                sys.stderr.write(f"[ssh-mcp] → id={resp.get('id')}\n")
                sys.stderr.flush()


if __name__ == "__main__":
    main()
