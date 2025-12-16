"""
打卡执行模块
基于原有的 Playwright 打卡逻辑，改造为支持多用户

这个模块负责：
1. 从数据库读取用户的 Cookie
2. 使用 Playwright 执行自动打卡
3. 记录打卡结果
4. 发送打卡通知（使用用户个人的 Server酱 SendKey）
"""

import json
import logging
import asyncio
from typing import Optional
from urllib.parse import urlencode
import requests

from playwright.async_api import async_playwright, BrowserContext, Page
from database import db, User

logger = logging.getLogger(__name__)

# 打卡目标 URL（可以改成从配置读取）
TARGET_URL = "https://f.kdocs.cn/你需要的链接"


async def do_checkin_for_user(user_id: int, max_retries: int = 2) -> bool:
    """
    为单个用户执行打卡（带重试机制）

    Args:
        user_id: 用户 ID
        max_retries: 最大重试次数（默认2次，即总共尝试3次）

    Returns:
        是否打卡成功
    """
    logger.info(f"开始为用户 {user_id} 执行打卡")

    # 获取用户信息
    user = await db.get_user(user_id)
    if not user:
        logger.error(f"用户 {user_id} 不存在")
        return False

    if not user.cookies:
        logger.error(f"用户 {user_id} 没有 Cookie")
        await db.add_checkin_log(user_id, "failed", "没有登录凭证")
        send_user_notification(user, False, "没有登录凭证")
        return False

    if not user.input_name:
        logger.error(f"用户 {user_id} 没有配置打卡内容")
        await db.add_checkin_log(user_id, "failed", "未配置打卡内容")
        send_user_notification(user, False, "未配置打卡内容")
        return False

    # 解析 Cookie
    try:
        cookies = json.loads(user.cookies)
    except Exception as e:
        logger.error(f"用户 {user_id} Cookie 解析失败: {e}")
        await db.add_checkin_log(user_id, "failed", "Cookie格式错误")
        send_user_notification(user, False, "Cookie格式错误")
        return False

    # 重试逻辑
    last_error = None
    for attempt in range(max_retries + 1):
        if attempt > 0:
            logger.info(f"用户 {user_id} 第 {attempt} 次重试，等待60秒...")
            await asyncio.sleep(60)  # 重试前等待60秒

        try:
            success, message = await execute_checkin(
                cookies=cookies,
                input_name=user.input_name,
                latitude=user.latitude,
                longitude=user.longitude
            )

            if success:
                logger.info(f"用户 {user_id} 打卡成功" + (f"（第{attempt+1}次尝试）" if attempt > 0 else ""))
                await db.add_checkin_log(user_id, "success", message or "打卡成功")
                await db.update_last_checkin(user_id)
                send_user_notification(user, True, message or "打卡成功")
                return True
            else:
                last_error = message
                logger.warning(f"用户 {user_id} 第 {attempt+1} 次打卡失败: {message}")

        except Exception as e:
            last_error = str(e)
            logger.warning(f"用户 {user_id} 第 {attempt+1} 次打卡出错: {last_error}")

    # 所有重试都失败
    final_message = f"重试{max_retries}次后仍失败: {last_error}"
    logger.error(f"用户 {user_id} {final_message}")
    await db.add_checkin_log(user_id, "failed", final_message)
    send_user_notification(user, False, final_message)
    return False


async def do_checkin_all():
    """
    为所有启用的用户执行打卡
    """
    logger.info("开始批量打卡")

    # 获取所有启用的用户
    users = await db.get_all_active_users()
    logger.info(f"共有 {len(users)} 个用户需要打卡")

    success_count = 0
    fail_count = 0

    for user in users:
        try:
            result = await do_checkin_for_user(user.id)
            if result:
                success_count += 1
            else:
                fail_count += 1
        except Exception as e:
            logger.error(f"用户 {user.id} 打卡异常: {e}")
            fail_count += 1

        # 每个用户之间间隔几秒，避免请求过快
        await asyncio.sleep(3)

    logger.info(f"批量打卡完成: 成功 {success_count}, 失败 {fail_count}")


