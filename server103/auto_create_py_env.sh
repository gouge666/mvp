#!/bin/bash
set -e  # 出现任何错误立即退出

# ===================== 脚本配置 =====================
# 您的SOCKS5代理设置（请根据实际情况修改）
SOCKS5_PROXY="127.0.0.1"
SOCKS5_PORT="1080"
PROXY_URL="socks5h://${SOCKS5_PROXY}:${SOCKS5_PORT}"

# 基础路径配置
PYTHON_BASE_DIR="/home/user/common/pythons"  # Python安装基础目录

# ===================== 参数校验 =====================
if [ $# -ne 3 ]; then
    echo "【错误】参数数量不正确！"
    echo "【正确用法】$0 <Python版本> <虚拟环境名称> <虚拟环境存放路径>"
    echo "【示例】    $0 3.10.10 myproject /home/user/gousker2/envs"
    exit 1
fi

PY_VERSION="$1"
ENV_NAME="$2"
ENV_BASE_DIR="$3"
ENV_FULL_PATH="${ENV_BASE_DIR}/${ENV_NAME}"

# 设置代理环境变量
export http_proxy="${PROXY_URL}"
export https_proxy="${PROXY_URL}"
export ALL_PROXY="${PROXY_URL}"

# ===================== 函数定义 =====================

# 颜色输出函数
print_color() {
    local color=$1
    local message=$2
    case $color in
        "red") echo -e "\033[31m[错误] $message\033[0m" ;;
        "green") echo -e "\033[32m[成功] $message\033[0m" ;;
        "yellow") echo -e "\033[33m[警告] $message\033[0m" ;;
        "blue") echo -e "\033[34m[信息] $message\033[0m" ;;
        *) echo "[信息] $message" ;;
    esac
}

# 检查命令是否存在
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# 测试网络连接
test_network() {
    print_color "blue" "测试网络连接..."
    if curl -s --proxy "${PROXY_URL}" -I https://www.python.org > /dev/null 2>&1; then
        print_color "green" "网络连接正常"
        return 0
    else
        print_color "red" "网络连接失败，请检查代理设置"
        return 1
    fi
}

# 检查Python是否已安装
check_python_installed() {
    local version=$1
    local install_path="${PYTHON_BASE_DIR}/python${version}/bin/python3"
    
    print_color "blue" "检查Python ${version} 是否已安装..."
    
    if [[ -f "$install_path" ]]; then
        local installed_version
        if installed_version=$("$install_path" --version 2>/dev/null | awk '{print $2}'); then
            if [[ "$installed_version" == "$version" ]]; then
                print_color "green" "Python ${version} 已安装: ${install_path}"
                echo "$install_path"
                return 0
            else
                print_color "yellow" "找到Python但版本不匹配: 期望=${version}, 实际=${installed_version}"
                return 1
            fi
        fi
    fi
    print_color "yellow" "Python ${version} 未安装"
    return 1
}

# 安装系统依赖
install_dependencies() {
    print_color "blue" "安装系统编译依赖..."
    
    # 临时取消代理进行apt操作
    if command -v apt > /dev/null; then
        https_proxy='' https_proxy='' sudo -E apt update || print_color "yellow" "apt update 有警告，继续..."
        https_proxy='' https_proxy='' sudo -E apt install -y \
            build-essential zlib1g-dev libssl-dev libffi-dev \
            libncurses5-dev libreadline-dev libsqlite3-dev \
            libgdbm-dev libbz2-dev libexpat1-dev liblzma-dev tk-dev wget curl || {
            print_color "red" "安装系统依赖失败"
            exit 1
        }
    elif command -v yum > /dev/null; then
        https_proxy='' https_proxy='' sudo -E yum groupinstall -y "Development Tools"
        https_proxy='' https_proxy='' sudo -E yum install -y zlib-devel openssl-devel libffi-devel
    else
        print_color "red" "不支持的系统"
        exit 1
    fi
    print_color "green" "系统依赖安装完成"
}

# 下载Python源码
download_python_source() {
    local version=$1
    local download_url="https://www.python.org/ftp/python/${version}/Python-${version}.tgz"
    local target_file="/tmp/Python-${version}.tgz"
    
    print_color "blue" "下载Python ${version} 源码..."
    
    # 检查是否已存在源码包
    if [[ -f "$target_file" ]]; then
        print_color "yellow" "使用现有源码包: $target_file"
        echo "$target_file"
        return 0
    fi
    
    # 使用curl下载，明确指定代理
    if command_exists curl; then
        if curl --proxy "${PROXY_URL}" -L -o "$target_file" "$download_url"; then
            # 验证文件完整性
            if [[ -s "$target_file" ]]; then
                print_color "green" "下载成功: $target_file ($(du -h "$target_file" | cut -f1))"
                echo "$target_file"
                return 0
            else
                print_color "red" "下载的文件可能已损坏"
                return 1
            fi
        else
            print_color "red" "下载失败: $download_url"
            return 1
        fi
    else
        print_color "red" "curl 未安装，无法下载"
        return 1
    fi
}

