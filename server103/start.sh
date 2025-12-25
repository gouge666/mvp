# 检查有无venv，没有则创建
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install -r requirements.txt
echo "启动 Server103 Flask 应用..."
echo "访问地址: http://0.0.0.0:5003"
python app.py

