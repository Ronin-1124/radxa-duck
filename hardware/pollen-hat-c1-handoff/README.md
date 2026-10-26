# Radxa Duck 的 Pollen HAT C1 驱动交接材料

本项目沿用已经在 Radxa ZERO 3W 上验证的 Pollen HAT C1 麦克风和扬声器通路。这里保留可直接复用的驱动交接包及安装适配，应用层另行开发。

- `hat-c1-simple/`：原始交接包，包含驱动模块、设备树 overlay、源码及许可证、初始化服务、录放音 helper、测试音和使用说明；保留原件与 `SHA256SUMS`。
- `install-with-npu.sh`：安装适配，保留音频与 NPU overlay、已验证内核及恢复启动项，提供检查、备份和失败回滚，不自动重启。

硬件验证基线为 2026-10-23：Debian 12、aarch64、内核 `6.1.84-10-rk2410-nocsf`。这些模块与内核、基础 DTB 匹配，不能直接套用到其他系统镜像。声卡为 `PollenHATC1`，硬件格式是 48 kHz 双声道 PCM16，当前麦克风有效信号在左声道。

在本项目目录中检查交接包完整性：

```sh
cd hardware/pollen-hat-c1-handoff/hat-c1-simple
sha256sum -c SHA256SUMS
```

板端已安装音频驱动和系统级 `pollen-hat-audio-init.service`，录放音 helper 为 `/opt/pollen-hat-audio/audio.py`。本次迁移没有重新安装或改变板端配置。后续确需安装时先阅读适配脚本，默认 `--check` 只检查，`--install` 才写入系统；保留 `pollen-hat-c1-audio.dtbo` 与 `rk3568-npu-enable.dtbo`。

独立 `audio.py` 不具备应用级录放互斥，不能与其他录放程序同时操作声卡。测试音是单音，不是机器人回复音效。原始包内说明及文件名保持原样。
