#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Server103 Flask Application
提供文件夹传输服务
"""

from flask import Flask, request, jsonify, render_template, Response, stream_with_context
import os
import subprocess
import shutil
import logging
from pathlib import Path
import paramiko
import io
import json
import time
import threading
from datetime import datetime
import stat
import stat

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # 支持中文JSON响应

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 服务器配置
SERVER_CONFIG = {
    'server101': {'host': '192.168.10.1', 'port': 22, 'user': 'user', 'password': '1234567'},
    'server102': {'host': '192.168.10.2', 'port': 22, 'user': 'user', 'password': '1234567'},
    'server103': {'host': '192.168.10.3', 'port': 22, 'user': 'user', 'password': '1234567'},
    'server104': {'host': '192.168.10.4', 'port': 22, 'user': 'user', 'password': '1234567'},
}

# 当前服务器标识
CURRENT_SERVER = 'server103'

# Sudo密码
SUDO_PASSWORD = '1234567'


def execute_ssh_command(ssh_client, command, use_sudo=False):
    """
    执行SSH命令，支持sudo
    
    Args:
        ssh_client: paramiko SSH客户端
        command: 要执行的命令
        use_sudo: 是否使用sudo
    
    Returns:
        tuple: (success: bool, stdout: str, stderr: str)
    """
    try:
        if use_sudo:
            # 使用sudo执行命令
            full_command = f'echo "{SUDO_PASSWORD}" | sudo -S {command}'
        else:
            full_command = command
        
        stdin, stdout, stderr = ssh_client.exec_command(full_command, timeout=300)
        exit_status = stdout.channel.recv_exit_status()
        
        stdout_text = stdout.read().decode('utf-8', errors='ignore')
        stderr_text = stderr.read().decode('utf-8', errors='ignore')
        
        if exit_status == 0:
            return True, stdout_text, stderr_text
        else:
            return False, stdout_text, stderr_text
    
    except Exception as e:
        logger.error(f"执行SSH命令失败: {str(e)}", exc_info=True)
        return False, "", str(e)


def ensure_remote_directory(ssh_client, remote_path, use_sudo=False):
    """
    确保远程目录存在，如果不存在则创建
    
    Args:
        ssh_client: paramiko SSH客户端
        remote_path: 远程目录路径
        use_sudo: 是否使用sudo创建目录
    
    Returns:
        bool: 是否成功
    """
    try:
        # 检查目录是否存在
        check_cmd = f'test -d "{remote_path}"'
        success, _, _ = execute_ssh_command(ssh_client, check_cmd, use_sudo=False)
        
        if not success:
            # 目录不存在，创建目录
            # 先创建父目录（如果需要）
            parent_dir = os.path.dirname(remote_path)
            if parent_dir and parent_dir != '/':
                ensure_remote_directory(ssh_client, parent_dir, use_sudo)
            
            # 创建目录
            mkdir_cmd = f'mkdir -p "{remote_path}"'
            success, stdout, stderr = execute_ssh_command(ssh_client, mkdir_cmd, use_sudo)
            
            if not success:
                logger.error(f"创建远程目录失败: {stderr}")
                return False
        
        # 设置权限（确保用户有权限）
        chmod_cmd = f'chmod 755 "{remote_path}"'
        execute_ssh_command(ssh_client, chmod_cmd, use_sudo)
        
        return True
    
    except Exception as e:
        logger.error(f"确保远程目录失败: {str(e)}", exc_info=True)
        return False


def copy_folder_remote_to_remote(source_host, source_port, source_user, source_password, source_path,
                                 target_host, target_port, target_user, target_password, target_path):
    """
    使用 paramiko SFTP 递归复制目录（方案 B）
    """
    ssh_source = None
    ssh_target = None
    sftp_source = None
    sftp_target = None
    
    try:
        logger.info(
            f"[copy_folder_remote_to_remote] 开始远程目录复制: "
            f"{source_user}@{source_host}:{source_port}{source_path} -> "
            f"{target_user}@{target_host}:{target_port}{target_path}"
        )
        # 连接源服务器
        ssh_source = paramiko.SSHClient()
        ssh_source.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh_source.connect(
            hostname=source_host,
            port=source_port,
            username=source_user,
            password=source_password,
            timeout=30
        )
        
        # 连接目标服务器
        ssh_target = paramiko.SSHClient()
        ssh_target.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh_target.connect(
            hostname=target_host,
            port=target_port,
            username=target_user,
            password=target_password,
            timeout=30
        )

        # 打开 SFTP
        sftp_source = ssh_source.open_sftp()
        sftp_target = ssh_target.open_sftp()

        # 统一去掉结尾的 /
        source_path_clean = source_path.rstrip('/')
        target_path_clean = target_path.rstrip('/')

        def sftp_mkdir_p(sftp, remote_path):
            remote_path = remote_path.rstrip('/')
            if not remote_path:
                return
            parts = remote_path.split('/')
            cur = ''
            for p in parts:
                if not p:
                    continue
                cur += f'/{p}'
                try:
                    sftp.stat(cur)
                except IOError:
                    try:
                        sftp.mkdir(cur)
                    except Exception as e:
                        logger.warning(f"[copy_folder_remote_to_remote] 创建目录失败 {cur}: {e}")

        def sftp_rmtree(sftp, remote_path):
            """递归删除远程目录（如果不存在则忽略）"""
            try:
                for entry in sftp.listdir_attr(remote_path):
                    name = entry.filename
                    if name in ('.', '..'):
                        continue
                    full = f"{remote_path.rstrip('/')}/{name}"
                    if stat.S_ISDIR(entry.st_mode):
                        sftp_rmtree(sftp, full)
                    else:
                        sftp.remove(full)
                sftp.rmdir(remote_path)
            except IOError:
                return

        def sftp_copy_dir(src_sftp, src, dst_sftp, dst):
            logger.info(f"[copy_folder_remote_to_remote] 复制目录: {src} -> {dst}")
            sftp_mkdir_p(dst_sftp, dst)
            for entry in src_sftp.listdir_attr(src):
                name = entry.filename
                if name in ('.', '..'):
                    continue
                src_item = f"{src.rstrip('/')}/{name}"
                dst_item = f"{dst.rstrip('/')}/{name}"
                if stat.S_ISDIR(entry.st_mode):
                    sftp_copy_dir(src_sftp, src_item, dst_sftp, dst_item)
                else:
                    logger.info(f"[copy_folder_remote_to_remote] 复制文件: {src_item} -> {dst_item}")
                    parent = '/'.join(dst_item.rstrip('/').split('/')[:-1])
                    sftp_mkdir_p(dst_sftp, parent)
                    with src_sftp.open(src_item, 'rb') as f_src, dst_sftp.open(dst_item, 'wb') as f_dst:
                        while True:
                            chunk = f_src.read(32768)
                            if not chunk:
                                break
                            f_dst.write(chunk)

        logger.info(f"[copy_folder_remote_to_remote] 清理目标目录（如果存在）: {target_path_clean}")
        sftp_rmtree(sftp_target, target_path_clean)

        # 执行复制
        sftp_copy_dir(sftp_source, source_path_clean, sftp_target, target_path_clean)

        # 校验目标目录
        list_cmd = f'ls -la "{target_path_clean}" 2>&1'
        logger.info(f"[copy_folder_remote_to_remote] 目标目录校验命令: {list_cmd}")
        ok, out, err = execute_ssh_command(ssh_target, list_cmd, use_sudo=False)
        debug_info = out if ok else err

        return True, (
            f"文件夹传输成功: {source_host}:{source_path} -> {target_host}:{target_path}\n"
            f"目标目录内容:\n{debug_info}"
        )
    
    except paramiko.AuthenticationException:
        return False, f"SSH认证失败: 用户名或密码错误"
    except paramiko.SSHException as e:
        return False, f"SSH连接错误: {str(e)}"
    except Exception as e:
        logger.error(f"传输过程出错: {str(e)}", exc_info=True)
        return False, f"传输过程出错: {str(e)}"
    
    finally:
        # 安全关闭连接
        if sftp_source:
            try:
                sftp_source.close()
            except Exception as e:
                logger.warning(f"关闭源SFTP失败: {str(e)}")
        if sftp_target:
            try:
                sftp_target.close()
            except Exception as e:
                logger.warning(f"关闭目标SFTP失败: {str(e)}")
        if ssh_source:
            try:
                ssh_source.close()
            except Exception as e:
                logger.warning(f"关闭源SSH连接失败: {str(e)}")
        if ssh_target:
            try:
                ssh_target.close()
            except Exception as e:
                logger.warning(f"关闭目标SSH连接失败: {str(e)}")

def copy_folder_paramiko(source_path, target_host, target_port, target_user, target_password, target_path):
    """
    使用paramiko复制文件夹到目标服务器（本地到远程）
    
    Args:
        source_path: 源文件夹路径（本地）
        target_host: 目标服务器IP
        target_port: 目标服务器SSH端口
        target_user: 目标服务器用户名
        target_password: 目标服务器密码
        target_path: 目标文件夹路径
    
    Returns:
        tuple: (success: bool, message: str)
    """
    ssh_client = None
    sftp_client = None
    
    try:
        # 确保源路径存在
        if not os.path.exists(source_path):
            return False, f"源路径不存在: {source_path}"
        
        if not os.path.isdir(source_path):
            return False, f"源路径不是文件夹: {source_path}"
        
        # 创建SSH客户端
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        logger.info(f"连接到 {target_host}:{target_port} (用户: {target_user})")
        ssh_client.connect(
            hostname=target_host,
            port=target_port,
            username=target_user,
            password=target_password,
            timeout=30
        )
        
        # 确保目标目录存在
        logger.info(f"确保目标目录存在: {target_path}")
        if not ensure_remote_directory(ssh_client, target_path, use_sudo=True):
            return False, f"无法创建目标目录: {target_path}"
        
        # 创建SFTP客户端
        sftp_client = ssh_client.open_sftp()
        
        # 递归复制文件
        def copy_recursive(local_dir, remote_dir):
            """递归复制目录"""
            try:
                # 确保远程目录存在
                try:
                    sftp_client.stat(remote_dir)
                except IOError:
                    # 目录不存在，需要创建（可能需要sudo）
                    # 先尝试不使用sudo创建
                    mkdir_cmd = f'mkdir -p "{remote_dir}"'
                    success, _, _ = execute_ssh_command(ssh_client, mkdir_cmd, use_sudo=False)
                    if not success:
                        # 使用sudo创建
                        success, _, _ = execute_ssh_command(ssh_client, mkdir_cmd, use_sudo=True)
                        if not success:
                            logger.warning(f"无法创建目录: {remote_dir}")
                            return False
                
                # 遍历本地目录
                for item in os.listdir(local_dir):
                    local_path = os.path.join(local_dir, item)
                    remote_path = os.path.join(remote_dir, item).replace('\\', '/')
                    
                    if os.path.isdir(local_path):
                        # 递归处理子目录
                        if not copy_recursive(local_path, remote_path):
                            return False
                    else:
                        # 复制文件
                        try:
                            logger.debug(f"复制文件: {local_path} -> {remote_path}")
                            sftp_client.put(local_path, remote_path)
                            # 设置文件权限
                            sftp_client.chmod(remote_path, 0o644)
                        except PermissionError:
                            # 权限不足，尝试使用sudo
                            logger.warning(f"权限不足，尝试使用sudo复制: {remote_path}")
                            # 先复制到临时位置，然后使用sudo移动
                            temp_path = f"/tmp/{os.path.basename(remote_path)}_{os.getpid()}"
                            sftp_client.put(local_path, temp_path)
                            # 使用sudo移动文件
                            mv_cmd = f'mv "{temp_path}" "{remote_path}" && chmod 644 "{remote_path}"'
                            success, _, stderr = execute_ssh_command(ssh_client, mv_cmd, use_sudo=True)
                            if not success:
                                logger.error(f"使用sudo移动文件失败: {stderr}")
                                return False
                        except Exception as e:
                            logger.error(f"复制文件失败 {local_path}: {str(e)}")
                            return False
                
                return True
            
            except Exception as e:
                logger.error(f"递归复制失败: {str(e)}", exc_info=True)
                return False
        
        # 开始复制
        logger.info(f"开始复制文件夹: {source_path} -> {target_host}:{target_path}")
        if copy_recursive(source_path, target_path):
            return True, f"文件夹传输成功: {source_path} -> {target_host}:{target_path}"
        else:
            return False, "文件夹传输过程中出现错误"
    
    except paramiko.AuthenticationException:
        return False, f"SSH认证失败: 用户名或密码错误"
    except paramiko.SSHException as e:
        return False, f"SSH连接错误: {str(e)}"
    except Exception as e:
        logger.error(f"传输过程出错: {str(e)}", exc_info=True)
        return False, f"传输过程出错: {str(e)}"
    
    finally:
        # 关闭连接
        if sftp_client:
            try:
                sftp_client.close()
            except:
                pass
        if ssh_client:
            try:
                ssh_client.close()
            except:
                pass


@app.route('/health', methods=['GET'])
def health_check():
    """健康检查接口"""
    return jsonify({
        'status': 'ok',
        'server': CURRENT_SERVER,
        'message': '服务运行正常'
    })


@app.route('/transfer', methods=['POST'])
def transfer_folder():
    """
    文件夹传输接口
    
    请求参数（JSON）:
        - username: 用户名
        - projectname: 项目名称
        - target_server: 目标服务器（server101/server102/server103/server104）
    
    返回:
        JSON格式的响应，包含传输结果
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': '请求体必须为JSON格式'
            }), 400
        
        # 获取参数
        username = data.get('username')
        projectname = data.get('projectname')
        target_server = data.get('target_server')
        
        # 参数验证
        if not username:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: username'
            }), 400
        
        if not projectname:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: projectname'
            }), 400
        
        if not target_server:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: target_server'
            }), 400
        
        # 验证目标服务器
        if target_server not in SERVER_CONFIG:
            return jsonify({
                'success': False,
                'error': f'无效的目标服务器: {target_server}。可用服务器: {", ".join(SERVER_CONFIG.keys())}'
            }), 400
        
        # 不能传输到自己
        if target_server == CURRENT_SERVER:
            return jsonify({
                'success': False,
                'error': '不能传输到当前服务器'
            }), 400
        
        # 构建路径
        # Server101: /home/user/{username}/projects/project
        # Server102: /home/user/{username}/projects/projectname
        # Server104: /home/user/{username}/outputs/output
        # username 参数就是用户输入的用户名（如 gousk）
        if not username:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: username'
            }), 400
        
        source_path = f'/home/user/{username}/projects/{projectname}'
        target_config = SERVER_CONFIG[target_server]
        target_host = target_config['host']
        target_port = target_config['port']
        target_user = target_config['user']
        target_password = target_config['password']
        
        # 根据目标服务器确定路径
        if target_server == 'server104':
            # Server104 使用 outputs 目录
            target_path = f'/home/user/{username}/outputs/{projectname}'
        else:
            # Server101, Server102 使用 projects 目录
            target_path = f'/home/user/{username}/projects/{projectname}'
        
        # 检查源路径是否存在
        if not os.path.exists(source_path):
            return jsonify({
                'success': False,
                'error': f'源路径不存在: {source_path}'
            }), 404
        
        if not os.path.isdir(source_path):
            return jsonify({
                'success': False,
                'error': f'源路径不是文件夹: {source_path}'
            }), 400
        
        # 执行传输
        logger.info(f"开始传输: {source_path} -> {target_server}:{target_path}")
        success, message = copy_folder_paramiko(
            source_path,
            target_host,
            target_port,
            target_user,
            target_password,
            target_path
        )
        
        if success:
            return jsonify({
                'success': True,
                'message': message,
                'source': source_path,
                'target': f'{target_server}:{target_path}'
            }), 200
        else:
            return jsonify({
                'success': False,
                'error': message,
                'source': source_path,
                'target': f'{target_server}:{target_path}'
            }), 500
    
    except Exception as e:
        logger.error(f"处理请求时出错: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'服务器内部错误: {str(e)}'
        }), 500


