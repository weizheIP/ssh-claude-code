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

    jump_info = f", 跳板: {jump_host}" if jump_host else ""
    return f"已连接 {ssh_target} → 远端 Claude Code 就绪 (项目: {project_dir}{jump_info})\n远端工具已自动加载，现在可以直接在远端机器上操作。"

def handle_ssh_disconnect(args: dict) -> str:
    state = load_state()
    host = state.get("ssh_target", "unknown")
    remote.stop()
    save_state({"connected": False})
    return f"已断开 {host}"

def handle_ssh_folder(args: dict) -> str:
    new_dir = args.get("path", "").strip()
    if not new_dir:
        return "错误: 请指定 path"

    state = load_state()
    if not state.get("connected"):
        return "未连接。请先 /ssh-connect"

    state["project_dir"] = new_dir
    save_state(state)

    # 重启远端进程到新目录
    remote.stop()
    try:
        remote.start(state)
    except Exception as e:
        return f"切换失败: {e}"
    return f"已切换到 {new_dir}\n（远端 Claude Code 已在新目录重启）"

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
            if debug:
                sys.stderr.write(f"[ssh-mcp] 恢复成功\n")
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
