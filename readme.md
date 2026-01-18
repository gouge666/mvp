## 分布式 Python 项目管理与执行平台总览

这是一个基于 **Flask + Paramiko** 的多服务器协同系统，用来在内网里完成：

- **多用户、多项目** 的统一管理；
- **虚拟环境** 统一创建 / 管理；
- **跨机器项目同步与算法执行**；
- **执行日志与结果的集中归档**；
- **本地 PyPI 镜像** 加速依赖安装。

整个系统通过 `server103` 提供的 Web 界面进行操作，其余服务器分别负责项目源、环境与执行、以及结果归档。

---

## 一、服务器与角色

- **Server101（192.168.140.201，端口 5001） — 项目源服务器**
  - 按用户组织的项目源代码与数据集：  
    `/home/user/{username}/projects/{projectname}/`
  - 是**唯一权威项目源**，所有执行前的项目都以这里为准。

- **Server102（192.168.140.202，端口 5002） — 环境与执行服务器**
  - 管理每个用户的虚拟环境：  
    `/home/user/{username}/envs/{env_name}/`
  - 执行算法时的实际运行目录：  
    `/home/user/{username}/projects/{projectname}/`（从 10.1 同步过来的副本）
  - 提供基础文件传输 API。

- **Server103（192.168.140.203，端口 5003） — Web 管理与调度中心**
  - 提供统一的 Web 管理界面：`http://192.168.140.203:5003/test`
  - 对外暴露统一 API，内部调用 10.1 / 10.2 / 10.4：
    - 扫描 10.1 上的项目列表；
    - 在 10.2 上创建 / 列出 / 删除虚拟环境；
    - 调度算法执行（同步 / 异步）；
    - 把执行结果与日志归档到 10.4。

- **Server104（192.168.140.204，端口 5004） — 结果与输出服务器**
  - 按用户、按项目集中存放执行结果与日志：  
    `/home/user/{username}/outputs/{projectname}/...`
  - 也暴露与其他节点风格一致的传输 API。

- **本地 PyPI 镜像（部署在 10.2）**
  - 监听端口：`8087`
  - 访问地址（在服务器内网）：`http://192.168.140.202:8087/simple/`
  - 包文件目录（在本项目中）：`mypackages/packages/`

SSH / Sudo 统一约定：

- **SSH 用户**：`user`  
- **SSH 密码**：`1234567`  
- **SSH 端口**：`22`  
- **Sudo 密码**：`1234567`

---

## 二、核心目录与路径约定

所有用户相关路径都以：

```text
/home/user/{username}/...
```

为前缀，其中 `{username}` 为 Web 界面中输入的用户名（如 `gousk`）。

- **项目源路径（仅 10.1 上有效）**

```text
/home/user/{username}/projects/{projectname}/
```

- **虚拟环境路径（10.2 上）**

```text
/home/user/{username}/envs/{env_name}/
```

- **执行用项目副本路径（10.2 上）**

```text
/home/user/{username}/projects/{projectname}/
```

- **输出 / 日志归档路径（10.4 上）**

```text
/home/user/{username}/outputs/{projectname}/
  ├─ run_YYYYMMDD_HHMMSS.log
  └─ run_YYYYMMDD_HHMMSS/   # 某次执行对应的完整项目拷贝
```

---

## 三、整体执行链路（从 10.1 → 10.2 → 10.4）

> 下述逻辑对应 `server103/app.py` 中 `/project/execute` 与 `/project/execute/async` 的最新实现。

0. **初始化用户目录（推荐先做一次）**
   - 在浏览器访问 `http://192.168.140.203:5003/test`；
   - 在“👤 用户初始化（在四台服务器创建目录）”区域输入 `username`，点击“创建用户基础目录”；  
   - 后端会在四台服务器上自动创建：  
     - 10.1：`/home/user/{username}`、`/home/user/{username}/projects`  
     - 10.2：`/home/user/{username}`、`/home/user/{username}/projects`、`/home/user/{username}/envs`  
     - 10.3：`/home/user/{username}`、`/home/user/{username}/projects`  
     - 10.4：`/home/user/{username}`、`/home/user/{username}/outputs`  

1. **准备项目（10.1 / server101）**
   - 按如下结构把项目放到 10.1：
     ```text
     /home/user/{username}/projects/{projectname}/
       ├─ 源代码（如 main.py）
       ├─ 数据（可选）
       └─ requirements.txt（推荐）
     ```