@app.route('/servers', methods=['GET'])
def list_servers():
    """获取可用服务器列表（不返回密码）"""
    server_config_safe = {}
    for server, config in SERVER_CONFIG.items():
        server_config_safe[server] = {
            'host': config['host'],
            'port': config['port'],
            'user': config['user']
        }
    
    return jsonify({
        'current_server': CURRENT_SERVER,
        'available_servers': list(SERVER_CONFIG.keys()),
        'server_config': server_config_safe
    })


@app.route('/list', methods=['POST'])
def list_files():
    """
    列出远程服务器指定路径下的文件和文件夹
    
    请求参数（JSON）:
        - server: 服务器名称（server101/server102/server103/server104）
        - path: 要查询的路径（默认为 /home/user）
    
    返回:
        JSON格式的响应，包含文件列表
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': '请求体必须为JSON格式'
            }), 400
        
        server_name = data.get('server')
        path = data.get('path', '/home')
        
        # 参数验证
        if not server_name:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: server'
            }), 400
        
        # 验证服务器
        if server_name not in SERVER_CONFIG:
            return jsonify({
                'success': False,
                'error': f'无效的服务器: {server_name}。可用服务器: {", ".join(SERVER_CONFIG.keys())}'
            }), 400
        
        # 获取服务器配置
        server_config = SERVER_CONFIG[server_name]
        target_host = server_config['host']
        target_port = server_config['port']
        target_user = server_config['user']
        target_password = server_config['password']
        
        ssh_client = None
        try:
            # 创建SSH客户端
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            logger.info(f"连接到 {target_host}:{target_port} (用户: {target_user})")
            ssh_client.connect(
                hostname=target_host,
                port=target_port,
                username=target_user,
                password=target_password,
                timeout=30
            )
            
            # 执行 ls -la 命令获取详细信息
            # 使用 -a 显示所有文件（包括隐藏文件），-l 显示详细信息
            ls_cmd = f'ls -la "{path}" 2>&1'
            success, stdout, stderr = execute_ssh_command(ssh_client, ls_cmd, use_sudo=False)
            
            if not success:
                # 如果普通用户没有权限，尝试使用sudo
                logger.warning(f"普通用户无权限，尝试使用sudo: {path}")
                success, stdout, stderr = execute_ssh_command(ssh_client, ls_cmd, use_sudo=True)
            
            if not success:
                return jsonify({
                    'success': False,
                    'error': f'无法访问路径: {path}。错误: {stderr}',
                    'server': server_name,
                    'path': path
                }), 404
            
            # 解析 ls -la 输出
            files = []
            lines = stdout.strip().split('\n')
            
            # 跳过第一行（total X）和空行
            for line in lines:
                line = line.strip()
                if not line or line.startswith('total'):
                    continue
                
                # 解析 ls -la 格式: permissions links owner group size date time name
                parts = line.split(None, 8)  # 最多分割8次，保留文件名（可能包含空格）
                
                if len(parts) >= 9:
                    permissions = parts[0]
                    links = parts[1]
                    owner = parts[2]
                    group = parts[3]
                    size = parts[4]
                    date = parts[5]
                    time = parts[6]
                    name = parts[8]  # 文件名（可能包含空格）
                    
                    # 判断是文件还是目录
                    is_dir = permissions.startswith('d')
                    is_link = permissions.startswith('l')
                    
                    # 跳过 . 和 .. 目录
                    if name in ['.', '..']:
                        continue
                    
                    file_info = {
                        'name': name,
                        'type': 'directory' if is_dir else ('link' if is_link else 'file'),
                        'permissions': permissions,
                        'owner': owner,
                        'group': group,
                        'size': size,
                        'date': f'{date} {time}',
                        'is_hidden': name.startswith('.'),
                        'full_path': os.path.join(path, name).replace('\\', '/')
                    }
                    
                    files.append(file_info)
            
            # 按类型和名称排序：目录在前，然后按名称排序
            files.sort(key=lambda x: (x['type'] != 'directory', x['name'].lower()))
            
            return jsonify({
                'success': True,
                'server': server_name,
                'path': path,
                'files': files,
                'count': len(files)
            }), 200
        
        except paramiko.AuthenticationException:
            return jsonify({
                'success': False,
                'error': 'SSH认证失败: 用户名或密码错误',
                'server': server_name
            }), 401
        except paramiko.SSHException as e:
            return jsonify({
                'success': False,
                'error': f'SSH连接错误: {str(e)}',
                'server': server_name
            }), 500
        except Exception as e:
            logger.error(f"列出文件时出错: {str(e)}", exc_info=True)
            return jsonify({
                'success': False,
                'error': f'服务器内部错误: {str(e)}',
                'server': server_name
            }), 500
        
        finally:
            if ssh_client:
                try:
                    ssh_client.close()
                except:
                    pass
    
    except Exception as e:
        logger.error(f"处理请求时出错: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'服务器内部错误: {str(e)}'
        }), 500


@app.route('/file/create', methods=['POST'])
def create_file():
    """
    在远程服务器指定目录下创建文件

    请求参数（JSON）:
        - server: 服务器名称（server101/server102/server103/server104）
        - path: 目录路径（例如: /home/user）
        - filename: 文件名（例如: test.txt）
        - content: 文件内容（字符串）

    返回:
        JSON格式的响应
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({
                'success': False,
                'error': '请求体必须为JSON格式'
            }), 400

        server_name = data.get('server')
        path = data.get('path')
        filename = data.get('filename')
        content = data.get('content', '')

        # 参数验证
        if not server_name:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: server'
            }), 400

        if not path:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: path'
            }), 400

        if not filename:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: filename'
            }), 400

        # 验证服务器
        if server_name not in SERVER_CONFIG:
            return jsonify({
                'success': False,
                'error': f'无效的服务器: {server_name}。可用服务器: {", ".join(SERVER_CONFIG.keys())}'
            }), 400

        server_config = SERVER_CONFIG[server_name]
        target_host = server_config['host']
        target_port = server_config['port']
        target_user = server_config['user']
        target_password = server_config['password']

        ssh_client = None
        sftp_client = None
        try:
            # 创建SSH客户端
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            logger.info(f"连接到 {target_host}:{target_port} (用户: {target_user}) 以创建文件")
            ssh_client.connect(
                hostname=target_host,
                port=target_port,
                username=target_user,
                password=target_password,
                timeout=30
            )

            # 检查目录是否存在
            check_dir_cmd = f'test -d "{path}"'
            success, _, _ = execute_ssh_command(ssh_client, check_dir_cmd, use_sudo=False)
            if not success:
                return jsonify({
                    'success': False,
                    'error': f'目录不存在或无法访问: {path}'
                }), 404

            # 通过 SFTP 写入文件
            sftp_client = ssh_client.open_sftp()
            remote_dir = path.rstrip('/').replace('\\', '/')
            remote_file = os.path.join(remote_dir, filename).replace('\\', '/')

            try:
                with sftp_client.open(remote_file, 'w') as f:
                    # content 可能是 str，也可能是 None（上面已默认空串）
                    if isinstance(content, str):
                        f.write(content)
                    else:
                        f.write(str(content))
            except PermissionError:
                return jsonify({
                    'success': False,
                    'error': f'没有权限在该目录创建文件: {remote_file}'
                }), 403
            except IOError as e:
                return jsonify({
                    'success': False,
                    'error': f'创建文件失败: {str(e)}'
                }), 500

            return jsonify({
                'success': True,
                'message': f'文件创建成功: {remote_file}',
                'server': server_name,
                'path': path,
                'filename': filename
            }), 200

        except paramiko.AuthenticationException:
            return jsonify({
                'success': False,
                'error': 'SSH认证失败: 用户名或密码错误',
                'server': server_name
            }), 401
        except paramiko.SSHException as e:
            return jsonify({
                'success': False,
                'error': f'SSH连接错误: {str(e)}',
                'server': server_name
            }), 500
        except Exception as e:
            logger.error(f"创建远程文件时出错: {str(e)}", exc_info=True)
            return jsonify({
                'success': False,
                'error': f'服务器内部错误: {str(e)}',
                'server': server_name
            }), 500
        finally:
            if sftp_client:
                try:
                    sftp_client.close()
                except:
                    pass
            if ssh_client:
                try:
                    ssh_client.close()
                except:
                    pass

    except Exception as e:
        logger.error(f"处理请求时出错: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'服务器内部错误: {str(e)}'
        }), 500


