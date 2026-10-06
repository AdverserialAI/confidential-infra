"""One-shot, resumable, integrity-checked private Hugging Face model backup."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from huggingface_hub import HfApi
from huggingface_hub.hf_api import RepoFile

REPO_ID = os.environ.get("HF_REPO_ID", "lordx64/cyberglm-fp8-abliterated")
SOURCE = Path(os.environ.get("BACKUP_SOURCE", "/data/cyberglm-fp8"))
STATE = Path(os.environ.get("BACKUP_STATE_DIR", "/state"))
WORKERS = int(os.environ.get("BACKUP_HASH_WORKERS", "2"))


def require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing required environment variable: {name}")
    return value


def atomic_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    tmp.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_files() -> list[Path]:
    if not SOURCE.is_dir():
        raise SystemExit(f"backup source is missing: {SOURCE}")
    files = []
    for path in SOURCE.rglob("*"):
        if path.is_symlink():
            raise SystemExit(f"refusing symlink in model backup: {path.relative_to(SOURCE)}")
        if path.is_file():
            files.append(path)
    if not files:
        raise SystemExit("backup source has no files")
    return sorted(files)


def build_manifest(files: list[Path]) -> dict[str, dict[str, int | str]]:
    STATE.mkdir(parents=True, exist_ok=True)
    manifest_path = STATE / "manifest.json"
    previous: dict[str, dict[str, int | str]] = {}
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            raise SystemExit("backup state manifest is malformed; remove only the state volume to restart")

    def one(path: Path) -> tuple[str, dict[str, int | str]]:
        stat = path.stat()
        key = str(path.relative_to(SOURCE))
        old = previous.get(key, {})
        if old.get("size") == stat.st_size and old.get("mtime_ns") == stat.st_mtime_ns and isinstance(old.get("sha256"), str):
            return key, old
        return key, {"sha256": sha256_file(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}

    manifest: dict[str, dict[str, int | str]] = {}
    with ThreadPoolExecutor(max_workers=max(1, WORKERS)) as pool:
        for index, (key, entry) in enumerate(pool.map(one, files), 1):
            manifest[key] = entry
            if index % 20 == 0 or index == len(files):
                print(f"hashed {index}/{len(files)}", flush=True)
    atomic_json(manifest_path, manifest)
    return manifest


def remote_matches(remote_file: RepoFile | None, local: dict[str, int | str]) -> bool:
    return bool(
        remote_file
        and remote_file.size == local["size"]
        and remote_file.lfs is not None
        and remote_file.lfs.sha256 == local["sha256"]
    )


def remote_files(api: HfApi) -> dict[str, RepoFile]:
    return {
        item.path: item
        for item in api.list_repo_tree(REPO_ID, repo_type="model", recursive=True, expand=True)
        if isinstance(item, RepoFile)
    }


def verify_remote(api: HfApi, manifest: dict[str, dict[str, int | str]]) -> None:
    remote = remote_files(api)
    failures: list[str] = []
    for path, local in manifest.items():
        remote_file = remote.get(path)
        if remote_matches(remote_file, local):
            continue
        if remote_file is None:
            failures.append(f"missing:{path}")
        elif remote_file.size != local["size"]:
            failures.append(f"size:{path}")
        else:
            failures.append(f"sha256:{path}")
    if failures:
        raise SystemExit(f"remote verification failed ({len(failures)}): {failures[:5]}")


def upload_missing(api: HfApi, manifest: dict[str, dict[str, int | str]]) -> None:
    # File-by-file commits are deliberate. They let us resume solely from the
    # private state volume and the Hub's LFS SHA-256 metadata without ever
    # making the read-only model volume writable for a client-side cache.
    remote = remote_files(api)
    attempts = int(os.environ.get("BACKUP_UPLOAD_ATTEMPTS", "5"))
    for index, (relative, entry) in enumerate(sorted(manifest.items()), 1):
        if remote_matches(remote.get(relative), entry):
            print(f"already uploaded {index}/{len(manifest)}: {relative}", flush=True)
            continue
        source = SOURCE / relative
        for attempt in range(1, attempts + 1):
            try:
                api.upload_file(
                    path_or_fileobj=str(source),
                    path_in_repo=relative,
                    repo_id=REPO_ID,
                    repo_type="model",
                    commit_message=f"backup: {relative}",
                )
                print(f"uploaded {index}/{len(manifest)}: {relative}", flush=True)
                break
            except Exception as exc:
                print(f"upload {relative} attempt {attempt}/{attempts} failed: {type(exc).__name__}", flush=True)
                if attempt == attempts:
                    raise
                time.sleep(60)


def wait_for_serving() -> None:
    url = os.environ.get("BACKUP_WAIT_FOR_URL", "").strip()
    if not url:
        return
    deadline = time.monotonic() + int(os.environ.get("BACKUP_WAIT_TIMEOUT_SECONDS", "21600"))
    while True:
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                if 200 <= response.status < 300:
                    print("inference health check passed; backup may begin", flush=True)
                    return
        except (urllib.error.URLError, TimeoutError):
            pass
        if time.monotonic() >= deadline:
            raise SystemExit("timed out waiting for inference health before backup")
        time.sleep(15)


def main() -> None:
    token = require("HF_TOKEN")
    if not token.startswith("hf_"):
        raise SystemExit("HF_TOKEN is not a Hugging Face access token")
    wait_for_serving()
    files = source_files()
    api = HfApi(token=token)
    info = api.repo_info(REPO_ID, repo_type="model")
    if not info.private:
        raise SystemExit(f"refusing to upload to non-private repository: {REPO_ID}")

    manifest = build_manifest(files)
    done = STATE / "verify-complete.json"
    if done.exists():
        verify_remote(api, manifest)
        print("backup already verified; nothing to upload", flush=True)
        return

    upload_missing(api, manifest)
    verify_remote(api, manifest)
    atomic_json(done, {
        "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repository": REPO_ID,
        "files": len(manifest),
        "manifest_sha256": hashlib.sha256((STATE / "manifest.json").read_bytes()).hexdigest(),
    })
    print(f"BACKUP_VERIFIED: {len(manifest)} files", flush=True)


if __name__ == "__main__":
    main()