2. **准备环境（10.2 / server102）**
   - 通过 Web 界面（server103）调用 `env/create`：
     - 在 10.2 的 `/home/user/{username}/envs/{env_name}/` 创建虚拟环境；
     - 基于事先准备好的基线 venv 拷贝，创建速度较快。
   - 后续可以用 `env/list` / `env/delete` 管理环境。

3. **执行算法（由 server103 调度）**
   - 用户在 Web 界面选择：
     - `username`
     - `projectname`
     - `env_name`
     - `command`（如 `python main.py`）
   - **服务端内部步骤：**
     1. 在 10.1 上检查项目目录是否存在：  
        `/home/user/{username}/projects/{projectname}`
     2. 使用 `copy_folder_remote_to_remote` 把项目从 10.1 **复制到 10.2**：
        ```text
        10.1:/home/user/{username}/projects/{projectname}
           → 10.2:/home/user/{username}/projects/{projectname}
        ```
     3. 在 10.2 上：
        - 激活虚拟环境 `/home/user/{username}/envs/{env_name}`；
        - `cd` 到项目目录；
        - 若存在 `requirements.txt`，则执行：
          ```bash
          pip install -r requirements.txt \
            --index-url http://192.168.140.202:8087/simple/ \
            --trusted-host 192.168.140.202
          ```
        - 执行用户命令 `command`，输出重定向到临时 log 文件。
     4. 执行完毕后，server103 会：
        - 把 log 内容写入 10.4 对应项目输出目录下的 `run_YYYYMMDD_HHMMSS.log`；
        - 再把 **10.2 上的整个项目执行目录** 拷贝到 10.4：
          ```text
          10.2:/home/user/{username}/projects/{projectname}
             → 10.4:/home/user/{username}/outputs/{projectname}/run_YYYYMMDD_HHMMSS/
          ```

4. **查看结果（10.4 / server104）**
   - 可以通过 server103 的 `/project/log` 读取最新 log 内容；
   - 也可以直接在 10.4 的对应 `outputs` 目录浏览结果。

---

## 四、功能模块概览（按服务器）

只列出关键点，详细字段可参考各子目录下的 `README.md` 与 `app.py`。

### 1. `server101` — 项目源管理

- **端口**：`5001`
- **核心能力**：
  - 维护项目源目录（手工/其他工具同步至 10.1）；
  - 提供基础 `/health`、`/transfer`、`/servers` 等接口（用于需要时的项目拷贝）。

### 2. `server102` — 环境与执行

- **端口**：`5002`
- **核心能力**：
  - 作为所有算法执行的实际运行节点；
  - 提供基础传输接口；
  - 在 server103 的调度下，**只负责执行，不负责业务编排**。

### 3. `server103` — Web 管理与调度中心

- **端口**：`5003`
- **Web 页面**：`/test`（主控制台）
- **主要 API（部分）**：
  - `/servers`：服务器配置与当前节点信息；
  - `/user/create`：为指定 `username` 在四台服务器上一次性创建所需目录（`projects/envs/outputs` 等）；  
  - `/list`：列出任意服务器指定路径的文件；
  - `/file/create`：在远程服务器创建文件；
  - `/env/create` / `/env/list` / `/env/delete`：在 10.2 上管理虚拟环境；
  - `/env/execute`：在指定虚拟环境中执行命令（类 Web Terminal）；
  - `/project/list`：从 10.1 扫描用户项目列表；
  - `/project/execute`：同步执行算法，立即返回执行结果和基本信息；
  - `/project/execute/async`：异步执行算法，立即返回运行中的状态和 log 路径；
  - `/project/log`：从 10.4 读取对应项目的 log 内容。

### 4. `server104` — 输出归档

- **端口**：`5004`
- **核心能力**：
  - 统一存储执行结果和 log；
  - 暴露 `/health`、`/transfer`、`/servers` 等接口，用于接收其他节点推送的文件。

---

## 五、本地 PyPI 服务（`mypackages`）

`mypackages` 目录用于在（建议）10.2 上搭建一个内网 PyPI 源，减少外网依赖。

### 启动步骤（在 10.2 上）

1. 将本项目上传到 10.3 任意目录，在 `mypackages` 下创建虚拟环境并安装 `pypiserver`（详见 `mypackages/readme.md`）。  
2. 把本项目整体放到 10.2 的 `/home/user/common/deploy/mypackages`（或你自己的统一部署目录）。  
3. 在部署目录执行：

```bash
sudo nohup ./deploy.sh > deploy_run.log 2>&1 &
```

4. 成功后，可通过：

```text
http://192.168.140.202:8087/simple/
```

作为 `pip` 的 `--index-url` 使用。

---

## 六、启动与部署速查