@app.route('/env/create', methods=['POST'])
def create_venv():
    """
    创建虚拟环境
    
    请求参数（JSON）:
        - env_name: 环境名称
        - username: 用户名（默认为 'user'）
    
    返回:
        JSON格式的响应
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': '请求体必须为JSON格式'
            }), 400
        
        env_name = data.get('env_name')
        username = data.get('username')
        
        if not env_name:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: env_name'
            }), 400
        
        if not username:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: username'
            }), 400
        
        # 目标服务器是 server102
        # Server102: /home/user/{username}/envs/envname
        server_config = SERVER_CONFIG['server102']
        target_host = server_config['host']
        target_port = server_config['port']
        target_user = server_config['user']
        target_password = server_config['password']
        
        # 环境路径
        envs_path = f'/home/user/{username}/envs'
        env_path = f'{envs_path}/{env_name}'
        base_venv_path = '/home/user/common/basevenv/venv'
        
        ssh_client = None
        try:
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh_client.connect(
                hostname=target_host,
                port=target_port,
                username=target_user,
                password=target_password,
                timeout=30
            )
            
            # 确保 envs 目录存在
            mkdir_envs_cmd = f'mkdir -p "{envs_path}"'
            success, _, stderr = execute_ssh_command(ssh_client, mkdir_envs_cmd, use_sudo=False)
            if not success:
                success, _, stderr = execute_ssh_command(ssh_client, mkdir_envs_cmd, use_sudo=True)
            
            # 检查环境是否已存在
            check_cmd = f'test -d "{env_path}"'
            success, _, _ = execute_ssh_command(ssh_client, check_cmd, use_sudo=False)
            if success:
                return jsonify({
                    'success': False,
                    'error': f'虚拟环境 {env_name} 已存在'
                }), 400
            
            # 创建环境目录
            mkdir_cmd = f'mkdir -p "{env_path}"'
            success, _, stderr = execute_ssh_command(ssh_client, mkdir_cmd, use_sudo=False)
            if not success:
                success, _, stderr = execute_ssh_command(ssh_client, mkdir_cmd, use_sudo=True)
            
            if not success:
                return jsonify({
                    'success': False,
                    'error': f'创建目录失败: {stderr}'
                }), 500
            
            # 确认基础虚拟环境存在
            check_base_cmd = f'test -d "{base_venv_path}"'
            base_exists, _, base_err = execute_ssh_command(ssh_client, check_base_cmd, use_sudo=False)
            if not base_exists:
                return jsonify({
                    'success': False,
                    'error': f'基础虚拟环境不存在: {base_venv_path}，请先在10.2准备好基线venv'
                }), 500
            
            # 从基础虚拟环境拷贝一份到目标目录并改名
            copy_cmd = f'cp -a "{base_venv_path}/." "{env_path}/"'
            success, stdout, stderr = execute_ssh_command(ssh_client, copy_cmd, use_sudo=True)
            print("------------------创建虚拟环境(基线拷贝)----------")
            print(success, stdout, stderr)
            print(copy_cmd)

            if success:
                return jsonify({
                    'success': True,
                    'message': f'虚拟环境 {env_name} 创建成功（基线拷贝）',
                    'path': env_path,
                    'output': stdout
                }), 200
            else:
                return jsonify({
                    'success': False,
                    'error': f'创建虚拟环境失败: {stderr or stdout}',
                    'path': env_path
                }), 500
        
        except Exception as e:
            logger.error(f"创建虚拟环境时出错: {str(e)}", exc_info=True)
            return jsonify({
                'success': False,
                'error': f'服务器内部错误: {str(e)}'
            }), 500
        
        finally:
            if ssh_client:
                try:
                    ssh_client.close()
                except:
                    pass
    
    except Exception as e:
        logger.error(f"处理请求时出错: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'服务器内部错误: {str(e)}'
        }), 500


@app.route('/env/list', methods=['POST'])
def list_venvs():
    """
    列出所有虚拟环境
    
    请求参数（JSON）:
        - username: 用户名（默认为 'user'）
    
    返回:
        JSON格式的响应，包含环境列表
    """
    try:
        data = request.get_json() or {}
        username = data.get('username')
        
        if not username:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: username',
                'envs': []
            }), 400
        
        # Server102: /home/user/{username}/envs/envname
        server_config = SERVER_CONFIG['server102']
        target_host = server_config['host']
        target_port = server_config['port']
        target_user = server_config['user']
        target_password = server_config['password']
        
        envs_path = f'/home/user/{username}/envs'
        
        ssh_client = None
        try:
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh_client.connect(
                hostname=target_host,
                port=target_port,
                username=target_user,
                password=target_password,
                timeout=30
            )
            
            # 列出目录
            list_cmd = f'ls -la "{envs_path}" 2>&1'
            success, stdout, stderr = execute_ssh_command(ssh_client, list_cmd, use_sudo=False)
            
            if not success:
                return jsonify({
                    'success': False,
                    'error': f'无法访问路径: {envs_path}。错误: {stderr}',
                    'envs': []
                }), 404
            
            # 解析输出
            envs = []
            lines = stdout.strip().split('\n')
            
            for line in lines:
                line = line.strip()
                if not line or line.startswith('total') or line.startswith('d') and '.' in line[:10]:
                    continue
                
                parts = line.split(None, 8)
                if len(parts) >= 9:
                    name = parts[8]
                    if name not in ['.', '..']:
                        # 检查是否是虚拟环境（包含 bin/activate 或 Scripts/activate）
                        check_venv_cmd = f'test -f "{envs_path}/{name}/bin/activate" || test -f "{envs_path}/{name}/Scripts/activate"'
                        is_venv, _, _ = execute_ssh_command(ssh_client, check_venv_cmd, use_sudo=False)
                        
                        envs.append({
                            'name': name,
                            'path': f'{envs_path}/{name}',
                            'is_venv': is_venv
                        })
            
            return jsonify({
                'success': True,
                'envs': envs,
                'count': len(envs)
            }), 200
        
        except Exception as e:
            logger.error(f"列出虚拟环境时出错: {str(e)}", exc_info=True)
            return jsonify({
                'success': False,
                'error': f'服务器内部错误: {str(e)}',
                'envs': []
            }), 500
        
        finally:
            if ssh_client:
                try:
                    ssh_client.close()
                except:
                    pass
    
    except Exception as e:
        logger.error(f"处理请求时出错: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'服务器内部错误: {str(e)}',
            'envs': []
        }), 500


@app.route('/env/delete', methods=['POST'])
def delete_venv():
    """
    删除虚拟环境
    
    请求参数（JSON）:
        - env_name: 环境名称
        - username: 用户名（默认为 'user'）
    
    返回:
        JSON格式的响应
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': '请求体必须为JSON格式'
            }), 400
        
        env_name = data.get('env_name')
        username = data.get('username')
        
        if not env_name:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: env_name'
            }), 400
        
        if not username:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: username'
            }), 400
        
        # Server102: /home/user/{username}/envs/envname
        server_config = SERVER_CONFIG['server102']
        target_host = server_config['host']
        target_port = server_config['port']
        target_user = server_config['user']
        target_password = server_config['password']
        
        env_path = f'/home/user/{username}/envs/{env_name}'
        
        ssh_client = None
        try:
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh_client.connect(
                hostname=target_host,
                port=target_port,
                username=target_user,
                password=target_password,
                timeout=30
            )
            
            # 删除目录
            delete_cmd = f'rm -rf "{env_path}"'
            success, stdout, stderr = execute_ssh_command(ssh_client, delete_cmd, use_sudo=False)
            
            if not success:
                success, stdout, stderr = execute_ssh_command(ssh_client, delete_cmd, use_sudo=True)
            
            if success:
                return jsonify({
                    'success': True,
                    'message': f'虚拟环境 {env_name} 删除成功'
                }), 200
            else:
                return jsonify({
                    'success': False,
                    'error': f'删除失败: {stderr or stdout}'
                }), 500
        
        except Exception as e:
            logger.error(f"删除虚拟环境时出错: {str(e)}", exc_info=True)
            return jsonify({
                'success': False,
                'error': f'服务器内部错误: {str(e)}'
            }), 500
        
        finally:
            if ssh_client:
                try:
                    ssh_client.close()
                except:
                    pass
    
    except Exception as e:
        logger.error(f"处理请求时出错: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'服务器内部错误: {str(e)}'
        }), 500


