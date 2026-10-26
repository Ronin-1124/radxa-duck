# Pollen HAT C1 驱动与音频配置

Radxa ZERO 3W 与 Pollen Robot HAT C1（TLV320AIC3104）的声卡驱动、设备树 overlay 和初始化服务。整个目录可直接复制到板端使用。

| 文件 | 作用 |
| --- | --- |
| `install.sh` | 安装 HAT 音频：内核模块、音频 overlay、开机初始化服务、`audio.py` |
| `install-with-npu.sh` | 在保留 `rk3568-npu-enable.dtbo` 的前提下安装同一套音频配置；校验合并后的设备树，失败自动回滚 |
| `audio.py` | ALSA 录放音 helper，安装到 `/opt/pollen-hat-audio/audio.py`，用法见其 `--help` |
| `payload/` | 预编译模块、overlay、systemd 服务、混音初始状态、测试音 |
| `source/` | 模块与设备树源码，GPL-2.0-only，许可证见 `source/COPYING` |

## 适用环境

Debian 12、aarch64、内核 `6.1.84-10-rk2410-nocsf`、ZERO 3W aic8800ds2 固件，预编译模块与内核及基础 DTB 绑定。

## 安装

在板端本目录执行，使用 `--check` 参数只进行检查：

```sh
sudo bash install.sh --check
sudo bash install-with-npu.sh --check
```

确认无误后写入系统：

```sh
sudo bash install.sh --install
```

在需要保留 NPU overlay 时使用后者：

```sh
sudo bash install-with-npu.sh --install
```

脚本会将 `extlinux.conf` 和 `/etc/default/u-boot` 等系统启动文件备份到 `/var/backups/pollen-hat-audio-*/`（权限 700，需 `sudo`），用途是安装失败时回滚以及日后卸载时还原。装好 HAT 并重启后确认：

```sh
cat /proc/asound/cards                                  # 应有 PollenHATC1
systemctl status pollen-hat-audio-init.service --no-pager
```

## 注意

- 硬件格式为 48 kHz、双声道、PCM16；当前有效麦克风信号在左声道。
- `audio.py` 不提供应用级录放互斥，不要与其他录放程序同时占用声卡。
- `payload/test-tone.wav` 为单音测试。