async def execute_checkin(
    cookies: dict,
    input_name: str,
    latitude: float,
    longitude: float,
    target_url: str = TARGET_URL
) -> tuple[bool, str]:
    """
    执行打卡的核心逻辑（基于原 main.py）

    Args:
        cookies: Cookie 字典
        input_name: 打卡填写内容
        latitude: 纬度
        longitude: 经度
        target_url: 打卡页面 URL

    Returns:
        (是否成功, 消息)
    """
    async with async_playwright() as p:
        # 启动浏览器（有头模式用于调试）
        browser = await p.chromium.launch(headless=False)

        # 创建浏览器上下文，设置地理位置和中文环境
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1",
            geolocation={"latitude": latitude, "longitude": longitude},
            permissions=["geolocation"],
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 375, "height": 812},  # iPhone X 尺寸
            extra_http_headers={
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            }
        )

        try:
            # 添加 Cookie
            playwright_cookies = convert_cookies_to_playwright(cookies)
            await context.add_cookies(playwright_cookies)

            # 打开页面
            page = await context.new_page()

            # 先访问目标页面
            logger.info(f"访问目标页面: {target_url}")
            await page.goto(target_url)

            # 等待页面加载
            await page.wait_for_load_state("load")

            # 检查是否需要登录（页面可能跳转到登录页）
            current_url = page.url
            if "account.wps.cn" in current_url or "login" in current_url.lower():
                logger.error("Cookie 已失效，需要重新登录")
                return False, "登录已过期，请重新扫码登录"

            # 等待页面完全加载
            await page.wait_for_load_state("networkidle")

            # 执行打卡流程
            success, message = await fill_and_submit_form(page, input_name)

            return success, message

        except Exception as e:
            logger.error(f"打卡执行出错: {e}")
            return False, str(e)

        finally:
            await browser.close()


def convert_cookies_to_playwright(cookies: dict) -> list:
    """
    将 Cookie 字典转换为 Playwright 格式

    输入格式1（从新版登录获取的）：
    {
        "rtk": {"value": "xxx", "domain": ".wps.cn", "path": "/"},
        "wps_sid": {"value": "xxx", "domain": ".wps.cn", "path": "/"},
        ...
    }

    输入格式2（原版 Playwright 直接保存的）：
    [
        {"name": "rtk", "value": "xxx", "domain": ".wps.cn", "path": "/"},
        ...
    ]

    输出格式（Playwright 需要的）：
    [
        {"name": "rtk", "value": "xxx", "domain": ".wps.cn", "path": "/"},
        ...
    ]
    """
    # 如果已经是列表格式（原版格式），直接返回
    if isinstance(cookies, list):
        return cookies

    result = []

    for name, info in cookies.items():
        if isinstance(info, dict):
            cookie = {
                "name": name,
                "value": info.get("value", ""),
                "domain": info.get("domain", ".wps.cn"),
                "path": info.get("path", "/"),
            }
        else:
            # 如果是简单的 key-value 格式
            cookie = {
                "name": name,
                "value": str(info),
                "domain": ".wps.cn",
                "path": "/",
            }
        result.append(cookie)

    # 添加 kdocs.cn 域名的 Cookie（打卡页面需要）
    kdocs_cookies = []
    for c in result:
        if c["domain"] == ".wps.cn":
            kdocs_cookie = c.copy()
            kdocs_cookie["domain"] = ".kdocs.cn"
            kdocs_cookies.append(kdocs_cookie)

    result.extend(kdocs_cookies)

    return result


