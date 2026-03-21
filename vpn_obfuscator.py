#!/usr/bin/env python3
"""
本地订阅伪装/还原工具

- encode: 真实 server/port -> 假 server/port，并注入 NID 标识
- decode: 假 server/port -> 真实 server/port，移除 NID 标识
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import random
import re
import ssl
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import yaml
except Exception:
    yaml = None


NID_PATTERN = re.compile(r"\s*\[NID:([a-f0-9]{10,16})\]\s*", re.IGNORECASE)
URI_SCHEME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
SUPPORTED_GENERIC_SCHEMES = {
    "vless",
    "trojan",
    "ss",
    "ssr",
    "socks",
    "http",
    "https",
    "hysteria",
    "hysteria2",
    "tuic",
    "wireguard",
}


class ObfuscationError(Exception):
    pass


@dataclass
class NodeRecord:
    node_id: str
    real_host: str
    real_port: int
    fake_host: str
    fake_port: int
    name_before: str
    name_after: str
    source_type: str


@dataclass
class ProcessResult:
    content: str
    records: List[NodeRecord]
    untouched_lines: List[int]


class MappingStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def profile_path(self, profile: str) -> Path:
        return self.root / f"{profile}.mapping.json"

    def save(self, profile: str, records: Sequence[NodeRecord], meta: Dict[str, Any]) -> Path:
        path = self.profile_path(profile)
        payload = {
            "version": 1,
            "profile": profile,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "meta": meta,
            "records": [
                {
                    "node_id": r.node_id,
                    "real_host": r.real_host,
                    "real_port": r.real_port,
                    "fake_host": r.fake_host,
                    "fake_port": r.fake_port,
                    "name_before": r.name_before,
                    "name_after": r.name_after,
                    "source_type": r.source_type,
                }
                for r in records
            ],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        return path

    def load(self, profile: str) -> Dict[str, Any]:
        path = self.profile_path(profile)
        if not path.exists():
            raise ObfuscationError(f"映射文件不存在: {path}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ObfuscationError(f"映射文件不是合法 JSON: {path}") from exc


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_padding(value: str) -> str:
    return value + "=" * (-len(value) % 4)


def b64_decode_loose(value: str) -> bytes:
    cleaned = "".join(value.strip().split())
    return base64.urlsafe_b64decode(add_padding(cleaned))


def b64_encode_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def is_mostly_base64(text: str) -> bool:
    stripped = "".join(text.split())
    if len(stripped) < 24:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9+/=_-]+", stripped))


def split_userinfo_and_hostport(netloc: str) -> Tuple[str, str]:
    if "@" in netloc:
        userinfo, hostport = netloc.rsplit("@", 1)
        return userinfo, hostport
    return "", netloc


def split_host_port(hostport: str) -> Tuple[Optional[str], Optional[int]]:
    hostport = hostport.strip()
    if not hostport:
        return None, None

    if hostport.startswith("["):
        right = hostport.find("]")
        if right == -1:
            return None, None
        host = hostport[1:right]
        remain = hostport[right + 1 :]
        if not remain.startswith(":"):
            return host, None
        port_str = remain[1:]
    else:
        if ":" not in hostport:
            return hostport, None
        host, port_str = hostport.rsplit(":", 1)

    if not port_str.isdigit():
        return None, None
    port = int(port_str)
    if port <= 0 or port > 65535:
        return None, None

    if hostport.startswith("["):
        return host, port
    host = host.strip()
    if not host:
        return None, None
    return host, port


def ensure_bracket_if_ipv6(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def strip_nid(name: str) -> str:
    return NID_PATTERN.sub(" ", name).strip()


def preferred_name_from_record(rec: Dict[str, Any], fallback_name: str) -> str:
    for key in ("name_before", "name_after"):
        value = rec.get(key)
        if isinstance(value, str) and value.strip():
            return strip_nid(value)
    return strip_nid(fallback_name)


def with_nid(name: str, node_id: str) -> str:
    cleaned = strip_nid(name)
    if cleaned:
        return f"{cleaned} [NID:{node_id}]"
    return f"[NID:{node_id}]"


def extract_nid(name: str) -> Optional[str]:
    m = NID_PATTERN.search(name)
    if not m:
        return None
    return m.group(1).lower()


def build_node_id(source_type: str, host: str, port: int, name: str, index: int) -> str:
    seed = f"{source_type}|{host}|{port}|{name}|{index}".encode("utf-8")
    return hashlib.sha1(seed).hexdigest()[:12]


def gen_fake_endpoint(node_id: str, fake_suffix: str, occupied: set[str]) -> Tuple[str, int]:
    # 生成唯一假地址端口；避免碰撞
    counter = 0
    while True:
        seed = hashlib.sha256(f"{node_id}:{counter}:{random.random()}".encode("utf-8")).hexdigest()
        label = f"n{seed[:10]}"
        fake_host = f"{label}.{fake_suffix}".lower()
        fake_port = 10000 + (int(seed[10:14], 16) % 50000)
        key = f"{fake_host}:{fake_port}"
        if key not in occupied:
            occupied.add(key)
            return fake_host, fake_port
        counter += 1


def fetch_input(
    input_url: Optional[str],
    input_file: Optional[Path],
    input_text: Optional[str] = None,
    timeout: int = 20,
    insecure: bool = False,
    ca_file: Optional[Path] = None,
) -> bytes:
    present = sum(1 for x in (input_url, input_file, input_text) if x)
    if present > 1:
        raise ObfuscationError("--input-url / --input-file / --input-text 只能三选一")
    if present == 0:
        raise ObfuscationError("必须指定 --input-url 或 --input-file 或 --input-text")

    if input_url:
        req = urllib.request.Request(
            input_url,
            headers={
                "User-Agent": "vpn-obfuscator/1.0",
                "Accept": "*/*",
            },
        )
        if insecure:
            ctx = ssl._create_unverified_context()
        else:
            try:
                ctx = ssl.create_default_context(cafile=str(ca_file) if ca_file else None)
            except Exception as exc:
                raise ObfuscationError(f"无法加载 CA 证书文件: {ca_file}, {exc}") from exc
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.read()
        except ssl.SSLCertVerificationError as exc:
            raise ObfuscationError(
                "HTTPS 证书校验失败。可选方案：1) 安装系统/企业根证书；2) encode 时指定 --ca-file；"
                "3) 仅测试时使用 --insecure 跳过校验。"
            ) from exc

    if input_file is not None:
        return input_file.read_bytes()
    assert input_text is not None
    return input_text.encode("utf-8")


def try_parse_yaml(text: str) -> Optional[Dict[str, Any]]:
    if yaml is None:
        return None
    try:
        loaded = yaml.safe_load(text)
    except Exception:
        return None
    if not isinstance(loaded, dict):
        return None
    return loaded


def text_contains_uri_lines(text: str) -> bool:
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if URI_SCHEME_PATTERN.match(line):
            return True
        if "|" in line:
            parts = [x.strip() for x in line.split("|") if x.strip()]
            if any(URI_SCHEME_PATTERN.match(p) for p in parts):
                return True
    return False


def normalize_uri_text(text: str) -> str:
    out: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if "|" in line:
            parts = [x.strip() for x in line.split("|") if x.strip()]
            if len(parts) > 1 and all(URI_SCHEME_PATTERN.match(p) for p in parts):
                out.extend(parts)
                continue
        out.append(raw)
    return "\n".join(out).strip()


def parse_vmess_line(line: str) -> Tuple[Dict[str, Any], str]:
    body = line[len("vmess://") :].strip()
    fragment = ""
    if "#" in body:
        body, fragment = body.split("#", 1)
        fragment = urllib.parse.unquote(fragment)

    raw_json = b64_decode_loose(body).decode("utf-8", errors="strict")
    obj = json.loads(raw_json)
    if not isinstance(obj, dict):
        raise ObfuscationError("vmess 内容不是 JSON 对象")

    host = str(obj.get("add", "")).strip()
    port_raw = str(obj.get("port", "")).strip()
    if not host or not port_raw.isdigit():
        raise ObfuscationError("vmess 缺少有效 add/port")

    return obj, fragment


def build_vmess_line(obj: Dict[str, Any], fragment: str) -> str:
    encoded = b64_encode_no_pad(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if fragment:
        return f"vmess://{encoded}#{urllib.parse.quote(fragment, safe='')}"
    return f"vmess://{encoded}"


def parse_generic_uri(line: str) -> Tuple[urllib.parse.SplitResult, str, str, int, str]:
    u = urllib.parse.urlsplit(line)
    scheme = u.scheme.lower()
    if scheme not in SUPPORTED_GENERIC_SCHEMES:
        raise ObfuscationError(f"暂不支持的 URI 协议: {scheme}")

    userinfo, hostport = split_userinfo_and_hostport(u.netloc)
    host, port = split_host_port(hostport)
    if host is None or port is None:
        raise ObfuscationError(f"无法解析 host/port: {line[:80]}")

    name = urllib.parse.unquote(u.fragment or "")
    return u, userinfo, host, port, name


def build_generic_uri(
    u: urllib.parse.SplitResult,
    userinfo: str,
    host: str,
    port: int,
    name: str,
) -> str:
    host_part = ensure_bracket_if_ipv6(host)
    host_port_part = f"{host_part}:{port}"
    netloc = f"{userinfo}@{host_port_part}" if userinfo else host_port_part
    fragment = urllib.parse.quote(name, safe="") if name else ""
    return urllib.parse.urlunsplit((u.scheme, netloc, u.path, u.query, fragment))


def parse_content(raw: bytes) -> Tuple[str, Any, str]:
    text = raw.decode("utf-8", errors="replace").strip()

    if yaml is None and "proxies:" in text and ("server:" in text or "proxy-groups:" in text):
        raise ObfuscationError("检测到可能是 Clash YAML，但未安装 PyYAML。请先执行: python3 -m pip install -r requirements.txt")

    # 1) Clash YAML
    loaded = try_parse_yaml(text)
    if loaded and isinstance(loaded.get("proxies"), list):
        return "clash_yaml", loaded, "plain"

    # 2) URI 明文
    if text_contains_uri_lines(text):
        return "uri_list", normalize_uri_text(text), "plain"

    # 3) Base64 包装 URI
    if is_mostly_base64(text):
        try:
            decoded = b64_decode_loose(text).decode("utf-8", errors="strict")
            if text_contains_uri_lines(decoded):
                return "uri_list", normalize_uri_text(decoded), "base64"
        except Exception:
            pass

    raise ObfuscationError("无法识别输入格式。当前支持: URI 列表 / Base64-URI / Clash YAML")


def encode_clash_yaml(
    data: Dict[str, Any],
    fake_suffix: str,
    strict: bool,
    inject_nid: bool,
) -> ProcessResult:
    proxies = data.get("proxies")
    if not isinstance(proxies, list):
        raise ObfuscationError("YAML 缺少 proxies 列表")

    occupied: set[str] = set()
    records: List[NodeRecord] = []
    untouched: List[int] = []

    for idx, node in enumerate(proxies):
        if not isinstance(node, dict):
            if strict:
                raise ObfuscationError(f"proxies[{idx}] 不是对象")
            untouched.append(idx + 1)
            continue

        host = str(node.get("server", "")).strip()
        port_raw = node.get("port")

        try:
            port = int(port_raw)
            if port <= 0 or port > 65535:
                raise ValueError
        except Exception:
            if strict:
                raise ObfuscationError(f"proxies[{idx}] 端口非法: {port_raw}")
            untouched.append(idx + 1)
            continue

        if not host:
            if strict:
                raise ObfuscationError(f"proxies[{idx}] server 为空")
            untouched.append(idx + 1)
            continue

        name_before = str(node.get("name", "")).strip()
        node_id = build_node_id("clash", host, port, name_before, idx)
        fake_host, fake_port = gen_fake_endpoint(node_id, fake_suffix, occupied)
        name_after = with_nid(name_before, node_id) if inject_nid else name_before

        node["server"] = fake_host
        node["port"] = fake_port
        node["name"] = name_after

        records.append(
            NodeRecord(
                node_id=node_id,
                real_host=host,
                real_port=port,
                fake_host=fake_host,
                fake_port=fake_port,
                name_before=name_before,
                name_after=name_after,
                source_type="clash",
            )
        )

    if yaml is None:
        raise ObfuscationError("缺少 PyYAML，无法输出 YAML")
    output = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    return ProcessResult(content=output, records=records, untouched_lines=untouched)


def decode_clash_yaml(
    data: Dict[str, Any],
    mapping_by_id: Dict[str, Dict[str, Any]],
    mapping_by_fake: Dict[str, Dict[str, Any]],
    mapping_by_real: Dict[str, Dict[str, Any]],
    strict: bool,
) -> ProcessResult:
    proxies = data.get("proxies")
    if not isinstance(proxies, list):
        raise ObfuscationError("YAML 缺少 proxies 列表")

    used_ids: set[str] = set()
    untouched: List[int] = []
    name_aliases: Dict[str, str] = {}

    for idx, node in enumerate(proxies):
        if not isinstance(node, dict):
            if strict:
                raise ObfuscationError(f"proxies[{idx}] 不是对象")
            untouched.append(idx + 1)
            continue

        host = str(node.get("server", "")).strip()
        port_raw = node.get("port")
        name = str(node.get("name", "")).strip()

        try:
            port = int(port_raw)
        except Exception:
            port = -1

        node_id = extract_nid(name)
        rec = None
        if node_id and node_id in mapping_by_id:
            rec = mapping_by_id[node_id]
            used_ids.add(node_id)
        else:
            rec = mapping_by_fake.get(f"{host}:{port}")
            if rec:
                used_ids.add(rec["node_id"])
            else:
                # 兼容：转换站可能已把真实地址回填，支持按真实 endpoint 命中映射
                rec = mapping_by_real.get(f"{host}:{port}")
                if rec:
                    used_ids.add(rec["node_id"])

        if rec is None:
            if strict:
                raise ObfuscationError(f"proxies[{idx}] 找不到映射: name={name}, endpoint={host}:{port}")
            untouched.append(idx + 1)
            continue

        node["server"] = rec["real_host"]
        node["port"] = int(rec["real_port"])
        restored_name = preferred_name_from_record(rec, name)
        node["name"] = restored_name

        for candidate in (
            name,
            strip_nid(name),
            rec.get("name_after"),
            rec.get("name_before"),
        ):
            if isinstance(candidate, str):
                cleaned = strip_nid(candidate)
                if cleaned:
                    name_aliases[cleaned] = restored_name

    missing = [k for k in mapping_by_id.keys() if k not in used_ids]
    if strict and missing:
        raise ObfuscationError(f"有 {len(missing)} 条映射未被使用，可能文件不匹配 profile")

    # 清理 proxy-groups 中残留的 NID，并尽量映射回还原后的代理名称
    proxy_groups = data.get("proxy-groups")
    if isinstance(proxy_groups, list):
        group_name_aliases: Dict[str, str] = {}
        for group in proxy_groups:
            if not isinstance(group, dict):
                continue
            group_name = group.get("name")
            if isinstance(group_name, str):
                cleaned_group_name = strip_nid(group_name)
                group["name"] = cleaned_group_name
                if cleaned_group_name:
                    group_name_aliases[cleaned_group_name] = cleaned_group_name

        for group in proxy_groups:
            if not isinstance(group, dict):
                continue
            proxies_ref = group.get("proxies")
            if isinstance(proxies_ref, list):
                cleaned: List[Any] = []
                for item in proxies_ref:
                    if isinstance(item, str):
                        token = strip_nid(item)
                        mapped = (
                            name_aliases.get(token)
                            or group_name_aliases.get(token)
                            or token
                        )
                        cleaned.append(mapped)
                    else:
                        cleaned.append(item)
                group["proxies"] = cleaned

    if yaml is None:
        raise ObfuscationError("缺少 PyYAML，无法输出 YAML")
    output = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    return ProcessResult(content=output, records=[], untouched_lines=untouched)


def encode_uri_list(text: str, fake_suffix: str, strict: bool, inject_nid: bool) -> ProcessResult:
    lines = text.splitlines()
    occupied: set[str] = set()
    records: List[NodeRecord] = []
    untouched: List[int] = []

    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not URI_SCHEME_PATTERN.match(line):
            untouched.append(idx + 1)
            continue

        try:
            if line.startswith("vmess://"):
                vm_obj, fragment = parse_vmess_line(line)
                host = str(vm_obj.get("add", "")).strip()
                port_raw = str(vm_obj.get("port", "")).strip()
                if not port_raw.isdigit():
                    raise ObfuscationError("vmess port 非数字")
                port = int(port_raw)
                name_before = str(vm_obj.get("ps", "")).strip() or fragment

                node_id = build_node_id("vmess", host, port, name_before, idx)
                fake_host, fake_port = gen_fake_endpoint(node_id, fake_suffix, occupied)
                name_after = with_nid(name_before, node_id) if inject_nid else name_before

                vm_obj["add"] = fake_host
                vm_obj["port"] = str(fake_port)
                vm_obj["ps"] = name_after

                lines[idx] = build_vmess_line(vm_obj, fragment)
                records.append(
                    NodeRecord(
                        node_id=node_id,
                        real_host=host,
                        real_port=port,
                        fake_host=fake_host,
                        fake_port=fake_port,
                        name_before=name_before,
                        name_after=name_after,
                        source_type="vmess",
                    )
                )
            else:
                u, userinfo, host, port, name_before = parse_generic_uri(line)
                node_id = build_node_id(u.scheme.lower(), host, port, name_before, idx)
                fake_host, fake_port = gen_fake_endpoint(node_id, fake_suffix, occupied)
                name_after = with_nid(name_before, node_id) if inject_nid else name_before
                lines[idx] = build_generic_uri(u, userinfo, fake_host, fake_port, name_after)
                records.append(
                    NodeRecord(
                        node_id=node_id,
                        real_host=host,
                        real_port=port,
                        fake_host=fake_host,
                        fake_port=fake_port,
                        name_before=name_before,
                        name_after=name_after,
                        source_type=u.scheme.lower(),
                    )
                )
        except ObfuscationError:
            if strict:
                raise
            untouched.append(idx + 1)
        except Exception as exc:
            if strict:
                raise ObfuscationError(f"第 {idx+1} 行处理失败: {exc}") from exc
            untouched.append(idx + 1)

    return ProcessResult(content="\n".join(lines) + "\n", records=records, untouched_lines=untouched)


def decode_uri_list(
    text: str,
    mapping_by_id: Dict[str, Dict[str, Any]],
    mapping_by_fake: Dict[str, Dict[str, Any]],
    mapping_by_real: Dict[str, Dict[str, Any]],
    strict: bool,
) -> ProcessResult:
    lines = text.splitlines()
    untouched: List[int] = []
    used_ids: set[str] = set()

    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not URI_SCHEME_PATTERN.match(line):
            untouched.append(idx + 1)
            continue

        rec = None
        try:
            if line.startswith("vmess://"):
                vm_obj, fragment = parse_vmess_line(line)
                host = str(vm_obj.get("add", "")).strip()
                port_raw = str(vm_obj.get("port", "")).strip()
                if not port_raw.isdigit():
                    raise ObfuscationError("vmess port 非数字")
                port = int(port_raw)
                name = str(vm_obj.get("ps", "")).strip() or fragment
                node_id = extract_nid(name)
                if node_id and node_id in mapping_by_id:
                    rec = mapping_by_id[node_id]
                    used_ids.add(node_id)
                else:
                    rec = mapping_by_fake.get(f"{host}:{port}")
                    if rec:
                        used_ids.add(rec["node_id"])
                    else:
                        rec = mapping_by_real.get(f"{host}:{port}")
                        if rec:
                            used_ids.add(rec["node_id"])

                if rec is None:
                    if strict:
                        raise ObfuscationError(f"第 {idx+1} 行找不到映射")
                    untouched.append(idx + 1)
                    continue

                vm_obj["add"] = rec["real_host"]
                vm_obj["port"] = str(rec["real_port"])
                restored_name = preferred_name_from_record(rec, name)
                vm_obj["ps"] = restored_name
                lines[idx] = build_vmess_line(vm_obj, strip_nid(fragment))
            else:
                u, userinfo, host, port, name = parse_generic_uri(line)
                node_id = extract_nid(name)
                if node_id and node_id in mapping_by_id:
                    rec = mapping_by_id[node_id]
                    used_ids.add(node_id)
                else:
                    rec = mapping_by_fake.get(f"{host}:{port}")
                    if rec:
                        used_ids.add(rec["node_id"])
                    else:
                        rec = mapping_by_real.get(f"{host}:{port}")
                        if rec:
                            used_ids.add(rec["node_id"])

                if rec is None:
                    if strict:
                        raise ObfuscationError(f"第 {idx+1} 行找不到映射")
                    untouched.append(idx + 1)
                    continue

                restored_name = preferred_name_from_record(rec, name)
                lines[idx] = build_generic_uri(
                    u,
                    userinfo,
                    rec["real_host"],
                    int(rec["real_port"]),
                    restored_name,
                )
        except ObfuscationError:
            if strict:
                raise
            untouched.append(idx + 1)
        except Exception as exc:
            if strict:
                raise ObfuscationError(f"第 {idx+1} 行处理失败: {exc}") from exc
            untouched.append(idx + 1)

    missing = [k for k in mapping_by_id.keys() if k not in used_ids]
    if strict and missing:
        raise ObfuscationError(f"有 {len(missing)} 条映射未被使用，可能文件不匹配 profile")

    return ProcessResult(content="\n".join(lines) + "\n", records=[], untouched_lines=untouched)


def encode_action(args: argparse.Namespace) -> int:
    raw = fetch_input(
        args.input_url,
        args.input_file,
        input_text=getattr(args, "input_text", None),
        insecure=bool(getattr(args, "insecure", False)),
        ca_file=getattr(args, "ca_file", None),
    )
    parsed_type, parsed_data, wrap_type = parse_content(raw)

    if parsed_type == "clash_yaml":
        result = encode_clash_yaml(parsed_data, args.fake_suffix, args.strict, bool(getattr(args, "inject_nid", False)))
        output = result.content
    elif parsed_type == "uri_list":
        result = encode_uri_list(parsed_data, args.fake_suffix, args.strict, bool(getattr(args, "inject_nid", False)))
        output = result.content
        if wrap_type == "base64":
            output = b64_encode_no_pad(output.encode("utf-8")) + "\n"
    else:
        raise ObfuscationError(f"未实现格式: {parsed_type}")

    out_path = args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output, encoding="utf-8")

    mapping_root = args.mapping_dir
    store = MappingStore(mapping_root)
    meta = {
        "mode": "encode",
        "source": args.input_url or str(args.input_file),
        "parsed_type": parsed_type,
        "wrap_type": wrap_type,
        "generated_at": utc_now(),
        "fake_suffix": args.fake_suffix,
        "strict": args.strict,
        "inject_nid": bool(getattr(args, "inject_nid", False)),
    }
    mapping_path = store.save(args.profile, result.records, meta)

    print(f"[OK] 已输出伪装订阅: {out_path}")
    print(f"[OK] 已保存映射: {mapping_path}")
    print(f"[INFO] 共处理节点: {len(result.records)}")
    if result.untouched_lines:
        print(f"[WARN] 未处理行号: {result.untouched_lines}")
    return 0


def decode_action(args: argparse.Namespace) -> int:
    raw = fetch_input(None, args.input_file)
    parsed_type, parsed_data, wrap_type = parse_content(raw)

    store = MappingStore(args.mapping_dir)
    mapping = store.load(args.profile)
    records = mapping.get("records")
    if not isinstance(records, list) or not records:
        raise ObfuscationError("映射文件 records 为空")

    mapping_by_id: Dict[str, Dict[str, Any]] = {}
    mapping_by_fake: Dict[str, Dict[str, Any]] = {}
    mapping_by_real: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        node_id = str(rec.get("node_id", "")).lower().strip()
        fake_host = str(rec.get("fake_host", "")).strip()
        fake_port = int(rec.get("fake_port"))
        real_host = str(rec.get("real_host", "")).strip()
        real_port = int(rec.get("real_port"))
        if node_id:
            mapping_by_id[node_id] = rec
        mapping_by_fake[f"{fake_host}:{fake_port}"] = rec
        mapping_by_real[f"{real_host}:{real_port}"] = rec

    if parsed_type == "clash_yaml":
        result = decode_clash_yaml(parsed_data, mapping_by_id, mapping_by_fake, mapping_by_real, args.strict)
        output = result.content
    elif parsed_type == "uri_list":
        result = decode_uri_list(parsed_data, mapping_by_id, mapping_by_fake, mapping_by_real, args.strict)
        output = result.content
        if wrap_type == "base64":
            output = b64_encode_no_pad(output.encode("utf-8")) + "\n"
    else:
        raise ObfuscationError(f"未实现格式: {parsed_type}")

    out_path = args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output, encoding="utf-8")

    print(f"[OK] 已输出还原订阅: {out_path}")
    print(f"[INFO] 映射 profile: {args.profile}")
    if result.untouched_lines:
        print(f"[WARN] 未处理行号: {result.untouched_lines}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    default_mapping_dir = Path.home() / ".vpn_obfuscator"

    parser = argparse.ArgumentParser(
        prog="vpn_obfuscator",
        description="本地订阅伪装与还原工具（防错位版）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_encode = sub.add_parser("encode", help="将真实节点替换为假地址+假端口")
    p_encode.add_argument("--input-url", type=str, default=None, help="订阅 URL")
    p_encode.add_argument("--input-file", type=Path, default=None, help="本地订阅文件")
    p_encode.add_argument("--input-text", type=str, default=None, help="直接输入订阅/节点文本")
    p_encode.add_argument("--output", type=Path, required=True, help="输出伪装后的订阅文件")
    p_encode.add_argument("--profile", type=str, default="default", help="映射 profile 名")
    p_encode.add_argument(
        "--mapping-dir",
        type=Path,
        default=default_mapping_dir,
        help=f"映射目录（默认: {default_mapping_dir}）",
    )
    p_encode.add_argument("--fake-suffix", type=str, default="mask.invalid", help="假域名后缀")
    p_encode.add_argument("--strict", action=argparse.BooleanOptionalAction, default=True, help="严格模式")
    p_encode.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验（仅测试用）")
    p_encode.add_argument("--ca-file", type=Path, default=None, help="自定义 CA 证书文件路径（PEM）")
    p_encode.add_argument("--inject-nid", action="store_true", help="将 NID 注入节点名称（默认关闭，避免干扰转换分组）")
    p_encode.set_defaults(func=encode_action)

    p_decode = sub.add_parser("decode", help="将假地址+假端口还原为真实节点")
    p_decode.add_argument("--input-file", type=Path, required=True, help="转换后的订阅文件")
    p_decode.add_argument("--output", type=Path, required=True, help="输出还原后的订阅文件")
    p_decode.add_argument("--profile", type=str, default="default", help="映射 profile 名")
    p_decode.add_argument(
        "--mapping-dir",
        type=Path,
        default=default_mapping_dir,
        help=f"映射目录（默认: {default_mapping_dir}）",
    )
    p_decode.add_argument("--strict", action=argparse.BooleanOptionalAction, default=True, help="严格模式")
    p_decode.set_defaults(func=decode_action)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except ObfuscationError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("[ERROR] 用户中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
