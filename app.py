"""
Web 服务模块 - FastAPI 版本
提供扫码登录、用户管理等 API

功能：
1. 普通用户：扫码登录 → 配置个人信息 → 查看打卡记录
2. 管理员：查看所有用户 → 管理打卡时间 → 手动触发打卡
"""

import os
import json
import asyncio
import logging
import secrets
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Cookie, Response, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from wps_auth import WPSAuthSession, QRCodeResult, LoginResult
from database import db, Database

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


# ==================== 配置 ====================
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "123456"

# 存储 session {token: {"type": "admin" | "user", "user_id": int}}
sessions: dict[str, dict] = {}


# ==================== 数据模型 ====================

class UserConfig(BaseModel):
    """用户配置（扫码后填写）"""
    input_name: str = ""           # 打卡填写的内容
    latitude: float = 0   # 纬度
    longitude: float = 0  # 经度
    nickname: str = ""             # 昵称


class UserUpdate(BaseModel):
    """用户更新"""
    input_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    nickname: Optional[str] = None
    is_active: Optional[bool] = None
    sendkey: Optional[str] = None
    checkin_hour: Optional[int] = None
    checkin_minute: Optional[int] = None


class AdminLogin(BaseModel):
    """管理员登录"""
    username: str
    password: str


# ==================== 全局状态 ====================
# 存储正在进行的登录会话（使用新的 WPSAuthSession）

# 正在进行的登录会话 {channel_id: WPSAuthSession}
login_sessions: dict[str, WPSAuthSession] = {}


# ==================== 认证函数 ====================

def create_session(session_type: str, user_id: int = None) -> str:
    """创建会话，返回 token"""
    token = secrets.token_urlsafe(32)
    sessions[token] = {"type": session_type, "user_id": user_id}
    return token


def get_session(token: str) -> Optional[dict]:
    """获取会话信息"""
    return sessions.get(token)


def delete_session(token: str):
    """删除会话"""
    if token in sessions:
        del sessions[token]


async def get_current_user(session_token: str = Cookie(None)):
    """获取当前登录的用户（依赖注入）"""
    if not session_token:
        return None
    session = get_session(session_token)
    if not session or session["type"] != "user":
        return None
    return await db.get_user(session["user_id"])


