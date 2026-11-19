#!/bin/bash
set -euo pipefail
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SOURCE_DIR/../.." && pwd)"
BUILD_DIR="$SOURCE_DIR/build"
ASSET_DIR="$PROJECT_DIR/app/assets/icons"
APP_NAME="$(/usr/bin/printf '\345\255\246\344\271\240\345\217\260')"
APP_DIR="$PROJECT_DIR/$APP_NAME.app"
mkdir -p "$BUILD_DIR/ModuleCache" "$ASSET_DIR"
xcrun swiftc --version
# Current icon (2026-10-14, warm white + orange): the supplied iconset already has every
# size, with bolder artwork at 16 and 32 px. iconutil packs it as is; nothing is rescaled.
# The earlier lamp icon files stay in app/assets/icons/ as an archive and are not used.
ICONSET_DIR="$ASSET_DIR/orange/AppIcon.iconset"
/usr/bin/iconutil -c icns "$ICONSET_DIR" -o "$BUILD_DIR/LearningHub.icns"
xcrun swiftc -swift-version 5 -O -target "$(uname -m)-apple-macosx13.0" \
    -module-cache-path "$BUILD_DIR/ModuleCache" -framework AppKit -framework WebKit \
    "$SOURCE_DIR/main.swift" "$SOURCE_DIR/AppDelegate.swift" "$SOURCE_DIR/WindowController.swift" \
    "$SOURCE_DIR/ServiceController.swift" "$SOURCE_DIR/Bridge.swift" "$SOURCE_DIR/Menus.swift" \
    -o "$BUILD_DIR/LearningHub"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
cp "$BUILD_DIR/LearningHub" "$APP_DIR/Contents/MacOS/LearningHub"
cp "$SOURCE_DIR/Info.plist" "$APP_DIR/Contents/Info.plist"
cp "$BUILD_DIR/LearningHub.icns" "$APP_DIR/Contents/Resources/LearningHub.icns"
chmod +x "$APP_DIR/Contents/MacOS/LearningHub"
/usr/bin/codesign --force --deep --sign - "$APP_DIR"
/usr/bin/codesign --verify --deep --strict "$APP_DIR"
# Notify file-based icon caches that this existing bundle was rebuilt.
/usr/bin/touch "$APP_DIR"
/usr/bin/printf 'Built: %s\n' "$APP_DIR"
