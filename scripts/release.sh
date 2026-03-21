#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

VERSION="${1:-$(cat VERSION | tr -d '[:space:]')}"
if [[ -z "${VERSION}" ]]; then
  echo "版本号为空，请提供参数或填写 VERSION 文件。"
  exit 1
fi
if [[ ! "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "版本号格式无效：${VERSION}（应为 x.y.z）"
  exit 1
fi

ICON_PATH="${ROOT_DIR}/assets/AegisMesh.icns"
if [[ ! -f "${ICON_PATH}" ]]; then
  echo "图标文件不存在：${ICON_PATH}"
  exit 1
fi

RELEASES_DIR="${ROOT_DIR}/releases"
RELEASE_NAME="AegisMesh-v${VERSION}"
VERSION_DIR="${RELEASES_DIR}/${RELEASE_NAME}"
APP_OUT_DIR="${VERSION_DIR}/${RELEASE_NAME}-app"
DMG_OUT_DIR="${VERSION_DIR}/${RELEASE_NAME}-dmg"
SRC_OUT_DIR="${VERSION_DIR}/source"
APP_NAME="AegisMesh"

mkdir -p "${RELEASES_DIR}" "${VERSION_DIR}"
rm -rf "${APP_OUT_DIR}" "${DMG_OUT_DIR}" "${SRC_OUT_DIR}"
mkdir -p "${APP_OUT_DIR}" "${DMG_OUT_DIR}" "${SRC_OUT_DIR}"

# 归档旧构建产物（不覆盖）
if [[ -d "${ROOT_DIR}/dist/AegisMesh.app" || -d "${ROOT_DIR}/dist/AegisMesh" ]]; then
  LEGACY_DIR="${RELEASES_DIR}/legacy/$(date +%Y%m%d_%H%M%S)"
  mkdir -p "${LEGACY_DIR}"
  [[ -d "${ROOT_DIR}/dist/AegisMesh.app" ]] && cp -R "${ROOT_DIR}/dist/AegisMesh.app" "${LEGACY_DIR}/AegisMesh.app"
  [[ -d "${ROOT_DIR}/dist/AegisMesh" ]] && cp -R "${ROOT_DIR}/dist/AegisMesh" "${LEGACY_DIR}/AegisMesh"
fi

rm -rf "${ROOT_DIR}/build" "${ROOT_DIR}/dist"
python3 -m PyInstaller AegisMesh.spec --noconfirm

if [[ ! -d "${ROOT_DIR}/dist/AegisMesh.app" ]]; then
  echo "打包失败：未找到 dist/AegisMesh.app"
  exit 1
fi

APP_BUNDLE_NAME="${APP_NAME}.app"
rm -rf "${APP_OUT_DIR}/${APP_BUNDLE_NAME}"
cp -R "${ROOT_DIR}/dist/AegisMesh.app" "${APP_OUT_DIR}/${APP_BUNDLE_NAME}"

DMG_PATH="${DMG_OUT_DIR}/${RELEASE_NAME}.dmg"
rm -f "${DMG_PATH}"
DMG_STAGE="$(mktemp -d "/tmp/aegismesh_dmg_${VERSION}_XXXX")"
cp -R "${APP_OUT_DIR}/${APP_BUNDLE_NAME}" "${DMG_STAGE}/${APP_BUNDLE_NAME}"
ln -s /Applications "${DMG_STAGE}/Applications"
hdiutil create -volname "${RELEASE_NAME}" -srcfolder "${DMG_STAGE}" -ov -format UDZO "${DMG_PATH}" >/dev/null
rm -rf "${DMG_STAGE}"

SOURCE_ARCHIVE="${SRC_OUT_DIR}/AegisMesh-v${VERSION}-source.tar.gz"
rm -f "${SOURCE_ARCHIVE}"
tar -czf "${SOURCE_ARCHIVE}" \
  --exclude='./build' \
  --exclude='./dist' \
  --exclude='./releases' \
  --exclude='./__pycache__' \
  --exclude='./encoded*.txt' \
  --exclude='./encoded*.yaml' \
  --exclude='./restored*.txt' \
  --exclude='./restored*.yaml' \
  --exclude='./pipe_*' \
  --exclude='./converted_sim.yaml' \
  --exclude='./*.mapping.json' \
  --exclude='./*.log' \
  --exclude='./*.pyc' \
  --exclude='./.DS_Store' \
  .

MANIFEST="${VERSION_DIR}/manifest.txt"
{
  echo "name=AegisMesh"
  echo "version=${VERSION}"
  echo "built_at=$(date '+%Y-%m-%d %H:%M:%S %z')"
  echo "app=${APP_OUT_DIR}/${APP_BUNDLE_NAME}"
  echo "dmg=${DMG_PATH}"
  echo "source=${SOURCE_ARCHIVE}"
} > "${MANIFEST}"

echo "发布完成："
echo "  ${APP_OUT_DIR}/${APP_BUNDLE_NAME}"
echo "  ${DMG_PATH}"
echo "  ${SOURCE_ARCHIVE}"
echo "  ${MANIFEST}"
