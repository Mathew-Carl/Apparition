#!/usr/bin/env python3
"""
幻影显形 - WPS 自动打卡服务
启动入口

使用方法：
    python run.py

或者直接：
    uvicorn app:app --host 0.0.0.0 --port 8000
"""

import uvicorn
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


def main():
    """启动服务"""
    print("""
    ╔═══════════════════════════════════════════════════════════╗
    ║                                                           ║
    ║           幻影显形 - WPS 自动打卡服务 v2.0                ║
    ║                                                           ║
    ║   启动后访问: http://localhost:8080                       ║
    ║   API 文档:   http://localhost:8080/docs                  ║
    ║                                                           ║
    ╚═══════════════════════════════════════════════════════════╝
    """)

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8080,
        reload=False,      # 生产环境关闭热重载
        log_level="info"
    )


if __name__ == "__main__":
    main()
