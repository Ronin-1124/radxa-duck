#!/bin/bash
# Adapter for the reviewed handoff package; the original package stays intact.
# Default is read-only preflight. Explicit --install is required; never reboots.
set -Eeuo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ADAPTER_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd -- "$ADAPTER_ROOT/hat-c1-simple"
KERNEL=6.1.84-10-rk2410-nocsf
MODE=${1:---check}
NPU=/boot/dtbo/rk3568-npu-enable.dtbo
HAT=/boot/dtbo/pollen-hat-c1-audio.dtbo
BOOT_CONFIG=/boot/extlinux/extlinux.conf
SERVICE=pollen-hat-audio-init.service
TMP=
BACKUP=
INSTALL_STARTED=0
INSTALL_COMPLETE=0
die() { echo "ERROR: $*" >&2; exit 1; }

# Parse the real boot entries, retaining the original recovery sections byte for
# byte after u-boot-update. Refuse changes to normal boot's essential parameters.
boot_config() {
  python3 - "$1" "$2" "${3:-$BOOT_CONFIG}" "$KERNEL" "$NPU" "$HAT" <<'PY'
from pathlib import Path
import os
import re
import shlex
import stat
import sys

mode, original_path, generated_path, kernel, npu, hat = sys.argv[1:]
allowed = {npu, hat}
expected = [npu, hat]
label_re = re.compile(r'(?im)^[ \t]*label[ \t]+([^\s#]+)[^\n]*(?:\n|$)')

def fail(message):
    raise SystemExit('ERROR: boot configuration: ' + message)

def parse(text):
    matches = list(label_re.finditer(text))
    if not matches:
        fail('no LABEL entries')
    sections = [(m[1], text[m.start():matches[i+1].start() if i+1 < len(matches) else len(text)])
                for i, m in enumerate(matches)]
    if len({label for label, _ in sections}) != len(sections):
        fail('duplicate LABEL entries')
    return text[:matches[0].start()], sections

def values(section, key):
    return re.findall(r'(?im)^[ \t]*' + re.escape(key) + r'[ \t]+([^\n]*)$', section)

def overlays(section):
    lines = values(section, 'fdtoverlays')
    if len(lines) > 1:
        fail('multiple fdtoverlays lines in one entry')
    tokens = shlex.split(lines[0], comments=True) if lines else []
    if len(tokens) != len(set(tokens)) or any(token not in allowed for token in tokens):
        fail('unexpected or duplicate overlay: ' + repr(tokens))
    return tokens

def recovery(section):
    return bool(re.search(r'(?i)\b(?:rescue|recovery|single)\b|systemd\.unit=(?:rescue|emergency)\.target', section))

def current_normal(section):
    images = values(section, 'linux') + values(section, 'kernel')
    return images == ['/boot/vmlinuz-' + kernel] and not recovery(section)

def important(section):
    return {key: values(section, key) for key in ('linux', 'kernel', 'initrd', 'fdt', 'fdtdir', 'append')}

original = Path(original_path).read_text()
header, sections = parse(original)
for _, section in sections:
    overlays(section)
normal = {label for label, section in sections if current_normal(section)}
rescues = {label for label, section in sections if recovery(section)}
defaults = values(header, 'default')
if not normal or not rescues:
    fail('cannot identify both current-kernel normal and recovery entries')
if len(defaults) != 1 or defaults[0].strip() not in normal:
    fail('DEFAULT must identify a current-kernel normal entry')
if mode == 'preflight':
    print('Boot preflight: normal=' + ','.join(sorted(normal)) + '; recovery=' + ','.join(sorted(rescues)))
    raise SystemExit(0)
if mode != 'preserve':
    fail('unknown operation')

generated_file = Path(generated_path)
_, generated_sections = parse(generated_file.read_text())
updated = []
for label, section in sections:
    if label in normal:
        # u-boot-update may discover another installed kernel and renumber its
        # labels. Match the original kernel image, never its generated label.
        images = values(section, 'linux') + values(section, 'kernel')
        candidates = [candidate for _, candidate in generated_sections
                      if not recovery(candidate)
                      and values(candidate, 'linux') + values(candidate, 'kernel') == images]
        if len(candidates) != 1:
            fail('missing or ambiguous generated normal entry for ' + label)
        replacement = candidates[0]
        if not current_normal(replacement) or important(replacement) != important(section):
            fail('u-boot-update changed kernel, initrd, device-tree path or command line in ' + label)
        if overlays(replacement) != expected:
            fail('generated normal entry does not contain NPU then audio overlays: ' + label)
        replacement = re.sub(r'(?im)^([ \t]*label[ \t]+)[^\s#]+',
                             lambda match: match[1] + label, replacement, count=1)
        updated.append((label, replacement))
    else:
        # Includes recovery entries: preserve their complete original contents.
        updated.append((label, section))
result = header + ''.join(section for _, section in updated)
final_header, final_sections = parse(result)
final = dict(final_sections)
if set(final) != {label for label, _ in sections}:
    fail('final boot labels differ from the originals')
if final_header != header or any(final[label] != dict(sections)[label] for label in rescues):
    fail('recovery entry or original boot menu changed')
for label in normal:
    if overlays(final[label]) != expected:
        fail('final normal entry lacks the two explicit overlays')
metadata = generated_file.stat()
temporary = generated_file.with_name(generated_file.name + '.pollen-new')
try:
    with temporary.open('x') as output:
        output.write(result)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(temporary, stat.S_IMODE(metadata.st_mode))
    os.chown(temporary, metadata.st_uid, metadata.st_gid)
    temporary.replace(generated_file)
finally:
    if temporary.exists():
        temporary.unlink()
print('Boot verified: NPU + audio in normal entries; original recovery and boot menu preserved.')
PY
}

