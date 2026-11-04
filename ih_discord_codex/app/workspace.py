"""Seed a private local IH source branch from the public, generic add-on repo."""
import os
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path

SOURCE_ROOT = Path(os.environ.get("CODEX_SOURCE_ROOT", "/opt/ih-source"))
SOURCE_REVISION_FILE = Path("/opt/ih-source-revision")


def source_revision(path=None):
    marker = (Path(path) / ".codex_source_revision") if path else None
    try:
        if marker and marker.is_file():
            return marker.read_text(encoding="utf-8").strip()[:40] or "unknown"
        return SOURCE_REVISION_FILE.read_text(encoding="utf-8").strip()[:40] or "unknown"
    except OSError:
        return "unknown"


def prepare_workspace(path):
    """Initialize once; never overwrite an existing user branch or workspace."""
    target = Path(path).resolve()
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (target / ".git").exists():
        return source_revision(target)
    if any(target.iterdir()):
        raise RuntimeError("workspace contains files but has no Git repository; refusing to overwrite")
    if not (SOURCE_ROOT / "ih_discord_codex" / "config.yaml").is_file():
        raise RuntimeError("public IH source snapshot is missing from this image")
    shutil.copytree(SOURCE_ROOT, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".DS_Store"))
    (target / ".codex_source_revision").write_text(source_revision() + "\n", encoding="utf-8")
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    for args in (("init", "-b", "main"), ("config", "user.name", "Codex Discord"),
                 ("config", "user.email", "codex@localhost"), ("add", "-A"),
                 ("commit", "-m", "Public IH source snapshot"), ("checkout", "-b", "codex/work")):
        result = subprocess.run(("git",) + args, cwd=target, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if result.returncode:
            shutil.rmtree(target, ignore_errors=True)
            raise RuntimeError("could not prepare the isolated IH code branch (%s)" % args[0])
    os.chmod(target, 0o700)
    return source_revision(target)


def refresh_workspace(path):
    """Refresh a workspace by archiving the complete old checkout before reseeding."""
    target = Path(path).resolve()
    if not (target / ".git").is_dir():
        raise RuntimeError("the IH code workspace is not initialized")

    def git(*args):
        return subprocess.run(("git",) + args, cwd=target, capture_output=True,
                              text=True, timeout=10,
                              env=dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0"))

    branch = git("branch", "--show-current")
    if branch.returncode or branch.stdout.strip() != "codex/work":
        raise RuntimeError("refresh is allowed only from the codex/work branch")
    status = git("status", "--porcelain", "--untracked-files=all")
    ahead = git("rev-list", "--count", "main..codex/work")
    if status.returncode or ahead.returncode or not ahead.stdout.strip().isdigit():
        raise RuntimeError("workspace state could not be checked; no files were changed")
    has_local_changes = bool(status.stdout.strip()) or ahead.stdout.strip() != "0"

    old_revision = source_revision(target)
    try:
        new_revision = SOURCE_REVISION_FILE.read_text(encoding="utf-8").strip()[:40]
    except OSError:
        new_revision = ""
    if not new_revision or new_revision == "unknown":
        raise RuntimeError("the bundled IH source revision is unavailable")
    if new_revision == old_revision and not has_local_changes:
        return "The IH code workspace already uses the latest bundled source (%s)." % new_revision[:12]
    if not (SOURCE_ROOT / "ih_discord_codex" / "config.yaml").is_file():
        raise RuntimeError("the bundled public IH source is missing; workspace was not changed")

    parent = target.parent
    temporary = parent / (".%s-refresh-%s" % (target.name, secrets.token_hex(6)))
    backup = parent / ("%s-previous-%s-%s-%s" % (
        target.name, old_revision[:8], time.strftime("%Y%m%d%H%M%S"), secrets.token_hex(3)))
    try:
        prepare_workspace(temporary)
        os.replace(target, backup)
        try:
            os.replace(temporary, target)
        except Exception:
            os.replace(backup, target)
            raise
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        raise
    os.chmod(target, 0o700)
    return "Refreshed the IH code workspace to source %s. The previous workspace, including local edits, is preserved as `%s`." % (
        new_revision[:12], backup.name)


def rebase_workspace(path):
    """Rebase local edits and commits onto the latest source bundled in this image."""
    target = Path(path).resolve()
    if not (target / ".git").is_dir():
        raise RuntimeError("the IH code workspace is not initialized")
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
               GIT_EDITOR="true", GIT_SEQUENCE_EDITOR="true")

    def git(cwd, *args, timeout=30):
        return subprocess.run(("git",) + args, cwd=cwd, env=env, capture_output=True,
                              text=True, timeout=timeout)

    def call(*args, timeout=30):
        result = git(target, *args, timeout=timeout)
        if result.returncode:
            raise RuntimeError("git %s failed; the existing workspace backup is preserved" % args[0])
        return result.stdout.strip()

    branch = call("branch", "--show-current")
    if branch != "codex/work":
        raise RuntimeError("rebase is allowed only from the codex/work branch")
    old_main = call("rev-parse", "main")
    ancestor = git(target, "merge-base", "--is-ancestor", old_main, "codex/work")
    if ancestor.returncode:
        raise RuntimeError("the current source snapshot is not the workspace branch base; use `/code refresh` after preserving your work")
    old_revision = source_revision(target)
    try:
        new_revision = SOURCE_REVISION_FILE.read_text(encoding="utf-8").strip()[:40]
    except OSError:
        new_revision = ""
    if not re.fullmatch(r"[0-9a-f]{40}", new_revision or ""):
        raise RuntimeError("the bundled IH source revision is unavailable")
    if old_revision == new_revision:
        return "The workspace already uses the source bundled in this add-on (%s)." % new_revision[:12]
    if not (SOURCE_ROOT / "ih_discord_codex" / "config.yaml").is_file():
        raise RuntimeError("the bundled public IH source is missing; workspace was not changed")

    parent = target.parent
    suffix = "%s-%s-%s" % (old_revision[:8], time.strftime("%Y%m%d%H%M%S"), secrets.token_hex(3))
    backup = parent / (target.name + "-pre-rebase-" + suffix)
    snapshot = parent / ("." + target.name + "-rebase-source-" + secrets.token_hex(6))
    if backup.exists() or snapshot.exists():
        raise RuntimeError("a rebase workspace path already exists; no files were changed")

    try:
        prepare_workspace(snapshot)
        new_main = git(snapshot, "rev-parse", "main").stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", new_main):
            raise RuntimeError("the bundled IH source snapshot could not be prepared")
        shutil.copytree(target, backup, symlinks=True)

        status = call("status", "--porcelain", "--untracked-files=all")
        if status:
            call("add", "-A", timeout=60)
            result = git(target, "commit", "-m", "Checkpoint before IH source rebase", timeout=60)
            if result.returncode:
                raise RuntimeError("local edits could not be checkpointed; the original workspace is preserved")

        fetch = git(target, "fetch", str(snapshot), "main", timeout=60)
        if fetch.returncode:
            raise RuntimeError("latest bundled IH source could not be fetched; the original workspace is preserved")
        call("branch", "-f", "main", new_main)
        result = git(target, "rebase", "--onto", new_main, old_main, "codex/work", timeout=120)
        if result.returncode:
            conflict_result = git(target, "diff", "--name-only", "--diff-filter=U", timeout=10)
            conflicts = [line for line in conflict_result.stdout.splitlines() if line]
            shutil.rmtree(snapshot, ignore_errors=True)
            if conflicts:
                shown = ", ".join(conflicts[:12])
                if len(conflicts) > 12:
                    shown += ", …"
                return "Rebase stopped on conflicts in: %s. The original workspace is preserved as `%s`. Resolve conflicts, then use `/code rebase --continue`." % (
                    shown, backup.name)
            raise RuntimeError("rebase failed; the original workspace is being restored")

        if source_revision(target) != new_revision:
            raise RuntimeError("rebased workspace source marker did not update; restoring the previous workspace")
        shutil.rmtree(snapshot, ignore_errors=True)
        return "Rebased `codex/work` onto bundled source %s. Local edits and commits are preserved; the pre-rebase workspace is backed up as `%s`. Use `/code submit` to open a review PR." % (
            new_revision[:12], backup.name)
    except Exception:
        shutil.rmtree(snapshot, ignore_errors=True)
        if backup.exists():
            failed = parent / ("." + target.name + "-failed-rebase-" + secrets.token_hex(4))
            try:
                os.replace(target, failed)
                os.replace(backup, target)
                shutil.rmtree(failed, ignore_errors=True)
            except Exception:
                if failed.exists() and not target.exists():
                    os.replace(failed, target)
        raise


def continue_workspace_rebase(path):
    """Continue an in-progress local rebase after conflicts have been resolved."""
    target = Path(path).resolve()
    if not (target / ".git").is_dir():
        raise RuntimeError("the IH code workspace is not initialized")
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", GIT_EDITOR="true")

    def git(*args, timeout=30):
        return subprocess.run(("git",) + args, cwd=target, env=env, capture_output=True,
                              text=True, timeout=timeout)

    branch = git("branch", "--show-current")
    if branch.returncode or branch.stdout.strip() != "codex/work":
        raise RuntimeError("rebase can continue only from the codex/work branch")
    marker = git("rev-parse", "--verify", "REBASE_HEAD")
    if marker.returncode:
        raise RuntimeError("there is no IH source rebase waiting to continue")
    conflicts = git("diff", "--name-only", "--diff-filter=U")
    if conflicts.returncode:
        raise RuntimeError("could not check rebase conflicts")
    if conflicts.stdout.strip():
        names = ", ".join(conflicts.stdout.splitlines()[:12])
        return "Resolve these files before continuing: %s." % names
    staged = git("add", "-A", timeout=60)
    if staged.returncode:
        raise RuntimeError("resolved files could not be staged")
    result = git("rebase", "--continue", timeout=120)
    if result.returncode:
        remaining = git("diff", "--name-only", "--diff-filter=U")
        names = ", ".join(remaining.stdout.splitlines()[:12]) if remaining.returncode == 0 else "unknown files"
        return "Rebase still needs conflict resolution: %s." % (names or "check `/code status`")
    return "Rebase completed on the bundled IH source. Local edits and commits are preserved. Use `/code submit` to open a review PR."


def branch_status(path):
    target = Path(path).resolve()
    if not (target / ".git").exists():
        return "IH code workspace is not initialized."
    branch = subprocess.run(("git", "branch", "--show-current"), cwd=target,
                            capture_output=True, text=True, timeout=5)
    status = subprocess.run(("git", "status", "--short"), cwd=target,
                            capture_output=True, text=True, timeout=5)
    if branch.returncode or status.returncode:
        return "IH code branch status is unavailable."
    changed = status.stdout.strip() or "no uncommitted changes"
    if len(changed) > 6000:
        changed = changed[:6000] + "\n… file list shortened"
    return "Local IH branch `%s` (source snapshot %s):\n%s\nChanges stay in the private Codex workspace until `/code submit` creates a review pull request; nothing is merged or deployed." % (
        branch.stdout.strip() or "unknown", source_revision(target), changed)


def branch_diff(path):
    target = Path(path).resolve()
    result = subprocess.run(("git", "diff", "--binary", "HEAD"), cwd=target,
                            capture_output=True, text=True, timeout=10)
    if result.returncode:
        return "Could not read the local IH branch diff."
    diff = result.stdout.strip()
    if not diff:
        return "The local IH branch has no changes yet."
    if len(diff) > 14000:
        stat = subprocess.run(("git", "diff", "--stat", "HEAD"), cwd=target,
                              capture_output=True, text=True, timeout=5).stdout.strip()
        return "The branch diff is too large to send here. Summary:\n%s\nAsk for a smaller change set." % stat[:5000]
    return "Local branch diff (review only; not merged or deployed):\n```diff\n%s\n```" % diff
