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
    'server101': {'host': '192.168.140.201', 'port': 22, 'user': 'user', 'password': '1234567'},
    'server102': {'host': '192.168.140.202', 'port': 22, 'user': 'user', 'password': '1234567'},
    'server103': {'host': '192.168.140.203', 'port': 22, 'user': 'user', 'password': '1234567'},
    'server104': {'host': '192.168.140.204', 'port': 22, 'user': 'user', 'password': '1234567'},
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
        chmod_cmd = f'chmod 777 "{remote_path}"'
        execute_ssh_command(ssh_client, chmod_cmd, use_sudo)
        
        return True
    
    except Exception as e:
        logger.error(f"确保远程目录失败: {str(e)}", exc_info=True)
        return False


def copy_file_remote_to_remote(source_host, source_port, source_user, source_password, source_path,
                                target_host, target_port, target_user, target_password, target_path):
    """
    使用 paramiko SFTP 复制单个文件（远程到远程）
    
    Args:
        source_host: 源服务器IP
        source_port: 源服务器SSH端口
        source_user: 源服务器用户名
        source_password: 源服务器密码
        source_path: 源文件路径
        target_host: 目标服务器IP
        target_port: 目标服务器SSH端口
        target_user: 目标服务器用户名
        target_password: 目标服务器密码
        target_path: 目标文件路径
    
    Returns:
        tuple: (success: bool, message: str)
    """
    ssh_source = None
    ssh_target = None
    sftp_source = None
    sftp_target = None
    
    try:
        logger.info(
            f"[copy_file_remote_to_remote] 开始远程文件复制: "
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

        # 确保目标目录存在
        target_dir = os.path.dirname(target_path)
        if target_dir:
            parts = target_dir.split('/')
            cur = ''
            for p in parts:
                if not p:
                    continue
                cur += f'/{p}'
                try:
                    sftp_target.stat(cur)
                except IOError:
                    try:
                        sftp_target.mkdir(cur)
                    except Exception as e:
                        logger.warning(f"[copy_file_remote_to_remote] 创建目录失败 {cur}: {e}")

        # 复制文件
        logger.info(f"[copy_file_remote_to_remote] 复制文件: {source_path} -> {target_path}")
        with sftp_source.open(source_path, 'rb') as f_src, sftp_target.open(target_path, 'wb') as f_dst:
            while True:
                chunk = f_src.read(32768)
                if not chunk:
                    break
                f_dst.write(chunk)

        return True, f"文件传输成功: {source_host}:{source_path} -> {target_host}:{target_path}"
    
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

def copy_multiple_remote_to_remote(source_host, source_port, source_user, source_password, source_paths,
                                   target_host, target_port, target_user, target_password, target_path):
    """
    从源服务器复制多个文件或目录到目标服务器的指定目录
    
    Args:
        source_host: 源服务器IP
        source_port: 源服务器SSH端口
        source_user: 源服务器用户名
        source_password: 源服务器密码
        source_paths: 源路径列表（可以是文件或目录）
        target_host: 目标服务器IP
        target_port: 目标服务器SSH端口
        target_user: 目标服务器用户名
        target_password: 目标服务器密码
        target_path: 目标目录路径（如果存在则先清空，不存在则创建）
    
    Returns:
        tuple: (success: bool, message: str, details: list)
    """
    ssh_source = None
    ssh_target = None
    sftp_source = None
    sftp_target = None
    
    try:
        logger.info(
            f"[copy_multiple_remote_to_remote] 开始多文件/目录传输: "
            f"{source_user}@{source_host}:{source_port} -> "
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
        target_path_clean = target_path.rstrip('/')
        
        def sftp_mkdir_p(sftp, remote_path):
            """递归创建远程目录"""
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
                        logger.warning(f"[copy_multiple_remote_to_remote] 创建目录失败 {cur}: {e}")
        
        def sftp_rmtree(sftp, remote_path, ssh_client=None):
            """递归删除远程目录（如果不存在则忽略）"""
            try:
                for entry in sftp.listdir_attr(remote_path):
                    name = entry.filename
                    if name in ('.', '..'):
                        continue
                    full = f"{remote_path.rstrip('/')}/{name}"
                    try:
                        if stat.S_ISDIR(entry.st_mode):
                            sftp_rmtree(sftp, full, ssh_client)
                        else:
                            sftp.remove(full)
                    except Exception as e:
                        # SFTP删除失败，尝试使用sudo通过SSH命令删除
                        if ssh_client:
                            try:
                                if stat.S_ISDIR(entry.st_mode):
                                    rm_cmd = f'rm -rf "{full}"'
                                else:
                                    rm_cmd = f'rm -f "{full}"'
                                success, stdout, stderr = execute_ssh_command(ssh_client, rm_cmd, use_sudo=True)
                                if success:
                                    logger.info(f"[copy_multiple_remote_to_remote] 使用sudo成功删除: {full}")
                                    continue  # 成功删除，继续下一个
                                else:
                                    logger.warning(f"[copy_multiple_remote_to_remote] 使用sudo删除失败 {full}: {stderr}")
                            except Exception as sudo_e:
                                logger.warning(f"[copy_multiple_remote_to_remote] 使用sudo删除时出错 {full}: {str(sudo_e)}")
                        # 如果ssh_client不存在或sudo删除也失败，重新抛出异常
                        logger.warning(f"[copy_multiple_remote_to_remote] 删除文件/目录失败 {full}: {str(e)}")
                        raise  # 重新抛出异常，让上层处理
                sftp.rmdir(remote_path)
            except IOError:
                # 目录不存在，忽略
                return
            except Exception as e:
                # 如果删除目录失败，尝试使用sudo
                if ssh_client:
                    try:
                        rm_cmd = f'rm -rf "{remote_path}"'
                        success, stdout, stderr = execute_ssh_command(ssh_client, rm_cmd, use_sudo=True)
                        if success:
                            logger.info(f"[copy_multiple_remote_to_remote] 使用sudo成功删除目录: {remote_path}")
                            return  # 成功删除，返回
                        else:
                            logger.warning(f"[copy_multiple_remote_to_remote] 使用sudo删除目录失败 {remote_path}: {stderr}")
                    except Exception as sudo_e:
                        logger.warning(f"[copy_multiple_remote_to_remote] 使用sudo删除目录时出错 {remote_path}: {str(sudo_e)}")
                # 其他错误（如权限不足）重新抛出，让上层处理
                logger.warning(f"[copy_multiple_remote_to_remote] 删除目录失败 {remote_path}: {str(e)}")
                raise
        
        def sftp_copy_file(src_sftp, src_path, dst_sftp, dst_path):
            """复制单个文件"""
            logger.info(f"[copy_multiple_remote_to_remote] 复制文件: {src_path} -> {dst_path}")
            # 确保目标文件的父目录存在
            parent_dir = '/'.join(dst_path.rstrip('/').split('/')[:-1])
            if parent_dir:
                sftp_mkdir_p(dst_sftp, parent_dir)
            
            with src_sftp.open(src_path, 'rb') as f_src, dst_sftp.open(dst_path, 'wb') as f_dst:
                while True:
                    chunk = f_src.read(32768)
                    if not chunk:
                        break
                    f_dst.write(chunk)
        
        def sftp_copy_dir(src_sftp, src, dst_sftp, dst):
            """递归复制目录"""
            logger.info(f"[copy_multiple_remote_to_remote] 复制目录: {src} -> {dst}")
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
                    logger.info(f"[copy_multiple_remote_to_remote] 复制文件: {src_item} -> {dst_item}")
                    parent = '/'.join(dst_item.rstrip('/').split('/')[:-1])
                    sftp_mkdir_p(dst_sftp, parent)
                    with src_sftp.open(src_item, 'rb') as f_src, dst_sftp.open(dst_item, 'wb') as f_dst:
                        while True:
                            chunk = f_src.read(32768)
                            if not chunk:
                                break
                            f_dst.write(chunk)
        
        # 确保目标目录存在
        sftp_mkdir_p(sftp_target, target_path_clean)
        
        # 先尝试用 sudo 把目标目录改成「谁都能写」，不关心原来 owner 是谁
        try:
            chmod_cmd = (
                f'mkdir -p "{target_path_clean}" && '
                f'chmod -R 777 "{target_path_clean}"'
            )
            execute_ssh_command(ssh_target, chmod_cmd, use_sudo=True)
            logger.info(f"[copy_multiple_remote_to_remote] 已将目标目录设置为可写: {target_path_clean}")
        except Exception as e:
            logger.warning(f"[copy_multiple_remote_to_remote] 修改目标目录权限失败（继续尝试传输）: {str(e)}")
        
        # 如果目标目录存在且不为空，先清空
        try:
            entries = sftp_target.listdir_attr(target_path_clean)
            # 过滤掉 . 和 ..，检查是否真的不为空
            real_entries = [e for e in entries if e.filename not in ('.', '..')]
            if real_entries:
                logger.info(f"[copy_multiple_remote_to_remote] 目标目录不为空，开始清空: {target_path_clean}")
                for entry in real_entries:
                    name = entry.filename
                    full = f"{target_path_clean.rstrip('/')}/{name}"
                    if stat.S_ISDIR(entry.st_mode):
                        sftp_rmtree(sftp_target, full)
                    else:
                        sftp_target.remove(full)
                logger.info(f"[copy_multiple_remote_to_remote] 目标目录已清空")
        except IOError:
            # 目录不存在或为空，继续
            pass
        
        # 处理每个源路径
        results = []
        for source_path in source_paths:
            source_path_clean = source_path.rstrip('/')
            
            try:
                # 检查源路径是否存在
                try:
                    source_stat = sftp_source.stat(source_path_clean)
                    is_dir = stat.S_ISDIR(source_stat.st_mode)
                except (IOError, PermissionError) as perm_e:
                    # SFTP权限不足，尝试使用sudo通过SSH命令检查
                    logger.warning(f"[copy_multiple_remote_to_remote] SFTP访问源路径权限不足 {source_path_clean}: {str(perm_e)}，尝试使用sudo检查")
                    # 先尝试修改源路径权限
                    chmod_cmd = f'chmod 644 "{source_path_clean}" 2>/dev/null || chmod -R 755 "{source_path_clean}" 2>/dev/null || true'
                    execute_ssh_command(ssh_source, chmod_cmd, use_sudo=True)
                    # 再次尝试SFTP访问
                    try:
                        source_stat = sftp_source.stat(source_path_clean)
                        is_dir = stat.S_ISDIR(source_stat.st_mode)
                        logger.info(f"[copy_multiple_remote_to_remote] 使用sudo修改权限后成功访问源路径: {source_path_clean}")
                    except Exception:
                        # 如果还是失败，使用SSH命令检查文件是否存在
                        check_cmd = f'test -f "{source_path_clean}" && echo "file" || (test -d "{source_path_clean}" && echo "dir" || echo "not_exists")'
                        success, stdout, stderr = execute_ssh_command(ssh_source, check_cmd, use_sudo=True)
                        if success and stdout.strip() == "file":
                            is_dir = False
                            # 创建一个假的stat对象用于后续处理
                            class FakeStat:
                                st_mode = 0o100644
                            source_stat = FakeStat()
                        elif success and stdout.strip() == "dir":
                            is_dir = True
                            class FakeStat:
                                st_mode = 0o040755
                            source_stat = FakeStat()
                        else:
                            raise IOError(f"源路径不存在或无法访问: {source_path_clean}")
                
                # 获取源路径的基名（文件名或目录名）
                source_basename = os.path.basename(source_path_clean) if source_path_clean != '/' else ''
                if not source_basename:
                    # 如果路径是根目录，使用路径的最后一部分
                    parts = [p for p in source_path_clean.split('/') if p]
                    source_basename = parts[-1] if parts else 'root'
                
                # 构建目标路径
                if is_dir:
                    target_item_path = f"{target_path_clean}/{source_basename}"
                    # 复制目录
                    sftp_copy_dir(sftp_source, source_path_clean, sftp_target, target_item_path)
                    results.append({
                        'source': source_path_clean,
                        'target': target_item_path,
                        'type': 'directory',
                        'success': True,
                        'message': f'目录传输成功'
                    })
                else:
                    target_item_path = f"{target_path_clean}/{source_basename}"
                    # 复制文件
                    try:
                        sftp_copy_file(sftp_source, source_path_clean, sftp_target, target_item_path)
                        results.append({
                            'source': source_path_clean,
                            'target': target_item_path,
                            'type': 'file',
                            'success': True,
                            'message': f'文件传输成功'
                        })
                    except (IOError, PermissionError) as copy_e:
                        # SFTP复制失败，可能是权限问题，尝试使用sudo修改权限后再次复制
                        logger.warning(f"[copy_multiple_remote_to_remote] SFTP复制文件失败 {source_path_clean}: {str(copy_e)}，尝试使用sudo修改权限后复制")
                        try:
                            # 先修改源文件权限，确保可以读取
                            chmod_source_cmd = f'chmod 644 "{source_path_clean}"'
                            chmod_success, _, chmod_err = execute_ssh_command(ssh_source, chmod_source_cmd, use_sudo=True)
                            if not chmod_success:
                                logger.warning(f"[copy_multiple_remote_to_remote] 修改源文件权限失败: {chmod_err}")
                            
                            # 修改权限后，再次尝试使用SFTP复制
                            try:
                                sftp_copy_file(sftp_source, source_path_clean, sftp_target, target_item_path)
                                results.append({
                                    'source': source_path_clean,
                                    'target': target_item_path,
                                    'type': 'file',
                                    'success': True,
                                    'message': f'文件传输成功（使用sudo修改权限后）'
                                })
                                logger.info(f"[copy_multiple_remote_to_remote] 使用sudo修改权限后成功复制文件: {source_path_clean} -> {target_item_path}")
                            except Exception as retry_e:
                                # 如果SFTP仍然失败，使用scp或dd命令通过SSH传输
                                logger.warning(f"[copy_multiple_remote_to_remote] 修改权限后SFTP仍失败: {str(retry_e)}，尝试使用scp传输")
                                
                                # 使用scp从源服务器复制到目标服务器
                                # 由于是远程到远程，我们需要先复制到本地临时文件，然后再上传
                                # 或者使用ssh+dd的方式
                                
                                # 方案：使用base64编码，分块传输（避免大文件问题）
                                import base64
                                
                                # 先获取文件大小
                                size_cmd = f'stat -c %s "{source_path_clean}"'
                                size_success, size_out, _ = execute_ssh_command(ssh_source, size_cmd, use_sudo=True)
                                file_size = int(size_out.strip()) if size_success and size_out.strip() else 0
                                
                                # 如果文件太大（>10MB），使用分块传输
                                if file_size > 10 * 1024 * 1024:
                                    raise Exception(f"文件过大（{file_size}字节），请使用其他方式传输")
                                
                                # 读取文件内容（base64编码）
                                read_cmd = f'cat "{source_path_clean}" | base64'
                                read_success, read_stdout, read_stderr = execute_ssh_command(ssh_source, read_cmd, use_sudo=True)
                                
                                if not read_success:
                                    raise Exception(f"使用sudo读取文件失败: {read_stderr}")
                                
                                if not read_stdout or not read_stdout.strip():
                                    raise Exception("读取到的文件内容为空")
                                
                                # 解码base64内容
                                try:
                                    # 清理stdout，移除可能的空白字符
                                    base64_data = read_stdout.strip().replace('\n', '').replace('\r', '')
                                    file_content = base64.b64decode(base64_data)
                                    
                                    # 确保目标目录存在
                                    parent_dir = '/'.join(target_item_path.rstrip('/').split('/')[:-1])
                                    if parent_dir:
                                        sftp_mkdir_p(sftp_target, parent_dir)
                                    
                                    # 确保所有父目录存在且有写权限
                                    parent_dir = '/'.join(target_item_path.rstrip('/').split('/')[:-1])
                                    if parent_dir:
                                        mkdir_cmd = f'mkdir -p "{parent_dir}" && chmod -R 777 "{parent_dir}"'
                                        execute_ssh_command(ssh_target, mkdir_cmd, use_sudo=True)
                                    
                                    # 尝试写入目标文件
                                    try:
                                        with sftp_target.open(target_item_path, 'wb') as f_dst:
                                            f_dst.write(file_content)
                                    except (IOError, PermissionError) as write_e:
                                        # SFTP写入失败，使用sudo通过SSH命令写入
                                        logger.warning(f"[copy_multiple_remote_to_remote] SFTP写入失败，使用sudo写入: {str(write_e)}")
                                        
                                        # 使用base64编码后通过SSH写入
                                        base64_content = base64.b64encode(file_content).decode('utf-8')
                                        # 使用临时文件然后移动，更可靠
                                        import time
                                        temp_file = f'/tmp/tmp_transfer_{int(time.time() * 1000000)}_{abs(hash(target_item_path)) % 10000}.bin'
                                        
                                        # 方法1：尝试使用printf写入base64（适用于小文件）
                                        if len(base64_content) < 100000:  # 小于100KB的base64内容
                                            # 转义特殊字符
                                            escaped_content = base64_content.replace('\\', '\\\\').replace('$', '\\$').replace('`', '\\`').replace('"', '\\"')
                                            write_cmd = (
                                                f'printf "%s" "{escaped_content}" | base64 -d > "{temp_file}" && '
                                                f'chmod 666 "{temp_file}" && '
                                                f'mv "{temp_file}" "{target_item_path}" && '
                                                f'chmod 644 "{target_item_path}"'
                                            )
                                        else:
                                            # 方法2：对于大文件，使用heredoc（通过SSH执行）
                                            # 先写入base64文件
                                            b64_file = f'{temp_file}.b64'
                                            write_b64_cmd = f'cat > "{b64_file}" << \'EOFB64\'\n{base64_content}\nEOFB64'
                                            b64_success, _, b64_err = execute_ssh_command(ssh_target, write_b64_cmd, use_sudo=True)
                                            if not b64_success:
                                                raise Exception(f"写入base64临时文件失败: {b64_err}")
                                            
                                            # 解码并移动
                                            write_cmd = (
                                                f'base64 -d "{b64_file}" > "{temp_file}" && '
                                                f'rm -f "{b64_file}" && '
                                                f'chmod 666 "{temp_file}" && '
                                                f'mv "{temp_file}" "{target_item_path}" && '
                                                f'chmod 644 "{target_item_path}"'
                                            )
                                        
                                        write_success, write_stdout, write_stderr = execute_ssh_command(ssh_target, write_cmd, use_sudo=True)
                                        if not write_success:
                                            # 清理临时文件
                                            try:
                                                execute_ssh_command(ssh_target, f'rm -f "{temp_file}" "{temp_file}.b64"', use_sudo=True)
                                            except:
                                                pass
                                            error_msg = write_stderr.strip() if write_stderr and write_stderr.strip() else (write_stdout.strip() if write_stdout and write_stdout.strip() else "未知错误（请查看服务器日志）")
                                            logger.error(f"[copy_multiple_remote_to_remote] sudo写入失败详情 - stdout: {write_stdout}, stderr: {write_stderr}, cmd: {write_cmd[:200]}")
                                            raise Exception(f"使用sudo写入目标文件失败: {error_msg}")
                                    
                                    # 设置文件权限
                                    try:
                                        sftp_target.chmod(target_item_path, 0o644)
                                    except:
                                        chmod_target_cmd = f'chmod 644 "{target_item_path}"'
                                        execute_ssh_command(ssh_target, chmod_target_cmd, use_sudo=True)
                                    
                                    results.append({
                                        'source': source_path_clean,
                                        'target': target_item_path,
                                        'type': 'file',
                                        'success': True,
                                        'message': f'文件传输成功（使用sudo）'
                                    })
                                    logger.info(f"[copy_multiple_remote_to_remote] 使用sudo成功复制文件: {source_path_clean} -> {target_item_path}")
                                except base64.binascii.Error as decode_e:
                                    raise Exception(f"Base64解码失败: {str(decode_e)}")
                                except Exception as decode_e:
                                    logger.error(f"[copy_multiple_remote_to_remote] 处理文件内容时出错: {str(decode_e)}", exc_info=True)
                                    raise Exception(f"处理文件内容失败: {str(decode_e)}")
                        except Exception as sudo_e:
                            logger.error(f"[copy_multiple_remote_to_remote] 使用sudo复制文件时出错: {str(sudo_e)}", exc_info=True)
                            results.append({
                                'source': source_path_clean,
                                'target': target_item_path,
                                'type': 'file',
                                'success': False,
                                'message': f'文件传输失败: {str(sudo_e)}'
                            })
                
            except IOError as e:
                # 源路径不存在
                results.append({
                    'source': source_path_clean,
                    'target': None,
                    'type': 'unknown',
                    'success': False,
                    'message': f'源路径不存在: {str(e)}'
                })
            except Exception as e:
                logger.error(f"传输 {source_path_clean} 时出错: {str(e)}", exc_info=True)
                results.append({
                    'source': source_path_clean,
                    'target': None,
                    'type': 'unknown',
                    'success': False,
                    'message': f'传输失败: {str(e)}'
                })
        
        # 检查是否有失败的传输
        failed_count = sum(1 for r in results if not r['success'])
        success_count = len(results) - failed_count
        
        # 校验目标目录
        list_cmd = f'ls -la "{target_path_clean}" 2>&1'
        ok, out, err = execute_ssh_command(ssh_target, list_cmd, use_sudo=False)
        debug_info = out if ok else err
        
        if failed_count == 0:
            return True, (
                f"所有文件/目录传输成功 ({success_count}/{len(results)})\n"
                f"目标目录: {target_path_clean}\n"
                f"目标目录内容:\n{debug_info}"
            ), results
        else:
            return False, (
                f"部分文件/目录传输失败 (成功: {success_count}/{len(results)})\n"
                f"目标目录: {target_path_clean}\n"
                f"目标目录内容:\n{debug_info}"
            ), results
    
    except paramiko.AuthenticationException:
        return False, "SSH认证失败: 用户名或密码错误", []
    except paramiko.SSHException as e:
        return False, f"SSH连接错误: {str(e)}", []
    except Exception as e:
        logger.error(f"传输过程出错: {str(e)}", exc_info=True)
        return False, f"传输过程出错: {str(e)}", []
    
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
        if not ensure_remote_directory(ssh_client, target_path, use_sudo=False):
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
                        success, _, _ = execute_ssh_command(ssh_client, mkdir_cmd, use_sudo=False)
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
                            success, _, stderr = execute_ssh_command(ssh_client, mv_cmd, use_sudo=False)
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


@app.route('/transfer/multi', methods=['POST'])
def transfer_multiple():
    """
    多文件/目录传输接口
    
    请求参数（JSON）:
        - source_server: 源服务器名称（server101/server102/server103/server104）
        - source_paths: 源路径列表（可以是文件或目录），例如: ["/path/to/file1", "/path/to/dir1"]
        - target_server: 目标服务器名称（server101/server102/server103/server104）
        - target_path: 目标目录路径（如果存在则先清空，不存在则创建）
    
    返回:
        JSON格式的响应，包含传输结果和详细信息
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': '请求体必须为JSON格式'
            }), 400
        
        # 获取参数
        source_server = data.get('source_server')
        source_paths = data.get('source_paths')
        target_server = data.get('target_server')
        target_path = data.get('target_path')
        
        # 参数验证
        if not source_server:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: source_server'
            }), 400
        
        if not source_paths:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: source_paths'
            }), 400
        
        if not isinstance(source_paths, list) or len(source_paths) == 0:
            return jsonify({
                'success': False,
                'error': 'source_paths 必须是非空列表'
            }), 400
        
        if not target_server:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: target_server'
            }), 400
        
        if not target_path:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: target_path'
            }), 400
        
        # 验证服务器
        if source_server not in SERVER_CONFIG:
            return jsonify({
                'success': False,
                'error': f'无效的源服务器: {source_server}。可用服务器: {", ".join(SERVER_CONFIG.keys())}'
            }), 400
        
        if target_server not in SERVER_CONFIG:
            return jsonify({
                'success': False,
                'error': f'无效的目标服务器: {target_server}。可用服务器: {", ".join(SERVER_CONFIG.keys())}'
            }), 400
        
        # 获取服务器配置
        source_config = SERVER_CONFIG[source_server]
        target_config = SERVER_CONFIG[target_server]
        
        # 执行传输
        logger.info(
            f"开始多文件/目录传输: {source_server} -> {target_server}, "
            f"源路径: {source_paths}, 目标路径: {target_path}"
        )
        
        success, message, details = copy_multiple_remote_to_remote(
            source_config['host'],
            source_config['port'],
            source_config['user'],
            source_config['password'],
            source_paths,
            target_config['host'],
            target_config['port'],
            target_config['user'],
            target_config['password'],
            target_path
        )
        
        if success:
            return jsonify({
                'success': True,
                'message': message,
                'source_server': source_server,
                'target_server': target_server,
                'target_path': target_path,
                'details': details
            }), 200
        else:
            return jsonify({
                'success': False,
                'error': message,
                'source_server': source_server,
                'target_server': target_server,
                'target_path': target_path,
                'details': details
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
                f'pip install -r requirements.txt --index-url http://192.168.140.202:8087/simple/ --trusted-host 192.168.140.202; '
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


@app.route('/project/execute/background', methods=['POST'])
def execute_project_background():
    """
    后台执行项目算法，返回进程ID（不进行拷贝操作）
    
    请求参数（JSON）:
        - username: 用户名
        - projectname: 项目名称
        - env_name: 虚拟环境名称（必需）
        - command: 要执行的命令（如: python xxx.py）
    
    返回:
        JSON格式的响应，包含进程ID和执行状态
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
        # - 执行与虚拟环境在 10.2（server102）
        server101_config = SERVER_CONFIG['server101']
        server102_config = SERVER_CONFIG['server102']
        target_host = server102_config['host']
        target_port = server102_config['port']
        target_user = server102_config['user']
        target_password = server102_config['password']

        # 构建路径
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
                    'pid': None
                }), 404

            # 每次执行前，将项目从 server101 拷贝到 server102 对应目录
            logger.info(
                f"[background] 从 server101 同步项目到 server102 以便执行: "
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
                    'pid': None
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
                    'pid': None
                }), 404
            
            # 先用 sudo 修正输出目录权限
            fix_output_cmd = (
                f'bash -lc \'cd "{project_path}" && '
                f'mkdir -p output && chmod -R 775 output && '
                f'chown -R {target_user}:{target_user} output\''
            )
            execute_ssh_command(ssh_client, fix_output_cmd, use_sudo=True)

            ensure_output_cmd = 'mkdir -p output && chmod -R 775 output || true'
            
            # 使用临时脚本 + nohup 后台执行，确保命令脱离会话并立刻返回PID
            # 日志文件命名为 {projectname}_{pid}.log
            temp_script = f'/tmp/run_{projectname}_{os.urandom(4).hex()}.sh'
            
            # 创建脚本内容，脚本内部会获取自己的PID并创建日志文件
            # 使用 exec 重定向确保所有输出都写入日志文件
            script_content = f'''#!/bin/bash
# 获取当前进程PID（使用 $$ 获取当前shell的PID）
MY_PID=$$
LOG_FILE="/tmp/{projectname}_$MY_PID.log"

# 将PID写入临时文件，方便外部读取（在重定向之前）
PID_FILE="/tmp/{projectname}_pid_$MY_PID.tmp"
echo "$MY_PID" > "$PID_FILE"

# 将输出重定向到日志文件（包括标准输出和标准错误）
# 使用 exec 确保所有后续命令的输出都写入日志文件
exec > "$LOG_FILE" 2>&1

# 输出开始信息
echo "=== 任务开始执行 ==="
echo "PID: $MY_PID"
echo "日志文件: $LOG_FILE"
echo "时间: $(date)"
echo ""

cd "{project_path}"
{ensure_output_cmd}
source "{env_path}/bin/activate"
if [ -f "requirements.txt" ]; then
    echo "安装依赖..."
    pip install -r requirements.txt --index-url http://192.168.140.202:8087/simple/ --trusted-host 192.168.140.202
    echo ""
fi
echo "执行命令: {command}"
echo ""
{command}
EXIT_CODE=$?
echo ""
echo "=== 任务执行完成 ==="
echo "退出码: $EXIT_CODE"
echo "时间: $(date)"
exit $EXIT_CODE
'''
            
            write_script_cmd = (
                f"cat > {temp_script} <<'EOF'\n{script_content}EOF\nchmod +x {temp_script}"
            )
            execute_ssh_command(ssh_client, write_script_cmd, use_sudo=False)

            # 使用 nohup 后台执行脚本
            # nohup 会确保进程在后台运行，即使SSH连接断开也不会终止
            # 脚本内部已经处理了输出重定向，所以这里不需要额外重定向
            background_command = f'nohup {temp_script} > /dev/null 2>&1 & echo $!'
            
            logger.info(f"[background] 后台执行项目算法: 项目={projectname}, 环境={env_name}, 命令={command}")
            
            # 直接通过 exec_command 获取 PID
            pid = None
            try:
                stdin_bg, stdout_bg, stderr_bg = ssh_client.exec_command(background_command, get_pty=False)
                pid_line = stdout_bg.readline().strip()
                stderr_text = stderr_bg.readline().strip()
                stdout_bg.channel.close()
                stderr_bg.channel.close()
                if pid_line and pid_line.isdigit():
                    pid = int(pid_line)
                    logger.info(f"[background] 从命令输出获取PID: {pid}")
                elif stderr_text:
                    logger.warning(f"[background] 未能获取PID，stderr: {stderr_text}")
            except Exception as e:
                logger.error(f"[background] 启动后台进程时出错: {str(e)}", exc_info=True)
            
            if not pid:
                # 如果无法从输出中获取，尝试通过 ps 命令查找脚本进程
                import time
                time.sleep(0.2)  # 等待进程启动
                find_pid_cmd = (
                    f'ps aux | grep "{temp_script}" | '
                    f'grep -v grep | grep -v "ps aux" | head -1 | awk \'{{print $2}}\''
                )
                pid_success, pid_stdout, _ = execute_ssh_command(ssh_client, find_pid_cmd, use_sudo=False)
                if pid_success and pid_stdout.strip():
                    try:
                        pid = int(pid_stdout.strip().split()[0])
                        logger.info(f"[background] 通过ps命令找到PID: {pid}")
                    except (ValueError, IndexError):
                        pid = None
            
            if not pid:
                return jsonify({
                    'success': False,
                    'error': '无法获取进程ID，进程可能启动失败',
                    'pid': None
                }), 500
            
            # 等待一小段时间，确保脚本开始执行并创建PID文件
            import time
            time.sleep(0.5)
            
            # 尝试从PID文件中读取脚本的实际PID（脚本内部使用 $$ 获取的PID）
            # 查找所有匹配的PID文件
            find_pid_file_cmd = f'ls -t /tmp/{projectname}_pid_*.tmp 2>/dev/null | head -1'
            pid_file_success, pid_file_stdout, _ = execute_ssh_command(ssh_client, find_pid_file_cmd, use_sudo=False)
            if pid_file_success and pid_file_stdout.strip():
                pid_file_path = pid_file_stdout.strip()
                read_pid_cmd = f'cat "{pid_file_path}" 2>/dev/null'
                read_pid_success, read_pid_stdout, _ = execute_ssh_command(ssh_client, read_pid_cmd, use_sudo=False)
                if read_pid_success and read_pid_stdout.strip():
                    try:
                        script_pid = int(read_pid_stdout.strip())
                        # 使用脚本的PID作为日志文件名
                        pid = script_pid
                        logger.info(f"[background] 从PID文件读取到脚本PID: {pid}")
                        # 清理PID文件
                        execute_ssh_command(ssh_client, f'rm -f "{pid_file_path}"', use_sudo=False)
                    except (ValueError, IndexError):
                        pass
            
            # 根据PID构建日志文件路径（日志文件在脚本内部使用 $$ 创建，命名为 {projectname}_{pid}.log）
            log_file = f'/tmp/{projectname}_{pid}.log'
            
            # 验证日志文件是否存在
            check_log_cmd = f'test -f "{log_file}" && echo "exists" || echo "not_exists"'
            log_check_success, log_check_stdout, _ = execute_ssh_command(ssh_client, check_log_cmd, use_sudo=False)
            if log_check_success and log_check_stdout.strip().lower() == 'exists':
                logger.info(f"[background] 日志文件已创建: {log_file}")
            else:
                # 如果日志文件不存在，尝试查找所有匹配的日志文件
                find_log_cmd = f'ls -t /tmp/{projectname}_*.log 2>/dev/null | head -1'
                log_find_success, log_find_stdout, _ = execute_ssh_command(ssh_client, find_log_cmd, use_sudo=False)
                if log_find_success and log_find_stdout.strip():
                    found_log_file = log_find_stdout.strip()
                    # 从日志文件名中提取PID
                    import re
                    match = re.search(rf'{projectname}_(\d+)\.log', found_log_file)
                    if match:
                        pid = int(match.group(1))
                        log_file = f'/tmp/{projectname}_{pid}.log'
                        logger.info(f"[background] 从日志文件名提取PID: {pid}, 日志文件: {log_file}")
                    else:
                        log_file = found_log_file
                        logger.info(f"[background] 找到日志文件: {log_file}")
                else:
                    logger.warning(f"[background] 日志文件可能尚未创建: {log_file}，将在稍后创建")
            
            logger.info(f"[background] 后台进程已启动，PID: {pid}, 日志文件: {log_file}")
            
            return jsonify({
                'success': True,
                'pid': pid,
                'log_file': log_file,
                'project': projectname,
                'env_name': env_name,
                'command': command,
                'project_path': project_path,
                'env_path': env_path,
                'message': f'后台进程已启动，进程ID: {pid}'
            }), 200
        
        except paramiko.AuthenticationException:
            return jsonify({
                'success': False,
                'error': 'SSH认证失败: 用户名或密码错误',
                'pid': None
            }), 401
        except paramiko.SSHException as e:
            return jsonify({
                'success': False,
                'error': f'SSH连接错误: {str(e)}',
                'pid': None
            }), 500
        except Exception as e:
            logger.error(f"执行项目算法时出错: {str(e)}", exc_info=True)
            return jsonify({
                'success': False,
                'error': f'服务器内部错误: {str(e)}',
                'pid': None
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
            'pid': None
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
                    f'pip install -r requirements.txt --index-url http://192.168.140.202:8087/simple/ --trusted-host 192.168.140.202; '
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


@app.route('/task/check_and_copy', methods=['POST'])
def check_task_and_copy():
    """
    检查任务进程状态并复制输出目录和日志文件
    
    请求参数（JSON）:
        - pid: 进程ID（10.2服务器上的进程ID）
        - username: 用户名
        - projectname: 算法项目名
        - taskid: 数据库中的任务ID
        - log_file: 日志文件路径（可选，如果不提供则根据项目名和PID自动查找：/tmp/{projectname}_{pid}.log）
    
    返回:
        JSON格式的响应，包含进程状态和复制结果
    
    逻辑：
        1. 查询10.2服务器上PID进程的状态（已完成/运行中）
        2. 如果已完成：
           - 检查10.4服务器的 /home/user/{username}/outputs/{projectname}/{taskid} 目录是否存在
           - 如果不存在，将10.2服务器的 /home/user/{username}/projects/{projectname} 目录
             拷贝到10.4服务器的 /home/user/{username}/outputs/{projectname}/{taskid}
           - 同时将指定的日志文件也复制到目标目录
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': '请求体必须为JSON格式'
            }), 400
        
        # 获取参数
        pid = data.get('pid')
        username = data.get('username')
        projectname = data.get('projectname')
        taskid = data.get('taskid')
        log_file = data.get('log_file')  # 可选的日志文件路径
        
        # 参数验证
        if not pid:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: pid'
            }), 400
        
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
        
        if not taskid:
            return jsonify({
                'success': False,
                'error': '缺少必需参数: taskid'
            }), 400
        
        # 获取服务器配置
        server102_config = SERVER_CONFIG['server102']
        server104_config = SERVER_CONFIG['server104']
        
        ssh_client_102 = None
        ssh_client_104 = None
        
        try:
            # 连接10.2服务器查询进程状态
            ssh_client_102 = paramiko.SSHClient()
            ssh_client_102.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh_client_102.connect(
                hostname=server102_config['host'],
                port=server102_config['port'],
                username=server102_config['user'],
                password=server102_config['password'],
                timeout=30
            )
            
            # 查询进程状态：使用 ps 命令检查进程是否存在
            # 如果进程存在且正在运行，返回"运行中"；如果不存在，返回"已完成"
            check_pid_cmd = f'ps -p {pid} > /dev/null 2>&1 && echo "running" || echo "completed"'
            success, stdout, stderr = execute_ssh_command(ssh_client_102, check_pid_cmd, use_sudo=False)
            
            if not success:
                return jsonify({
                    'success': False,
                    'error': f'查询进程状态失败: {stderr}',
                    'pid': pid
                }), 500
            
            process_status = stdout.strip().lower()
            
            # 判断进程状态
            if process_status == 'running':
                # 进程正在运行中
                return jsonify({
                    'success': True,
                    'status': 'running',
                    'message': '进程正在运行中，无需复制',
                    'pid': pid,
                    'username': username,
                    'projectname': projectname,
                    'taskid': taskid
                }), 200
            
            # 进程已完成，检查10.4服务器上的目录是否存在
            output_path = f'/home/user/{username}/outputs/{projectname}/{taskid}'
            
            # 连接10.4服务器
            ssh_client_104 = paramiko.SSHClient()
            ssh_client_104.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh_client_104.connect(
                hostname=server104_config['host'],
                port=server104_config['port'],
                username=server104_config['user'],
                password=server104_config['password'],
                timeout=30
            )
            
            # 检查目录是否存在
            check_dir_cmd = f'test -d "{output_path}" && echo "exists" || echo "not_exists"'
            success, stdout, stderr = execute_ssh_command(ssh_client_104, check_dir_cmd, use_sudo=False)
            
            if not success:
                return jsonify({
                    'success': False,
                    'error': f'检查目录状态失败: {stderr}',
                    'output_path': output_path
                }), 500
            
            dir_exists = stdout.strip().lower() == 'exists'
            
            if dir_exists:
                # 目录已存在，无需复制
                return jsonify({
                    'success': True,
                    'status': 'completed',
                    'message': '进程已完成，但输出目录已存在，无需复制',
                    'pid': pid,
                    'username': username,
                    'projectname': projectname,
                    'taskid': taskid,
                    'output_path': output_path,
                    'dir_exists': True
                }), 200
            
            # 目录不存在，需要从10.2服务器复制
            source_path = f'/home/user/{username}/projects/{projectname}'
            
            logger.info(
                f"开始复制目录: {server102_config['host']}:{source_path} -> "
                f"{server104_config['host']}:{output_path}"
            )
            
            # 使用远程到远程复制函数复制目录
            copy_success, copy_message = copy_folder_remote_to_remote(
                server102_config['host'],
                server102_config['port'],
                server102_config['user'],
                server102_config['password'],
                source_path,
                server104_config['host'],
                server104_config['port'],
                server104_config['user'],
                server104_config['password'],
                output_path
            )
            
            log_file_copied = None
            if copy_success:
                # 复制成功后，查找并复制日志文件
                # 如果提供了log_file参数，使用它；否则根据项目名和PID自动查找
                log_file_to_copy = log_file
                
                if not log_file_to_copy:
                    # 根据项目名和PID自动查找日志文件
                    # 日志文件命名规则：/tmp/{projectname}_{pid}.log
                    log_file_to_copy = f'/tmp/{projectname}_{pid}.log'
                    logger.info(f"根据项目名和PID自动查找日志文件: {log_file_to_copy}")
                
                if log_file_to_copy:
                    # 检查日志文件是否存在
                    check_log_cmd = f'test -f "{log_file_to_copy}" && echo "exists" || echo "not_exists"'
                    success, stdout, stderr = execute_ssh_command(ssh_client_102, check_log_cmd, use_sudo=False)
                    
                    if success and stdout.strip().lower() == 'exists':
                        # 复制日志文件到目标目录
                        log_filename = os.path.basename(log_file_to_copy)
                        log_target_path = f'{output_path}/{log_filename}'
                        
                        logger.info(f"开始复制日志文件: {log_file_to_copy} -> {log_target_path}")
                        
                        # 使用远程到远程文件复制函数
                        log_copy_success, log_copy_message = copy_file_remote_to_remote(
                            server102_config['host'],
                            server102_config['port'],
                            server102_config['user'],
                            server102_config['password'],
                            log_file_to_copy,
                            server104_config['host'],
                            server104_config['port'],
                            server104_config['user'],
                            server104_config['password'],
                            log_target_path
                        )
                        
                        if log_copy_success:
                            log_file_copied = {
                                'source': log_file_to_copy,
                                'target': log_target_path,
                                'filename': log_filename
                            }
                            logger.info(f"日志文件复制成功: {log_file_to_copy} -> {log_target_path}")
                        else:
                            logger.warning(f"日志文件复制失败: {log_copy_message}")
                    else:
                        logger.warning(f"日志文件不存在: {log_file_to_copy}")
                else:
                    logger.info("未找到日志文件，跳过日志文件复制")
            
            if copy_success:
                response_data = {
                    'success': True,
                    'status': 'completed',
                    'message': '进程已完成，目录复制成功',
                    'pid': pid,
                    'username': username,
                    'projectname': projectname,
                    'taskid': taskid,
                    'source_path': source_path,
                    'output_path': output_path,
                    'copy_message': copy_message
                }
                if log_file_copied:
                    response_data['log_file_copied'] = log_file_copied
                    response_data['message'] += f'，日志文件已复制'
                return jsonify(response_data), 200
            else:
                return jsonify({
                    'success': False,
                    'status': 'completed',
                    'error': f'进程已完成，但目录复制失败: {copy_message}',
                    'pid': pid,
                    'username': username,
                    'projectname': projectname,
                    'taskid': taskid,
                    'source_path': source_path,
                    'output_path': output_path
                }), 500
        
        except paramiko.AuthenticationException:
            return jsonify({
                'success': False,
                'error': 'SSH认证失败: 用户名或密码错误'
            }), 401
        except paramiko.SSHException as e:
            return jsonify({
                'success': False,
                'error': f'SSH连接错误: {str(e)}'
            }), 500
        except Exception as e:
            logger.error(f"检查任务并复制时出错: {str(e)}", exc_info=True)
            return jsonify({
                'success': False,
                'error': f'服务器内部错误: {str(e)}'
            }), 500
        
        finally:
            # 关闭SSH连接
            if ssh_client_102:
                try:
                    ssh_client_102.close()
                except:
                    pass
            if ssh_client_104:
                try:
                    ssh_client_104.close()
                except:
                    pass
    
    except Exception as e:
        logger.error(f"处理请求时出错: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'服务器内部错误: {str(e)}'
        }), 500


@app.route('/file/read', methods=['POST'])
def read_file():
    """
    读取远程服务器上的文件内容
    
    请求参数:
        server: 服务器名称 (server101/server102/server103/server104)
        path: 文件完整路径
    
    返回:
        success: 是否成功
        content: 文件内容（字符串）
        path: 文件路径
        server: 服务器名称
    """
    try:
        data = request.get_json()
        server = data.get('server', CURRENT_SERVER)
        file_path = data.get('path', '')
        
        if not file_path:
            return jsonify({
                'success': False,
                'error': '文件路径不能为空'
            }), 400
        
        # 获取服务器配置
        if server not in SERVER_CONFIG:
            return jsonify({
                'success': False,
                'error': f'未知的服务器: {server}'
            }), 400
        
        config = SERVER_CONFIG[server]
        
        ssh_client = None
        sftp_client = None
        
        try:
            # 连接服务器
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh_client.connect(
                hostname=config['host'],
                port=config['port'],
                username=config['user'],
                password=config['password'],
                timeout=30
            )
            
            sftp_client = ssh_client.open_sftp()
            
            # 检查文件是否存在
            try:
                file_stat = sftp_client.stat(file_path)
                # 检查是否是文件（不是目录）
                if stat.S_ISDIR(file_stat.st_mode):
                    return jsonify({
                        'success': False,
                        'error': f'指定路径是目录，不是文件: {file_path}'
                    }), 400
            except IOError:
                return jsonify({
                    'success': False,
                    'error': f'文件不存在: {file_path}'
                }), 404
            
            # 读取文件内容
            try:
                with sftp_client.open(file_path, 'r') as f:
                    content = f.read()
                    # 尝试解码为字符串
                    if isinstance(content, bytes):
                        content = content.decode('utf-8', errors='replace')
            except PermissionError:
                # 尝试使用 sudo 读取
                logger.warning(f"SFTP 读取权限不足，尝试使用 sudo: {file_path}")
                read_cmd = f'cat "{file_path}"'
                success, stdout, stderr = execute_ssh_command(ssh_client, read_cmd, use_sudo=True)
                if success:
                    content = stdout
                else:
                    return jsonify({
                        'success': False,
                        'error': f'读取文件失败（权限不足）: {stderr}'
                    }), 403
            except Exception as e:
                return jsonify({
                    'success': False,
                    'error': f'读取文件失败: {str(e)}'
                }), 500
            
            return jsonify({
                'success': True,
                'content': content,
                'path': file_path,
                'server': server
            }), 200
        
        except paramiko.AuthenticationException:
            return jsonify({
                'success': False,
                'error': 'SSH认证失败: 用户名或密码错误'
            }), 401
        except paramiko.SSHException as e:
            return jsonify({
                'success': False,
                'error': f'SSH连接错误: {str(e)}'
            }), 500
        except Exception as e:
            logger.error(f"读取文件时出错: {str(e)}", exc_info=True)
            return jsonify({
                'success': False,
                'error': f'服务器内部错误: {str(e)}'
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


if __name__ == '__main__':
    # 开发环境运行
    app.run(host='0.0.0.0', port=5003, debug=True)