@app.route('/env/execute', methods=['POST'])
def execute_command():
    """
    在虚拟环境中执行命令（网页终端）
    
    请求参数（JSON）:
        - env_name: 环境名称（可选，如果提供则先激活环境）
        - command: 要执行的命令
        - username: 用户名（默认为 'user'）
    
    返回:
        JSON格式的响应，包含命令输出
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': '请求体必须为JSON格式'
            }), 400
        
        env_name = data.get('env_name')
        command = data.get('command')
        username = data.get('username')
        
        if not command:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: command'
            }), 400
        
        if not username:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: username'
            }), 400
        
        server_config = SERVER_CONFIG['server102']
        target_host = server_config['host']
        target_port = server_config['port']
        target_user = server_config['user']
        target_password = server_config['password']
        
        ssh_client = None
        try:
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh_client.connect(
                hostname=target_host,
                port=target_port,
                username=target_user,
                password=target_password,
                timeout=30
            )
            
            # 构建命令
            if env_name:
                # 激活虚拟环境并执行命令
                # Server102: /home/user/{username}/envs/{env_name}
                env_path = f'/home/user/{username}/envs/{env_name}'
                # 检测是 Linux 还是 Windows（通过检查 bin/activate 或 Scripts/activate）
                activate_script = f'{env_path}/bin/activate'
                check_cmd = f'test -f "{activate_script}"'
                success, _, _ = execute_ssh_command(ssh_client, check_cmd, use_sudo=False)
                
                if success:
                    # Linux 环境
                    full_command = f'source {env_path}/bin/activate && {command}'
                else:
                    # Windows 环境（虽然不太可能，但兼容处理）
                    activate_script = f'{env_path}/Scripts/activate'
                    full_command = f'source {activate_script} && {command}'
            else:
                full_command = command
            
            # 执行命令
            success, stdout, stderr = execute_ssh_command(ssh_client, full_command, use_sudo=False)
            
            return jsonify({
                'success': success,
                'stdout': stdout,
                'stderr': stderr,
                'command': command,
                'env_name': env_name
            }), 200
        
        except Exception as e:
            logger.error(f"执行命令时出错: {str(e)}", exc_info=True)
            return jsonify({
                'success': False,
                'error': f'服务器内部错误: {str(e)}',
                'stdout': '',
                'stderr': str(e)
            }), 500
        
        finally:
            if ssh_client:
                try:
                    ssh_client.close()
                except:
                    pass
    
    except Exception as e:
        logger.error(f"处理请求时出错: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'服务器内部错误: {str(e)}'
        }), 500


@app.route('/project/execute', methods=['POST'])
def execute_project():
    """
    在项目目录下使用指定虚拟环境执行算法
    
    请求参数（JSON）:
        - username: 用户名
        - projectname: 项目名称
        - env_name: 虚拟环境名称（必需）
        - command: 要执行的命令（如: python xxx.py）
    
    返回:
        JSON格式的响应，包含命令输出
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': '请求体必须为JSON格式'
            }), 400
        
        username = data.get('username')
        projectname = data.get('projectname')
        env_name = data.get('env_name')
        command = data.get('command')
        # 固定使用 server102 执行，忽略传入的 server 参数
        # 项目代码从 server101 拷贝到 server102 执行
        server = 'server102'
        
        # 参数验证
        if not username:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: username'
            }), 400
        
        if not projectname:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: projectname'
            }), 400
        
        if not env_name:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: env_name（必须指定虚拟环境）'
            }), 400

        if not command:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: command'
            }), 400

        # 统一约定：
        # - 项目代码源在 10.1（server101）：/home/user/{username}/projects/{projectname}
        # - 执行与虚拟环境在 10.2（server102）：
        #     - 项目执行目录：/home/user/{username}/projects/{projectname}
        #     - 虚拟环境目录：/home/user/{username}/envs/{env_name}
        server101_config = SERVER_CONFIG['server101']
        server102_config = SERVER_CONFIG['server102']
        target_host = server102_config['host']
        target_port = server102_config['port']
        target_user = server102_config['user']
        target_password = server102_config['password']

        # 构建路径：源项目在 server101，目标项目在 server102
        project_path_source = f'/home/user/{username}/projects/{projectname}'
        project_path = f'/home/user/{username}/projects/{projectname}'
        env_path = f'/home/user/{username}/envs/{env_name}'
        
        ssh_client = None
        try:
            # 先检查 server101 上项目是否存在
            ssh_server101 = paramiko.SSHClient()
            ssh_server101.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh_server101.connect(
                hostname=server101_config['host'],
                port=server101_config['port'],
                username=server101_config['user'],
                password=server101_config['password'],
                timeout=30
            )
            check_project_cmd = f'test -d "{project_path_source}"'
            exists, _, _ = execute_ssh_command(ssh_server101, check_project_cmd, use_sudo=False)
            ssh_server101.close()
            if not exists:
                return jsonify({
                    'success': False,
                    'error': f'项目目录不存在(10.1/server101): {project_path_source}',
                    'stdout': '',
                    'stderr': ''
                }), 404

            # 每次执行前，将项目从 server101 拷贝到 server102 对应目录
            logger.info(
                f"从 server101 同步项目到 server102 以便执行: "
                f"{server101_config['host']}:{project_path_source} "
                f"-> {server102_config['host']}:{project_path}"
            )
            copy_success, copy_message = copy_folder_remote_to_remote(
                server101_config['host'],
                server101_config['port'],
                server101_config['user'],
                server101_config['password'],
                project_path_source,
                server102_config['host'],
                server102_config['port'],
                server102_config['user'],
                server102_config['password'],
                project_path
            )
            if not copy_success:
                return jsonify({
                    'success': False,
                    'error': f'从 server101 同步项目到 server102 失败: {copy_message}',
                    'stdout': '',
                    'stderr': ''
                }), 500

            # 连接执行服务器 server102
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh_client.connect(
                hostname=target_host,
                port=target_port,
                username=target_user,
                password=target_password,
                timeout=30
            )
            
            # 检查虚拟环境是否存在
            check_env_cmd = f'test -f "{env_path}/bin/activate"'
            success, _, _ = execute_ssh_command(ssh_client, check_env_cmd, use_sudo=False)
            if not success:
                return jsonify({
                    'success': False,
                    'error': f'虚拟环境不存在: {env_name}',
                    'stdout': '',
                    'stderr': ''
                }), 404
            
            # 在Server104创建log文件路径
            output_path = f'/home/user/{username}/outputs/{projectname}'
            log_file = f'{output_path}/run_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
            # 本次运行名（例如 run_20251222_101517），用于在Server104上为每次执行单独建目录
            run_name = os.path.splitext(os.path.basename(log_file))[0]
            run_output_path = f'{output_path}/{run_name}'
            
            # 确保Server104的输出目录存在
            server104_config = SERVER_CONFIG['server104']
            ssh_server104 = None
            try:
                ssh_server104 = paramiko.SSHClient()
                ssh_server104.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh_server104.connect(
                    hostname=server104_config['host'],
                    port=server104_config['port'],
                    username=server104_config['user'],
                    password=server104_config['password'],
                    timeout=30
                )
                # 创建输出目录（项目输出根目录，按项目聚合）
                mkdir_cmd = f'mkdir -p "{output_path}"'
                execute_ssh_command(ssh_server104, mkdir_cmd, use_sudo=False)
            except Exception as e:
                logger.warning(f"无法创建Server104输出目录: {str(e)}")
            finally:
                if ssh_server104:
                    try:
                        ssh_server104.close()
                    except:
                        pass
            
            # 构建命令：激活虚拟环境，若有 requirements.txt 则先安装依赖，再执行命令并将输出写入临时log文件
            # 然后传输到Server104
            temp_log = f'/tmp/run_{datetime.now().strftime("%Y%m%d_%H%M%S")}_{os.getpid()}.log'
            pip_cmd = (
                f'if [ -f "requirements.txt" ]; then '
                f'pip install -r requirements.txt --index-url http://192.168.10.2:8087/simple/ --trusted-host 192.168.10.2; '
                f'fi'
            )
            # 先用 sudo 修正输出目录权限（作用于项目目录下的 output）
            fix_output_cmd = (
                f'bash -lc \'cd "{project_path}" && '
                f'mkdir -p output && chmod -R 775 output && '
                f'chown -R {target_user}:{target_user} output\''
            )
            execute_ssh_command(ssh_client, fix_output_cmd, use_sudo=True)

            ensure_output_cmd = 'mkdir -p output && chmod -R 775 output || true'
            full_command = (
                f'cd "{project_path}" && '
                f'{ensure_output_cmd} && '
                f'source "{env_path}/bin/activate" && '
                f'{pip_cmd} && '
                f'({command} 2>&1 | tee "{temp_log}")'
            )
            print("------------------执行项目算法----------")
            print(pip_cmd)
            print(full_command)
            
            logger.info(f"执行项目算法: 项目={projectname}, 环境={env_name}, 命令={command}, log={log_file}")
            
            # 执行命令（不再使用 sudo，避免工作目录丢失导致 python 在 /home/user 下找脚本）
            success, stdout, stderr = execute_ssh_command(ssh_client, full_command, use_sudo=False)
            
            # 读取临时log文件内容
            log_content = stdout + (stderr if stderr else '')
            try:
                sftp_client = ssh_client.open_sftp()
                try:
                    # 尝试读取临时log文件（如果存在）
                    with sftp_client.open(temp_log, 'r') as f:
                        log_content = f.read().decode('utf-8', errors='ignore')
                except IOError:
                    # 如果文件不存在，使用stdout/stderr
                    pass
                finally:
                    sftp_client.close()
            except Exception as e:
                logger.warning(f"读取临时log文件失败: {str(e)}")
            
            # 保存log到Server104
            if log_content:
                try:
                    ssh_server104 = paramiko.SSHClient()
                    ssh_server104.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh_server104.connect(
                        hostname=server104_config['host'],
                        port=server104_config['port'],
                        username=server104_config['user'],
                        password=server104_config['password'],
                        timeout=30
                    )
                    sftp_server104 = ssh_server104.open_sftp()
                    try:
                        # 确保目录存在
                        mkdir_cmd = f'mkdir -p "{output_path}"'
                        execute_ssh_command(ssh_server104, mkdir_cmd, use_sudo=False)
                        
                        # 写入log文件
                        with sftp_server104.open(log_file, 'w') as f:
                            f.write(log_content)
                    finally:
                        sftp_server104.close()
                        ssh_server104.close()
                except Exception as e:
                    logger.warning(f"保存log文件到Server104失败: {str(e)}")
            
            # 清理临时文件
            try:
                rm_cmd = f'rm -f "{temp_log}"'
                execute_ssh_command(ssh_client, rm_cmd, use_sudo=False)
            except:
                pass
            
            # 执行完成后，将整个项目目录传输到Server104的outputs目录，按运行名归档：output/项目名/运行名/
            # Server104的outputs目录等同于其他服务器的projects目录
            logger.info(
                f"开始传输项目到Server104: {server102_config['host']}:{project_path} "
                f"-> {server104_config['host']}:{run_output_path}"
            )
            
            try:
                # 使用远程到远程复制函数传输整个项目目录
                copy_success, copy_message = copy_folder_remote_to_remote(
                    server102_config['host'],      # 源服务器（执行服务器 server102）
                    server102_config['port'],
                    server102_config['user'],
                    server102_config['password'],
                    project_path,               # 源路径
                    server104_config['host'],   # 目标服务器（Server104）
                    server104_config['port'],
                    server104_config['user'],
                    server104_config['password'],
                    run_output_path             # 目标路径（按运行名的子目录）
                )
                
                if copy_success:
                    logger.info(f"项目传输成功: {copy_message}")
                else:
                    logger.warning(f"项目传输失败: {copy_message}")
            except Exception as e:
                logger.error(f"传输项目到Server104时出错: {str(e)}", exc_info=True)
            
            # 确保log文件也保存在项目目录中
            if log_content:
                try:
                    ssh_server104 = paramiko.SSHClient()
                    ssh_server104.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh_server104.connect(
                        hostname=server104_config['host'],
                        port=server104_config['port'],
                        username=server104_config['user'],
                        password=server104_config['password'],
                        timeout=30
                    )
                    sftp_server104 = ssh_server104.open_sftp()
                    try:
                        # 确保目录存在（项目传输后应该已存在）
                        mkdir_cmd = f'mkdir -p "{output_path}"'
                        execute_ssh_command(ssh_server104, mkdir_cmd, use_sudo=False)
                        
                        # 写入log文件到项目目录中
                        with sftp_server104.open(log_file, 'w') as f:
                            f.write(log_content)
                    finally:
                        sftp_server104.close()
                        ssh_server104.close()
                except Exception as e:
                    logger.warning(f"保存log文件到Server104失败: {str(e)}")
            
            return jsonify({
                'success': success,
                'stdout': stdout,
                'stderr': stderr,
                'command': command,
                'project': projectname,
                'env_name': env_name,
                'project_path': project_path,
                'env_path': env_path,
                'log_file': log_file,
                'output_path': output_path,
                'run_output_path': run_output_path,
                'copy_success': copy_success,
                'copy_message': copy_message,
                'message': (
                    f'项目已在Server104创建输出目录: {output_path}，'
                    f'并尝试将项目复制到: {run_output_path}'
                )
            }), 200
        
        except paramiko.AuthenticationException:
            return jsonify({
                'success': False,
                'error': 'SSH认证失败: 用户名或密码错误',
                'stdout': '',
                'stderr': ''
            }), 401
        except paramiko.SSHException as e:
            return jsonify({
                'success': False,
                'error': f'SSH连接错误: {str(e)}',
                'stdout': '',
                'stderr': ''
            }), 500
        except Exception as e:
            logger.error(f"执行项目算法时出错: {str(e)}", exc_info=True)
            return jsonify({
                'success': False,
                'error': f'服务器内部错误: {str(e)}',
                'stdout': '',
                'stderr': ''
            }), 500
        
        finally:
            if ssh_client:
                try:
                    ssh_client.close()
                except:
                    pass
    
    except Exception as e:
        logger.error(f"处理请求时出错: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'服务器内部错误: {str(e)}'
        }), 500


