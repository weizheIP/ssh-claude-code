---
name: ssh-connect
description: 连接/切换到远端 Linux 机器上的 Claude Code
---

# /ssh-connect — 连接远端 Claude Code

连接远端 Linux 机器上的 Claude Code。连接后远端全部工具以 `remote_` 前缀自动可用。

## 用法

```
/ssh-connect <主机名> [项目目录]
```

- `主机名`：hosts.tsv 中配置的名称，或 `user@host` 格式
- `项目目录`（可选）：覆盖 hosts.tsv 中的默认目录

## 示例

```
/ssh-connect pc-130
/ssh-connect pc-130 /civi/re-algo
/ssh-connect dev@10.0.0.50 /srv/backend
```

## 执行

1. 调用 `ssh_status` 查看可用主机
2. 调用 `ssh_connect`，参数 `{"host": "<主机名>", "project_dir": "<项目目录>"}`
   - 若用户未指定项目目录，`project_dir` 省略（使用 hosts.tsv 中的默认值）
3. 远端工具自动加载（带 `remote_` 前缀）

切换主机：直接 `/ssh-connect <另一台>` 即可。
