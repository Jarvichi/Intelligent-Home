# Intelligent Home Files

Per-person Samba file shares: `andrew`, `claire`, `rob` (private), `family`, `guest` (5 GB cap, 30-day clean-up) and `brain` (Andrew only).

- **Family:** Files. Stand-alone.
- **Starts safe:** all passwords are `REPLACE_ME` (login off) until the owner types their own.
- Replaces the local `House files (per-person)` add-on (0.1.6). 0.2.0 adds the Apple settings (`fruit`, `streams_xattr`, `catia`, `ea support`) and clears ACLs on `andrew` and `family`, so iPhone Files no longer shows every folder read-only.
- Test port is 4451 (the old add-on uses 4450). Move to 445 only after the old share is stopped.
- Version history: [CHANGELOG.md](CHANGELOG.md)
