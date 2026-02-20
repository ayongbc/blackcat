# OpenClaw 工作区 Skills

本目录是 **OpenClaw** 的「工作区 skills」目录。用 OpenClaw 打开本项目时，会从这里加载 skill，无需额外配置。

## 当前 skill

- **openclaw-project-usage**：本项目执行说明（流水线、回测、选股、输出文件命名等）。  
  当 OpenClaw 需要跑流水线、回测、选股或查看 `bt_config.yaml` / 产出路径时会自动参考。

## 若 OpenClaw 未打开本项目

若你是从**其他工作区**用 OpenClaw，仍想用本项目的 skill，可以二选一：

1. **把本目录加入全局配置**  
   在 `~/.openclaw/openclaw.json` 的 `skills.load.extraDirs` 里添加本项目路径，例如：  
   `"/Users/betta/work/python/skills"`  
   （路径需为绝对路径或 `~` 展开后的路径。）

2. **复制到本地 managed skills**  
   将 `openclaw-project-usage` 复制到 `~/.openclaw/skills/openclaw-project-usage`，则本机所有 OpenClaw 会话都会加载。

详见 [OpenClaw Skills 文档](https://docs.openclaw.ai/skills)。
