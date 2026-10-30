"""本地启动入口。

启动前请设置 QWEN_BASE_URL / QWEN_API_KEY / QWEN_MODEL；
生产环境建议使用 uvicorn/gunicorn 托管 app.api:app。
"""

import uvicorn

from app.api import app


def main() -> None:
    # 本地开发使用 127.0.0.1；容器部署时再按环境调整 host/port。
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
