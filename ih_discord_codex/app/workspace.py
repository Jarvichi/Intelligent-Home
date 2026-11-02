"""Seed a private local IH source branch from the public, generic add-on repo."""
import os
import shutil
import subprocess
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