async def require_user(session_token: str = Cookie(None)):
    """要求用户登录（依赖注入）"""
    if not session_token:
        raise HTTPException(status_code=401, detail="请先登录")
    session = get_session(session_token)
    if not session or session["type"] != "user":
        raise HTTPException(status_code=401, detail="请先登录")
    user = await db.get_user(session["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


async def require_admin(session_token: str = Cookie(None)):
    """要求管理员登录（依赖注入）"""
    if not session_token:
        raise HTTPException(status_code=401, detail="请先登录管理员账户")
    session = get_session(session_token)
    if not session or session["type"] != "admin":
        raise HTTPException(status_code=401, detail="需要管理员权限")
    return True


# ==================== FastAPI 应用 ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理

    FastAPI 知识点：
    - lifespan 是 FastAPI 的生命周期钩子
    - yield 之前的代码在启动时执行
    - yield 之后的代码在关闭时执行
    """
    # 启动时：初始化数据库
    logger.info("应用启动，初始化数据库...")
    await db.init()

    # 启动定时任务
    from scheduler import start_scheduler
    start_scheduler()

    yield

    # 关闭时：清理资源
    from scheduler import stop_scheduler
    stop_scheduler()
    logger.info("应用关闭")


app = FastAPI(
    title="幻影显形 - WPS 自动打卡",
    description="多用户 WPS 自动打卡服务",
    version="2.0.0",
    lifespan=lifespan
)

# 跨域配置（允许前端访问）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== 页面路由 ====================

@app.get("/", response_class=HTMLResponse)
async def index():
    """首页 - 普通用户扫码登录页面"""
    html_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>请创建 templates/index.html</h1>")


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    """管理员页面"""
    html_path = os.path.join(os.path.dirname(__file__), "templates", "admin.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>请创建 templates/admin.html</h1>")


# ==================== 管理员认证 API ====================

@app.post("/api/admin/login")
async def admin_login(data: AdminLogin, response: Response):
    """管理员登录"""
    if data.username == ADMIN_USERNAME and data.password == ADMIN_PASSWORD:
        token = create_session("admin")
        response.set_cookie(key="session_token", value=token, httponly=True, max_age=86400)
        return {"message": "登录成功"}
    raise HTTPException(status_code=401, detail="用户名或密码错误")


@app.post("/api/admin/logout")
async def admin_logout(response: Response, session_token: str = Cookie(None)):
    """管理员登出"""
    if session_token:
        delete_session(session_token)
    response.delete_cookie(key="session_token")
    return {"message": "已登出"}


@app.get("/api/admin/check")
async def admin_check(session_token: str = Cookie(None)):
    """检查管理员登录状态"""
    if not session_token:
        return {"logged_in": False}
    session = get_session(session_token)
    if session and session["type"] == "admin":
        return {"logged_in": True}
    return {"logged_in": False}


# ==================== 用户认证 API ====================

@app.get("/api/me")
async def get_me(session_token: str = Cookie(None)):
    """获取当前登录用户信息"""
    if not session_token:
        return {"logged_in": False}
    session = get_session(session_token)
    if not session or session["type"] != "user":
        return {"logged_in": False}
    user = await db.get_user(session["user_id"])
    if not user:
        return {"logged_in": False}
    return {
        "logged_in": True,
        "user": {
            "id": user.id,
            "wps_uid": user.wps_uid,
            "nickname": user.nickname,
            "input_name": user.input_name,
            "latitude": user.latitude,
            "longitude": user.longitude,
            "is_active": user.is_active,
            "last_checkin": user.last_checkin,
            "sendkey": user.sendkey or "",
            "checkin_hour": user.checkin_hour,
            "checkin_minute": user.checkin_minute
        }
    }


@app.post("/api/me/logout")
async def user_logout(response: Response, session_token: str = Cookie(None)):
    """用户登出"""
    if session_token:
        delete_session(session_token)
    response.delete_cookie(key="session_token")
    return {"message": "已登出"}


@app.put("/api/me")
async def update_me(data: UserUpdate, user=Depends(require_user)):
    """更新当前用户配置"""
    update_data = {k: v for k, v in data.dict().items() if v is not None}
    if update_data:
        await db.update_user(user.id, **update_data)
    return {"message": "更新成功"}


@app.get("/api/me/logs")
async def get_my_logs(user=Depends(require_user), limit: int = 20):
    """获取当前用户的打卡记录"""
    logs = await db.get_user_checkin_logs(user.id, limit)
    return [{
        "id": log.id,
        "status": log.status,
        "message": log.message,
        "created_at": log.created_at
    } for log in logs]


@app.post("/api/me/checkin")
async def manual_self_checkin(background_tasks: BackgroundTasks, user=Depends(require_user)):
    """用户手动触发自己的打卡"""
    from checkin import do_checkin_for_user
    background_tasks.add_task(do_checkin_for_user, user.id)
    return {"message": "打卡任务已提交"}


# ==================== 扫码登录 API ====================

@app.post("/api/login/start")
async def start_login(background_tasks: BackgroundTasks):
    """
    开始登录流程 - 获取二维码

    1. 创建 WPSAuthSession
    2. 启动浏览器获取二维码
    3. 在后台等待用户扫码

    Returns:
        {
            "channel_id": "xxx",      # 会话ID，用于查询状态
            "qrcode_url": "https://..."  # 二维码图片URL
        }
    """
    session = WPSAuthSession()

    try:
        # 获取二维码（会启动浏览器）
        qr = await session.start()

        # 存储会话
        login_sessions[qr.channel_id] = session

        # 在后台等待扫码
        background_tasks.add_task(
            wait_for_scan_task,
            qr.channel_id
        )

        logger.info(f"创建登录会话: {qr.channel_id}")

        return {
            "channel_id": qr.channel_id,
            "qrcode_url": qr.qrcode_url
        }

    except Exception as e:
        await session.close()
        logger.error(f"获取二维码失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def wait_for_scan_task(channel_id: str):
    """
    后台任务：等待用户扫码

    这个函数在后台运行，不阻塞 API 响应
    """
    session = login_sessions.get(channel_id)
    if not session:
        return

    try:
        # 等待扫码并登录
        result = await session.wait_and_login()

        if result.success:
            # 保存到数据库
            user_id = await db.add_user(
                wps_uid=result.user_id or 0,
                cookies=result.cookies,
                nickname=f"用户{result.user_id}" if result.user_id else "新用户"
            )

            # 更新会话状态（用于状态查询）
            session.db_user_id = user_id  # 添加数据库用户ID到会话
            logger.info(f"登录成功: user_id={user_id}, wps_uid={result.user_id}")
        else:
            logger.error(f"登录失败: {result.error}")

    except Exception as e:
        session.status = "failed"
        session.error = str(e)
        logger.error(f"登录流程出错: {e}")

    finally:
        # 注意：不要在这里关闭会话，等状态查询后再关闭
        pass


@app.get("/api/login/status/{channel_id}")
async def get_login_status(channel_id: str, response: Response):
    """
    查询登录状态

    前端轮询这个接口，检查用户是否已扫码
    登录成功时会自动设置用户 session cookie
    """
    session = login_sessions.get(channel_id)

    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    result = {"status": session.status}

    if session.status == "success":
        # 从会话结果中获取信息
        user_id = getattr(session, 'db_user_id', None)
        if session.result:
            result["user_id"] = user_id
            result["wps_uid"] = session.result.user_id

        # 创建用户 session 并设置 cookie
        if user_id:
            token = create_session("user", user_id)
            response.set_cookie(key="session_token", value=token, httponly=True, max_age=86400*30)

        # 登录成功后清理会话
        await session.close()
        del login_sessions[channel_id]

    elif session.status == "failed":
        result["error"] = session.error
        # 失败后清理会话
        await session.close()
        del login_sessions[channel_id]

    return result


# ==================== 用户管理 API（管理员） ====================

@app.get("/api/users")
async def list_users(_=Depends(require_admin)):
    """获取所有用户列表（需要管理员权限）"""
    users = await db.get_all_users()

    return [{
        "id": u.id,
        "wps_uid": u.wps_uid,
        "nickname": u.nickname,
        "input_name": u.input_name,
        "latitude": u.latitude,
        "longitude": u.longitude,
        "is_active": u.is_active,
        "last_checkin": u.last_checkin,
        "created_at": u.created_at,
        "sendkey": u.sendkey or "",
        "checkin_hour": u.checkin_hour,
        "checkin_minute": u.checkin_minute
    } for u in users]


@app.get("/api/users/{user_id}")
async def get_user(user_id: int, _=Depends(require_admin)):
    """获取单个用户信息（需要管理员权限）"""
    user = await db.get_user(user_id)

    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    return {
        "id": user.id,
        "wps_uid": user.wps_uid,
        "nickname": user.nickname,
        "input_name": user.input_name,
        "latitude": user.latitude,
        "longitude": user.longitude,
        "is_active": user.is_active,
        "last_checkin": user.last_checkin,
        "created_at": user.created_at,
        "sendkey": user.sendkey or "",
        "checkin_hour": user.checkin_hour,
        "checkin_minute": user.checkin_minute
    }


@app.put("/api/users/{user_id}")
async def update_user(user_id: int, data: UserUpdate, _=Depends(require_admin)):
    """更新用户配置（需要管理员权限）"""
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    update_data = {k: v for k, v in data.dict().items() if v is not None}

    if update_data:
        await db.update_user(user_id, **update_data)

    return {"message": "更新成功"}


@app.delete("/api/users/{user_id}")
async def delete_user(user_id: int, _=Depends(require_admin)):
    """删除用户（需要管理员权限）"""
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    await db.delete_user(user_id)
    return {"message": "删除成功"}


@app.post("/api/users/{user_id}/toggle")
async def toggle_user(user_id: int, _=Depends(require_admin)):
    """切换用户的启用/禁用状态（需要管理员权限）"""
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    await db.update_user(user_id, is_active=not user.is_active)

    return {
        "message": "状态已更新",
        "is_active": not user.is_active
    }


# ==================== 打卡 API（管理员） ====================

@app.get("/api/users/{user_id}/logs")
async def get_user_logs(user_id: int, limit: int = 10, _=Depends(require_admin)):
    """获取用户的打卡记录（需要管理员权限）"""
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    logs = await db.get_user_checkin_logs(user_id, limit)

    return [{
        "id": log.id,
        "status": log.status,
        "message": log.message,
        "created_at": log.created_at
    } for log in logs]


@app.post("/api/checkin/{user_id}")
async def manual_checkin(user_id: int, background_tasks: BackgroundTasks, _=Depends(require_admin)):
    """手动触发单个用户打卡（需要管理员权限）"""
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    from checkin import do_checkin_for_user
    background_tasks.add_task(do_checkin_for_user, user_id)

    return {"message": "打卡任务已提交"}


@app.post("/api/checkin/all")
async def checkin_all(background_tasks: BackgroundTasks, _=Depends(require_admin)):
    """触发所有启用用户的打卡（需要管理员权限）"""
    from checkin import do_checkin_all
    background_tasks.add_task(do_checkin_all)

    return {"message": "批量打卡任务已提交"}


# ==================== 打卡时间配置 API（管理员） ====================

class ScheduleCreate(BaseModel):
    """创建打卡时间"""
    name: str              # 任务名称
    hour: int              # 小时 (0-23)
    minute: int            # 分钟 (0-59)


class ScheduleUpdate(BaseModel):
    """更新打卡时间"""
    name: Optional[str] = None
    hour: Optional[int] = None
    minute: Optional[int] = None
    is_enabled: Optional[bool] = None


@app.get("/api/schedules")
async def list_schedules():
    """获取所有打卡时间配置（公开接口，用户查看系统打卡时间）"""
    schedules = await db.get_all_schedules()

    return [{
        "id": s.id,
        "name": s.name,
        "hour": s.hour,
        "minute": s.minute,
        "time_str": f"{s.hour:02d}:{s.minute:02d}",
        "is_enabled": s.is_enabled,
        "created_at": s.created_at
    } for s in schedules]


@app.post("/api/schedules")
async def create_schedule(data: ScheduleCreate, _=Depends(require_admin)):
    """添加新的打卡时间（需要管理员权限）"""
    try:
        schedule_id = await db.add_schedule(
            name=data.name,
            hour=data.hour,
            minute=data.minute
        )

        from scheduler import refresh_scheduler
        await refresh_scheduler()

        return {
            "message": "添加成功",
            "id": schedule_id
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/schedules/{schedule_id}")
async def update_schedule(schedule_id: int, data: ScheduleUpdate, _=Depends(require_admin)):
    """更新打卡时间配置（需要管理员权限）"""
    update_data = {k: v for k, v in data.dict().items() if v is not None}

    if not update_data:
        raise HTTPException(status_code=400, detail="没有要更新的内容")

    try:
        await db.update_schedule(schedule_id, **update_data)

        from scheduler import refresh_scheduler
        await refresh_scheduler()

        return {"message": "更新成功"}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/schedules/{schedule_id}")
async def delete_schedule(schedule_id: int, _=Depends(require_admin)):
    """删除打卡时间配置（需要管理员权限）"""
    await db.delete_schedule(schedule_id)

    from scheduler import refresh_scheduler
    await refresh_scheduler()

    return {"message": "删除成功"}


@app.post("/api/schedules/{schedule_id}/toggle")
async def toggle_schedule(schedule_id: int, _=Depends(require_admin)):
    """切换打卡时间的启用/禁用状态（需要管理员权限）"""
    new_status = await db.toggle_schedule(schedule_id)

    from scheduler import refresh_scheduler
    await refresh_scheduler()

    return {
        "message": "状态已更新",
        "is_enabled": new_status
    }


@app.get("/api/scheduler/status")
async def get_scheduler_status(_=Depends(require_admin)):
    """获取调度器状态（需要管理员权限）"""
    from scheduler import get_scheduler_status as get_status
    return get_status()


# ==================== 启动服务 ====================

if __name__ == "__main__":
    import uvicorn

    # uvicorn 是 ASGI 服务器，用于运行 FastAPI
    uvicorn.run(
        "app:app",
        host="0.0.0.0",    # 监听所有网卡
        port=8000,          # 端口
        reload=True         # 开发模式：代码修改后自动重启
    )
