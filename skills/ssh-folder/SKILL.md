---
name: ssh-folder
description: 切换远端工作目录
---

# /ssh-folder — 切换远端工作目录

切换远端工作目录。会:
1. 卸载旧的 sshfs 挂载
2. 重启远端 `claude mcp serve` 到新目录
3. 重新挂载新目录到本地 cwd

## 用法

```
/ssh-folder <远端绝对路径>
```

## 示例

```
/ssh-folder /civi/re-algo
```

## 执行

调用 `ssh_folder`,参数 `{"path": "<路径>"}`。
