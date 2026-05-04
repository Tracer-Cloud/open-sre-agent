#!/bin/bash
# Check if running in Git Bash / MINGW
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
    echo "⚠️ Windows environment detected. Tracer requires Ubuntu/WSL."
    echo "🚀 Attempting automated migration to WSL..."

    # 1. Check if WSL is installed
    if ! command -v wsl.exe &> /dev/null; then
        echo "❌ WSL not found. Please run: wsl --install"
        exit 1
    fi

    # 2. Define WSL path (Standard Ubuntu distro)
    WSL_USER=$(wsl.exe whoami)
    TARGET_DIR="/home/$WSL_USER/opensre"

    echo "📂 Migrating repo to: \\\\wsl.localhost\\Ubuntu$TARGET_DIR"
    
    # 3. Use WSL to clone or move the current folder
    wsl.exe bash -c "mkdir -p $TARGET_DIR && cp -r . $TARGET_DIR"
    
    echo "✅ Migration complete."
    echo "👉 Please run the following command to finish setup in Ubuntu:"
    echo "   wsl -d Ubuntu -e bash -c 'cd $TARGET_DIR && ./setup.sh'"
    exit 0
fi
