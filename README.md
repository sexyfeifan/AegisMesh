# 🛡️ AegisMesh（订阅伪装/还原）

> 借外站转换能力，不暴露真实节点 endpoint。  
> 适用于网站转换与 OpenClash 在线转换两种场景。

这个工具用于你描述的本地流程：

1. 输入真实订阅（URL 或文件）。
2. 将节点里的真实 `server/ip + port` 替换成假地址+假端口。
3. 你把伪装后的订阅送到订阅转换网站处理。
4. 拿到转换后的文件，再用本工具还原成真实地址端口。

## ✨ 特点

- 本地运行，不依赖云端。
- 防错位：每个节点注入 `NID` 标识，解密时优先按 `NID` 匹配。
- 兜底匹配：如果转换器改了名字，会回退按假 `host:port` 匹配。
- 严格模式默认开启，避免漏替换/错还原。

## 📦 支持格式

- URI 列表（明文）
- Base64 包装的 URI 列表（常见机场订阅格式）
- Clash YAML（需要 PyYAML）

## 🚀 安装

```bash
python3 -m pip install -r requirements.txt
```

## 🖥️ 图形界面（推荐）

启动 GUI：

```bash
python3 vpn_obfuscator_gui.py
```

当前版本：`v1.0.7`

## 🧭 文档导航

- 使用教程：`USER_GUIDE.md`
- 项目说明（小红书风格）：`XHS_PROJECT_NOTE.md`
- 软件内置入口：顶部 `使用说明` 按钮（可直接查看教程与项目说明）

界面支持两套可切换流程（互不干扰）：

- `网站转换流程`：原有文本粘贴式流程。
- `OpenClash链接流程`：URL -> 伪装上传 OpenList -> 拿假链接去 OpenClash -> 粘贴返回内容 -> 还原。

### ① 网站转换流程（原流程）

1. 大输入框直接粘贴内容（支持 Base64、URI 节点列表、YAML）。
2. 点击 `提取并展示节点` 查看输入中的节点。
3. 点击 `执行伪装`，自动展示伪装前后节点对比。
4. 点击 `复制伪装后链接到剪贴板`，直接粘贴到转换网站。
5. 把转换网站返回内容全量粘贴到“转换后内容”框，或直接粘贴转换结果 URL 抓取。
6. 点击 `执行还原`，在第 `④ 还原结果` 页查看结果文档。
7. 点击 `保存还原文档` 一键保存（可选自动上传 OpenList 并复制下载链接）。

### ② OpenClash 链接流程（新增）

- OpenClash 模式会优先使用 `clash.meta` / `clash` User-Agent 抓取订阅。
- 必须拿到完整 Clash YAML 配置后才会继续伪装与上传。
- 上传到 OpenList 的伪装文件固定为完整 `.yaml`。

1. 切换到 `OpenClash链接流程`，粘贴订阅 URL。
2. 点击 `抓取并伪装上传`，程序会抓取、解析节点、替换假地址，并上传到 OpenList。
3. 复制伪装订阅链接，交给 OpenClash 在线转换。
4. 将 OpenClash 返回内容粘贴回步骤 `③`，点击 `执行还原`。
5. 在步骤 `④` 查看并复制/保存还原文档。

### 🎁 额外能力

1. 默认跳过 HTTPS 证书校验（无需手动设置）。
2. 自动记录每次完整流程到 `~/.vpn_obfuscator/history/`。
3. 自动执行一次性验证，并在“执行验证窗口”展示结果。
4. 保存还原文档时自动输出 YAML，文件名为 `YYYYMMDDHHMMSSmmm.yaml`（毫秒级，无中文和特殊符号）。
5. 保存前自动清理 `NID` 干扰标记，尽量保持代理名/分组引用可直接使用。
6. 界面按流程分步展示（输入/伪装/还原/结果），同一时间只展开一个步骤，减轻拥挤感。
7. 支持导出验证详情（用于排查差异）。
8. 支持 OpenList 单独设置，含联通测试与延迟显示；保存后可自动上传到指定目录并复制下载地址。
9. OpenList 登录 token 自动缓存，减少重复上传时登录耗时。
10. OpenList 上传支持进度窗口、超时与失败重试。
11. 上传成功可弹出二维码供手机扫码，同时保留并可复制下载链接。
12. 保存后可按“保留天数”自动清理旧 YAML 文件（0 表示关闭）。
13. OpenList 上传改为队列串行处理，支持“一键重试失败上传”。
14. 顶部新增 `使用说明`，可在软件内直接查看教程与项目说明（内置文档）。

