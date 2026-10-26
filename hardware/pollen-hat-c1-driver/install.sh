#!/bin/bash
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
ROOT=$PWD
KERNEL=6.1.84-10-rk2410-nocsf
MODE=${1:---install}
die() { echo "ERROR: $*" >&2; exit 1; }
[[ $# -le 1 && ( $MODE == --check || $MODE == --install ) ]] || die 'Usage: sudo bash install.sh [--check]'
[[ $EUID -eq 0 ]] || die 'Run with sudo bash install.sh'
[[ $(uname -r) == "$KERNEL" && $(uname -m) == aarch64 ]] || die "Requires aarch64 kernel $KERNEL; no changes made."
grep -zqx 'radxa,zero3w-aic8800ds2' /proc/device-tree/compatible || die 'Requires the tested ZERO 3W aic8800ds2 board firmware.'
for cmd in python3 sha256sum modinfo fdtoverlay u-boot-update depmod systemctl alsactl amixer aplay arecord; do
  command -v "$cmd" >/dev/null || die "Missing command: $cmd"
done
(. /etc/os-release; [[ $ID == debian && $VERSION_ID == 12 ]]) || die 'Requires Debian 12.'
BASE=/usr/lib/linux-image-$KERNEL/rockchip/rk3566-radxa-zero-3w-aic8800ds2.dtb
[[ -f $BASE && -f /etc/default/u-boot && -f /boot/extlinux/extlinux.conf ]] || die 'Expected Radxa boot files not found.'
BASE_HASH=$(sha256sum "$BASE"); BASE_HASH=${BASE_HASH%% *}
[[ $BASE_HASH == $(cat payload/base-dtb.sha256) ]] || die 'Base device tree differs from the tested firmware.'
[[ $(systemctl show -p Version --value) ]] || die 'Systemd is not available.'
for f in payload/modules/*.ko; do
  [[ $(modinfo -F vermagic "$f") == "$KERNEL "* ]] || die "Module kernel mismatch: $f"
done
# Refuse existing overlay changes instead of silently disabling other hardware.
for f in /boot/dtbo/*.dtbo; do
  [[ -e $f ]] || continue
  [[ ${f##*/} == pollen-hat-c1-audio.dtbo ]] || die "Another overlay is enabled: $f. Review and disable conflicting overlays first."
done
CURRENT_OVERLAYS=$(bash -c 'source /etc/default/u-boot; printf "%s" "${U_BOOT_FDT_OVERLAYS:-}"')
[[ -z $CURRENT_OVERLAYS || $CURRENT_OVERLAYS == pollen-hat-c1-audio.dtbo ]] || die 'Custom U_BOOT_FDT_OVERLAYS found; manual review required.'
TMP=$(mktemp -d)
trap 'rm -f -- "$TMP/merged.dtb"; rmdir -- "$TMP"' EXIT
fdtoverlay -i "$BASE" -o "$TMP/merged.dtb" payload/pollen-hat-c1-audio.dtbo
echo 'Environment, package checksums, module versions and device-tree merge: OK'
[[ $MODE == --install ]] || { echo 'CHECK ONLY: no system configuration changed.'; exit 0; }

BACKUP=$(mktemp -d /var/backups/pollen-hat-audio-XXXXXXXX)
TARGETS=(
  "lib/modules/$KERNEL/updates/hat-audio/i2c-gpio.ko"
  "lib/modules/$KERNEL/updates/hat-audio/snd-soc-tlv320aic3x.ko"
  "lib/modules/$KERNEL/updates/hat-audio/snd-soc-tlv320aic3x-i2c.ko"
  'boot/dtbo/pollen-hat-c1-audio.dtbo'
  'boot/extlinux/extlinux.conf'
  'etc/default/u-boot'
  'etc/systemd/system/pollen-hat-audio-init.service'
  'opt/pollen-hat-audio/audio.py'
  'opt/pollen-hat-audio/onboard-muted.state'
)
for f in "${TARGETS[@]}"; do
  if [[ -e /$f ]]; then (cd /; cp --parents -a -- "$f" "$BACKUP/"); else printf '%s\n' "$f" >> "$BACKUP/new-files.txt"; fi
done
printf '%s\n' "${TARGETS[@]}" > "$BACKUP/changed-paths.txt"
systemctl is-enabled pollen-hat-audio-init.service > "$BACKUP/service-enabled-before.txt" 2>&1 || true
echo "Original files backed up to $BACKUP"
trap 'echo "Installation failed. Do not reboot until configuration is reviewed. Backup: $BACKUP" >&2' ERR
install -d "/lib/modules/$KERNEL/updates/hat-audio" /boot/dtbo /opt/pollen-hat-audio
install -m 644 payload/modules/*.ko "/lib/modules/$KERNEL/updates/hat-audio/"
install -m 644 payload/pollen-hat-c1-audio.dtbo /boot/dtbo/pollen-hat-c1-audio.dtbo
install -m 644 payload/onboard-muted.state /opt/pollen-hat-audio/onboard-muted.state
install -m 644 audio.py /opt/pollen-hat-audio/audio.py
install -m 644 payload/pollen-hat-audio-init.service /etc/systemd/system/pollen-hat-audio-init.service
python3 - <<'PY'
from pathlib import Path
import re
p = Path('/etc/default/u-boot')
s = re.sub(r'^U_BOOT_FDT_OVERLAYS=.*\n?', '', p.read_text(), flags=re.M)
s = s.replace('# Pollen HAT C1 audio\n', '').rstrip()
p.write_text(s + '\n\n# Pollen HAT C1 audio\nU_BOOT_FDT_OVERLAYS="pollen-hat-c1-audio.dtbo"\n')
PY
depmod -a "$KERNEL"
u-boot-update
grep -Eq '^[[:space:]]*fdtoverlays[[:space:]].*/boot/dtbo/pollen-hat-c1-audio.dtbo' /boot/extlinux/extlinux.conf || die 'Generated boot entry does not contain the HAT overlay.'
systemctl daemon-reload
systemctl enable pollen-hat-audio-init.service
echo 'Installed. Shut down, disconnect power, align and fit the HAT, then power on.'
echo 'If already assembled correctly, reboot. Speaker stays muted. See README.md.'
