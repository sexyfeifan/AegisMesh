#!/usr/bin/env python3
"""AegisMesh - 订阅伪装/还原一体化界面"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import ssl
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
from typing import Callable

import yaml

import vpn_obfuscator as core


APP_TITLE = "AegisMesh"
APP_VERSION = "1.0.7"
APP_TITLE_WITH_VERSION = f"{APP_TITLE} v{APP_VERSION}"
USER_AGENT = f"{APP_TITLE}/{APP_VERSION}"
FETCH_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)
OPENCLASH_FETCH_UA_CANDIDATES = [
    "clash.meta",
    "clash",
    "mihomo",
]
DEFAULT_MAPPING_DIR = Path.home() / ".vpn_obfuscator"
HISTORY_DIR = DEFAULT_MAPPING_DIR / "history"
DEFAULT_SAVE_DIR = Path.home() / "Downloads"
OPENLIST_CONFIG_PATH = DEFAULT_MAPPING_DIR / "openlist_config.json"
BUILTIN_USER_GUIDE = """AegisMesh 使用说明（内置）

快速开始：
1) 先点 OpenList设置，填写地址/账号/密码并联通测试。
2) 推荐使用 OpenClash链接流程：输入URL -> 抓取并伪装上传 -> 复制伪装链接 -> 粘贴转换结果 -> 执行还原。
3) 在步骤④保存还原文档（输出 YAML）。