rollback() {
  local failed=0 f
  echo "Installation failed; restoring backed-up files from $BACKUP" >&2
  for f in "${TARGETS[@]}"; do
    if [[ -e $BACKUP/$f || -L $BACKUP/$f ]]; then
      cp -a --remove-destination -- "$BACKUP/$f" "/$f" || failed=1
    elif [[ -f $BACKUP/new-files.txt ]] && grep -Fxq -- "$f" "$BACKUP/new-files.txt"; then
      rm -f -- "/$f" || failed=1
    else
      echo "No rollback record for /$f" >&2
      failed=1
    fi
  done
  depmod -a "$KERNEL" || failed=1
  systemctl daemon-reload || failed=1
  if (( failed )); then
    echo "ROLLBACK INCOMPLETE. Do not reboot. Review $BACKUP and restore boot files first." >&2
  else
    echo 'Original files and service enable link restored; no reboot was performed.' >&2
  fi
}

finish() {
  local result=$?
  trap - EXIT ERR INT TERM
  set +e
  if (( INSTALL_STARTED && ! INSTALL_COMPLETE )); then
    rollback
    (( result )) || result=1
  fi
  [[ -z $TMP ]] || rm -rf -- "$TMP"
  exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[[ $# -le 1 && ( $MODE == --check || $MODE == --install ) ]] || die 'Usage: sudo bash install-with-npu.sh [--check|--install]'
[[ $EUID -eq 0 ]] || die 'Run with sudo bash install-with-npu.sh [--check|--install]'
[[ $(uname -r) == "$KERNEL" && $(uname -m) == aarch64 ]] || die "Requires aarch64 kernel $KERNEL; no changes made."
grep -zqx 'radxa,zero3w-aic8800ds2' /proc/device-tree/compatible || die 'Requires the tested ZERO 3W aic8800ds2 board firmware.'
for cmd in python3 sha256sum modinfo fdtoverlay fdtget u-boot-update depmod systemctl alsactl amixer aplay arecord; do
  command -v "$cmd" >/dev/null || die "Missing command: $cmd"
done
(. /etc/os-release; [[ $ID == debian && $VERSION_ID == 12 ]]) || die 'Requires Debian 12.'
sha256sum --strict --quiet -c SHA256SUMS || die 'Original package integrity check failed.'
BASE=/usr/lib/linux-image-$KERNEL/rockchip/rk3566-radxa-zero-3w-aic8800ds2.dtb
[[ -f $BASE && -f /etc/default/u-boot && -f $BOOT_CONFIG && -f $NPU ]] || die 'Expected Radxa boot files or existing NPU overlay not found.'
[[ ! -L /etc/default/u-boot && ! -L $BOOT_CONFIG ]] || die 'Symlinked boot configuration requires manual review.'
BASE_HASH=$(sha256sum "$BASE"); BASE_HASH=${BASE_HASH%% *}
[[ $BASE_HASH == $(cat payload/base-dtb.sha256) ]] || die 'Base device tree differs from the tested firmware.'
[[ $(systemctl show -p Version --value) ]] || die 'Systemd is not available.'
for f in payload/modules/*.ko; do
  [[ $(modinfo -F vermagic "$f") == "$KERNEL "* ]] || die "Module kernel mismatch: $f"
done
for f in /boot/dtbo/*.dtbo; do
  [[ -e $f ]] || continue
  [[ $f == "$NPU" || $f == "$HAT" ]] || die "Unexpected overlay: $f; no changes made."
done
CURRENT_OVERLAYS=$(bash -c 'source /etc/default/u-boot; printf "%s" "${U_BOOT_FDT_OVERLAYS:-}"')
python3 - "$CURRENT_OVERLAYS" <<'PY'
import shlex
import sys
items = shlex.split(sys.argv[1])
allowed = {'rk3568-npu-enable.dtbo', 'pollen-hat-c1-audio.dtbo',
           '/boot/dtbo/rk3568-npu-enable.dtbo', '/boot/dtbo/pollen-hat-c1-audio.dtbo'}
if any(item not in allowed for item in items) or len({item.rsplit('/', 1)[-1] for item in items}) != len(items):
    raise SystemExit('ERROR: unexpected or duplicate U_BOOT_FDT_OVERLAYS; manual review required.')
PY
TMP=$(mktemp -d)
cp -a -- "$BOOT_CONFIG" "$TMP/extlinux.before"
boot_config preflight "$TMP/extlinux.before"
fdtoverlay -i "$BASE" -o "$TMP/merged.dtb" "$NPU" payload/pollen-hat-c1-audio.dtbo
[[ $(fdtget "$TMP/merged.dtb" /npu@fde40000 status) == okay ]] || die 'Merged NPU node is not enabled.'
[[ $(fdtget "$TMP/merged.dtb" /i2s@fe430000 status) == okay ]] || die 'Merged I2S3 node is not enabled.'
[[ $(fdtget "$TMP/merged.dtb" /pollen-hat-sound simple-audio-card,name) == PollenHATC1 ]] || die 'Merged HAT sound card is missing.'
echo 'Environment, original package, module versions and combined NPU + audio device tree: OK'
[[ $MODE == --install ]] || { echo 'CHECK ONLY: no system configuration changed.'; exit 0; }

BACKUP=$(mktemp -d /var/backups/pollen-hat-audio-npu-XXXXXXXX)
TARGETS=(
  "lib/modules/$KERNEL/updates/hat-audio/i2c-gpio.ko"
  "lib/modules/$KERNEL/updates/hat-audio/snd-soc-tlv320aic3x.ko"
  "lib/modules/$KERNEL/updates/hat-audio/snd-soc-tlv320aic3x-i2c.ko"
  'boot/dtbo/pollen-hat-c1-audio.dtbo'
  'boot/extlinux/extlinux.conf'
  'etc/default/u-boot'
  'etc/systemd/system/pollen-hat-audio-init.service'
  'etc/systemd/system/multi-user.target.wants/pollen-hat-audio-init.service'
  'opt/pollen-hat-audio/audio.py'
  'opt/pollen-hat-audio/onboard-muted.state'
)
for f in "${TARGETS[@]}"; do
  if [[ -e /$f || -L /$f ]]; then (cd /; cp --parents -a -- "$f" "$BACKUP/"); else printf '%s\n' "$f" >> "$BACKUP/new-files.txt"; fi
done
printf '%s\n' "${TARGETS[@]}" > "$BACKUP/changed-paths.txt"
systemctl is-enabled "$SERVICE" > "$BACKUP/service-enabled-before.txt" 2>&1 || true
systemctl is-active "$SERVICE" > "$BACKUP/service-active-before.txt" 2>&1 || true
sha256sum "$BASE" "$NPU" > "$BACKUP/unchanged-boot-files.sha256"
cp -a -- "$ADAPTER_ROOT/install-with-npu.sh" "$BACKUP/install-with-npu.sh"
echo "Original files and service state backed up to $BACKUP"
INSTALL_STARTED=1
install -d "/lib/modules/$KERNEL/updates/hat-audio" /boot/dtbo /opt/pollen-hat-audio
install -m 644 payload/modules/*.ko "/lib/modules/$KERNEL/updates/hat-audio/"
install -m 644 payload/pollen-hat-c1-audio.dtbo "$HAT"
install -m 644 payload/onboard-muted.state /opt/pollen-hat-audio/onboard-muted.state
install -m 644 audio.py /opt/pollen-hat-audio/audio.py
install -m 644 payload/pollen-hat-audio-init.service "/etc/systemd/system/$SERVICE"
python3 - <<'PY'
from pathlib import Path
import re
path = Path('/etc/default/u-boot')
text = re.sub(r'^[ \t]*(?:export[ \t]+)?U_BOOT_FDT_OVERLAYS=.*\n?', '', path.read_text(), flags=re.M)
text = text.replace('# Pollen HAT C1 audio\n', '').replace('# Pollen HAT C1 audio with existing NPU\n', '').rstrip()
path.write_text(text + '\n\n# Pollen HAT C1 audio with existing NPU\n'
                'U_BOOT_FDT_OVERLAYS="rk3568-npu-enable.dtbo pollen-hat-c1-audio.dtbo"\n')
PY
depmod -a "$KERNEL"
u-boot-update
boot_config preserve "$BACKUP/boot/extlinux/extlinux.conf"
sha256sum --strict --quiet -c "$BACKUP/unchanged-boot-files.sha256" || die 'Base DTB or existing NPU overlay changed unexpectedly.'
systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl is-enabled --quiet "$SERVICE"
sync
INSTALL_COMPLETE=1
echo "Installed with NPU retained. Backup: $BACKUP"
echo 'No service was started and no reboot was performed. Speaker initialization runs on next boot.'
echo 'Shut down and disconnect power before fitting the HAT; reboot only when correctly assembled.'
