---
name: ssh-connect
description: 连接/切换到远端 Linux 机器上的 Claude Code
---

# /ssh-connect — 连接远端 Claude Code

连接远端 Linux 机器上的 Claude Code。连接后远端全部工具以 `remote_` 前缀自动可用。

## 用法

```
/ssh-connect <主机名>
```

## 示例

```
/ssh-connect pc-130
```

## 执行

1. 调用 `ssh_status` 查看可用主机
2. 调用 `ssh_connect`，参数 `{"host": "<主机名>"}
3. 远端工具自动加载（带 `remote_` 前缀）

切换主机：直接 `/ssh-connect <另一台>` 即可。
