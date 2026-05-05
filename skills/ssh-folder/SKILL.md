---
name: ssh-folder
description: 切换远端工作目录
---

# /ssh-folder — 切换远端工作目录

## 用法

```
/ssh-folder <远端绝对路径>
```

## 示例

```
/ssh-folder /civi/re-algo
```

## 执行

调用 `ssh_folder`，参数 `{"path": "<路径>"}`。
