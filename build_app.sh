#!/bin/bash
# build_app.sh — Build a self-contained Piclo Bot.app using py2app
# This bundles Python + all dependencies into a real macOS app.
#
# Usage: ./build_app.sh
# Output: dist/Piclo Bot.app (copy to /Applications when ready)

set -e

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BOT_DIR"

echo "🤖 Building Piclo Bot.app..."
echo "   Source: $BOT_DIR"
echo ""

# === Step 1: Check dependencies ===
echo "📦 Checking build dependencies..."

if ! command -v python3 &> /dev/null; then
    echo "❌ python3 not found. Install Python 3 first."
    exit 1
fi

# Install py2app if needed
python3 -c "import py2app" 2>/dev/null || {
    echo "   Installing py2app..."
    pip3 install py2app --break-system-packages 2>/dev/null || pip3 install py2app
}

# Install runtime dependencies if needed
echo "   Checking runtime dependencies..."
pip3 install rumps anthropic python-telegram-bot pywhispercpp --break-system-packages 2>/dev/null || \
pip3 install rumps anthropic python-telegram-bot pywhispercpp 2>/dev/null || \
echo "   ⚠️ Some deps may need manual install"

# === Step 2: Generate .icns icon ===
ICON_PNG="${BOT_DIR}/robot_icon_preview.png"
ICON_ICNS="${BOT_DIR}/icon.icns"

if [ -f "$ICON_PNG" ] && [ ! -f "$ICON_ICNS" ]; then
    echo "🎨 Generating app icon..."
    ICONSET_DIR=$(mktemp -d)/icon.iconset
    mkdir -p "$ICONSET_DIR"
    for size in 16 32 64 128 256 512; do
        sips -z $size $size "$ICON_PNG" --out "${ICONSET_DIR}/icon_${size}x${size}.png" > /dev/null 2>&1
        double=$((size * 2))
        if [ $double -le 1024 ]; then
            sips -z $double $double "$ICON_PNG" --out "${ICONSET_DIR}/icon_${size}x${size}@2x.png" > /dev/null 2>&1
        fi
    done
    iconutil -c icns "$ICONSET_DIR" -o "$ICON_ICNS" 2>/dev/null || \
        echo "   ⚠️ Icon conversion failed — app will use default icon"
    rm -rf "$(dirname "$ICONSET_DIR")"
elif [ -f "$ICON_ICNS" ]; then
    echo "🎨 Using existing icon.icns"
else
    echo "🎨 No icon PNG found — app will use default icon"
    # Create empty icns reference so setup.py doesn't fail
    touch "$ICON_ICNS"
fi

# === Step 3: Clean previous build ===
echo "🧹 Cleaning previous build..."
rm -rf build dist

# === Step 4: Build with py2app ===
echo "🔨 Building with py2app (this may take a minute)..."
python3 setup.py py2app 2>&1 | tail -5

# === Step 5: Code sign ===
if [ -d "dist/Piclo Bot.app" ]; then
    echo "🔏 Code signing..."
    codesign --force --deep --sign - "dist/Piclo Bot.app" 2>/dev/null && \
        echo "   ✅ Signed (ad-hoc)" || \
        echo "   ⚠️ Signing failed — you may need to allow in System Settings"

    echo ""
    echo "✅ Build complete!"
    echo ""
    echo "   App location: dist/Piclo Bot.app"
    echo ""
    echo "   To install, run:"
    echo "   cp -R \"dist/Piclo Bot.app\" /Applications/"
    echo ""
    echo "   Then launch from Launchpad, Spotlight, or Finder → Applications."
    echo ""
    echo "   First launch will ask you to:"
    echo "   1. Choose your projects folder"
    echo "   2. Set your API keys (if not already in Keychain)"
    echo ""
else
    echo "❌ Build failed. Check the output above for errors."
    echo "   Common fixes:"
    echo "   - pip3 install py2app rumps anthropic python-telegram-bot"
    echo "   - Make sure you're using Python 3.10+"
    exit 1
fi
