# Changelog

## [0.2.1] - 2026-11-14

### Fixed
- Image build failed: Alpine 3.20 has no `samba-vfs-modules` package. The vfs modules (`fruit`, `streams_xattr`, `catia`) ship in `samba-server`, so the package is no longer requested.

## [0.2.0] - 2026-11-14

### Added
- First IH release, ported from the local House files add-on 0.1.7.
- Apple settings: `vfs objects = catia fruit streams_xattr`, `fruit:metadata = stream`, `fruit:model = MacSamba`, `ea support = yes`.
- `setfacl -R -b` on `andrew` and `family` at start.
- `samba-vfs-modules` and `acl` packages in the image.
