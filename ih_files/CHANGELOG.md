# Changelog

## [0.2.4] - 2026-11-27
- Apply the active Intelligent Home appearance across add-ons.

## [0.2.3] - 2026-11-27
- Update the shared Intelligent Home visual theme and components.

## [0.2.2] - 2026-11-25

### Added
- IH · Files page in the Home Assistant sidebar (read-only): running state and version, shares with their folders and read-only/read-write access by person, login names (never passwords or hashes), who is connected now (smbstatus), free space on the data volume and the SMB port.
- Small threaded Python server (stdlib only) on ingress port 8826; it answers only the Supervisor gateway. `python3` added to the image.
- Sharing itself is unchanged (same smb.conf, same users, same port mapping).

## [0.2.1] - 2026-11-14

### Fixed
- Image build failed: Alpine 3.20 has no `samba-vfs-modules` package. The vfs modules (`fruit`, `streams_xattr`, `catia`) ship in `samba-server`, so the package is no longer requested.

## [0.2.0] - 2026-11-14

### Added
- First IH release, ported from the local House files add-on 0.1.7.
- Apple settings: `vfs objects = catia fruit streams_xattr`, `fruit:metadata = stream`, `fruit:model = MacSamba`, `ea support = yes`.
- `setfacl -R -b` on `andrew` and `family` at start.
- `samba-vfs-modules` and `acl` packages in the image.
