#!/bin/bash

# deploy_all.sh
# 一键在 10.3 上为 mypackages 和四个 server 准备 venv + 安装依赖，
# 然后通过 scp 分发到各自对应的服务器。
#
# 运行环境：建议在 10.3 上、代码仓库根目录（localpythonmvp）执行：
#   chmod +x ./deploy_all.sh
#   ./deploy_all.sh

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

#############################################
# 可按需修改的配置
#############################################

# SSH 账号
SSH_USER="user"
# SSH 密码在脚本中不会使用，只假定你已经配置好免密或使用 ssh-agent

# 各服务器 IP
SERVER101_HOST="192.168.140.201"
SERVER102_HOST="192.168.140.202"
SERVER103_HOST="192.168.140.203"
SERVER104_HOST="192.168.140.204"

# 远端部署根目录
REMOTE_BASE="/home/user/common"

# pip 源（默认使用 10.2 上的 pypiserver）
PIP_INDEX_URL="${PIP_INDEX_URL:-http://192.168.140.202:8087/simple/}"
PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-192.168.140.202}"

#############################################
# 工具函数
#############################################

info() {
  echo -e "\033[1;34m[INFO]\033[0m $*"
}

warn() {
  echo -e "\033[1;33m[WARN]\033[0m $*"
}

error() {
  echo -e "\033[1;31m[ERROR]\033[0m $*"
}

#############################################
# 1. 在 10.3 上准备 venv + 依赖
#############################################

info "项目根目录: ${ROOT_DIR}"

# 1.1 在 mypackages 目录下准备 venv 并安装 pypiserver
info "==> 在 mypackages 下创建并安装 pypiserver 所需 venv..."
MYPKG_DIR="${ROOT_DIR}/mypackages"
if [[ -d "${MYPKG_DIR}" ]]; then
  cd "${MYPKG_DIR}"
  if [[ ! -d "venv" ]]; then
    info "创建 mypackages venv: ${MYPKG_DIR}/venv"
    python3 -m venv venv
  else
    info "mypackages/venv 已存在，复用"
  fi

  # 使用 mypackages 自己的 venv 安装 pypiserver
  # shellcheck disable=SC1091
  source "venv/bin/activate"
  info "在 mypackages/venv 中安装 pypiserver..."
  pip install --upgrade pip
  pip install pypiserver
  deactivate || true
  cd "${ROOT_DIR}"
else
  warn "未找到 mypackages 目录，跳过 mypackages venv 安装"
fi

# 1.3 准备基线虚拟环境 basevenv（供 10.2 上按用户快速拷贝使用）
info "==> 准备基线虚拟环境 basevenv（稍后将同步到 10.2 的 /home/user/common/basevenv/venv）..."
BASEVENV_LOCAL_DIR="${ROOT_DIR}/basevenv"
mkdir -p "${BASEVENV_LOCAL_DIR}"
if [[ ! -d "${BASEVENV_LOCAL_DIR}/venv" ]]; then
  info "创建 basevenv: ${BASEVENV_LOCAL_DIR}/venv"
  python3 -m venv "${BASEVENV_LOCAL_DIR}/venv"
else
  info "已存在 basevenv，复用: ${BASEVENV_LOCAL_DIR}/venv"
fi

#############################################
# 2. 用 scp 分发到各服务器
#############################################

info "==> 开始通过 scp 分发目录到各服务器（请确认已配置好 SSH 免密）..."

cd "${ROOT_DIR}"

# server103（通常就是当前机器，如果你先在别的路径准备，再同步到正式目录可以用这一步）
info "如需将 server103 从当前机器同步到 10.3 的正式目录，可执行："
info "  scp -r server103 ${SSH_USER}@${SERVER103_HOST}:${REMOTE_BASE}/"

# mypackages 仅需部署到 10.2（作为 pypiserver）
info "分发 mypackages -> ${SSH_USER}@${SERVER102_HOST}:${REMOTE_BASE}/mypackages"
ssh "${SSH_USER}@${SERVER102_HOST}" "mkdir -p '${REMOTE_BASE}'"
scp -r mypackages "${SSH_USER}@${SERVER102_HOST}:${REMOTE_BASE}/"

# basevenv 仅需部署到 10.2（供 /home/user/common/basevenv/venv 使用）
info "分发 basevenv -> ${SSH_USER}@${SERVER102_HOST}:${REMOTE_BASE}/basevenv"
ssh "${SSH_USER}@${SERVER102_HOST}" "mkdir -p '${REMOTE_BASE}'"
scp -r basevenv "${SSH_USER}@${SERVER102_HOST}:${REMOTE_BASE}/"

info "全部处理完成。"
info "请分别登录 10.2 / 10.3，在各自的目录下使用 start.sh 启动对应服务。"


