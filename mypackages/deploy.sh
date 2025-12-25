#!/bin/bash

# deploy.sh - 简单启动脚本，用于启动 pypi-server
# 要求：在【mypackages 目录】已经创建并安装好虚拟环境：
#   cd mypackages
#   python3 -m venv venv
#   在 venv 里执行: pip install pypiserver

set -e

# 项目目录 = 当前脚本所在目录 (mypackages)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 使用 mypackages 目录下的 venv
if [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    VENV_ACTIVATE="$SCRIPT_DIR/venv/bin/activate"
else
    echo "错误: 未找到虚拟环境 venv（期望路径: $SCRIPT_DIR/venv）"
    exit 1
fi

echo "正在激活虚拟环境: $VENV_ACTIVATE"
source "$VENV_ACTIVATE"

# 直接使用当前虚拟环境中的 pypi-server 命令
PYPI_SERVER="pypi-server"

# 包目录：mypackages/packages
PACKAGES_DIR="$SCRIPT_DIR/packages"

echo "正在启动 pypi-server..."
echo "端口: 8087"
echo "包目录: $PACKAGES_DIR"
echo "访问地址: http://localhost:8087"
echo ""
echo "按 Ctrl+C 停止服务器"
echo ""

exec "$PYPI_SERVER" -p 8087 "$PACKAGES_DIR"

