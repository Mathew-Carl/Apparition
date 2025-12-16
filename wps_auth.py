"""
WPS 认证模块 - 使用 Playwright 实现
通过真实浏览器完成扫码登录，确保获取到完整的 Cookie

流程：
1. 启动浏览器，访问 WPS 登录页面
2. 获取二维码 URL 返回给前端显示
3. 等待用户扫码，浏览器自动完成登录
4. 提取浏览器中的 Cookie
"""

import time
import json
import asyncio
import logging
from typing import Optional
from dataclasses import dataclass
from playwright.async_api import async_playwright, Browser, Page

logger = logging.getLogger(__name__)


@dataclass
class QRCodeResult:
    """二维码获取结果"""
    channel_id: str
    qrcode_url: str


@dataclass
class LoginResult:
    """登录结果"""
    success: bool
    cookies: Optional[dict] = None
    user_id: Optional[int] = None
    error: Optional[str] = None


class WPSAuth:
    """
    WPS 扫码登录认证类 - Playwright 版本

    使用真实浏览器完成登录流程，确保获取到所有必要的 Cookie
    """

    LOGIN_URL = "https://account.wps.cn/"

    def __init__(self):
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.playwright = None

    async def init(self):
        """初始化浏览器"""
        logger.info("初始化浏览器...")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True  # 无头模式，服务器上运行
        )

    async def close(self):
        """关闭浏览器"""
        if self.page:
            await self.page.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("浏览器已关闭")

    async def get_qrcode(self) -> QRCodeResult:
        """
        获取登录二维码

        打开登录页面，等待二维码加载，提取二维码 URL
        """
        logger.info("正在获取二维码...")

        # 创建新的浏览器上下文和页面
        context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        self.page = await context.new_page()

        # 访问登录页面
        await self.page.goto(self.LOGIN_URL)

        # 等待二维码图片出现
        # WPS 登录页面的二维码图片选择器
        qrcode_selectors = [
            'img[src*="qrcode"]',
            'img[src*="minicode"]',
            '.qrcode img',
            '.login-qrcode img',
        ]

        qrcode_url = None
        channel_id = None

        # 尝试多种选择器
        for selector in qrcode_selectors:
            try:
                await self.page.wait_for_selector(selector, timeout=5000)
                img = self.page.locator(selector).first
                qrcode_url = await img.get_attribute('src')
                if qrcode_url:
                    break
            except:
                continue

        # 如果选择器没找到，尝试通过网络请求获取
        if not qrcode_url:
            logger.info("通过选择器未找到二维码，尝试从网络请求获取...")

            # 监听网络请求，找到二维码 API 的响应
            # 重新加载页面并监听
            qrcode_data = {}

            async def handle_response(response):
                nonlocal qrcode_data
                if "miniprogram/code/img" in response.url or "qrcode" in response.url:
                    try:
                        data = await response.json()
                        if "url" in data:
                            qrcode_data["url"] = data["url"]
                            qrcode_data["channel_id"] = data.get("channel_id", "")
                    except:
                        pass

            self.page.on("response", handle_response)
            await self.page.reload()
            await asyncio.sleep(3)  # 等待请求完成

            if qrcode_data.get("url"):
                qrcode_url = qrcode_data["url"]
                channel_id = qrcode_data.get("channel_id", "")

        if not qrcode_url:
            raise Exception("无法获取二维码")

        # 从 URL 中提取 channel_id
        if not channel_id and "minicodes/" in qrcode_url:
            # URL 格式: https://qrcode.qwps.cn/wxmp/minicodes/wxDuonqoATlwABQpwn?...
            parts = qrcode_url.split("minicodes/")
            if len(parts) > 1:
                channel_id = parts[1].split("?")[0]

        if not channel_id:
            channel_id = f"ch_{int(time.time())}"

        logger.info(f"二维码获取成功, channel_id: {channel_id}")

        return QRCodeResult(
            channel_id=channel_id,
            qrcode_url=qrcode_url
        )

    async def wait_for_login(self, timeout: int = 300) -> LoginResult:
        """
        等待用户扫码登录

        监控页面变化，检测登录成功
        """
        logger.info(f"等待用户扫码登录... (超时: {timeout}秒)")

        if not self.page:
            return LoginResult(success=False, error="页面未初始化")

        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                # 检查是否已登录（页面跳转或出现用户信息）
                current_url = self.page.url

                # 登录成功后通常会跳转
                if "account.wps.cn" not in current_url or "callback" in current_url:
                    logger.info(f"检测到页面跳转: {current_url}")
                    await asyncio.sleep(2)  # 等待 Cookie 设置完成
                    break

                # 检查是否有登录成功的标志
                # 例如：用户头像出现、登录按钮消失等
                try:
                    # 检查是否还在登录页面
                    login_form = await self.page.query_selector('.qrcode-container, .login-qrcode, [class*="qrcode"]')
                    if not login_form:
                        logger.info("二维码容器消失，可能已登录")
                        await asyncio.sleep(2)
                        break
                except:
                    pass

                # 检查 Cookie 是否已设置
                cookies = await self.page.context.cookies()
                cookie_names = [c["name"] for c in cookies]

                if "wps_sid" in cookie_names or "rtk" in cookie_names:
                    logger.info("检测到登录 Cookie")
                    break

                await asyncio.sleep(1)

            except Exception as e:
                logger.debug(f"等待过程出错: {e}")
                await asyncio.sleep(1)

        # 提取 Cookie
        try:
            cookies = await self.page.context.cookies()

            if not cookies:
                return LoginResult(success=False, error="未获取到 Cookie")

            # 转换为字典格式
            cookies_dict = {}
            for cookie in cookies:
                cookies_dict[cookie["name"]] = {
                    "value": cookie["value"],
                    "domain": cookie.get("domain", ""),
                    "path": cookie.get("path", "/"),
                }

            # 检查是否有关键 Cookie
            has_auth = any(name in cookies_dict for name in ["wps_sid", "rtk", "kso_sid"])

            if not has_auth:
                logger.warning(f"获取到 {len(cookies_dict)} 个 Cookie，但缺少认证 Cookie")
                logger.debug(f"Cookie 列表: {list(cookies_dict.keys())}")
                return LoginResult(success=False, error="登录未完成，缺少认证凭证")

            # 提取用户 ID
            user_id = None
            if "uid" in cookies_dict:
                try:
                    user_id = int(cookies_dict["uid"]["value"])
                except:
                    pass

            logger.info(f"登录成功! 获取到 {len(cookies_dict)} 个 Cookie, 用户ID: {user_id}")

            return LoginResult(
                success=True,
                cookies=cookies_dict,
                user_id=user_id
            )

        except Exception as e:
            logger.error(f"提取 Cookie 失败: {e}")
            return LoginResult(success=False, error=str(e))