提示：
- 每次操作建议先新建会话；
- 若上传失败，请重试并查看日志窗口中的 OpenList 错误详情。
"""


@dataclass
class NodeView:
    scheme: str
    host: str
    port: int
    name: str
    raw: str
    token: str


@dataclass
class OpenListConfig:
    enabled: bool = False
    base_url: str = ""
    username: str = ""
    password: str = ""
    remote_dir: str = "/subscriptions"
    link_template: str = "{base_url}/d/{path}"
    verify_tls: bool = False
    auto_copy_url: bool = True
    upload_timeout_sec: int = 60
    upload_retry_count: int = 2
    cleanup_keep_days: int = 30


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE_WITH_VERSION)
        self.geometry("1220x840")
        self.minsize(1080, 760)

        self.session_profile = self._new_profile()
        self.original_content = ""
        self.original_nodes: list[NodeView] = []
        self.original_sigs: set[tuple[str, str, int, str]] = set()

        self.encoded_content = ""
        self.encoded_nodes: list[NodeView] = []

        self.restored_content = ""
        self.restored_nodes: list[NodeView] = []
        self.validation_passed: bool | None = None

        self.mapping_dir = DEFAULT_MAPPING_DIR
        self.save_dir = DEFAULT_SAVE_DIR
        self.openlist_config = self._load_openlist_config()
        self._openlist_token_cached: str = ""
        self._openlist_token_cached_at: float = 0.0
        self._openlist_token_cached_fp: str = ""
        self.openlist_upload_queue: list[Path] = []
        self.openlist_failed_uploads: list[Path] = []
        self.openlist_queue_processing: bool = False
        self.flow_mode: str = "text"
        self.text_flow_frame: ttk.Frame | None = None
        self.oc_flow_frame: ttk.Frame | None = None
        self.oc_step_notebook: ttk.Notebook | None = None
        self.oc_profile: str = ""
        self.oc_input_url_var = tk.StringVar(value="")
        self.oc_fake_link_var = tk.StringVar(value="")
        self.oc_original_content = ""
        self.oc_original_nodes: list[NodeView] = []
        self.oc_original_sigs: set[tuple[str, str, int, str]] = set()
        self.oc_encoded_content = ""
        self.oc_encoded_nodes: list[NodeView] = []
        self.oc_restored_content = ""
        self.oc_restored_nodes: list[NodeView] = []
        self.oc_validation_passed: bool | None = None
        self.step_notebook: ttk.Notebook | None = None
        self.oc_source_text: scrolledtext.ScrolledText | None = None
        self.oc_before_text: scrolledtext.ScrolledText | None = None
        self.oc_after_text: scrolledtext.ScrolledText | None = None
        self.oc_converted_text: scrolledtext.ScrolledText | None = None
        self.oc_restored_text: scrolledtext.ScrolledText | None = None
        self._build_ui()

    # ---------------------- UI ----------------------

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        style.configure(
            "Primary.TButton",
            foreground="white",
            background="#0A84FF",
            padding=(12, 6),
            focuscolor="none",
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#006AE6"), ("pressed", "#0058C9"), ("disabled", "#5C5C5C")],
            foreground=[("disabled", "#E5E5E5")],
        )

        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        ttk.Label(root, text=APP_TITLE_WITH_VERSION, font=("PingFang SC", 16, "bold")).pack(anchor="w")
        ttk.Label(
            root,
            text="支持两套流程：网站转换流程 / OpenClash链接流程（互不干扰，可随时切换）",
            foreground="#4a4a4a",
        ).pack(anchor="w", pady=(4, 10))

        # Session bar
        session_bar = ttk.LabelFrame(root, text="会话", padding=8)
        session_bar.pack(fill=tk.X)
        self.profile_var = tk.StringVar(value=self.session_profile)
        ttk.Label(session_bar, text="本次流程ID").pack(side=tk.LEFT)
        self.profile_entry = ttk.Entry(session_bar, textvariable=self.profile_var, width=30)
        self.profile_entry.pack(side=tk.LEFT, padx=(6, 10))
        ttk.Button(session_bar, text="新建会话", command=self._reset_session).pack(side=tk.LEFT)
        ttk.Button(session_bar, text="映射目录", command=self._pick_mapping_dir).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(session_bar, text="保存目录", command=self._pick_save_dir).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(session_bar, text="OpenList设置", command=self._open_openlist_settings).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(session_bar, text="使用说明", command=self._open_help_dialog).pack(side=tk.LEFT, padx=(8, 0))
        self.flow_text_btn = ttk.Button(session_bar, text="网站转换流程", command=lambda: self._switch_flow_mode("text"))
        self.flow_text_btn.pack(side=tk.LEFT, padx=(12, 0))
        self.flow_oc_btn = ttk.Button(session_bar, text="OpenClash链接流程", command=lambda: self._switch_flow_mode("oc"))
        self.flow_oc_btn.pack(side=tk.LEFT, padx=(8, 0))

        # Main split
        main = ttk.Panedwindow(root, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        left = ttk.Frame(main, padding=6)
        right = ttk.Frame(main, padding=6)
        main.add(left, weight=3)
        main.add(right, weight=2)

        self._build_left(left)
        self._build_right(right)
        self._switch_flow_mode("text")

    def _build_left(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        self.text_flow_frame = ttk.Frame(parent)
        self.oc_flow_frame = ttk.Frame(parent)
        self.text_flow_frame.grid(row=0, column=0, sticky="nsew")
        self.oc_flow_frame.grid(row=0, column=0, sticky="nsew")

        self._build_text_flow(self.text_flow_frame)
        self._build_oc_flow(self.oc_flow_frame)

    def _build_text_flow(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        self.step_notebook = ttk.Notebook(parent)
        self.step_notebook.grid(row=0, column=0, sticky="nsew")

        step1 = ttk.Frame(self.step_notebook, padding=8)
        step2 = ttk.Frame(self.step_notebook, padding=8)
        step3 = ttk.Frame(self.step_notebook, padding=8)
        step4 = ttk.Frame(self.step_notebook, padding=8)
        self.step_notebook.add(step1, text="① 输入与解析")
        self.step_notebook.add(step2, text="② 伪装与对比")
        self.step_notebook.add(step3, text="③ 转换结果与还原")
        self.step_notebook.add(step4, text="④ 还原结果")

        # Step 1
        step1.columnconfigure(0, weight=1)
        step1.rowconfigure(1, weight=1)
        tool_row = ttk.Frame(step1)
        tool_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(tool_row, text="从文件载入", command=self._load_input_from_file).pack(side=tk.LEFT)
        ttk.Button(tool_row, text="从URL抓取", command=self._fetch_input_from_url).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(tool_row, text="智能Base64解码", command=self._smart_decode_input).pack(side=tk.LEFT, padx=(8, 0))

        self.input_text = scrolledtext.ScrolledText(step1, wrap=tk.WORD)
        self.input_text.grid(row=1, column=0, sticky="nsew")
        self.input_text.configure(
            background="#101010",
            foreground="#ececec",
            insertbackground="#ffffff",
            relief="solid",
            borderwidth=1,
        )

        next_row = ttk.Frame(step1)
        next_row.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(
            next_row,
            text="提取并展示节点 →",
            command=self._analyze_input,
            style="Primary.TButton",
        ).pack(side=tk.RIGHT)

        # Step 2
        step2.columnconfigure(0, weight=1)
        step2.columnconfigure(1, weight=1)
        step2.rowconfigure(1, weight=1)
        ttk.Label(step2, text="加密前节点链接/信息").grid(row=0, column=0, sticky="w")
        ttk.Label(step2, text="加密后节点链接/信息").grid(row=0, column=1, sticky="w")

        self.before_text = scrolledtext.ScrolledText(step2, wrap=tk.WORD)
        self.before_text.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self.after_text = scrolledtext.ScrolledText(step2, wrap=tk.WORD)
        self.after_text.grid(row=1, column=1, sticky="nsew", padx=(6, 0))

        action_row = ttk.Frame(step2)
        action_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(action_row, text="执行伪装（默认跳过证书校验）", command=self._run_encode).pack(side=tk.LEFT)
        ttk.Button(
            action_row,
            text="复制伪装后链接到剪贴板",
            command=self._copy_after_links,
            style="Primary.TButton",
        ).pack(side=tk.RIGHT)

        # Step 3
        step3.columnconfigure(0, weight=1)
        step3.rowconfigure(1, weight=1)
        ttk.Label(step3, text="转换后内容（全量粘贴，或直接从URL抓取）").grid(row=0, column=0, sticky="w")
        self.converted_text = scrolledtext.ScrolledText(step3, wrap=tk.WORD)
        self.converted_text.grid(row=1, column=0, sticky="nsew")

        restore_actions = ttk.Frame(step3)
        restore_actions.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(restore_actions, text="从URL抓取转换结果", command=self._fetch_converted_from_url).pack(side=tk.LEFT)
        ttk.Button(
            restore_actions,
            text="执行还原",
            command=self._run_decode,
            style="Primary.TButton",
        ).pack(side=tk.RIGHT)

        # Step 4
        step4.columnconfigure(0, weight=1)
        step4.rowconfigure(1, weight=1)
        ttk.Label(step4, text="还原结果文档").grid(row=0, column=0, sticky="w")
        self.restored_text = scrolledtext.ScrolledText(step4, wrap=tk.WORD)
        self.restored_text.grid(row=1, column=0, sticky="nsew")
        save_actions = ttk.Frame(step4)
        save_actions.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(save_actions, text="保存还原文档", command=self._save_restored, style="Primary.TButton").pack(side=tk.RIGHT)

    def _build_oc_flow(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        self.oc_step_notebook = ttk.Notebook(parent)
        self.oc_step_notebook.grid(row=0, column=0, sticky="nsew")

        step1 = ttk.Frame(self.oc_step_notebook, padding=8)
        step2 = ttk.Frame(self.oc_step_notebook, padding=8)
        step3 = ttk.Frame(self.oc_step_notebook, padding=8)
        step4 = ttk.Frame(self.oc_step_notebook, padding=8)
        self.oc_step_notebook.add(step1, text="① 输入订阅URL")
        self.oc_step_notebook.add(step2, text="② 伪装与上传")
        self.oc_step_notebook.add(step3, text="③ OpenClash返回与还原")
        self.oc_step_notebook.add(step4, text="④ 还原结果")

        # Step 1
        step1.columnconfigure(0, weight=1)
        step1.rowconfigure(2, weight=1)
        ttk.Label(step1, text="输入订阅URL（自动抓取 + 去包装 + 节点整理）").grid(row=0, column=0, sticky="w")
        url_row = ttk.Frame(step1)
        url_row.grid(row=1, column=0, sticky="ew", pady=(6, 6))
        url_row.columnconfigure(0, weight=1)
        ttk.Entry(url_row, textvariable=self.oc_input_url_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(url_row, text="粘贴URL", command=self._paste_oc_url).grid(row=0, column=1, padx=(8, 0))

        self.oc_source_text = scrolledtext.ScrolledText(step1, wrap=tk.WORD)
        self.oc_source_text.grid(row=2, column=0, sticky="nsew")
        self.oc_source_text.configure(
            background="#101010",
            foreground="#ececec",
            insertbackground="#ffffff",
            relief="solid",
            borderwidth=1,
        )

        step1_actions = ttk.Frame(step1)
        step1_actions.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(step1_actions, text="抓取并伪装上传 →", command=self._run_oc_encode_upload, style="Primary.TButton").pack(side=tk.RIGHT)

        # Step 2
        step2.columnconfigure(0, weight=1)
        step2.columnconfigure(1, weight=1)
        step2.rowconfigure(1, weight=1)
        ttk.Label(step2, text="原始节点信息").grid(row=0, column=0, sticky="w")
        ttk.Label(step2, text="伪装后节点信息").grid(row=0, column=1, sticky="w")
        self.oc_before_text = scrolledtext.ScrolledText(step2, wrap=tk.WORD)
        self.oc_before_text.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self.oc_after_text = scrolledtext.ScrolledText(step2, wrap=tk.WORD)
        self.oc_after_text.grid(row=1, column=1, sticky="nsew", padx=(6, 0))

        link_row = ttk.Frame(step2)
        link_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        link_row.columnconfigure(1, weight=1)
        ttk.Label(link_row, text="伪装订阅链接").grid(row=0, column=0, sticky="w")
        ttk.Entry(link_row, textvariable=self.oc_fake_link_var).grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(link_row, text="复制伪装后链接到剪贴板", command=self._copy_oc_fake_link, style="Primary.TButton").grid(row=0, column=2)

        # Step 3
        step3.columnconfigure(0, weight=1)
        step3.rowconfigure(1, weight=1)
        ttk.Label(step3, text="粘贴 OpenClash 转换返回内容（全量），然后执行还原").grid(row=0, column=0, sticky="w")
        self.oc_converted_text = scrolledtext.ScrolledText(step3, wrap=tk.WORD)
        self.oc_converted_text.grid(row=1, column=0, sticky="nsew")
        oc_restore_actions = ttk.Frame(step3)
        oc_restore_actions.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(oc_restore_actions, text="从URL抓取转换结果", command=self._fetch_oc_converted_from_url).pack(side=tk.LEFT)
        ttk.Button(oc_restore_actions, text="执行还原", command=self._run_oc_decode, style="Primary.TButton").pack(side=tk.RIGHT)

        # Step 4
        step4.columnconfigure(0, weight=1)
        step4.rowconfigure(1, weight=1)
        ttk.Label(step4, text="还原结果文档（可直接复制回 OpenClash）").grid(row=0, column=0, sticky="w")
        self.oc_restored_text = scrolledtext.ScrolledText(step4, wrap=tk.WORD)
        self.oc_restored_text.grid(row=1, column=0, sticky="nsew")
        oc_save_actions = ttk.Frame(step4)
        oc_save_actions.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(oc_save_actions, text="复制还原文档", command=self._copy_oc_restored).pack(side=tk.LEFT)
        ttk.Button(
            oc_save_actions,
            text="保存并上传到OpenList",
            command=self._save_oc_restored_and_upload,
            style="Primary.TButton",
        ).pack(side=tk.RIGHT, padx=(0, 8))
        ttk.Button(oc_save_actions, text="保存还原文档", command=self._save_oc_restored, style="Primary.TButton").pack(side=tk.RIGHT)

    def _build_right(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        status_row = ttk.Frame(parent)
        status_row.grid(row=0, column=0, sticky="ew")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status_row, text="状态：").pack(side=tk.LEFT)
        ttk.Label(status_row, textvariable=self.status_var, foreground="#0a7").pack(side=tk.LEFT)

        detail_tabs = ttk.Notebook(parent)
        detail_tabs.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        parent.rowconfigure(1, weight=1)

        tab_validation = ttk.Frame(detail_tabs, padding=8)
        tab_log = ttk.Frame(detail_tabs, padding=8)
        detail_tabs.add(tab_validation, text="执行验证")
        detail_tabs.add(tab_log, text="日志")

        tab_validation.columnconfigure(0, weight=1)
        tab_validation.rowconfigure(0, weight=1)
        self.validation_text = scrolledtext.ScrolledText(tab_validation, wrap=tk.WORD)
        self.validation_text.grid(row=0, column=0, sticky="nsew")

        tab_log.columnconfigure(0, weight=1)
        tab_log.rowconfigure(0, weight=1)
        self.log_text = scrolledtext.ScrolledText(tab_log, wrap=tk.WORD)
        self.log_text.grid(row=0, column=0, sticky="nsew")

        btn_row = ttk.Frame(parent)
        btn_row.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(btn_row, text="导出验证详情", command=self._export_validation_detail).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="重试失败上传", command=self._retry_failed_uploads).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btn_row, text="清空验证", command=lambda: self.validation_text.delete("1.0", tk.END)).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btn_row, text="清空日志", command=lambda: self.log_text.delete("1.0", tk.END)).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="退出", command=self.destroy).pack(side=tk.RIGHT)

    # ---------------------- helpers ----------------------

    def _new_profile(self) -> str:
        return "flow_" + datetime.now().strftime("%Y%m%d_%H%M%S")

    def _resource_path(self, file_name: str) -> Path:
        if getattr(sys, "frozen", False):
            base_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        else:
            base_dir = Path(__file__).resolve().parent
        return base_dir / file_name

    def _load_embedded_doc(self, file_name: str, fallback_text: str) -> str:
        path = self._resource_path(file_name)
        try:
            if path.exists():
                return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
        return fallback_text

    def _open_help_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title(f"{APP_TITLE} 使用说明")
        dialog.transient(self)
        dialog.geometry("980x720")

        wrap = ttk.Frame(dialog, padding=10)
        wrap.pack(fill=tk.BOTH, expand=True)
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(1, weight=1)

        ttk.Label(wrap, text="内置文档（可直接复制到 GitHub）", font=("PingFang SC", 12, "bold")).grid(row=0, column=0, sticky="w")

        tabs = ttk.Notebook(wrap)
        tabs.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

        usage_tab = ttk.Frame(tabs, padding=8)
        note_tab = ttk.Frame(tabs, padding=8)
        tabs.add(usage_tab, text="使用教程")
        tabs.add(note_tab, text="项目说明")

        usage_tab.columnconfigure(0, weight=1)
        usage_tab.rowconfigure(0, weight=1)
        note_tab.columnconfigure(0, weight=1)
        note_tab.rowconfigure(0, weight=1)

        usage_text = scrolledtext.ScrolledText(usage_tab, wrap=tk.WORD)
        usage_text.grid(row=0, column=0, sticky="nsew")
        usage_text.insert("1.0", self._load_embedded_doc("USER_GUIDE.md", BUILTIN_USER_GUIDE))
        usage_text.configure(state=tk.DISABLED)

        note_text = scrolledtext.ScrolledText(note_tab, wrap=tk.WORD)
        note_text.grid(row=0, column=0, sticky="nsew")
        note_text.insert("1.0", self._load_embedded_doc("XHS_PROJECT_NOTE.md", "项目说明文档未找到。"))
        note_text.configure(state=tk.DISABLED)

        btns = ttk.Frame(wrap)
        btns.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(btns, text="关闭", command=dialog.destroy).pack(side=tk.RIGHT)

    def _log(self, msg: str) -> None:
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)

    def _set_validation(self, text: str) -> None:
        self.validation_text.delete("1.0", tk.END)
        self.validation_text.insert("1.0", text)

    def _goto_step(self, index: int) -> None:
        if self.step_notebook is None:
            return
        try:
            self.step_notebook.select(index)
        except Exception:
            pass

    def _goto_oc_step(self, index: int) -> None:
        if self.oc_step_notebook is None:
            return
        try:
            self.oc_step_notebook.select(index)
        except Exception:
            pass

    def _switch_flow_mode(self, mode: str) -> None:
        target = "oc" if mode == "oc" else "text"
        self.flow_mode = target
        if target == "oc":
            if self.oc_flow_frame is not None:
                self.oc_flow_frame.tkraise()
            self.flow_oc_btn.configure(style="Primary.TButton")
            self.flow_text_btn.configure(style="TButton")
            self.status_var.set("OpenClash链接流程")
            self._goto_oc_step(0)
        else:
            if self.text_flow_frame is not None:
                self.text_flow_frame.tkraise()
            self.flow_text_btn.configure(style="Primary.TButton")
            self.flow_oc_btn.configure(style="TButton")
            self.status_var.set("网站转换流程")
            self._goto_step(0)

    def _load_openlist_config(self) -> OpenListConfig:
        try:
            if OPENLIST_CONFIG_PATH.exists():
                data = json.loads(OPENLIST_CONFIG_PATH.read_text(encoding="utf-8"))
                return OpenListConfig(
                    enabled=bool(data.get("enabled", False)),
                    base_url=str(data.get("base_url", "")).strip(),
                    username=str(data.get("username", "")).strip(),
                    password=str(data.get("password", "")).strip(),
                    remote_dir=str(data.get("remote_dir", "/subscriptions")).strip() or "/subscriptions",
                    link_template=str(data.get("link_template", "{base_url}/d/{path}")).strip() or "{base_url}/d/{path}",
                    verify_tls=bool(data.get("verify_tls", False)),
                    auto_copy_url=bool(data.get("auto_copy_url", True)),
                    upload_timeout_sec=max(5, int(data.get("upload_timeout_sec", 60))),
                    upload_retry_count=max(0, int(data.get("upload_retry_count", 2))),
                    cleanup_keep_days=max(0, int(data.get("cleanup_keep_days", 30))),
                )
        except Exception:
            pass
        return OpenListConfig()

    def _save_openlist_config(self) -> None:
        OPENLIST_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "enabled": self.openlist_config.enabled,
            "base_url": self.openlist_config.base_url,
            "username": self.openlist_config.username,
            "password": self.openlist_config.password,
            "remote_dir": self.openlist_config.remote_dir,
            "link_template": self.openlist_config.link_template,
            "verify_tls": self.openlist_config.verify_tls,
            "auto_copy_url": self.openlist_config.auto_copy_url,
            "upload_timeout_sec": int(self.openlist_config.upload_timeout_sec),
            "upload_retry_count": int(self.openlist_config.upload_retry_count),
            "cleanup_keep_days": int(self.openlist_config.cleanup_keep_days),
        }
        OPENLIST_CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _open_openlist_settings(self, wait_for_close: bool = False) -> None:
        cfg = self.openlist_config
        dialog = tk.Toplevel(self)
        dialog.title("OpenList 上传设置")
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("760x430")

        enabled_var = tk.BooleanVar(value=cfg.enabled)
        verify_tls_var = tk.BooleanVar(value=cfg.verify_tls)
        auto_copy_var = tk.BooleanVar(value=cfg.auto_copy_url)
        base_url_var = tk.StringVar(value=cfg.base_url)
        username_var = tk.StringVar(value=cfg.username)
        password_var = tk.StringVar(value=cfg.password)
        remote_dir_var = tk.StringVar(value=cfg.remote_dir)
        link_template_var = tk.StringVar(value=cfg.link_template)
        timeout_var = tk.StringVar(value=str(cfg.upload_timeout_sec))
        retry_var = tk.StringVar(value=str(cfg.upload_retry_count))
        cleanup_var = tk.StringVar(value=str(cfg.cleanup_keep_days))
        test_status_var = tk.StringVar(value="联通测试：未测试")

        form = ttk.Frame(dialog, padding=12)
        form.pack(fill=tk.BOTH, expand=True)
        form.columnconfigure(1, weight=1)

        row = 0
        ttk.Checkbutton(form, text="启用保存后自动上传到 OpenList", variable=enabled_var).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        ttk.Label(form, text="OpenList 地址").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(form, textvariable=base_url_var).grid(row=row, column=1, sticky="ew", pady=(8, 0))
        row += 1
        ttk.Label(form, text="用户名").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(form, textvariable=username_var).grid(row=row, column=1, sticky="ew", pady=(8, 0))
        row += 1
        ttk.Label(form, text="密码").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(form, textvariable=password_var, show="*").grid(row=row, column=1, sticky="ew", pady=(8, 0))
        row += 1
        ttk.Label(form, text="目标目录").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(form, textvariable=remote_dir_var).grid(row=row, column=1, sticky="ew", pady=(8, 0))
        row += 1
        ttk.Label(form, text="链接模板").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(form, textvariable=link_template_var).grid(row=row, column=1, sticky="ew", pady=(8, 0))
        row += 1
        ttk.Label(form, text="模板变量: {base_url} {path} {path_raw} {filename}").grid(row=row, column=1, sticky="w", pady=(4, 0))
        row += 1
        ttk.Label(form, text="上传超时(秒)").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(form, textvariable=timeout_var).grid(row=row, column=1, sticky="ew", pady=(8, 0))
        row += 1
        ttk.Label(form, text="失败重试次数").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(form, textvariable=retry_var).grid(row=row, column=1, sticky="ew", pady=(8, 0))
        row += 1
        ttk.Label(form, text="本地文件保留天数(0关闭)").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(form, textvariable=cleanup_var).grid(row=row, column=1, sticky="ew", pady=(8, 0))
        row += 1
        ttk.Checkbutton(form, text="严格校验证书（不建议，失败会自动忽略）", variable=verify_tls_var).grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Checkbutton(form, text="上传后自动复制链接", variable=auto_copy_var).grid(row=row, column=1, sticky="w", pady=(8, 0))
        row += 1
        ttk.Label(form, textvariable=test_status_var, foreground="#0a7").grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 0))

        btn_row = ttk.Frame(form)
        btn_row.grid(row=row + 1, column=0, columnspan=2, sticky="ew", pady=(14, 0))

        def build_cfg_from_form() -> OpenListConfig:
            def parse_int(value: str, default: int, min_value: int) -> int:
                try:
                    number = int(value.strip())
                    return max(min_value, number)
                except Exception:
                    return default

            return OpenListConfig(
                enabled=enabled_var.get(),
                base_url=base_url_var.get().strip().rstrip("/"),
                username=username_var.get().strip(),
                password=password_var.get().strip(),
                remote_dir=self._normalize_remote_dir(remote_dir_var.get().strip()),
                link_template=link_template_var.get().strip() or "{base_url}/d/{path}",
                verify_tls=verify_tls_var.get(),
                auto_copy_url=auto_copy_var.get(),
                upload_timeout_sec=parse_int(timeout_var.get(), 60, 5),
                upload_retry_count=parse_int(retry_var.get(), 2, 0),
                cleanup_keep_days=parse_int(cleanup_var.get(), 30, 0),
            )

        test_btn: ttk.Button | None = None
        save_btn: ttk.Button | None = None

        def set_testing(is_testing: bool) -> None:
            state = tk.DISABLED if is_testing else tk.NORMAL
            if test_btn is not None:
                test_btn.configure(state=state)
            if save_btn is not None:
                save_btn.configure(state=state)

        def test_connection() -> None:
            cfg_for_test = build_cfg_from_form()
            test_status_var.set("联通测试：测试中...")
            set_testing(True)

            def worker() -> None:
                try:
                    latency_ms = self._openlist_test_connection(cfg_for_test)
                    msg = f"联通测试：成功，延迟 {latency_ms:.0f} ms"
                except Exception as exc:
                    msg = f"联通测试：失败（{exc}）"

                def finish() -> None:
                    test_status_var.set(msg)
                    set_testing(False)

                dialog.after(0, finish)

            threading.Thread(target=worker, daemon=True).start()

        def save_and_close() -> None:
            self.openlist_config = build_cfg_from_form()
            try:
                self._save_openlist_config()
                self._log("[设置] OpenList 配置已保存")
                dialog.destroy()
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc), parent=dialog)

        test_btn = ttk.Button(btn_row, text="联通测试", command=test_connection)
        test_btn.pack(side=tk.LEFT)
        save_btn = ttk.Button(btn_row, text="保存", command=save_and_close)
        save_btn.pack(side=tk.RIGHT)
        ttk.Button(btn_row, text="取消", command=dialog.destroy).pack(side=tk.RIGHT, padx=(0, 8))
        if wait_for_close:
            dialog.wait_window()

    def _openlist_has_required_fields(self, cfg: OpenListConfig | None = None) -> bool:
        c = cfg or self.openlist_config
        return bool(c.base_url.strip() and c.username.strip() and c.password.strip())

    def _get_active_profile(self) -> str:
        p = self.profile_var.get().strip()
        if not p:
            p = self._new_profile()
            self.profile_var.set(p)
        return p

    def _get_oc_profile(self) -> str:
        active = self._get_active_profile()
        if not self.oc_profile or not self.oc_profile.startswith(active):
            self.oc_profile = f"{active}_oc"
        return self.oc_profile

    def _pick_mapping_dir(self) -> None:
        path = filedialog.askdirectory(title="选择映射目录")
        if path:
            self.mapping_dir = Path(path)
            self._log(f"[设置] 映射目录: {path}")

    def _pick_save_dir(self) -> None:
        path = filedialog.askdirectory(title="选择自动保存目录")
        if path:
            self.save_dir = Path(path)
            self._log(f"[设置] 保存目录: {path}")

    def _load_input_from_file(self) -> None:
        path = filedialog.askopenfilename(title="选择输入文件")
        if not path:
            return
        content = Path(path).read_text(encoding="utf-8", errors="replace")
        self.input_text.delete("1.0", tk.END)
        self.input_text.insert("1.0", content)
        self._log(f"[输入] 已载入文件: {path}")

    def _fetch_input_from_url(self) -> None:
        url = simpledialog.askstring("输入URL", "请输入订阅URL")
        if not url:
            return
        try:
            data = self._fetch_text_from_url(url.strip())
            self.input_text.delete("1.0", tk.END)
            self.input_text.insert("1.0", data)
            self._log(f"[输入] 已抓取URL: {url.strip()}")
            self._goto_step(0)
        except Exception as exc:
            messagebox.showerror("抓取失败", str(exc))

    def _fetch_converted_from_url(self) -> None:
        url = simpledialog.askstring("输入URL", "请输入转换后文件URL")
        if not url:
            return
        try:
            data = self._fetch_text_from_url(url.strip())
            self.converted_text.delete("1.0", tk.END)
            self.converted_text.insert("1.0", data)
            self._log(f"[转换结果] 已抓取URL: {url.strip()}")
            self._goto_step(2)
        except Exception as exc:
            messagebox.showerror("抓取失败", str(exc))

    def _paste_oc_url(self) -> None:
        try:
            text = str(self.clipboard_get()).strip()
        except Exception:
            text = ""
        if not text:
            messagebox.showwarning("剪贴板为空", "未检测到可用 URL")
            return
        self.oc_input_url_var.set(text)
        self._log("[OpenClash流程] 已从剪贴板填入订阅URL")

    def _fetch_oc_converted_from_url(self) -> None:
        url = simpledialog.askstring("输入URL", "请输入 OpenClash 转换后文件 URL")
        if not url:
            return
        try:
            data = self._fetch_text_from_url(url.strip())
            if self.oc_converted_text is not None:
                self.oc_converted_text.delete("1.0", tk.END)
                self.oc_converted_text.insert("1.0", data)
            self._log(f"[OpenClash流程] 已抓取转换结果URL: {url.strip()}")
            self._goto_oc_step(2)
        except Exception as exc:
            messagebox.showerror("抓取失败", str(exc))

    def _fetch_text_from_url(
        self,
        url: str,
        user_agent: str | None = None,
        auto_decode_base64: bool = True,
        accept: str | None = None,
    ) -> str:
        ua = (user_agent or FETCH_USER_AGENT).strip() or FETCH_USER_AGENT
        accept_value = accept or "text/plain, application/yaml, application/x-yaml, */*;q=0.8"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": ua,
                "Accept": accept_value,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            raw_bytes = resp.read()
        text = raw_bytes.decode("utf-8", errors="replace")
        compact = "".join(text.split())
        if auto_decode_base64 and core.is_mostly_base64(compact):
            try:
                decoded = core.b64_decode_loose(text).decode("utf-8", errors="strict")
                if core.text_contains_uri_lines(decoded) or "proxies:" in decoded:
                    text = decoded
                    self._log("[抓取] 检测到Base64包装，已自动解码")
            except Exception:
                pass
        return text

    def _fetch_openclash_source_yaml(self, url: str) -> str:
        errors: list[str] = []
        for ua in OPENCLASH_FETCH_UA_CANDIDATES:
            try:
                text = self._fetch_text_from_url(
                    url,
                    user_agent=ua,
                    auto_decode_base64=True,
                    accept="application/yaml, application/x-yaml, text/yaml, text/plain, */*;q=0.8",
                )
                parsed_type, _, wrap_type = core.parse_content(text.encode("utf-8"))
                self._log(f"[OpenClash流程] UA={ua} 抓取成功，格式={parsed_type}, 包装={wrap_type}")
                if parsed_type == "clash_yaml":
                    return text
                errors.append(f"UA={ua} 返回 {parsed_type}")
            except Exception as exc:
                errors.append(f"UA={ua} 失败: {exc}")
                continue

        hint = "；".join(errors[:3]) if errors else "无可用响应"
        raise RuntimeError(f"未获取到 Clash YAML 完整配置（{hint}）。请确认订阅支持 clash/meta User-Agent 返回配置。")

    def _normalize_remote_dir(self, value: str) -> str:
        raw = value.replace("\\", "/").strip()
        if not raw:
            return "/"
        parts = [p for p in raw.split("/") if p not in ("", ".")]
        return "/" + "/".join(parts)

    def _encode_multipart_file(self, field_name: str, file_name: str, data: bytes, boundary: str) -> bytes:
        head = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field_name}"; filename="{file_name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
        return head + data + tail

    def _openlist_api_json(
        self,
        method: str,
        path: str,
        body: dict,
        token: str | None = None,
        cfg: OpenListConfig | None = None,
        timeout_sec: int = 30,
    ) -> dict:
        cfg = cfg or self.openlist_config
        if not cfg.base_url:
            raise RuntimeError("OpenList 地址未配置")
        url = cfg.base_url.rstrip("/") + path
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", USER_AGENT)
        if token:
            req.add_header("Authorization", token)
        text = self._openlist_request_with_ssl_fallback(req, timeout_sec, cfg)
        return json.loads(text)

    def _openlist_request_with_ssl_fallback(self, req: urllib.request.Request, timeout_sec: int, cfg: OpenListConfig) -> str:
        verify_ctx = ssl.create_default_context() if cfg.verify_tls else ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec, context=verify_ctx) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except ssl.SSLCertVerificationError:
            self._log("[OpenList] 证书校验失败，已自动忽略并重试")
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if not isinstance(reason, ssl.SSLCertVerificationError):
                raise
            self._log("[OpenList] 证书校验失败，已自动忽略并重试")

        unverified_ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=timeout_sec, context=unverified_ctx) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def _openlist_cfg_fingerprint(self, cfg: OpenListConfig) -> str:
        return "|".join(
            [
                cfg.base_url.rstrip("/"),
                cfg.username,
                cfg.remote_dir,
                "1" if cfg.verify_tls else "0",
            ]
        )

    def _openlist_get_cached_token(self, cfg: OpenListConfig) -> str:
        fingerprint = self._openlist_cfg_fingerprint(cfg)
        if self._openlist_token_cached and self._openlist_token_cached_fp == fingerprint:
            if time.time() - self._openlist_token_cached_at < 50 * 60:
                return self._openlist_token_cached
        return ""

    def _openlist_set_cached_token(self, cfg: OpenListConfig, token: str) -> None:
        self._openlist_token_cached = token
        self._openlist_token_cached_at = time.time()
        self._openlist_token_cached_fp = self._openlist_cfg_fingerprint(cfg)

    def _openlist_invalidate_cached_token(self) -> None:
        self._openlist_token_cached = ""
        self._openlist_token_cached_at = 0.0
        self._openlist_token_cached_fp = ""

    def _openlist_login(self, cfg: OpenListConfig | None = None, force_refresh: bool = False) -> str:
        cfg = cfg or self.openlist_config
        if not cfg.username or not cfg.password:
            raise RuntimeError("OpenList 用户名或密码未配置")
        if not force_refresh:
            cached = self._openlist_get_cached_token(cfg)
            if cached:
                return cached
        data = self._openlist_api_json(
            "POST",
            "/api/auth/login",
            {"username": cfg.username, "password": cfg.password},
            cfg=cfg,
            timeout_sec=max(5, cfg.upload_timeout_sec),
        )
        if int(data.get("code", 500)) != 200:
            raise RuntimeError(f"OpenList 登录失败: {data.get('message', 'unknown error')}")
        token = str((data.get("data") or {}).get("token", "")).strip()
        if not token:
            raise RuntimeError("OpenList 登录成功但未返回 token")
        self._openlist_set_cached_token(cfg, token)
        return token

    def _openlist_test_connection(self, cfg: OpenListConfig) -> float:
        start = time.perf_counter()
        self._openlist_login(cfg, force_refresh=True)
        return (time.perf_counter() - start) * 1000.0

    def _openlist_upload_file(
        self,
        local_path: Path,
        token: str,
        cfg: OpenListConfig | None = None,
        timeout_sec: int | None = None,
    ) -> None:
        cfg = cfg or self.openlist_config
        remote_dir = self._normalize_remote_dir(cfg.remote_dir)
        timeout = timeout_sec or max(5, cfg.upload_timeout_sec)
        file_name = local_path.name
        file_bytes = local_path.read_bytes()
        auth_candidates = self._build_openlist_auth_candidates(token)

        self._openlist_ensure_remote_dir(remote_dir, auth_candidates, cfg, timeout)

        errors: list[str] = []
        for auth_value in auth_candidates:
            try:
                self._openlist_upload_form_once(
                    cfg=cfg,
                    auth_value=auth_value,
                    remote_dir=remote_dir,
                    file_name=file_name,
                    file_bytes=file_bytes,
                    timeout_sec=timeout,
                )
                return
            except Exception as exc:
                errors.append(f"form({auth_value[:10]}...): {exc}")

            try:
                self._openlist_upload_put_once(
                    cfg=cfg,
                    auth_value=auth_value,
                    remote_dir=remote_dir,
                    file_name=file_name,
                    file_bytes=file_bytes,
                    timeout_sec=timeout,
                )
                return
            except Exception as exc:
                errors.append(f"put({auth_value[:10]}...): {exc}")

        short = " | ".join(errors[:4]) if errors else "unknown error"
        raise RuntimeError(f"OpenList 上传失败: {short}")

    def _build_openlist_auth_candidates(self, token: str) -> list[str]:
        raw = token.strip()
        if not raw:
            return []
        values = [raw]
        if raw.lower().startswith("bearer "):
            trimmed = raw[7:].strip()
            if trimmed:
                values.append(trimmed)
        else:
            values.append(f"Bearer {raw}")
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            if value and value not in seen:
                deduped.append(value)
                seen.add(value)
        return deduped

    def _openlist_parse_response_or_raise(self, text: str, action_name: str) -> None:
        try:
            data = json.loads(text)
        except Exception:
            raise RuntimeError(f"{action_name} 返回非JSON: {text[:180]}")
        if int(data.get("code", 500)) != 200:
            raise RuntimeError(f"{action_name}失败: {data.get('message', 'unknown error')}")

    def _openlist_upload_form_once(
        self,
        cfg: OpenListConfig,
        auth_value: str,
        remote_dir: str,
        file_name: str,
        file_bytes: bytes,
        timeout_sec: int,
    ) -> None:
        boundary = f"----AegisMeshBoundary{self._timestamp_ms()}"
        content = self._encode_multipart_file("file", file_name, file_bytes, boundary)
        url = cfg.base_url.rstrip("/") + "/api/fs/form"
        req = urllib.request.Request(url, data=content, method="PUT")
        req.add_header("Authorization", auth_value)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        req.add_header("File-Path", remote_dir)
        req.add_header("Overwrite", "true")
        req.add_header("As-Task", "false")
        req.add_header("User-Agent", USER_AGENT)
        try:
            text = self._openlist_request_with_ssl_fallback(req, timeout_sec, cfg)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise RuntimeError(f"HTTP {exc.code} {exc.reason}; {body[:180]}")
        self._openlist_parse_response_or_raise(text, "OpenList form上传")

    def _openlist_upload_put_once(
        self,
        cfg: OpenListConfig,
        auth_value: str,
        remote_dir: str,
        file_name: str,
        file_bytes: bytes,
        timeout_sec: int,
    ) -> None:
        remote_file = f"{remote_dir.rstrip('/')}/{file_name}" if remote_dir != "/" else f"/{file_name}"
        url = cfg.base_url.rstrip("/") + "/api/fs/put"
        req = urllib.request.Request(url, data=file_bytes, method="PUT")
        req.add_header("Authorization", auth_value)
        req.add_header("File-Path", remote_file)
        req.add_header("Content-Type", "application/octet-stream")
        req.add_header("Content-Length", str(len(file_bytes)))
        req.add_header("As-Task", "false")
        req.add_header("User-Agent", USER_AGENT)
        try:
            text = self._openlist_request_with_ssl_fallback(req, timeout_sec, cfg)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise RuntimeError(f"HTTP {exc.code} {exc.reason}; {body[:180]}")
        self._openlist_parse_response_or_raise(text, "OpenList put上传")

    def _openlist_ensure_remote_dir(
        self,
        remote_dir: str,
        auth_candidates: list[str],
        cfg: OpenListConfig,
        timeout_sec: int,
    ) -> None:
        if remote_dir in ("", "/"):
            return
        payload = json.dumps({"path": remote_dir}, ensure_ascii=False).encode("utf-8")
        url = cfg.base_url.rstrip("/") + "/api/fs/mkdir"
        last_error: Exception | None = None
        for auth_value in auth_candidates:
            req = urllib.request.Request(url, data=payload, method="POST")
            req.add_header("Authorization", auth_value)
            req.add_header("Content-Type", "application/json")
            req.add_header("User-Agent", USER_AGENT)
            try:
                text = self._openlist_request_with_ssl_fallback(req, timeout_sec, cfg)
                try:
                    data = json.loads(text)
                    code = int(data.get("code", 500))
                    if code == 200:
                        return
                    msg = str(data.get("message", ""))
                    # 已存在目录时不阻断上传
                    if "exist" in msg.lower() or "exists" in msg.lower():
                        return
                    last_error = RuntimeError(f"mkdir 失败: {msg}")
                except Exception as exc:
                    last_error = exc
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            self._log(f"[OpenList] mkdir 失败，继续尝试上传: {last_error}")

    def _openlist_build_link(self, file_name: str) -> str:
        cfg = self.openlist_config
        remote_dir = self._normalize_remote_dir(cfg.remote_dir).strip("/")
        remote_path_raw = f"{remote_dir}/{file_name}" if remote_dir else file_name
        remote_path = urllib.parse.quote(remote_path_raw, safe="/")
        template = cfg.link_template or "{base_url}/d/{path}"
        return template.format(
            base_url=cfg.base_url.rstrip("/"),
            path=remote_path,
            path_raw=remote_path_raw,
            filename=urllib.parse.quote(file_name),
        )

    def _try_upload_to_openlist(
        self,
        local_path: Path,
        status_cb: Callable[[str], None] | None = None,
        force_upload: bool = False,
    ) -> str:
        cfg = self.openlist_config
        if not cfg.enabled and not force_upload:
            return ""

        attempts = max(1, cfg.upload_retry_count + 1)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            if status_cb:
                status_cb(f"OpenList 上传中（第 {attempt}/{attempts} 次）...")
            try:
                token = self._openlist_login(cfg)
                self._openlist_upload_file(local_path, token, cfg=cfg, timeout_sec=cfg.upload_timeout_sec)
                return self._openlist_build_link(local_path.name)
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    self._openlist_invalidate_cached_token()
                last_error = exc
            except Exception as exc:
                last_error = exc

            if attempt < attempts and status_cb:
                status_cb(f"上传失败，准备重试（{attempt}/{attempts}）...")
                time.sleep(0.8)

        if last_error is None:
            raise RuntimeError("OpenList 上传失败：未知错误")
        raise last_error

    def _export_validation_detail(self) -> None:
        text = self.validation_text.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("无内容", "当前没有可导出的验证详情")
            return
        self.save_dir.mkdir(parents=True, exist_ok=True)
        default_name = f"{self._timestamp_ms()}_validation.txt"
        path = filedialog.asksaveasfilename(
            title="导出验证详情",
            initialdir=str(self.save_dir),
            initialfile=default_name,
            defaultextension=".txt",
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")],
        )
        if not path:
            return
        log_tail = self.log_text.get("1.0", tk.END).strip()
        payload = [
            f"导出时间: {datetime.now().isoformat()}",
            f"流程ID: {self._get_active_profile()}",
            "",
            "=== 验证详情 ===",
            text,
            "",
            "=== 日志 ===",
            log_tail,
            "",
        ]
        Path(path).write_text("\n".join(payload), encoding="utf-8")
        self._log(f"[导出] 已导出验证详情: {path}")

    def _smart_decode_input(self) -> None:
        raw = self.input_text.get("1.0", tk.END).strip()
        if not raw:
            return
        try:
            decoded = core.b64_decode_loose(raw).decode("utf-8", errors="strict")
            if core.text_contains_uri_lines(decoded) or "proxies:" in decoded:
                self.input_text.delete("1.0", tk.END)
                self.input_text.insert("1.0", decoded)
                self._log("[输入] 已自动识别并解码Base64")
                return
        except Exception:
            pass
        self._log("[输入] 内容无需Base64解码")

    def _extract_nodes(self, content: str) -> tuple[list[NodeView], str, str]:
        parsed_type, parsed_data, wrap_type = core.parse_content(content.encode("utf-8"))
        nodes: list[NodeView] = []

        if parsed_type == "uri_list":
            text = parsed_data if isinstance(parsed_data, str) else str(parsed_data)
            for raw in text.splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if not core.URI_SCHEME_PATTERN.match(line):
                    continue
                if line.startswith("vmess://"):
                    vm, frag = core.parse_vmess_line(line)
                    host = str(vm.get("add", "")).strip()
                    port = int(str(vm.get("port", "0")))
                    name = str(vm.get("ps", "")).strip() or frag
                    token = str(vm.get("id", ""))
                    nodes.append(NodeView("vmess", host, port, name, line, token))
                else:
                    u, userinfo, host, port, name = core.parse_generic_uri(line)
                    nodes.append(NodeView(u.scheme.lower(), host, port, name, line, userinfo))

        elif parsed_type == "clash_yaml":
            data = parsed_data if isinstance(parsed_data, dict) else {}
            for p in data.get("proxies", []):
                if not isinstance(p, dict):
                    continue
                scheme = str(p.get("type", "unknown"))
                host = str(p.get("server", "")).strip()
                try:
                    port = int(p.get("port", 0))
                except Exception:
                    port = 0
                name = str(p.get("name", "")).strip()
                token = str(p.get("uuid", p.get("password", p.get("cipher", ""))))
                raw = f"{scheme}://{host}:{port}#{urllib.parse.quote(name, safe='')}"
                nodes.append(NodeView(scheme, host, port, name, raw, token))

        return nodes, parsed_type, wrap_type

    def _nodes_to_text(self, nodes: list[NodeView]) -> str:
        return "\n".join(n.raw for n in nodes)

    def _normalize_ss_token(self, token: str) -> str:
        raw = token.strip()
        if not raw:
            return ""

        candidates: list[str] = [raw, urllib.parse.unquote(raw)]
        try:
            decoded = core.b64_decode_loose(raw).decode("utf-8", errors="strict")
            candidates.append(decoded)
            candidates.append(urllib.parse.unquote(decoded))
        except Exception:
            pass

        for candidate in candidates:
            head = candidate.split("@", 1)[0].strip()
            if ":" in head:
                method, password = head.split(":", 1)
                if method and password:
                    return password.strip()
        return raw

    def _normalize_token(self, scheme: str, token: str) -> str:
        s = scheme.lower().strip()
        t = token.strip()
        if not t:
            return ""
        if s == "ss":
            return self._normalize_ss_token(t)
        return urllib.parse.unquote(t)

    def _node_signature(self, n: NodeView) -> tuple[str, str, int, str]:
        return (n.scheme.lower(), n.host, int(n.port), self._normalize_token(n.scheme, n.token))

    def _analyze_input(self) -> None:
        raw = self.input_text.get("1.0", tk.END).strip()
        if not raw:
            messagebox.showwarning("空输入", "请先粘贴输入内容")
            return
        try:
            nodes, ptype, wrap = self._extract_nodes(raw)
        except Exception as exc:
            messagebox.showerror("解析失败", str(exc))
            return

        self.original_content = raw
        self.original_nodes = nodes
        self.original_sigs = {self._node_signature(n) for n in nodes}

        self.before_text.delete("1.0", tk.END)
        self.before_text.insert("1.0", self._nodes_to_text(nodes))

        self._log(f"[解析] 格式={ptype}, 包装={wrap}, 提取节点={len(nodes)}")
        self.status_var.set(f"已提取节点: {len(nodes)}")
        self._goto_step(1)

    # ---------------------- encode/decode ----------------------

    def _run_encode(self) -> None:
        raw = self.input_text.get("1.0", tk.END).strip()
        if not raw:
            messagebox.showwarning("空输入", "请先粘贴输入内容")
            return
        profile = self._get_active_profile()

        # 自动先解析输入
        if self.original_content != raw or not self.original_nodes:
            self._analyze_input()
            if not self.original_nodes:
                return

        try:
            with tempfile.TemporaryDirectory(prefix="aegismesh_") as td:
                out = Path(td) / "encoded.txt"
                args = argparse.Namespace(
                    input_url=None,
                    input_file=None,
                    input_text=raw,
                    output=out,
                    profile=profile,
                    mapping_dir=self.mapping_dir,
                    fake_suffix="mask.invalid",
                    strict=True,
                    insecure=True,  # 默认跳过证书校验
                    ca_file=None,
                    inject_nid=False,  # 默认不注入，降低转换站侧分组干扰
                )
                buf = io.StringIO()
                with redirect_stdout(buf), redirect_stderr(buf):
                    code = core.encode_action(args)
                logs = buf.getvalue().strip()
                if logs:
                    self._log(logs)
                if code != 0:
                    raise RuntimeError(f"伪装失败，退出码: {code}")
                self.encoded_content = out.read_text(encoding="utf-8", errors="replace")

            self.encoded_nodes, _, _ = self._extract_nodes(self.encoded_content)
            self.after_text.delete("1.0", tk.END)
            self.after_text.insert("1.0", self._nodes_to_text(self.encoded_nodes))

            self.status_var.set(f"伪装完成: {len(self.encoded_nodes)} 节点")
            self._log("[伪装] 已完成，可直接复制伪装后链接")
            self._goto_step(1)
        except Exception as exc:
            self._log(f"[错误] 伪装失败: {exc}")
            messagebox.showerror("伪装失败", str(exc))

    def _copy_after_links(self) -> None:
        txt = self.after_text.get("1.0", tk.END).strip()
        if not txt:
            messagebox.showwarning("无内容", "请先执行伪装")
            return
        self.clipboard_clear()
        self.clipboard_append(txt)
        self._log("[复制] 已复制伪装后链接到剪贴板")
        self._goto_step(2)
        messagebox.showinfo("已复制", "伪装后链接已复制到剪贴板")

    def _run_decode(self) -> None:
        converted = self.converted_text.get("1.0", tk.END).strip()
        if not converted:
            messagebox.showwarning("空输入", "请先粘贴转换后的内容")
            return

        profile = self._get_active_profile()

        try:
            with tempfile.TemporaryDirectory(prefix="aegismesh_") as td:
                inp = Path(td) / "converted.txt"
                out = Path(td) / "restored.txt"
                inp.write_text(converted, encoding="utf-8")
                args = argparse.Namespace(
                    input_file=inp,
                    output=out,
                    profile=profile,
                    mapping_dir=self.mapping_dir,
                    strict=True,
                )
                buf = io.StringIO()
                with redirect_stdout(buf), redirect_stderr(buf):
                    code = core.decode_action(args)
                logs = buf.getvalue().strip()
                if logs:
                    self._log(logs)
                if code != 0:
                    raise RuntimeError(f"还原失败，退出码: {code}")
                self.restored_content = out.read_text(encoding="utf-8", errors="replace")

            self.restored_text.delete("1.0", tk.END)
            self.restored_text.insert("1.0", self.restored_content)
            self.restored_nodes, _, _ = self._extract_nodes(self.restored_content)

            self._run_auto_validation()
            self._save_flow_history()

            self.status_var.set("还原完成，并已自动验证")
            self._log("[还原] 已完成，验证结果见右侧执行验证窗口")
            self._goto_step(3)
        except Exception as exc:
            self._log(f"[错误] 还原失败: {exc}")
            messagebox.showerror("还原失败", str(exc))

    def _detect_output_ext(self, content: str) -> str:
        try:
            parsed_type, _, _ = core.parse_content(content.encode("utf-8"))
            if parsed_type == "clash_yaml":
                return ".yaml"
        except Exception:
            pass
        # OpenClash 流程里尽量避免 .txt 后缀，部分在线转换流程会按后缀做限制
        return ".sub"

    def _run_oc_encode_upload(self) -> None:
        url = self.oc_input_url_var.get().strip()
        if not url:
            messagebox.showwarning("空输入", "请先输入订阅 URL")
            return
        if not self._openlist_has_required_fields():
            should_open = messagebox.askyesno("未配置OpenList", "OpenClash 链接流程需要 OpenList 地址/账号/密码，是否现在打开设置？")
            if not should_open:
                return
            self._open_openlist_settings(wait_for_close=True)
            if not self._openlist_has_required_fields():
                messagebox.showwarning("配置未完成", "请先完整填写 OpenList 地址、用户名和密码并保存")
                return

        if not self.openlist_config.enabled:
            self._log("[OpenClash流程] 已检测到 OpenList 配置完整，忽略“启用自动上传”开关并继续")

        try:
            source = self._fetch_openclash_source_yaml(url)
            if self.oc_source_text is not None:
                self.oc_source_text.delete("1.0", tk.END)
                self.oc_source_text.insert("1.0", source)

            nodes, ptype, wrap = self._extract_nodes(source)
            if ptype != "clash_yaml":
                raise RuntimeError("OpenClash 模式要求抓取到 Clash YAML 完整配置，当前不是 YAML 结构。")
            self.oc_original_content = source
            self.oc_original_nodes = nodes
            self.oc_original_sigs = {self._node_signature(n) for n in nodes}
            if self.oc_before_text is not None:
                self.oc_before_text.delete("1.0", tk.END)
                self.oc_before_text.insert("1.0", self._nodes_to_text(nodes))
            self._log(f"[OpenClash流程] 输入抓取成功，格式={ptype}, 包装={wrap}, 节点={len(nodes)}")

            profile = self._get_oc_profile()
            with tempfile.TemporaryDirectory(prefix="aegismesh_oc_") as td:
                out = Path(td) / "encoded.txt"
                args = argparse.Namespace(
                    input_url=None,
                    input_file=None,
                    input_text=source,
                    output=out,
                    profile=profile,
                    mapping_dir=self.mapping_dir,
                    fake_suffix="mask.invalid",
                    strict=True,
                    insecure=True,
                    ca_file=None,
                    inject_nid=False,
                )
                buf = io.StringIO()
                with redirect_stdout(buf), redirect_stderr(buf):
                    code = core.encode_action(args)
                logs = buf.getvalue().strip()
                if logs:
                    self._log(logs)
                if code != 0:
                    raise RuntimeError(f"伪装失败，退出码: {code}")
                self.oc_encoded_content = out.read_text(encoding="utf-8", errors="replace")

            self.oc_encoded_nodes, _, _ = self._extract_nodes(self.oc_encoded_content)
            if self.oc_after_text is not None:
                self.oc_after_text.delete("1.0", tk.END)
                self.oc_after_text.insert("1.0", self._nodes_to_text(self.oc_encoded_nodes))

            self.save_dir.mkdir(parents=True, exist_ok=True)
            ts = self._timestamp_ms()
            ext = self._detect_output_ext(self.oc_encoded_content)
            if ext != ".yaml":
                raise RuntimeError("OpenClash 模式伪装后内容不是 YAML，已终止上传。")
            encoded_path = self.save_dir / f"{ts}_obfuscated{ext}"
            encoded_path.write_text(self.oc_encoded_content, encoding="utf-8")
            self._log(f"[OpenClash流程] 已保存伪装中间文件: {encoded_path}")

            self.status_var.set("OpenList 上传中...")
            uploaded_url = self._upload_to_openlist_with_progress(encoded_path)
            if not uploaded_url:
                raise RuntimeError("上传成功但未返回链接")
            self.oc_fake_link_var.set(uploaded_url)
            self._show_qr_for_link(uploaded_url)
            if self.openlist_config.auto_copy_url:
                self.clipboard_clear()
                self.clipboard_append(uploaded_url)
                self._log("[OpenClash流程] 已复制伪装订阅链接到剪贴板")

            self.status_var.set(f"OpenClash 伪装链接就绪: {len(self.oc_encoded_nodes)} 节点")
            self._log(f"[OpenClash流程] 上传成功: {uploaded_url}")
            self._goto_oc_step(1)
            messagebox.showinfo("已生成伪装链接", f"请将此链接交给 OpenClash 转换：\n{uploaded_url}")
        except Exception as exc:
            self.status_var.set("OpenClash流程执行失败")
            self._log(f"[错误] OpenClash流程伪装上传失败: {exc}")
            messagebox.showerror("OpenClash流程失败", str(exc))

    def _copy_oc_fake_link(self) -> None:
        link = self.oc_fake_link_var.get().strip()
        if not link:
            messagebox.showwarning("无链接", "请先执行抓取并伪装上传")
            return
        self.clipboard_clear()
        self.clipboard_append(link)
        self._log("[OpenClash流程] 已复制伪装订阅链接")
        self._goto_oc_step(2)
        messagebox.showinfo("已复制", "伪装订阅链接已复制到剪贴板")

    def _run_oc_decode(self) -> None:
        if self.oc_converted_text is None:
            return
        converted = self.oc_converted_text.get("1.0", tk.END).strip()
        if not converted:
            messagebox.showwarning("空输入", "请先粘贴 OpenClash 转换后的内容")
            return

        profile = self._get_oc_profile()
        try:
            with tempfile.TemporaryDirectory(prefix="aegismesh_oc_") as td:
                inp = Path(td) / "converted.txt"
                out = Path(td) / "restored.txt"
                inp.write_text(converted, encoding="utf-8")
                args = argparse.Namespace(
                    input_file=inp,
                    output=out,
                    profile=profile,
                    mapping_dir=self.mapping_dir,
                    strict=True,
                )
                buf = io.StringIO()
                with redirect_stdout(buf), redirect_stderr(buf):
                    code = core.decode_action(args)
                logs = buf.getvalue().strip()
                if logs:
                    self._log(logs)
                if code != 0:
                    raise RuntimeError(f"还原失败，退出码: {code}")
                self.oc_restored_content = out.read_text(encoding="utf-8", errors="replace")

            if self.oc_restored_text is not None:
                self.oc_restored_text.delete("1.0", tk.END)
                self.oc_restored_text.insert("1.0", self.oc_restored_content)
            self.oc_restored_nodes, _, _ = self._extract_nodes(self.oc_restored_content)
            self._run_oc_auto_validation()

            self.status_var.set("OpenClash流程还原完成")
            self._log("[OpenClash流程] 还原完成，结果见步骤④")
            self._goto_oc_step(3)
        except Exception as exc:
            self._log(f"[错误] OpenClash流程还原失败: {exc}")
            messagebox.showerror("还原失败", str(exc))

    def _run_oc_auto_validation(self) -> None:
        if not self.oc_original_nodes:
            self.oc_validation_passed = False
            self._set_validation("OpenClash流程验证失败：缺少原始节点基准（请先完成步骤①）。")
            return

        orig_sigs = {self._node_signature(n) for n in self.oc_original_nodes}
        rest_sigs = {self._node_signature(n) for n in self.oc_restored_nodes}
        ok = orig_sigs == rest_sigs
        self.oc_validation_passed = ok

        lines = []
        lines.append(f"流程ID: {self._get_oc_profile()}")
        lines.append(f"原始节点数: {len(self.oc_original_nodes)}")
        lines.append(f"还原节点数: {len(self.oc_restored_nodes)}")
        lines.append(f"签名集合一致: {'通过' if ok else '不通过'}")
        if not ok:
            only_orig = sorted(orig_sigs - rest_sigs)
            only_rest = sorted(rest_sigs - orig_sigs)
            lines.append(f"仅原始存在: {len(only_orig)}")
            lines.append(f"仅还原存在: {len(only_rest)}")
            if only_orig:
                lines.append(f"样例(原始): {only_orig[0]}")
            if only_rest:
                lines.append(f"样例(还原): {only_rest[0]}")
        has_mask_restored = any(n.host.endswith(".mask.invalid") for n in self.oc_restored_nodes)
        lines.append(f"还原结果仍含mask域: {'是' if has_mask_restored else '否'}")
        self._set_validation("\n".join(lines))

    def _run_auto_validation(self) -> None:
        if not self.original_nodes:
            self.validation_passed = False
            self._set_validation("验证失败：缺少原始节点基准（请先执行提取/伪装）。")
            return

        orig_sigs = {self._node_signature(n) for n in self.original_nodes}
        rest_sigs = {self._node_signature(n) for n in self.restored_nodes}

        ok = orig_sigs == rest_sigs
        self.validation_passed = ok

        lines = []
        lines.append(f"流程ID: {self._get_active_profile()}")
        lines.append(f"原始节点数: {len(self.original_nodes)}")
        lines.append(f"还原节点数: {len(self.restored_nodes)}")
        lines.append(f"签名集合一致: {'通过' if ok else '不通过'}")

        if not ok:
            only_orig = sorted(orig_sigs - rest_sigs)
            only_rest = sorted(rest_sigs - orig_sigs)
            lines.append(f"仅原始存在: {len(only_orig)}")
            lines.append(f"仅还原存在: {len(only_rest)}")
            if only_orig:
                lines.append(f"样例(原始): {only_orig[0]}")
            if only_rest:
                lines.append(f"样例(还原): {only_rest[0]}")

        # 补充状态提示：是否包含伪装域
        has_mask_restored = any(n.host.endswith(".mask.invalid") for n in self.restored_nodes)
        lines.append(f"还原结果仍含mask域: {'是' if has_mask_restored else '否'}")

        self._set_validation("\n".join(lines))

    def _save_flow_history(self) -> None:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = HISTORY_DIR / f"{self._get_active_profile()}_{now}.json"

        def sha256_text(s: str) -> str:
            return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()

        payload = {
            "app": APP_TITLE,
            "version": APP_VERSION,
            "saved_at": datetime.now().isoformat(),
            "profile": self._get_active_profile(),
            "mapping_dir": str(self.mapping_dir),
            "counts": {
                "original": len(self.original_nodes),
                "encoded": len(self.encoded_nodes),
                "restored": len(self.restored_nodes),
            },
            "validation": {
                "passed": self.validation_passed,
                "detail": self.validation_text.get("1.0", tk.END).strip(),
            },
            "hashes": {
                "original_input_sha256": sha256_text(self.original_content or ""),
                "encoded_sha256": sha256_text(self.encoded_content or ""),
                "restored_sha256": sha256_text(self.restored_content or ""),
            },
        }

        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._log(f"[记录] 已保存流程记录: {path}")

    # ---------------------- save output ----------------------

    def _enqueue_openlist_upload(self, local_path: Path) -> None:
        self.openlist_upload_queue.append(local_path)
        self._log(f"[OpenList] 已加入上传队列: {local_path.name}（待处理 {len(self.openlist_upload_queue)}）")
        if not self.openlist_queue_processing:
            self.after(10, self._process_openlist_upload_queue)

    def _process_openlist_upload_queue(self) -> None:
        if self.openlist_queue_processing:
            return
        self.openlist_queue_processing = True
        try:
            while self.openlist_upload_queue:
                current = self.openlist_upload_queue.pop(0)
                if not current.exists():
                    self._log(f"[OpenList] 跳过不存在文件: {current}")
                    continue

                self.status_var.set(f"OpenList 上传中: {current.name}")
                try:
                    uploaded_url = self._upload_to_openlist_with_progress(current)
                    if not uploaded_url:
                        raise RuntimeError("上传成功但未获得下载地址")
                    self._on_openlist_upload_success(current, uploaded_url)
                except Exception as exc:
                    self.openlist_failed_uploads.append(current)
                    self._log(f"[OpenList] 上传失败: {current.name} -> {exc}")
                    messagebox.showwarning("OpenList上传失败", f"文件: {current.name}\n失败原因: {exc}\n可点击“重试失败上传”再次上传。")
        finally:
            self.openlist_queue_processing = False
            self.status_var.set("就绪")

    def _on_openlist_upload_success(self, local_path: Path, uploaded_url: str) -> None:
        self._log(f"[OpenList] 上传成功: {uploaded_url}")
        if self.openlist_config.auto_copy_url:
            self.clipboard_clear()
            self.clipboard_append(uploaded_url)
            self._log("[OpenList] 已复制下载地址到剪贴板")
        messagebox.showinfo("OpenList上传成功", f"文件: {local_path.name}\n下载地址:\n{uploaded_url}")
        self._show_qr_for_link(uploaded_url)

    def _retry_failed_uploads(self) -> None:
        if not self.openlist_failed_uploads:
            messagebox.showinfo("提示", "当前没有失败上传任务")
            return
        failed = list(self.openlist_failed_uploads)
        self.openlist_failed_uploads = []
        for path in failed:
            if path.exists():
                self.openlist_upload_queue.append(path)
        self._log(f"[OpenList] 已加入失败任务重试队列: {len(failed)}")
        if not self.openlist_queue_processing:
            self.after(10, self._process_openlist_upload_queue)

    def _cleanup_old_saved_files(self) -> int:
        keep_days = max(0, int(self.openlist_config.cleanup_keep_days))
        if keep_days <= 0:
            return 0

        cutoff = time.time() - keep_days * 86400
        removed = 0
        for pattern in ("*.yaml", "*.yml"):
            for path in self.save_dir.glob(pattern):
                try:
                    if path.is_file() and path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except Exception:
                    continue
        return removed

    def _upload_to_openlist_with_progress(self, local_path: Path, force_upload: bool = False) -> str:
        dialog = tk.Toplevel(self)
        dialog.title("OpenList 上传中")
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("460x130")
        dialog.resizable(False, False)

        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        status_var = tk.StringVar(value="准备上传...")
        ttk.Label(frame, textvariable=status_var).pack(anchor="w")
        progress = ttk.Progressbar(frame, mode="indeterminate")
        progress.pack(fill=tk.X, pady=(10, 0))
        progress.start(12)
        ttk.Label(frame, text="请稍候，上传会自动重试。", foreground="#666").pack(anchor="w", pady=(8, 0))

        result: dict[str, object] = {"url": "", "error": None}

        def set_status(text: str) -> None:
            dialog.after(0, lambda: status_var.set(text))

        def worker() -> None:
            try:
                url = self._try_upload_to_openlist(local_path, status_cb=set_status, force_upload=force_upload)
                result["url"] = url
            except Exception as exc:
                result["error"] = exc
            finally:
                dialog.after(0, finish)

        def finish() -> None:
            progress.stop()
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", lambda: None)
        threading.Thread(target=worker, daemon=True).start()
        dialog.wait_window()

        if result["error"] is not None:
            err = result["error"]
            if isinstance(err, Exception):
                raise err
            raise RuntimeError(str(err))

        return str(result["url"] or "")

    def _show_qr_for_link(self, link: str) -> None:
        qr_dialog = tk.Toplevel(self)
        qr_dialog.title("下载二维码")
        qr_dialog.transient(self)
        qr_dialog.geometry("420x520")

        wrap = ttk.Frame(qr_dialog, padding=12)
        wrap.pack(fill=tk.BOTH, expand=True)
        ttk.Label(wrap, text="手机扫码下载订阅").pack(anchor="w")

        img_holder = ttk.Label(wrap, text="正在生成二维码...")
        img_holder.pack(fill=tk.BOTH, expand=True, pady=(8, 8))

        link_entry = ttk.Entry(wrap)
        link_entry.insert(0, link)
        link_entry.pack(fill=tk.X)

        def copy_link() -> None:
            self.clipboard_clear()
            self.clipboard_append(link)
            self._log("[OpenList] 已复制下载地址到剪贴板")

        btns = ttk.Frame(wrap)
        btns.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btns, text="复制链接", command=copy_link).pack(side=tk.LEFT)
        ttk.Button(btns, text="关闭", command=qr_dialog.destroy).pack(side=tk.RIGHT)

        try:
            qr_api = "https://api.qrserver.com/v1/create-qr-code/?size=320x320&data=" + urllib.parse.quote(link, safe="")
            req = urllib.request.Request(qr_api, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as resp:
                img_bytes = resp.read()
            tmp_path = Path(tempfile.gettempdir()) / f"aegismesh_qr_{self._timestamp_ms()}.png"
            tmp_path.write_bytes(img_bytes)
            image = tk.PhotoImage(file=str(tmp_path))
            img_holder.configure(image=image, text="")
            img_holder.image = image
        except Exception as exc:
            img_holder.configure(text=f"二维码生成失败，请直接使用下方链接。\n{exc}")

    def _next_yaml_save_path(self) -> Path:
        self.save_dir.mkdir(parents=True, exist_ok=True)
        ts = self._timestamp_ms()
        path = self.save_dir / f"{ts}.yaml"
        if not path.exists():
            return path
        suffix = 1
        while True:
            candidate = self.save_dir / f"{ts}_{suffix:03d}.yaml"
            if not candidate.exists():
                return candidate
            suffix += 1

    def _next_marked_yaml_save_path(self, marker: str) -> Path:
        self.save_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        safe_marker = "".join(ch for ch in marker if ch.isascii() and (ch.isalnum() or ch in {"_", "-"}))
        safe_marker = safe_marker.strip("_-") or "tag"
        path = self.save_dir / f"{ts}_{safe_marker}.yaml"
        if not path.exists():
            return path
        suffix = 1
        while True:
            candidate = self.save_dir / f"{ts}_{safe_marker}_{suffix:03d}.yaml"
            if not candidate.exists():
                return candidate
            suffix += 1

    def _save_yaml_content(self, content: str) -> Path:
        yaml_text = self._prepare_yaml_for_save(content)
        path = self._next_yaml_save_path()
        path.write_text(yaml_text, encoding="utf-8")
        return path

    def _copy_oc_restored(self) -> None:
        if self.oc_restored_text is None:
            return
        content = self.oc_restored_text.get("1.0", tk.END).strip()
        if not content:
            messagebox.showwarning("无内容", "当前没有可复制的还原文档")
            return
        self.clipboard_clear()
        self.clipboard_append(content)
        self._log("[OpenClash流程] 已复制还原文档到剪贴板")
        messagebox.showinfo("已复制", "还原文档已复制到剪贴板")

    def _save_oc_restored(self) -> None:
        if self.oc_restored_text is None:
            return
        content = self.oc_restored_text.get("1.0", tk.END).strip()
        if not content:
            messagebox.showwarning("无内容", "当前没有可保存的还原文档")
            return
        try:
            path = self._save_yaml_content(content)
        except Exception as exc:
            messagebox.showerror("保存失败", f"无法转换为 YAML: {exc}")
            return

        self._log(f"[OpenClash流程] 已自动保存还原文档: {path}")
        removed_count = self._cleanup_old_saved_files()
        if removed_count > 0:
            self._log(f"[清理] 已清理旧文件: {removed_count} 个")
        messagebox.showinfo("保存成功", f"已保存到:\n{path}")

    def _save_oc_restored_and_upload(self) -> None:
        if self.oc_restored_text is None:
            return
        content = self.oc_restored_text.get("1.0", tk.END).strip()
        if not content:
            messagebox.showwarning("无内容", "当前没有可保存的还原文档")
            return

        if not self._openlist_has_required_fields():
            should_open = messagebox.askyesno("未配置OpenList", "需要先配置 OpenList 地址/账号/密码，是否现在打开设置？")
            if should_open:
                self._open_openlist_settings(wait_for_close=True)
            if not self._openlist_has_required_fields():
                messagebox.showwarning("未配置OpenList", "OpenList 配置不完整，已取消上传。")
                return

        try:
            yaml_text = self._prepare_yaml_for_save(content)
            path = self._next_marked_yaml_save_path("ocrestored")
            path.write_text(yaml_text, encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("保存失败", f"无法转换为 YAML: {exc}")
            return

        self._log(f"[OpenClash流程] 已保存待上传还原文档: {path}")
        removed_count = self._cleanup_old_saved_files()
        if removed_count > 0:
            self._log(f"[清理] 已清理旧文件: {removed_count} 个")

        try:
            uploaded_url = self._upload_to_openlist_with_progress(path, force_upload=True)
            if not uploaded_url:
                raise RuntimeError("上传成功但未返回链接")
            self._on_openlist_upload_success(path, uploaded_url)
        except Exception as exc:
            self._log(f"[OpenClash流程] 上传失败: {exc}")
            messagebox.showwarning("上传失败", f"文件已保存：\n{path}\n\nOpenList 上传失败：{exc}")

    def _save_restored(self) -> None:
        content = self.restored_text.get("1.0", tk.END).strip()
        if not content:
            messagebox.showwarning("无内容", "当前没有可保存的还原文档")
            return
        try:
            path = self._save_yaml_content(content)
        except Exception as exc:
            messagebox.showerror("保存失败", f"无法转换为 YAML: {exc}")
            return

        self._log(f"[保存] 已自动保存还原文档: {path}")
        removed_count = self._cleanup_old_saved_files()
        if removed_count > 0:
            self._log(f"[清理] 已清理旧文件: {removed_count} 个")

        if self.openlist_config.enabled:
            self._enqueue_openlist_upload(path)
            messagebox.showinfo("保存成功", f"已保存到:\n{path}\n\n已加入 OpenList 上传队列。")
        else:
            messagebox.showinfo("保存成功", f"已保存到:\n{path}")

    def _timestamp_ms(self) -> str:
        now = datetime.now()
        return now.strftime("%Y%m%d%H%M%S") + f"{now.microsecond // 1000:03d}"

    def _prepare_yaml_for_save(self, content: str) -> str:
        parsed_type, parsed_data, _ = core.parse_content(content.encode("utf-8"))

        if parsed_type == "clash_yaml" and isinstance(parsed_data, dict):
            obj = parsed_data
        else:
            # best-effort: URI 列表转成最小可读 YAML
            nodes, _, _ = self._extract_nodes(content)
            proxies = []
            for n in nodes:
                proxies.append(
                    {
                        "name": core.strip_nid(n.name),
                        "type": n.scheme,
                        "server": n.host,
                        "port": int(n.port),
                    }
                )
            obj = {
                "proxies": proxies,
                "proxy-groups": [
                    {
                        "name": "AUTO",
                        "type": "select",
                        "proxies": [p["name"] for p in proxies] + ["DIRECT"],
                    }
                ],
                "rules": ["MATCH,AUTO"],
            }

        self._sanitize_yaml_obj(obj)
        return yaml.safe_dump(obj, allow_unicode=True, sort_keys=False)

    def _sanitize_yaml_obj(self, obj: dict) -> None:
        # 1) 代理名称清理（移除 NID 干扰）
        proxies = obj.get("proxies")
        proxy_names: set[str] = set()
        proxy_aliases: dict[str, str] = {}
        if isinstance(proxies, list):
            for p in proxies:
                if not isinstance(p, dict):
                    continue
                if isinstance(p.get("name"), str):
                    cleaned_name = core.strip_nid(p["name"])
                    p["name"] = cleaned_name
                name = p.get("name")
                if isinstance(name, str):
                    proxy_names.add(name)
                    proxy_aliases[name] = name

        # 2) 分组名称/引用清理（仅去干扰，不做删减，避免误伤有效分组）
        proxy_groups = obj.get("proxy-groups")
        group_names: set[str] = set()
        if isinstance(proxy_groups, list):
            for g in proxy_groups:
                if not isinstance(g, dict):
                    continue
                if isinstance(g.get("name"), str):
                    cleaned_group_name = core.strip_nid(g["name"])
                    g["name"] = cleaned_group_name
                    group_names.add(cleaned_group_name)
                    proxy_aliases[cleaned_group_name] = cleaned_group_name
                    proxy_aliases[core.strip_nid(cleaned_group_name)] = cleaned_group_name

            for g in proxy_groups:
                if not isinstance(g, dict):
                    continue
                refs = g.get("proxies")
                if isinstance(refs, list):
                    cleaned: list[object] = []
                    for r in refs:
                        if not isinstance(r, str):
                            cleaned.append(r)
                            continue
                        stripped = core.strip_nid(r)
                        mapped = (
                            proxy_aliases.get(stripped)
                            or proxy_aliases.get(r)
                            or stripped
                        )

                        if (
                            mapped in {"DIRECT", "REJECT", "GLOBAL"}
                            or mapped in proxy_names
                            or mapped in group_names
                        ):
                            cleaned.append(mapped)
                        else:
                            cleaned.append(stripped)
                    g["proxies"] = cleaned

        # 3) 规则行中如果带 NID，统一剔除
        rules = obj.get("rules")
        if isinstance(rules, list):
            obj["rules"] = [core.strip_nid(r) if isinstance(r, str) else r for r in rules]

    # ---------------------- session ----------------------

    def _reset_session(self) -> None:
        self.session_profile = self._new_profile()
        self.profile_var.set(self.session_profile)
        self.oc_profile = ""

        self.original_content = ""
        self.original_nodes = []
        self.original_sigs = set()
        self.encoded_content = ""
        self.encoded_nodes = []
        self.restored_content = ""
        self.restored_nodes = []
        self.validation_passed = None
        self.oc_original_content = ""
        self.oc_original_nodes = []
        self.oc_original_sigs = set()
        self.oc_encoded_content = ""
        self.oc_encoded_nodes = []
        self.oc_restored_content = ""
        self.oc_restored_nodes = []
        self.oc_validation_passed = None
        self.oc_input_url_var.set("")
        self.oc_fake_link_var.set("")

        self.input_text.delete("1.0", tk.END)
        self.before_text.delete("1.0", tk.END)
        self.after_text.delete("1.0", tk.END)
        self.converted_text.delete("1.0", tk.END)
        self.restored_text.delete("1.0", tk.END)
        if self.oc_source_text is not None:
            self.oc_source_text.delete("1.0", tk.END)
        if self.oc_before_text is not None:
            self.oc_before_text.delete("1.0", tk.END)
        if self.oc_after_text is not None:
            self.oc_after_text.delete("1.0", tk.END)
        if self.oc_converted_text is not None:
            self.oc_converted_text.delete("1.0", tk.END)
        if self.oc_restored_text is not None:
            self.oc_restored_text.delete("1.0", tk.END)
        self._set_validation("")
        self.status_var.set("已新建会话")
        self._log(f"[会话] 已重置为: {self.session_profile}")
        if self.flow_mode == "oc":
            self._goto_oc_step(0)
        else:
            self._goto_step(0)


def main() -> int:
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
