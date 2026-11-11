"""Fixed-file instruction store: read, back up, save. No HTTP, no tokens, no house text.

The caller fixes the file path, the private work folder and the short id server-side.
Nothing here accepts a path from a request.
"""
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
import time
from pathlib import Path

MAX_BYTES = 128 * 1024          # largest instruction file that is read or saved
MAX_BACKUPS_PER_FILE = 50       # oldest backups beyond this are removed after a save
MAX_AUDIT_ENTRIES = 500         # oldest audit lines beyond this are dropped
REVISION_LENGTH = 64            # hex SHA-256
PRIVATE_FILE_MODE = 0o600
PRIVATE_DIR_MODE = 0o700
BACKUP_NAME = re.compile(r"^[a-z_]{1,32}\.\d{8}T\d{6}Z\.\d{1,20}\.bak$")
AUDIT_FILE = "audit.jsonl"


class Conflict(Exception):
    """The file on disk is not the revision the editor started from."""


class InvalidContent(Exception):
    """The text or the file cannot be accepted."""


def revision_of(raw):
    return hashlib.sha256(raw).hexdigest()


def validate(content):
    if not isinstance(content, str) or not content.strip() or "\x00" in content:
        raise InvalidContent("Use non-empty text without NUL characters")
    try:
        raw = content.encode("utf-8")
    except UnicodeError:
        raise InvalidContent("Use valid UTF-8 text")
    if len(raw) > MAX_BYTES:
        raise InvalidContent("Instructions exceed the size limit")
    return raw


class InstructionStore:
    def __init__(self, file_id, path, work_dir, clock=time.time):
        self.file_id = file_id
        self.path = Path(path)
        self.work_dir = Path(work_dir)
        self.backup_dir = self.work_dir / "backups"
        self.clock = clock

    # ---- reading -------------------------------------------------------------------------
    def _check_location(self):
        # Refuse a symlink anywhere in the path: the file must be exactly the fixed file.
        if self.path.is_symlink() or os.path.realpath(self.path) != os.path.abspath(self.path):
            raise InvalidContent("Instruction file must not be reached through a symlink")

    def _read(self):
        self._check_location()
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise InvalidContent("Instructions must be a regular file")
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise InvalidContent("Instructions exceed the size limit")
        raw.decode("utf-8")
        return raw

    def available(self):
        try:
            self._read()
            return True
        except (OSError, InvalidContent, UnicodeError):
            return False

    def read(self):
        raw = self._read()
        return {"content": raw.decode("utf-8"), "revision": revision_of(raw), "size": len(raw)}

    # ---- private work folder -------------------------------------------------------------
    def _private_dir(self, folder):
        folder.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
        if folder.is_symlink() or not folder.is_dir():
            raise OSError("work folder is not a plain folder")
        return folder

    def _backup_name(self):
        now = self.clock()
        return "%s.%s.%d.bak" % (self.file_id, time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now)), time.time_ns())

    def backups(self):
        rows = []
        if not self.backup_dir.is_dir():
            return rows
        for f in self.backup_dir.iterdir():
            if not BACKUP_NAME.match(f.name) or not f.name.startswith(self.file_id + ".") or f.is_symlink():
                continue
            stamp = f.name.split(".")[1]
            rows.append({"name": f.name, "time": "%s-%s-%sT%s:%s:%sZ" % (stamp[0:4], stamp[4:6], stamp[6:8], stamp[9:11], stamp[11:13], stamp[13:15]),
                         "size": f.stat().st_size})
        rows.sort(key=lambda r: r["name"], reverse=True)
        return rows

    def read_backup(self, name):
        if not isinstance(name, str) or not BACKUP_NAME.match(name) or not name.startswith(self.file_id + "."):
            raise InvalidContent("Unknown backup")
        fd = os.open(self.backup_dir / name, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise InvalidContent("Backup exceeds the size limit")
        return {"name": name, "content": raw.decode("utf-8"), "size": len(raw)}

    def _prune_backups(self):
        for row in self.backups()[MAX_BACKUPS_PER_FILE:]:
            try:
                os.unlink(self.backup_dir / row["name"])
            except OSError:
                pass

    # ---- audit: time and sizes only, never content ---------------------------------------
    def _audit(self, before, after, backup):
        line = json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock())), "file": self.file_id,
                           "bytes_before": before, "bytes_after": after, "backup": backup})
        log = self.work_dir / AUDIT_FILE
        try:
            lines = audit_lines(self.work_dir)
            lines.append(line)
            fd = os.open(log, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, PRIVATE_FILE_MODE)
            with os.fdopen(fd, "w") as stream:
                stream.write("\n".join(lines[-MAX_AUDIT_ENTRIES:]) + "\n")
        except OSError:
            pass   # the save already happened and has its backup; a full disk must not turn it into an error

    # ---- saving --------------------------------------------------------------------------
    def save(self, content, expected_revision):
        raw = validate(content)
        if not isinstance(expected_revision, str) or len(expected_revision) != REVISION_LENGTH:
            raise InvalidContent("A valid revision is required")
        self._private_dir(self.work_dir)
        lock = self.work_dir / (self.file_id + ".lock")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, PRIVATE_FILE_MODE)
        with os.fdopen(fd, "a") as lockfile:
            fcntl.flock(lockfile, fcntl.LOCK_EX)
            previous = self._read()
            if revision_of(previous) != expected_revision:
                raise Conflict("Instructions changed; reload before saving")
            if raw == previous:
                return {"revision": expected_revision, "changed": False, "backup": None, "size": len(raw)}
            info = self.path.stat()
            backup = self._private_dir(self.backup_dir) / self._backup_name()
            bfd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, PRIVATE_FILE_MODE)
            with os.fdopen(bfd, "wb") as stream:
                stream.write(previous)
                stream.flush()
                os.fsync(stream.fileno())
            tfd, tmp = tempfile.mkstemp(prefix="." + self.path.name + ".editor-", dir=self.path.parent)
            try:
                with os.fdopen(tfd, "wb") as stream:
                    os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
                    try:
                        os.fchown(stream.fileno(), info.st_uid, info.st_gid)
                    except OSError:
                        pass
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Savers through this store are serialised by the lock. Another program may still
                # have written the file while the backup was made: refuse instead of overwriting it.
                if self._read() != previous:
                    raise Conflict("Instructions changed; reload before saving")
                os.replace(tmp, self.path)
                dfd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            self._prune_backups()
            self._audit(len(previous), len(raw), backup.name)
            return {"revision": revision_of(raw), "changed": True, "backup": backup.name, "size": len(raw)}


def audit_lines(work_dir):
    try:
        fd = os.open(Path(work_dir) / AUDIT_FILE, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return []
    with os.fdopen(fd, "r") as stream:
        return [line for line in stream.read().splitlines() if line.strip()]


def audit_entries(work_dir):
    out = []
    for line in audit_lines(work_dir):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out[::-1]