### 1. 各服务目录布局（本仓库）

- `server101/`：10.1 服务代码与启动脚本
- `server102/`：10.2 服务代码与启动脚本
- `server103/`：10.3 Web 管理与调度中心
- `server104/`：10.4 结果归档服务
- `mypackages/`：本地 PyPI 镜像部署脚本与离线包

每个 `serverXXX` 目录下都包含：

- `app.py`：Flask 后端逻辑  
- `requirements.txt`：依赖列表  
- `start.sh` / `start.bat`：Linux / Windows 启动脚本  

### 2. 前期部署：**先在 10.3 搭好 venv，再用 scp 分发**

**强烈建议的顺序：**

1. **在 10.3 上准备好完整代码仓库和统一虚拟环境**  
   - 假设你把代码放在：`/home/user/common/localpythonmvp`；  
   - 在 10.3 的每个服务目录（至少 `server101/102/103/104/`，以及有需要的工具目录）先创建并安装好虚拟环境，例如：  
     ```bash
     cd /home/user/common/localpythonmvp/server101
     python3 -m venv venv
     source venv/bin/activate
     pip install -r requirements.txt --index-url http://192.168.140.202:8087/simple/ --trusted-host 192.168.140.202
     
     cd /home/user/common/localpythonmvp/server102
     python3 -m venv venv
     source venv/bin/activate
     pip install -r requirements.txt --index-url http://192.168.140.202:8087/simple/ --trusted-host 192.168.140.202
     
     # server103 / server104 同理
     ```
   - 这样做的目的是：**只在 10.3 上跑一次依赖安装，其他机器直接拷贝好用，避免每台服务器都重新下载依赖。**

2. **从 10.3 用 `scp` 把“带 venv 的目录”分发到各服务器**  

   ```bash
   # 假设当前在 10.3 的 /home/user/common/localpythonmvp 目录下

   # 分发 server101 代码 + venv 到 10.1
   scp -r server101 user@192.168.140.201:/home/user/common/

   # 分发 server102 代码 + venv 到 10.2
   scp -r server102 user@192.168.140.202:/home/user/common/

   # 分发 server103 代码 + venv 到 10.3（如果你先在别处准备好，再拷到正式目录）
   scp -r server103 user@192.168.140.203:/home/user/common/

   # 分发 server104 代码 + venv 到 10.4
   scp -r server104 user@192.168.140.204:/home/user/common/

   # 如需同步本地 PyPI 相关目录（mypackages），可以只放在 10.2：
   scp -r mypackages user@192.168.140.202:/home/user/common/
   ```

   说明：
   - `user` / `192.168.10.x` 与实际 SSH 账号、IP 保持一致（目前代码中默认 `user/1234567`）；  
   - 目标路径 `/home/user/common/` 可以根据你的实际部署规划调整，但需与各 `serverXXX/README.md` 中的说明一致；  
   - **关键点：10.3 上先把 venv 和依赖准备好，再整体 `scp -r`，这样所有服务器拿到的就是“可直接运行”的一套目录结构。**  
   - 后续如果在 10.3 更新了代码或依赖，也可以用相同的 `scp -r` 命令覆盖对应服务器上的目录（注意提前备份或使用 git 管理）。  

### 3. 典型启动流程（Linux）
### 3. 典型启动流程（Linux）

在对应服务器上，进入相应目录：

```bash
cd server101 && sudo nohup ./start.sh > start.log 2>&1 &
cd server102 && sudo nohup ./start.sh > start.log 2>&1 &
cd server103 && sudo nohup ./start.sh > start.log 2>&1 &
cd server104 && sudo nohup ./start.sh > start.log 2>&1 &
```

然后在浏览器访问：

```text
http://192.168.140.203:5003/test
```

完成后续的环境、项目和执行操作。

---

## 七、开发者提示

- **代码结构**（简要）
  - `server101/102/103/104/app.py`：各自服务的 Flask 应用；
  - `server103/app.py`：包含所有“跨服务器编排”逻辑，是阅读与扩展的重点；
  - `server103/templates/test.html`：主 Web 界面，前端交互主要集中于此；
  - `mypackages/deploy.sh`：PyPI 镜像启动脚本。

- **扩展建议**
  - 新能力尽量通过 `server103` 统一编排，对外暴露一个出口即可；
  - 遵守已有的路径与用户/项目/环境命名约定；
  - 所有跨机器操作优先走 Paramiko + 统一的 `execute_ssh_command` / `copy_folder_paramiko` / `copy_folder_remote_to_remote`；
  - 注意日志记录，便于排查跨机器问题。
