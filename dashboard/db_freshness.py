"""Detect stale local market.db vs origin/paper-live-data (Git LFS)."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_LFS_OID_RE = re.compile(r"^oid sha256:([0-9a-f]{64})$", re.MULTILINE)

FreshnessStatus = Literal["ok", "stale_remote", "stale_worktree", "unknown"]


@dataclass(frozen=True)
class DbFreshnessReport:
    status: FreshnessStatus
    local_oid: str | None
    remote_oid: str | None
    commits_behind: int | None
    worktree_dirty: bool
    remote_ref: str
    db_path: str
    message: str
    sync_hint: str


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )


def parse_lfs_oid(pointer_text: str) -> str | None:
    match = _LFS_OID_RE.search(pointer_text)
    return match.group(1) if match else None


def _lfs_oid_at_ref(repo_root: Path, ref: str, db_rel: str) -> str | None:
    proc = _run_git(repo_root, "show", f"{ref}:{db_rel}")
    if proc.returncode != 0:
        return None
    return parse_lfs_oid(proc.stdout)


def fetch_remote_ref(repo_root: Path, remote_branch: str = "paper-live-data") -> bool:
    proc = _run_git(repo_root, "fetch", "origin", remote_branch)
    return proc.returncode == 0


def sync_db_from_remote(
    repo_root: Path,
    *,
    db_rel: str = "data/market.db",
    remote_ref: str = "origin/paper-live-data",
) -> None:
    """Replace working-tree market.db with the version tracked on *remote_ref*."""
    if (repo_root / ".git" / "MERGE_HEAD").exists():
        raise RuntimeError("Hay un merge en curso. Terminá o abortá el merge antes de sincronizar.")

    fetch = _run_git(repo_root, "fetch", "origin", remote_ref.removeprefix("origin/"))
    if fetch.returncode != 0:
        raise RuntimeError(fetch.stderr.strip() or "git fetch falló")

    checkout = _run_git(repo_root, "checkout", remote_ref, "--", db_rel)
    if checkout.returncode != 0:
        raise RuntimeError(checkout.stderr.strip() or "git checkout de market.db falló")

    lfs = _run_git(repo_root, "lfs", "checkout", db_rel)
    if lfs.returncode != 0:
        raise RuntimeError(lfs.stderr.strip() or "git lfs checkout falló")


def check_db_freshness(
    db_path: Path,
    *,
    remote_ref: str = "origin/paper-live-data",
    fetch: bool = False,
) -> DbFreshnessReport:
    """Compare local working-tree DB with the Git LFS object on *remote_ref*."""
    repo_root = db_path.resolve().parent.parent if db_path.name == "market.db" else db_path.resolve().parent
    while repo_root != repo_root.parent and not (repo_root / ".git").exists():
        repo_root = repo_root.parent
    if not (repo_root / ".git").exists():
        return DbFreshnessReport(
            status="unknown",
            local_oid=None,
            remote_oid=None,
            commits_behind=None,
            worktree_dirty=False,
            remote_ref=remote_ref,
            db_path=str(db_path),
            message="No es un repo git; no se puede comparar con el remoto.",
            sync_hint="",
        )

    db_rel = str(db_path.resolve().relative_to(repo_root)).replace("\\", "/")

    if fetch:
        fetch_remote_ref(repo_root, remote_ref.removeprefix("origin/"))

    head_oid = _lfs_oid_at_ref(repo_root, "HEAD", db_rel)
    remote_oid = _lfs_oid_at_ref(repo_root, remote_ref, db_rel)

    behind_proc = _run_git(
        repo_root,
        "rev-list",
        "--count",
        f"HEAD..{remote_ref}",
    )
    commits_behind = int(behind_proc.stdout.strip()) if behind_proc.returncode == 0 else None

    dirty_proc = _run_git(repo_root, "status", "--porcelain", db_rel)
    worktree_dirty = bool(dirty_proc.stdout.strip())

    # Working file may differ from HEAD pointer (common after partial merges).
    work_file_oid: str | None = None
    if db_path.is_file():
        lfs_proc = _run_git(repo_root, "lfs", "pointer", "--file", str(db_path.resolve()))
        if lfs_proc.returncode == 0:
            work_file_oid = parse_lfs_oid(lfs_proc.stdout)

    sync_hint = (
        f"git fetch origin paper-live-data && "
        f"git checkout {remote_ref} -- {db_rel} && "
        f"git lfs checkout {db_rel}"
    )

    if remote_oid is None:
        return DbFreshnessReport(
            status="unknown",
            local_oid=head_oid or work_file_oid,
            remote_oid=None,
            commits_behind=commits_behind,
            worktree_dirty=worktree_dirty,
            remote_ref=remote_ref,
            db_path=db_rel,
            message=f"No se pudo leer {db_rel} en {remote_ref}. ¿Hiciste git fetch?",
            sync_hint=sync_hint,
        )

    local_effective = work_file_oid or head_oid
    stale_remote = remote_oid != local_effective or (commits_behind or 0) > 0
    stale_worktree = worktree_dirty and head_oid is not None and work_file_oid != head_oid

    if stale_remote:
        msg = (
            f"La DB local no coincide con {remote_ref}. "
            f"Commits atrás: {commits_behind if commits_behind is not None else '?'}"
        )
        return DbFreshnessReport(
            status="stale_remote",
            local_oid=local_effective,
            remote_oid=remote_oid,
            commits_behind=commits_behind,
            worktree_dirty=worktree_dirty,
            remote_ref=remote_ref,
            db_path=db_rel,
            message=msg,
            sync_hint=sync_hint,
        )

    if stale_worktree:
        return DbFreshnessReport(
            status="stale_worktree",
            local_oid=local_effective,
            remote_oid=remote_oid,
            commits_behind=commits_behind,
            worktree_dirty=True,
            remote_ref=remote_ref,
            db_path=db_rel,
            message=f"{db_rel} tiene cambios locales sin commitear respecto a HEAD.",
            sync_hint=sync_hint,
        )

    return DbFreshnessReport(
        status="ok",
        local_oid=local_effective,
        remote_oid=remote_oid,
        commits_behind=commits_behind or 0,
        worktree_dirty=worktree_dirty,
        remote_ref=remote_ref,
        db_path=db_rel,
        message="DB local alineada con el remoto.",
        sync_hint=sync_hint,
    )
