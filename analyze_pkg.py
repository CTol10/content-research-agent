"""Analyze PyInstaller PKG archive contents by size."""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(
    0,
    r"C:\Users\chenwentao\.workbuddy\binaries\python\versions\3.13.12\Lib\site-packages",
)
from PyInstaller.archive.readers import CArchiveReader

pkg = CArchiveReader(r"build\build\评论抓取工具.pkg")

by_ext = defaultdict(int)
heavy_pkgs = defaultdict(int)
total = 0

for name, info in pkg.toc.items():
    pos, length = info[0], info[1]
    size = length if length else 0
    total += size

    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "noext"
    by_ext[ext] += size

    # Group by top-level package
    parts = Path(name).parts
    if parts:
        top = parts[0].lower()
        heavy_pkgs[top] += size

print(f"Total size: {total/1024/1024:.1f} MB\n")

print("By extension (top 10):")
for ext, size in sorted(by_ext.items(), key=lambda x: -x[1])[:10]:
    print(f"  .{ext:12s}: {size/1024/1024:6.1f} MB")

print("\nBy top-level path (top 20):")
for pkg_name, size in sorted(heavy_pkgs.items(), key=lambda x: -x[1])[:20]:
    print(f"  {pkg_name:40s}: {size/1024/1024:6.1f} MB")
