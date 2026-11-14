#!/bin/bash
# House files add-on. Reads /data/options.json. Exports ONLY /share/files/* and /share/brain.
# DRY_RUN=1 OPTIONS=<file> CONF_OUT=<file> prints the smb.conf and exits (no users, no folders).
set -e
OPTIONS="${OPTIONS:-/data/options.json}"
CONF="${CONF_OUT:-/etc/samba/smb.conf}"
ROOT=/share/files
HOSTS="127.0.0.1 $(jq -r '.allow_hosts | join(" ")' "$OPTIONS")"
CAP=$(jq -r '.guest_cap_gb' "$OPTIONS"); DAYS=$(jq -r '.guest_days' "$OPTIONS")

cat > "$CONF" <<CONFEOF
[global]
  workgroup = WORKGROUP
  server string = House files
  server min protocol = SMB2
  smb ports = 445
  disable netbios = yes
  map to guest = Never
  restrict anonymous = 2
  hosts allow = $HOSTS
  hosts deny = 0.0.0.0/0
  follow symlinks = no
  wide links = no
  unix extensions = no
  load printers = no
  printing = bsd
  printcap name = /dev/null
  disable spoolss = yes
  logging = file
  log file = /share/files/_samba_log/log.%m
  log level = 2
  max log size = 1000
  # Apple clients (iPhone Files, Mac) need these or show folders read-only
  vfs objects = catia fruit streams_xattr
  fruit:metadata = stream
  fruit:model = MacSamba
  fruit:posix_rename = yes
  fruit:nfs_aces = no
  ea support = yes

[andrew]
  path = $ROOT/andrew
  valid users = andrew
  read only = no
  create mask = 0600
  directory mask = 0700

[claire]
  path = $ROOT/claire
  valid users = claire
  read only = no
  create mask = 0600
  directory mask = 0700

[rob]
  path = $ROOT/rob
  valid users = rob
  read only = no
  create mask = 0600
  directory mask = 0700

[family]
  path = $ROOT/family
  valid users = andrew claire rob
  force group = hf_family
  read only = no
  create mask = 0660
  directory mask = 0770

[guest]
  path = $ROOT/guest
  valid users = andrew claire rob guest
  force group = hf_guest
  read only = no
  create mask = 0660
  directory mask = 0770

[brain]
  comment = Andrew only (Obsidian vault)
  path = /share/brain
  valid users = andrew
  force user = root
  read only = no
CONFEOF
[ "${DRY_RUN:-0}" = 1 ] && { echo "dry run ok: cap=$CAP days=$DAYS"; exit 0; }

mkdir -p /var/log/samba "$ROOT"/{andrew,claire,rob,family,guest} "$ROOT/_samba_log"; chmod 700 "$ROOT/_samba_log"
addgroup -S hf_family 2>/dev/null || true
addgroup -S hf_guest 2>/dev/null || true
for u in andrew claire rob guest; do
  id "$u" >/dev/null 2>&1 || adduser -D -H -s /sbin/nologin "$u"
done
for u in andrew claire rob; do adduser "$u" hf_family; adduser "$u" hf_guest; done
adduser guest hf_guest
# passwords: from options only, never printed
set_pw() { printf '%s\n%s\n' "$2" "$2" | smbpasswd -s -a "$1" >/dev/null; }
for u in andrew claire rob; do
  pw=$(jq -r --arg n "$u" '.users[] | select(.name==$n) | .password' "$OPTIONS")
  [ -n "$pw" ] && [ "$pw" != "REPLACE_ME" ] && set_pw "$u" "$pw" || echo "user $u has no password set: login disabled"
done
gpw=$(jq -r '.guest_password' "$OPTIONS")
[ "$gpw" != "REPLACE_ME" ] && set_pw guest "$gpw" || echo "guest has no password set: login disabled"
for u in andrew claire rob; do chown "$u:$u" "$ROOT/$u"; chmod 700 "$ROOT/$u"; done
chown root:hf_family "$ROOT/family"; chmod 2770 "$ROOT/family"
chown root:hf_guest "$ROOT/guest"; chmod 2770 "$ROOT/guest"
chmod 755 "$ROOT"
# clear stray ACL entries (the + on ls -l) that make Apple clients see read-only
for d in andrew family; do setfacl -R -b "$ROOT/$d" 2>/dev/null || true; done
# guest clean-up every 15 minutes
echo "*/15 * * * * /cleanup.sh $ROOT/guest $DAYS $CAP >> /var/log/cleanup.log 2>&1" > /etc/crontabs/root
crond -b -L /var/log/crond.log
/cleanup.sh "$ROOT/guest" "$DAYS" "$CAP" || true
exec smbd --foreground --no-process-group --debug-stdout