## 🛠️ 用法

### 1) 🔐 伪装（encode）

```bash
python3 vpn_obfuscator.py encode \
  --input-url "你的真实订阅URL" \
  --output encoded.txt \
  --profile mysub
```

或从本地文件输入：

```bash
python3 vpn_obfuscator.py encode \
  --input-file ./sub.txt \
  --output encoded.txt \
  --profile mysub
```

或直接粘贴节点文本（多行或 `|` 分隔）：

```bash
python3 vpn_obfuscator.py encode \
  --input-text "trojan://...#A|ss://...#B" \
  --output encoded.txt \
  --profile mysub
```

可选参数：

- `--fake-suffix mask.invalid`：假域名后缀。
- `--no-strict`：关闭严格模式（不建议）。
- `--mapping-dir ~/.vpn_obfuscator`：映射目录。
- `--ca-file /path/to/ca.pem`：指定自定义 CA 证书（PEM）。
- `--insecure`：跳过 HTTPS 证书校验（仅测试，不建议长期使用）。

执行后会生成：

- 伪装后的订阅文件（你指定的 `--output`）
- 映射文件：`~/.vpn_obfuscator/<profile>.mapping.json`

### 2) 🔓 还原（decode）

```bash
python3 vpn_obfuscator.py decode \
  --input-file ./converted_from_website.txt \
  --output restored.txt \
  --profile mysub
```

`--profile` 必须和 encode 时一致。

## 📋 推荐实际流程

1. `encode` 真实订阅，得到 `encoded.txt`。
2. 把 `encoded.txt` 提交给订阅转换网站（你提到的站点）。
3. 下载转换结果（例如 Clash YAML）。
4. `decode` 转换结果，得到 `restored.txt`。
5. 将 `restored.txt` 导入你的客户端。

## ⚠️ 注意事项

- 这个工具不会把映射发到网络；但映射文件本地含真实地址，请保管好。
- 若你重新执行同一 `profile` 的 encode，会覆盖旧映射；旧转换文件将无法用新映射还原。
- 如果转换器极端改写节点导致 `NID` 与假地址都丢失，工具会在严格模式下报错阻止错误还原。

## 🧯 常见报错：证书校验失败

如果你看到 `CERTIFICATE_VERIFY_FAILED`：

1. 优先方案：安装系统/企业根证书，或用 `--ca-file` 指定 CA 证书。
2. GUI 里可填写 `CA 证书文件`。
3. 仅测试时可勾选 GUI 的 `跳过HTTPS证书校验（仅测试）`，或命令行加 `--insecure`。

## ✅ 快速自测

```bash
python3 vpn_obfuscator.py encode --input-file sample_uri.txt --output encoded.txt --profile t1
python3 vpn_obfuscator.py decode --input-file encoded.txt --output restored.txt --profile t1
```

## 📦 版本发布（保留历史版本）

1. 修改根目录 `VERSION`（格式 `x.y.z`）。
2. 执行：

```bash
./scripts/release.sh
```

发布脚本会自动：

- 归档旧构建到 `releases/legacy/<timestamp>/`（保留老版本）
- 生成新版本 APP：`releases/AegisMesh-v<版本>/AegisMesh-v<版本>-app/AegisMesh.app`
- 生成新版本 DMG：`releases/AegisMesh-v<版本>/AegisMesh-v<版本>-dmg/AegisMesh-v<版本>.dmg`
- 生成对应源码包：`releases/AegisMesh-v<版本>/source/AegisMesh-v<版本>-source.tar.gz`
