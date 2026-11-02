"""Publish only an explicit, source-only IH branch as a review pull request."""
import base64
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = "Jarvichi/Intelligent-Home"
MAX_FILE = 2_000_000
MAX_TOTAL = 8_000_000
MAX_FILES = 20
PRIVATE_PATTERNS = (
    re.compile(r"(?i)\b(?:192\.168\.|10\.\d{1,3}\.|172\.(?:1[6-9]|2\d|3[01])\.)\d{1,3}\.\d{1,3}\b"),
    re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b"),
    re.compile(r"(?i)\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,})\b"),
    re.compile(r"(?i)\b(?:api[_-]?token|password|secret|signing[_-]?key)\s*[:=]\s*['\"][^'\"]{12,}['\"]"),
    re.compile(r"\b\d{17,20}\b"),
)


class PublishError(RuntimeError):
    pass


class GitHub:
    def __init__(self, token, opener=None):
        if not isinstance(token, str) or len(token) < 20:
            raise PublishError("A repository-scoped review token is not configured")
        self.token = token
        self.opener = opener or urllib.request.urlopen

    def request(self, method, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request("https://api.github.com" + path, data=body, method=method,
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "IH-Codex-review-branch",
                     "Content-Type": "application/json"})
        try:
            with self.opener(req, timeout=15) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise PublishError("GitHub response exceeded the safe limit")
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            # Do not propagate response bodies; they can include private request details.
            raise PublishError("GitHub rejected the review-branch request (HTTP %s)" % exc.code) from None
        except (OSError, ValueError, urllib.error.URLError):
            raise PublishError("GitHub review-branch request failed; no deployment was attempted") from None


def _git(path, *args):
    result = subprocess.run(("git",) + args, cwd=path, capture_output=True, timeout=15)
    if result.returncode:
        raise PublishError("The local IH branch could not be read")
    return result.stdout


def _changed_files(path):
    raw = _git(path, "diff", "--no-renames", "--name-status", "-z", "main")
    parts = raw.decode("utf-8", "strict").split("\0")
    changes = {}
    i = 0
    while i < len(parts) - 1:
        status, name = parts[i], parts[i + 1]
        i += 2
        if status not in ("A", "M", "D"):
            raise PublishError("This review flow accepts only added, edited or deleted source files")
        changes[name] = status
    for name in _git(path, "ls-files", "--others", "--exclude-standard", "-z").decode("utf-8", "strict").split("\0"):
        if name:
            changes.setdefault(name, "A")
    if not changes:
        raise PublishError("The local IH branch has no changes to submit")
    if len(changes) > MAX_FILES:
        raise PublishError("A review branch can contain at most %s source files" % MAX_FILES)
    total = 0
    files = []
    for name, status in sorted(changes.items()):
        if not re.fullmatch(r"ih_[a-z0-9_]+/[A-Za-z0-9_./+-]+", name) or ".." in Path(name).parts:
            raise PublishError("Only files inside a public IH add-on folder can be submitted")
        candidate = Path(path) / name
        if any(part.is_symlink() for part in (candidate, *candidate.parents) if part != Path(path)):
            raise PublishError("Only regular source files can be submitted")
        full = candidate.resolve()
        if Path(path).resolve() not in full.parents:
            raise PublishError("Unsafe source path")
        if status == "D":
            files.append({"path": name, "status": "D"})
            continue
        if not full.is_file() or full.is_symlink():
            raise PublishError("Only regular source files can be submitted")
        data = full.read_bytes()
        if len(data) > MAX_FILE:
            raise PublishError("A changed source file exceeds the review limit")
        total += len(data)
        if total > MAX_TOTAL:
            raise PublishError("The source change set exceeds the review limit")
        text = data.decode("utf-8", "strict")
        if any(pattern.search(text) for pattern in PRIVATE_PATTERNS):
            raise PublishError("A private address, device identifier or secret-like value was found; nothing was uploaded")
        files.append({"path": name, "status": status, "content": data})
    return files


def publish_review_branch(path, token, repo=REPO, opener=None):
    """Create a new GitHub branch and PR only when the user sends /code submit."""
    if repo != REPO:
        raise PublishError("Only the configured IH source repository is allowed")
    path = Path(path).resolve()
    base_sha = (path / ".codex_source_revision").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise PublishError("Workspace source revision is unknown; refresh the code snapshot before submitting")
    files = _changed_files(path)
    api = GitHub(token, opener)
    main_ref = api.request("GET", "/repos/%s/git/ref/heads/main" % repo)
    main_sha = main_ref["object"]["sha"]
    base_tree_sha = api.request("GET", "/repos/%s/git/commits/%s" % (repo, base_sha))["tree"]["sha"]
    main_tree = api.request("GET", "/repos/%s/git/trees/%s?recursive=1" % (repo, main_sha))
    base_tree = api.request("GET", "/repos/%s/git/trees/%s?recursive=1" % (repo, base_tree_sha))
    current = {entry["path"]: entry for entry in main_tree.get("tree", [])}
    original = {entry["path"]: entry for entry in base_tree.get("tree", [])}
    tree = []
    for item in files:
        old = original.get(item["path"])
        new = current.get(item["path"])
        if ((old or {}).get("sha")) != ((new or {}).get("sha")):
            raise PublishError("The public IH source changed since this workspace was created; no branch was uploaded")
        if item["status"] == "D":
            tree.append({"path": item["path"], "mode": "100644", "type": "blob", "sha": None})
        else:
            blob = api.request("POST", "/repos/%s/git/blobs" % repo,
                {"content": base64.b64encode(item["content"]).decode(), "encoding": "base64"})
            mode = "100755" if os.access(path / item["path"], os.X_OK) else "100644"
            tree.append({"path": item["path"], "mode": mode, "type": "blob", "sha": blob["sha"]})
    new_tree = api.request("POST", "/repos/%s/git/trees" % repo, {"base_tree": main_tree["sha"], "tree": tree})
    commit = api.request("POST", "/repos/%s/git/commits" % repo,
        {"message": "Codex IH source changes (review required)", "tree": new_tree["sha"], "parents": [main_sha]})
    branch = "codex/discord-%s-%s" % (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), os.urandom(3).hex())
    api.request("POST", "/repos/%s/git/refs" % repo, {"ref": "refs/heads/" + branch, "sha": commit["sha"]})
    try:
        pr = api.request("POST", "/repos/%s/pulls" % repo, {
            "title": "Codex IH source changes",
            "body": "Prepared by the private Codex Discord worker. Review this code diff before merging. No house state or user prompt is included. Nothing is deployed automatically.",
            "head": branch, "base": "main"})
    except PublishError:
        # Avoid leaving an orphaned branch if PR creation is refused.
        try:
            api.request("DELETE", "/repos/%s/git/refs/heads/%s" % (repo, urllib.parse.quote(branch, safe="/")))
        except PublishError:
            pass
        raise
    if not isinstance(pr.get("html_url"), str) or not pr["html_url"].startswith("https://github.com/"):
        raise PublishError("GitHub created a response without a valid review URL")
    return {"branch": branch, "url": pr["html_url"]}
