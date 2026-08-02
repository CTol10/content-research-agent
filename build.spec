# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for 评论抓取工具."""
import glob as _glob
import os
from pathlib import Path

# ── Collect rapidocr_onnxruntime data files (.yaml and .onnx) ──
# PyInstaller only auto-collects .py files; .yaml config and .onnx model
# files must be added explicitly or rapidocr fails with "config.yaml does
# not exist" at runtime.
_RAPIDOCR_PKG = Path(os.environ.get(
    "RAPIDOCR_PKG",
    r"C:\Users\chenwentao\.workbuddy\binaries\python\versions\3.13.12\Lib\site-packages\rapidocr_onnxruntime"
))
_rapidocr_datas = []
for _pattern in ["**/*.yaml", "**/*.onnx"]:
    for _src in _glob.glob(str(_RAPIDOCR_PKG / _pattern), recursive=True):
        _src_path = Path(_src)
        _rel = _src_path.relative_to(_RAPIDOCR_PKG.parent)
        _rapidocr_datas.append((str(_src_path), str(_rel.parent)))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('config.ini.example', '.'),
        ('input', 'input'),
    ] + _rapidocr_datas,
    hiddenimports=[
        'classifier',
        'config',
        'video_processor',
        'scrapers',
        'scrapers.base',
        'scrapers.douyin',
        'scrapers.xiaohongshu',
        'scrapers.weibo',
        'scrapers.wechat_pc_base',
        'scrapers.wechat_official_pc',
        'scrapers.wechat_channels_pc',
        'scrapers.wechat_window_manager',
        'scrapers.wechat_ocr',
        'scrapers.wechat_calibrator',
        'scrapers.wechat',
        'scrapers.wechat.ocr',
        'scrapers.wechat.official_article_fetcher',
        'scrapers.wechat.channels_sph_parser',
        'scrapers.wechat.v411',
        'scrapers.wechat.v411.base',
        'scrapers.wechat.v411.official_pc',
        'scrapers.wechat.v411.channels_pc',
        'scrapers.wechat.v411.window_manager',
        'scrapers.wechat.v411.calibrator',
        'scrapers.wechat.v417',
        'scrapers.wechat.v417.base',
        'scrapers.wechat.v417.official_pc',
        'scrapers.wechat.v417.channels_pc',
        'scrapers.wechat.v417.window_manager',
        'scrapers.wechat.v417.calibrator',
        'scrapers.wechat.calibration',
        'scrapers.wechat.calibration.models',
        'scrapers.wechat.calibration.validator',
        'scrapers.wechat.calibration.overlay',
        'scrapers.toutiao',
        'scrapers.bilibili',
        'scrapers.comment_cleaner',
        'playwright',
        'playwright.async_api',
        'playwright.sync_api',
        'playwright._impl',
        'playwright._impl._browser_type',
        'playwright._impl._browser_context',
        'playwright._impl._page',
        'openpyxl',
        'requests',
        'pyautogui',
        'rapidocr_onnxruntime',
        'rapidocr_onnxruntime.ch_ppocr_v3_det',
        'rapidocr_onnxruntime.ch_ppocr_v3_det.text_detect',
        'rapidocr_onnxruntime.ch_ppocr_v3_rec',
        'rapidocr_onnxruntime.ch_ppocr_v3_rec.text_recognize',
        'rapidocr_onnxruntime.ch_ppocr_v2_cls',
        'rapidocr_onnxruntime.ch_ppocr_v2_cls.text_cls',
        'onnxruntime',
        'numpy',
        'asyncio',
        'json',
        'urllib.parse',
        'httpx',
        'httpcore',
        'anyio',
        'h11',
        'imageio_ffmpeg',
        'imageio',
        'hashlib',
        'base64',
        'pathlib',
        'logging',
        'os',
        'sys',
        'platform',
        're',
        'subprocess',
        'concurrent.futures',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', 'mitmproxy', 'pywinauto', 'tkinter', 'Tkinter',
              '_tkinter', 'tcl', 'tk', 'setuptools', 'pkg_resources',
              'pygments', 'PIL.ImageTk', 'unittest', 'test'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='评论抓取工具',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=None,
)