@app.route('/project/list', methods=['POST'])
def list_projects():
    """
    列出指定服务器上的项目列表
    
    请求参数（JSON）:
        - username: 用户名
        - server: 服务器名称（固定为 server101，忽略传入的 server 参数）
    
    返回:
        JSON格式的响应，包含项目列表
    
    注意：项目列表固定从 server101 查询，因为项目代码源统一存储在 server101
    """
    try:
        data = request.get_json() or {}
        username = data.get('username')
        # 固定从 10.1（server101）扫描项目，忽略前端传入的 server 参数
        server = 'server101'
        
        if not username:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: username',
                'projects': []
            }), 400
        
        if server not in SERVER_CONFIG:
            return jsonify({
                'success': False,
                'error': f'无效的服务器: {server}',
                'projects': []
            }), 400
        
        server_config = SERVER_CONFIG[server]
        print("------------------列出项目----------")
        print(server_config)
        target_host = server_config['host']
        target_port = server_config['port']
        target_user = server_config['user']
        target_password = server_config['password']
        
        projects_path = f'/home/user/{username}/projects'
        
        ssh_client = None
        try:
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh_client.connect(
                hostname=target_host,
                port=target_port,
                username=target_user,
                password=target_password,
                timeout=30
            )
            
            # 列出项目目录
            list_cmd = f'ls -la "{projects_path}" 2>&1'
            success, stdout, stderr = execute_ssh_command(ssh_client, list_cmd, use_sudo=True)
            print("------------------列出项目----------")
            print(success, stdout, stderr)
            print(list_cmd)
            if not success:
                return jsonify({
                    'success': False,
                    'error': f'无法访问路径: {projects_path}。错误: {stderr}',
                    'projects': []
                }), 404
            
            # 解析输出
            projects = []
            lines = stdout.strip().split('\n')
            
            for line in lines:
                line = line.strip()
                if not line or line.startswith('total') or (line.startswith('d') and '.' in line[:10]):
                    continue
                
                parts = line.split(None, 8)
                if len(parts) >= 9:
                    name = parts[8]
                    if name not in ['.', '..']:
                        # 检查是否是目录
                        check_dir_cmd = f'test -d "{projects_path}/{name}"'
                        is_dir, _, _ = execute_ssh_command(ssh_client, check_dir_cmd, use_sudo=False)
                        
                        if is_dir:
                            # 检查是否有requirements.txt
                            check_req_cmd = f'test -f "{projects_path}/{name}/requirements.txt"'
                            has_requirements, _, _ = execute_ssh_command(ssh_client, check_req_cmd, use_sudo=False)
                            
                            projects.append({
                                'name': name,
                                'path': f'{projects_path}/{name}',
                                'has_requirements': has_requirements
                            })
            
            return jsonify({
                'success': True,
                'projects': projects,
                'count': len(projects),
                'server': server
            }), 200
        
        except Exception as e:
            logger.error(f"列出项目时出错: {str(e)}", exc_info=True)
            return jsonify({
                'success': False,
                'error': f'服务器内部错误: {str(e)}',
                'projects': []
            }), 500
        
        finally:
            if ssh_client:
                try:
                    ssh_client.close()
                except:
                    pass
    
    except Exception as e:
        logger.error(f"处理请求时出错: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'服务器内部错误: {str(e)}',
            'projects': []
        }), 500


