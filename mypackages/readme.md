1. **在 10.3 服务器上为 mypackages 准备专用虚拟环境并安装 pypiserver**
   - 将本项目上传到 10.3 服务器任意位置；
   - `cd` 到 `mypackages` 目录；
   - 执行 `python3 -m venv venv` 创建虚拟环境（会在 `mypackages/venv` 下生成）；
   - 执行 `source venv/bin/activate && pip install pypiserver` 安装 pypiserver；
   - 确认 `mypackages/venv` 目录下已包含 `bin/pip`、`bin/pypi-server` 等文件。

2. **在 10.2 服务器上部署 mypackages**
   - 推荐使用根目录的 `deploy_all.sh`，它会在 10.3 上自动为 `mypackages` 创建 `venv`，并将整个 `mypackages` 目录（含 `venv`）同步到 10.2 的 `/home/user/common/`。  
   - 如果需要手工操作，可以在 10.3 上执行：  
     ```bash
     cd /home/user/common/localpythonmvp
     scp -r mypackages user@192.168.140.202:/home/user/common/
     ```
   - 然后在 10.2 上：  
     ```bash
     cd /home/user/common/mypackages
     nohup bash ./deploy.sh > deploy_run.log 2>&1 &
     ```

3. **访问本地 pypiserver**
   - 服务启动后，可通过 `http://192.168.140.202:8087` 访问本地 pypiserver，作为 `pip` 的镜像源地址；
   - 如需添加其他包，可从其他镜像源网站（如 `https://pypi.tuna.tsinghua.edu.cn/simple/pillow/`）下载好对应 `whl` 或 `tar.gz` 包，上传到本项目的 `packages` 目录下即可。