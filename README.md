# Radxa Duck

以 Radxa 名义开展的小鸭子机器人项目，当前主控为 Radxa ZERO 3W。本仓库负责感知层与应用层，应用代码将重新开发。

当前仅保留可直接复用的配置与硬件交接材料：

| 路径 | 内容 |
| --- | --- |
| [AGENTS.md](AGENTS.md) | 项目范围、硬件基线、交互要求与开发约定 |
| `.env` | 本地 DeepSeek API 配置，权限 0600，已被 Git 忽略 |
| [.env.example](.env.example) | 不含真实密钥的配置示例 |
| [hardware/pollen-hat-c1-handoff](hardware/pollen-hat-c1-handoff/README.md) | 已验证的 HAT 驱动交接包与保留 NPU 的安装适配 |

应用开发从本项目开始，不依赖此前的工作目录。旧原型代码、模型、参考源码及评测产物不纳入本项目；板端已有的部署、SSH 配置与系统服务保持现状。

目前没有应用启动或测试入口；`.env` 尚无应用自动加载，后续接入云端时使用。本地密钥配置随当前工作区保存，Git 检出不包含该文件。