@app.route('/project/execute/async', methods=['POST'])
def execute_project_async():
    """
    异步执行项目算法，立即返回，日志写入Server104的log文件
    
    请求参数（JSON）:
        - username: 用户名
        - projectname: 项目名称
        - env_name: 虚拟环境名称（必需）
        - command: 要执行的命令
        - server: 执行服务器（默认为 server102）
    
    返回:
        JSON格式的响应，包含log文件路径和执行状态
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': '请求体必须为JSON格式'
            }), 400
        
        username = data.get('username')
        projectname = data.get('projectname')
        env_name = data.get('env_name')
        command = data.get('command')
        # 固定使用 server102 执行，忽略传入的 server 参数
        # 项目代码从 server101 拷贝到 server102 执行
        server = 'server102'
        
        # 参数验证
        if not all([username, projectname, env_name, command]):
            return jsonify({
                'success': False,
                'error': '缺少必需参数'
            }), 400
        
        # 在Server104创建log文件路径
        output_path = f'/home/user/{username}/outputs/{projectname}'
        log_file = f'{output_path}/run_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
        # 本次运行名（例如 run_20251222_101517），用于在Server104上为每次执行单独建目录
        run_name = os.path.splitext(os.path.basename(log_file))[0]
        run_output_path = f'{output_path}/{run_name}'
        
        # 在后台线程中执行命令
        def run_command():
            # 同步执行逻辑：
            # - 项目代码源在 10.1（server101）：/home/user/{username}/projects/{projectname}
            # - 执行与虚拟环境在 10.2（server102）
            server101_config = SERVER_CONFIG['server101']
            server102_config = SERVER_CONFIG['server102']
            project_path_source = f'/home/user/{username}/projects/{projectname}'
            project_path = f'/home/user/{username}/projects/{projectname}'
            env_path = f'/home/user/{username}/envs/{env_name}'
            
            ssh_client = None
            ssh_server104 = None
            
            try:
                # 每次执行前，将项目从 server101 拷贝到 server102 对应目录
                logger.info(
                    f"[async] 从 server101 同步项目到 server102 以便执行: "
                    f"{server101_config['host']}:{project_path_source} "
                    f"-> {server102_config['host']}:{project_path}"
                )
                copy_success, copy_message = copy_folder_remote_to_remote(
                    server101_config['host'],
                    server101_config['port'],
                    server101_config['user'],
                    server101_config['password'],
                    project_path_source,
                    server102_config['host'],
                    server102_config['port'],
                    server102_config['user'],
                    server102_config['password'],
                    project_path
                )
                if not copy_success:
                    logger.error(f"[async] 从 server101 同步项目到 server102 失败: {copy_message}")
                    return

                # 连接执行服务器 server102
                ssh_client = paramiko.SSHClient()
                ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh_client.connect(
                    hostname=server102_config['host'],
                    port=server102_config['port'],
                    username=server102_config['user'],
                    password=server102_config['password'],
                    timeout=30
                )
                
                # 连接Server104创建输出目录
                server104_config = SERVER_CONFIG['server104']
                ssh_server104 = paramiko.SSHClient()
                ssh_server104.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh_server104.connect(
                    hostname=server104_config['host'],
                    port=server104_config['port'],
                    username=server104_config['user'],
                    password=server104_config['password'],
                    timeout=30
                )
                
                # 创建输出目录（项目输出根目录，按项目聚合）
                mkdir_cmd = f'mkdir -p "{output_path}"'
                execute_ssh_command(ssh_server104, mkdir_cmd, use_sudo=False)
                
                # 构建命令：激活虚拟环境，必要时安装依赖，再执行并将输出写入临时log文件
                temp_log = f'/tmp/run_{datetime.now().strftime("%Y%m%d_%H%M%S")}_{os.getpid()}.log'
                pip_cmd = (
                    f'if [ -f "requirements.txt" ]; then '
                    f'pip install -r requirements.txt --index-url http://192.168.10.2:8087/simple/ --trusted-host 192.168.10.2; '
                    f'fi'
                )
                # 先用 sudo 修正输出目录权限（作用于项目目录下的 output）
                fix_output_cmd = (
                    f'bash -lc \'cd "{project_path}" && '
                    f'mkdir -p output && chmod -R 775 output && '
                    f'chown -R {server102_config["user"]}:{server102_config["user"]} output\''
                )
                execute_ssh_command(ssh_client, fix_output_cmd, use_sudo=True)

                ensure_output_cmd = 'mkdir -p output && chmod -R 775 output || true'
                full_command = (
                    f'cd "{project_path}" && '
                    f'{ensure_output_cmd} && '
                    f'source "{env_path}/bin/activate" && '
                    f'{pip_cmd} && '
                    f'({command} 2>&1 | tee "{temp_log}")'
                )
                
                logger.info(f"异步执行项目算法: 项目={projectname}, 环境={env_name}, 命令={command}, log={log_file}")
                
                # 执行命令
                success, stdout, stderr = execute_ssh_command(ssh_client, full_command, use_sudo=False)
                
                # 读取临时log文件内容
                log_content = stdout + (stderr if stderr else '')
                try:
                    sftp_client = ssh_client.open_sftp()
                    try:
                        # 尝试读取临时log文件（如果存在）
                        with sftp_client.open(temp_log, 'r') as f:
                            log_content = f.read().decode('utf-8', errors='ignore')
                    except IOError:
                        # 如果文件不存在，使用stdout/stderr
                        pass
                    finally:
                        sftp_client.close()
                except Exception as e:
                    logger.warning(f"读取临时log文件失败: {str(e)}")
                
                # 保存log到Server104
                if log_content:
                    try:
                        sftp_server104 = ssh_server104.open_sftp()
                        try:
                            # 确保目录存在
                            mkdir_cmd = f'mkdir -p "{output_path}"'
                            execute_ssh_command(ssh_server104, mkdir_cmd, use_sudo=False)
                            
                            # 写入log文件
                            with sftp_server104.open(log_file, 'w') as f:
                                f.write(log_content)
                        finally:
                            sftp_server104.close()
                    except Exception as e:
                        logger.warning(f"保存log文件到Server104失败: {str(e)}")
                
                # 清理临时文件
                try:
                    rm_cmd = f'rm -f "{temp_log}"'
                    execute_ssh_command(ssh_client, rm_cmd, use_sudo=False)
                except:
                    pass
                
                # 执行完成后，将整个项目目录传输到Server104的outputs目录，按运行名归档：output/项目名/运行名/
                # Server104的outputs目录等同于其他服务器的projects目录
                logger.info(
                    f"开始传输项目到Server104: {server102_config['host']}:{project_path} "
                    f"-> {server104_config['host']}:{run_output_path}"
                )
                
                try:
                    # 使用远程到远程复制函数传输整个项目目录
                    copy_success, copy_message = copy_folder_remote_to_remote(
                        server102_config['host'],      # 源服务器（执行服务器 server102）
                        server102_config['port'],
                        server102_config['user'],
                        server102_config['password'],
                        project_path,               # 源路径
                    server104_config['host'],   # 目标服务器（Server104）
                    server104_config['port'],
                    server104_config['user'],
                    server104_config['password'],
                    run_output_path             # 目标路径（按运行名的子目录）
                    )
                    
                    if copy_success:
                        logger.info(f"项目传输成功: {copy_message}")
                    else:
                        logger.warning(f"项目传输失败: {copy_message}")
                except Exception as e:
                    logger.error(f"传输项目到Server104时出错: {str(e)}", exc_info=True)
                
            except Exception as e:
                logger.error(f"异步执行出错: {str(e)}", exc_info=True)
            finally:
                if ssh_client:
                    try:
                        ssh_client.close()
                    except:
                        pass
                if ssh_server104:
                    try:
                        ssh_server104.close()
                    except:
                        pass
        
        # 启动后台线程
        thread = threading.Thread(target=run_command)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'success': True,
            'message': '算法已开始执行',
            'log_file': log_file,
            'status': 'running'
        }), 200
    
    except Exception as e:
        logger.error(f"处理请求时出错: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'服务器内部错误: {str(e)}'
        }), 500


@app.route('/project/log', methods=['POST'])
def get_project_log():
    """
    获取项目的log文件内容
    
    请求参数（JSON）:
        - username: 用户名
        - projectname: 项目名称
        - log_file: log文件名（可选，不提供则返回最新的log）
    
    返回:
        JSON格式的响应，包含log内容
    """
    try:
        data = request.get_json() or {}
        username = data.get('username')
        projectname = data.get('projectname')
        log_file = data.get('log_file')
        
        if not username or not projectname:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: username 或 projectname'
            }), 400
        
        server104_config = SERVER_CONFIG['server104']
        output_path = f'/home/user/{username}/outputs/{projectname}'
        
        ssh_client = None
        try:
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh_client.connect(
                hostname=server104_config['host'],
                port=server104_config['port'],
                username=server104_config['user'],
                password=server104_config['password'],
                timeout=30
            )
            
            if not log_file:
                # 获取最新的log文件
                list_cmd = f'ls -t "{output_path}"/*.log 2>/dev/null | head -1'
                success, stdout, _ = execute_ssh_command(ssh_client, list_cmd, use_sudo=False)
                if success and stdout.strip():
                    log_file = stdout.strip()
                else:
                    return jsonify({
                        'success': False,
                        'error': '未找到log文件'
                    }), 404
            
            # 读取log文件内容
            read_cmd = f'cat "{log_file}" 2>&1'
            success, content, stderr = execute_ssh_command(ssh_client, read_cmd, use_sudo=False)
            
            if success:
                return jsonify({
                    'success': True,
                    'log_file': log_file,
                    'content': content
                }), 200
            else:
                return jsonify({
                    'success': False,
                    'error': f'读取log文件失败: {stderr}',
                    'log_file': log_file
                }), 500
        
        except Exception as e:
            logger.error(f"获取log文件出错: {str(e)}", exc_info=True)
            return jsonify({
                'success': False,
                'error': f'服务器内部错误: {str(e)}'
            }), 500
        
        finally:
            if ssh_client:
                try:
                    ssh_client.close()
                except:
                    pass
    
    except Exception as e:
        logger.error(f"处理请求时出错: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'服务器内部错误: {str(e)}'
        }), 500


@app.route('/test', methods=['GET'])
def test_page():
    """测试页面"""
    return render_template('test.html')


@app.route('/user/create', methods=['POST'])
def create_user():
    """
    在四台服务器上为指定用户名创建基础目录结构

    约定：
    - 所有服务器：/home/user/{username}/ 作为用户根目录
    - server101: /home/user/{username}/projects/
    - server102: /home/user/{username}/projects/ 与 /home/user/{username}/envs/
    - server103: /home/user/{username}/projects/（如需本机存放临时文件可用）
    - server104: /home/user/{username}/outputs/
    """
    try:
        data = request.get_json() or {}
        username = data.get('username')

        if not username:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: username'
            }), 400

        # 各服务器需要创建的目录
        user_root = f'/home/user/{username}'
        server_dirs = {
            'server101': [
                user_root,
                f'{user_root}/projects',
            ],
            'server102': [
                user_root,
                f'{user_root}/projects',
                f'{user_root}/envs',
            ],
            'server103': [
                user_root,
                f'{user_root}/projects',
            ],
            'server104': [
                user_root,
                f'{user_root}/outputs',
            ],
        }

        results = {}
        for server_name, dirs in server_dirs.items():
            config = SERVER_CONFIG.get(server_name)
            if not config:
                results[server_name] = '跳过（未配置）'
                continue

            ssh_client = None
            try:
                ssh_client = paramiko.SSHClient()
                ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh_client.connect(
                    hostname=config['host'],
                    port=config['port'],
                    username=config['user'],
                    password=config['password'],
                    timeout=30
                )

                # 依次创建目录并设置权限
                for d in dirs:
                    ok = ensure_remote_directory(ssh_client, d, use_sudo=False)
                    if not ok:
                        raise RuntimeError(f'创建目录失败: {d}')

                results[server_name] = 'ok'
            except Exception as e:
                logger.error(f"在 {server_name} 上创建用户目录失败: {str(e)}", exc_info=True)
                results[server_name] = f'error: {str(e)}'
            finally:
                if ssh_client:
                    try:
                        ssh_client.close()
                    except:
                        pass

        # 判断是否全部成功
        all_ok = all(v == 'ok' for v in results.values())
        status_code = 200 if all_ok else 207  # 207: 部分成功

        return jsonify({
            'success': all_ok,
            'username': username,
            'details': results,
            'message': '用户基础目录创建完成' if all_ok else '部分服务器创建失败，请查看 details'
        }), status_code

    except Exception as e:
        logger.error(f"创建用户目录时出错: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'服务器内部错误: {str(e)}'
        }), 500


if __name__ == '__main__':
    # 开发环境运行
    app.run(host='0.0.0.0', port=5003, debug=True)