class WPSAuthSession:
    """
    单次登录会话

    管理一次完整的扫码登录流程
    """

    def __init__(self):
        self.auth = WPSAuth()
        self.qrcode: Optional[QRCodeResult] = None
        self.status = "init"  # init, waiting, success, failed
        self.result: Optional[LoginResult] = None
        self.error: Optional[str] = None

    async def start(self) -> QRCodeResult:
        """开始登录流程，返回二维码"""
        await self.auth.init()
        self.qrcode = await self.auth.get_qrcode()
        self.status = "waiting"
        return self.qrcode

    async def wait_and_login(self) -> LoginResult:
        """等待扫码并完成登录"""
        self.result = await self.auth.wait_for_login()

        if self.result.success:
            self.status = "success"
        else:
            self.status = "failed"
            self.error = self.result.error

        return self.result

    async def close(self):
        """关闭会话"""
        await self.auth.close()


async def test_auth():
    """测试认证流程"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    session = WPSAuthSession()

    try:
        # 1. 获取二维码
        qr = await session.start()
        print(f"\n{'='*60}")
        print(f"请用微信扫描此二维码登录:")
        print(f"{qr.qrcode_url}")
        print(f"{'='*60}\n")

        # 2. 等待登录
        result = await session.wait_and_login()

        if result.success:
            print(f"\n登录成功!")
            print(f"用户ID: {result.user_id}")
            print(f"获取到 {len(result.cookies)} 个 Cookie:")

            for name, info in result.cookies.items():
                value = info["value"]
                display = value[:40] + "..." if len(value) > 40 else value
                print(f"  {name}: {display}")
        else:
            print(f"\n登录失败: {result.error}")

    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(test_auth())
