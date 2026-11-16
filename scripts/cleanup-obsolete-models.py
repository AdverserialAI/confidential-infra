#!/usr/bin/env python3
"""Inventory/remove only reviewed obsolete model directories, never volumes."""
import argparse
import json
import os
from pathlib import Path
import shutil
import stat


# Historic inactive paths established from the deployment files. No wildcard
# deletion and no Docker daemon/volume operations are used.
OBSOLETE = (
    'cyberglm', 'cyberglm-download', 'cyberglm-staging',
    'cyberseek', 'cyberseek-download', 'cyberseek.tmp',
    'cyberseek-sglang', 'cyberseek-sglang-download', 'cyberseek-sglang-staging',
    'cyberseek-huihui-q2', 'cyberseek-huihui-q2-download',
    'cyberseek-huihui-q2-patch', 'cyberseek-huihui-q2.tmp',
    'cyberseek-huihui-q2-llama.cpp', 'cyberseek-huihui-q2-llama.cpp-src',
    'cyberseek-q2-download', 'cyberseek-q2-patch', 'cyberseek-q2.tmp',
)
PROTECTED = ('cyberseek-q2', 'cyberseek-q2-llama.cpp')


def allocated_bytes(directory):
    total = 0
    for parent, directories, files in os.walk(directory, followlinks=False):
        # Never descend into symlink or nested mount directories.
        directories[:] = [d for d in directories
                          if not (Path(parent) / d).is_symlink()
                          and not os.path.ismount(Path(parent) / d)]
        for name in files:
            item = Path(parent) / name
            status = item.lstat()
            if stat.S_ISREG(status.st_mode):
                total += status.st_blocks * 512
    return total


def cleanup(root, apply=False):
    root = Path(root).resolve(strict=True)
    if root == Path('/') or not root.is_dir():
        raise ValueError('Unsafe model-volume root')
    before = shutil.disk_usage(root)
    entries = []
    for name in OBSOLETE:
        if name in PROTECTED or name.startswith('cyberseek-q2-llama.cpp'):
            raise ValueError('Cleanup list overlaps a protected runtime')
        candidate = root / name
        if not candidate.exists() and not candidate.is_symlink():
            continue
        if candidate.is_symlink() or not candidate.is_dir() or os.path.ismount(candidate):
            entries.append({'artifact': name, 'action': 'skipped-not-a-plain-directory'})
            continue
        if candidate.resolve() != candidate or candidate.parent != root:
            raise ValueError('Cleanup candidate escaped model-volume root')
        # Refuse nested mounts rather than silently crossing into another volume.
        if any(os.path.ismount(Path(parent) / d)
               for parent, directories, _ in os.walk(candidate, followlinks=False)
               for d in directories if not (Path(parent) / d).is_symlink()):
            entries.append({'artifact': name, 'action': 'skipped-nested-mount'})
            continue
        size = allocated_bytes(candidate)
        entry = {'artifact': name, 'allocated_bytes_before': size,
                 'action': 'removed' if apply else 'would-remove'}
        # Emit only reviewed artifact names and sizes before a potentially long delete.
        print(json.dumps({'model_cleanup': entry}), flush=True)
        if apply:
            shutil.rmtree(candidate)
        entries.append(entry)
    after = shutil.disk_usage(root)
    unknown_count = sum(1 for p in root.iterdir()
                        if p.name.startswith(('cyberglm', 'cyberseek'))
                        and p.name not in OBSOLETE
                        and p.name != 'cyberseek-q2'
                        and not p.name.startswith('cyberseek-q2-llama.cpp'))
    return {
        'mode': 'apply' if apply else 'inventory-only',
        'disk_total_bytes': before.total,
        'disk_free_bytes_before': before.free,
        'disk_free_bytes_after': after.free,
        'entries': entries,
        'unreviewed_model_directory_count': unknown_count,
        'active_model_and_runtime_preserved': True,
        'volume_operations_performed': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/data'))
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps({'model_cleanup_summary': cleanup(args.root, args.apply)}), flush=True)


if __name__ == '__main__':
    main()