async def fill_and_submit_form(page: Page, input_name: str) -> tuple[bool, str]:
    """
    填写并提交打卡表单（基于原 main.py）

    Args:
        page: Playwright 页面对象
        input_name: 打卡填写的内容

    Returns:
        (是否成功, 消息)
    """
    try:
        logger.info("等待页面加载完成...")
        await page.wait_for_load_state("load")

        # 截图调试（可选，出问题时启用）
        # await page.screenshot(path="debug_step1.png")

        # 输入指定的文本
        logger.info(f"往文本框中填写内容：{input_name}")
        textbox = page.get_by_role("textbox", name="请输入")
        await textbox.fill(input_name)

        # 点击完成校验按钮
        logger.info("点击 '完成校验' 按钮")
        button = page.get_by_role("button", name="完成校验")
        await button.click()

        # 等待页面再次加载完成
        logger.info("等待页面再次加载完成...")
        await page.wait_for_load_state("load")
        await page.wait_for_load_state("networkidle")

        # 检查是否出现提示信息（等待最多3秒）
        prompt_text = "您之前填写过此打卡，是否接着上次继续填写"
        prompt_locator = page.locator(f"text={prompt_text}")
        try:
            await prompt_locator.wait_for(state="visible", timeout=3000)
            logger.info("检测到提示信息，点击 '取消' 按钮...")
            cancel_button = page.get_by_role("button", name="取消")
            await cancel_button.click()
            await asyncio.sleep(1)  # 等待弹窗关闭
        except:
            logger.info("未检测到继续填写提示，继续执行...")

        # 等待并点击打卡按钮
        logger.info("等待并点击打卡按钮...")
        circle_button_selector = ".src-pages-clock-components-common-clock-button-circle-index__container"
        await page.wait_for_selector(circle_button_selector, timeout=15000)
        button_circle = page.locator(circle_button_selector)
        await button_circle.click()

        # 等待点击后的响应
        await asyncio.sleep(2)

        # 等待填写成功的提示（多种检测方式）
        logger.info("等待打卡结果...")

        # 方式1：检测成功图片
        success_img = page.get_by_role("img", name="填写成功")
        # 方式2：检测成功文字
        success_text = page.locator("text=填写成功")
        # 方式3：检测已打卡状态
        already_checked = page.locator("text=已打卡")

        try:
            # 等待任意一个成功标志出现
            await page.wait_for_function(
                """() => {
                    return document.body.innerText.includes('填写成功') ||
                           document.body.innerText.includes('已打卡') ||
                           document.body.innerText.includes('打卡成功');
                }""",
                timeout=30000
            )
            logger.info("表单填写并提交完成")
            return True, "打卡成功"

        except Exception as wait_error:
            # 检查页面内容，看是否实际已成功
            page_text = await page.inner_text("body")
            if "填写成功" in page_text or "已打卡" in page_text or "打卡成功" in page_text:
                logger.info("检测到成功标志（通过文本）")
                return True, "打卡成功"

            logger.warning(f"未检测到成功标志: {wait_error}")
            # 截图保存以便调试
            try:
                await page.screenshot(path="data/debug_checkin_fail.png")
                logger.info("已保存调试截图到 data/debug_checkin_fail.png")
            except:
                pass

            return False, "未检测到打卡成功提示"

    except Exception as e:
        error_msg = str(e)
        logger.error(f"表单填写失败: {error_msg}")
        return False, error_msg


def send_user_notification(user: User, success: bool, message: str):
    """
    使用用户个人的 Server酱 SendKey 发送通知

    Args:
        user: 用户对象
        success: 是否成功
        message: 消息内容
    """
    if not user.sendkey:
        logger.debug(f"用户 {user.id} 未配置 SendKey，跳过通知")
        return

    try:
        title = f"WPS打卡{'成功' if success else '失败'}"
        content = f"用户：{user.nickname or user.wps_uid}\n结果：{message}"

        params = {
            "title": title,
            "desp": content
        }
        url_params = urlencode(params)
        api_url = f"https://sctapi.ftqq.com/{user.sendkey}.send?{url_params}"

        response = requests.get(api_url, timeout=10)

        if response.status_code == 200:
            logger.info(f"用户 {user.id} 通知发送成功")
        else:
            logger.error(f"用户 {user.id} 通知发送失败, 状态码: {response.status_code}")

    except Exception as e:
        logger.error(f"用户 {user.id} 发送通知出错: {e}")


# ==================== 测试 ====================

async def test_checkin():
    """测试打卡功能"""
    logging.basicConfig(level=logging.INFO)

    # 初始化数据库
    await db.init()

    # 获取第一个用户测试
    users = await db.get_all_users()
    if users:
        user = users[0]
        logger.info(f"测试用户: {user.nickname} (ID: {user.id})")
        await do_checkin_for_user(user.id)
    else:
        logger.info("没有用户，请先扫码登录")


if __name__ == "__main__":
    asyncio.run(test_checkin())