# 编译安装Python
compile_install_python() {
    local version=$1
    local source_file=$2
    local install_dir="${PYTHON_BASE_DIR}/python${version}"
    local build_dir="/tmp/Python-${version}"
    
    print_color "blue" "编译安装Python ${version}..."
    
    # 清理旧的构建目录
    rm -rf "$build_dir"
    
    # 解压源码
    if ! tar -xzf "$source_file" -C /tmp/; then
        print_color "red" "解压源码包失败"
        return 1
    fi
    
    # 验证解压结果
    if [[ ! -d "$build_dir" ]]; then
        print_color "red" "解压后目录不存在: $build_dir"
        return 1
    fi
    
    # 进入构建目录
    cd "$build_dir" || {
        print_color "red" "无法进入构建目录: $build_dir"
        return 1
    }
    
    # 配置和编译
    print_color "blue" "配置编译参数..."
    ./configure --prefix="$install_dir" --enable-optimizations --with-ensurepip=install || {
        print_color "red" "配置失败"
        return 1
    }
    
    print_color "blue" "编译Python (这可能需要几分钟)..."
    make -j$(nproc) || {
        print_color "red" "编译失败"
        return 1
    }
    
    # 安装
    print_color "blue" "安装到: $install_dir"
    make install || {
        print_color "red" "安装失败"
        return 1
    }
    
    # 创建软链接
    local major_minor=$(echo "$version" | cut -d. -f1-2)
    sudo ln -sf "${install_dir}/bin/python${major_minor}" "/usr/local/bin/python${version}" 2>/dev/null || true
    
    print_color "green" "Python ${version} 安装完成"
    echo "${install_dir}/bin/python${major_minor}"
}

# 创建虚拟环境
create_virtualenv() {
    local python_interpreter=$1
    local venv_path=$2
    
    print_color "blue" "创建虚拟环境: $venv_path"
    
    # 确保基础目录存在
    mkdir -p "$(dirname "$venv_path")"
    
    # 检查virtualenv
    if ! command_exists virtualenv; then
        print_color "blue" "安装virtualenv..."
        if ! "$python_interpreter" -m pip install virtualenv; then
            print_color "red" "安装virtualenv失败"
            return 1
        fi
    fi
    
    # 创建虚拟环境
    if virtualenv -p "$python_interpreter" "$venv_path"; then
        # 创建pip配置文件
        mkdir -p "$venv_path"
        cat > "${venv_path}/pip.conf" << EOF
[global]
index-url = https://pypi.tuna.tsinghua.edu.cn/simple
trusted-host = pypi.tuna.tsinghua.edu.cn
timeout = 600
EOF
        print_color "green" "虚拟环境创建成功"
        return 0
    else
        print_color "red" "虚拟环境创建失败"
        return 1
    fi
}

# ===================== 主逻辑 =====================

main() {
    echo "=================================================="
    echo "开始自动化部署Python环境"
    echo "=================================================="
    echo "Python版本: $PY_VERSION"
    echo "虚拟环境名称: $ENV_NAME"
    echo "虚拟环境路径: $ENV_FULL_PATH"
    echo "Python安装路径: $PYTHON_BASE_DIR"
    echo "代理设置: $PROXY_URL"
    echo "=================================================="
    
    # 步骤1: 测试网络
    test_network
    
    # 步骤2: 检查Python是否已安装
    local python_interpreter
    if python_interpreter=$(check_python_installed "$PY_VERSION"); then
        print_color "green" "Python $PY_VERSION 已存在，跳过安装步骤"
    else
        print_color "blue" "开始安装Python $PY_VERSION..."
        
        # 安装系统依赖
        install_dependencies
        
        # 下载源码
        local source_file
        if source_file=$(download_python_source "$PY_VERSION"); then
            # 编译安装
            if python_interpreter=$(compile_install_python "$PY_VERSION" "$source_file"); then
                print_color "green" "Python安装成功: $python_interpreter"
            else
                print_color "red" "Python安装失败"
                exit 1
            fi
        else
            print_color "red" "源码下载失败"
            exit 1
        fi
    fi
    
    # 步骤3: 创建虚拟环境
    if create_virtualenv "$python_interpreter" "$ENV_FULL_PATH"; then
        print_color "green" "虚拟环境创建成功"
    else
        print_color "red" "虚拟环境创建失败"
        exit 1
    fi
    
    # 步骤4: 输出使用说明
    echo -e "\n=================================================="
    print_color "green" "✅ Python环境部署完成！"
    echo "=================================================="
    echo "Python解释器: $python_interpreter"
    echo "虚拟环境路径: $ENV_FULL_PATH"
    echo -e "\n【使用说明】"
    echo "1. 激活虚拟环境:"
    echo "   source ${ENV_FULL_PATH}/bin/activate"
    echo ""
    echo "2. 在虚拟环境中安装包:"
    echo "   pip install 包名"
    echo ""
    echo "3. 退出虚拟环境:"
    echo "   deactivate"
    echo ""
    echo "4. 删除此虚拟环境:"
    echo "   rm -rf ${ENV_FULL_PATH}"
    echo "=================================================="
}

# 执行主函数
main "$@"