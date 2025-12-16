"""
数据库模块 - SQLite 版本
管理用户数据和打卡记录

SQLite 知识点：
1. Python 自带 sqlite3 模块，无需安装
2. 数据库就是一个 .db 文件
3. SQL 语法和 MySQL 几乎一样
4. 我们用 aiosqlite 实现异步操作（需要 pip install aiosqlite）
"""

import os
import json
import logging
import aiosqlite
from datetime import datetime
from typing import Optional, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 数据库文件路径
DATABASE_PATH = "data/apparition.db"


@dataclass
class User:
    """用户数据模型"""
    id: int
    wps_uid: int                    # WPS 用户ID
    nickname: str                   # 昵称（用于显示）
    cookies: str                    # Cookie JSON 字符串
    input_name: str                 # 打卡填写的内容（如 "1000&董泽谦"）
    latitude: float                 # 打卡位置 - 纬度
    longitude: float                # 打卡位置 - 经度
    is_active: bool                 # 是否启用自动打卡
    created_at: str                 # 创建时间
    last_checkin: Optional[str]     # 最后打卡时间
    sendkey: Optional[str] = None   # Server酱 SendKey（用于打卡通知）
    checkin_hour: Optional[int] = None    # 自定义打卡小时（None则用系统时间）
    checkin_minute: Optional[int] = None  # 自定义打卡分钟


@dataclass
class CheckinLog:
    """打卡记录"""
    id: int
    user_id: int
    status: str         # success / failed
    message: str        # 详细信息
    created_at: str


@dataclass
class ScheduleConfig:
    """打卡时间配置"""
    id: int
    name: str           # 任务名称（如 "早上打卡"）
    hour: int           # 小时（0-23）
    minute: int         # 分钟（0-59）
    is_enabled: bool    # 是否启用
    created_at: str


class Database:
    """
    数据库操作类

    使用方法：
        db = Database()
        await db.init()  # 初始化（创建表）

        # 添加用户
        user_id = await db.add_user(wps_uid=123, nickname="张三", ...)

        # 查询用户
        user = await db.get_user(user_id)
        users = await db.get_all_active_users()
    """

    def __init__(self, db_path: str = DATABASE_PATH):
        """
        初始化数据库

        Args:
            db_path: 数据库文件路径，默认 data/apparition.db
        """
        self.db_path = db_path

        # 确保 data 目录存在
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

    async def init(self):
        """
        初始化数据库，创建表结构

        SQLite 知识点：
        - CREATE TABLE IF NOT EXISTS: 如果表不存在才创建，避免重复创建报错
        - INTEGER PRIMARY KEY AUTOINCREMENT: 自增主键（类似 MySQL 的 AUTO_INCREMENT）
        - TEXT: 文本类型（SQLite 不区分 VARCHAR 长度）
        - REAL: 浮点数（用于经纬度）
        - BOOLEAN: 布尔值（SQLite 实际存储为 0/1）
        - DEFAULT: 默认值
        """
        logger.info(f"初始化数据库: {self.db_path}")

        async with aiosqlite.connect(self.db_path) as db:
            # 创建用户表
            await db.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wps_uid INTEGER UNIQUE NOT NULL,
                    nickname TEXT DEFAULT '',
                    cookies TEXT NOT NULL,
                    input_name TEXT DEFAULT '',
                    latitude REAL DEFAULT 100,
                    longitude REAL DEFAULT 100,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    last_checkin TEXT,
                    sendkey TEXT DEFAULT '',
                    checkin_hour INTEGER,
                    checkin_minute INTEGER
                )
            ''')

            # 创建打卡记录表
            await db.execute('''
                CREATE TABLE IF NOT EXISTS checkin_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            ''')

            # 创建索引，加速查询
            # 索引就像书的目录，让查找更快
            await db.execute('''
                CREATE INDEX IF NOT EXISTS idx_users_wps_uid ON users (wps_uid)
            ''')
            await db.execute('''
                CREATE INDEX IF NOT EXISTS idx_checkin_logs_user_id ON checkin_logs (user_id)
            ''')

            # 创建打卡时间配置表
            await db.execute('''
                CREATE TABLE IF NOT EXISTS schedule_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    hour INTEGER NOT NULL,
                    minute INTEGER NOT NULL,
                    is_enabled BOOLEAN DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 检查是否需要插入默认配置
            async with db.execute('SELECT COUNT(*) FROM schedule_configs') as cursor:
                count = (await cursor.fetchone())[0]

            if count == 0:
                # 插入默认的打卡时间 - 晚上7点
                await db.execute('''
                    INSERT INTO schedule_configs (name, hour, minute, is_enabled)
                    VALUES ('晚间打卡', 19, 0, 1)
                ''')
                logger.info("已添加默认打卡时间配置: 19:00")

            await db.commit()
            logger.info("数据库表创建完成")

            # 数据库迁移：为旧表添加新列
            migrations = [
                ('sendkey', 'ALTER TABLE users ADD COLUMN sendkey TEXT DEFAULT ""'),
                ('checkin_hour', 'ALTER TABLE users ADD COLUMN checkin_hour INTEGER'),
                ('checkin_minute', 'ALTER TABLE users ADD COLUMN checkin_minute INTEGER'),
            ]
            for col_name, sql in migrations:
                try:
                    await db.execute(sql)
                    await db.commit()
                    logger.info(f"数据库迁移：添加 {col_name} 列")
                except:
                    pass  # 列已存在，忽略错误

    async def add_user(
        self,
        wps_uid: int,
        cookies: dict,
        nickname: str = "",
        input_name: str = "",
        latitude: float = 100.100,
        longitude: float = 100.100
    ) -> int:
        """
        添加新用户

        Args:
            wps_uid: WPS 用户ID
            cookies: Cookie 字典
            nickname: 昵称
            input_name: 打卡填写内容
            latitude: 纬度
            longitude: 经度

        Returns:
            新用户的 ID

        SQLite 知识点：
        - INSERT OR REPLACE: 如果主键/唯一键冲突，则替换（类似 MySQL 的 REPLACE INTO）
        - lastrowid: 获取最后插入行的 ID
        """
        cookies_json = json.dumps(cookies, ensure_ascii=False)

        async with aiosqlite.connect(self.db_path) as db:
            # 检查用户是否已存在
            async with db.execute(
                'SELECT id FROM users WHERE wps_uid = ?',
                (wps_uid,)
            ) as cursor:
                existing = await cursor.fetchone()

            if existing:
                # 用户已存在，更新 Cookie
                await db.execute('''
                    UPDATE users
                    SET cookies = ?, nickname = ?
                    WHERE wps_uid = ?
                ''', (cookies_json, nickname, wps_uid))
                await db.commit()
                logger.info(f"更新用户 Cookie: wps_uid={wps_uid}")
                return existing[0]
            else:
                # 新用户，插入记录
                cursor = await db.execute('''
                    INSERT INTO users (wps_uid, nickname, cookies, input_name, latitude, longitude)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (wps_uid, nickname, cookies_json, input_name, latitude, longitude))
                await db.commit()
                logger.info(f"添加新用户: wps_uid={wps_uid}, id={cursor.lastrowid}")
                return cursor.lastrowid

    async def get_user(self, user_id: int) -> Optional[User]:
        """
        根据 ID 获取用户

        SQLite 知识点：
        - fetchone(): 获取一行结果，没有则返回 None
        - 结果是元组，按 SELECT 的列顺序排列
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                'SELECT * FROM users WHERE id = ?',
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()

        if row:
            return self._row_to_user(row)
        return None

    async def get_user_by_wps_uid(self, wps_uid: int) -> Optional[User]:
        """根据 WPS UID 获取用户"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                'SELECT * FROM users WHERE wps_uid = ?',
                (wps_uid,)
            ) as cursor:
                row = await cursor.fetchone()

        if row:
            return self._row_to_user(row)
        return None

    async def get_all_users(self) -> List[User]:
        """获取所有用户"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute('SELECT * FROM users') as cursor:
                rows = await cursor.fetchall()

        return [self._row_to_user(row) for row in rows]

    async def get_all_active_users(self) -> List[User]:
        """
        获取所有启用的用户（用于定时打卡）

        SQLite 知识点：
        - WHERE is_active = 1: SQLite 中布尔值用 0/1 表示
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                'SELECT * FROM users WHERE is_active = 1'
            ) as cursor:
                rows = await cursor.fetchall()

        return [self._row_to_user(row) for row in rows]

    async def update_user(
        self,
        user_id: int,
        **kwargs
    ) -> bool:
        """
        更新用户信息

        Args:
            user_id: 用户 ID
            **kwargs: 要更新的字段，如 nickname="新名字", is_active=False

        Returns:
            是否更新成功

        用法：
            await db.update_user(1, nickname="新名字", input_name="1234&张三")
        """
        if not kwargs:
            return False

        # 构造 SET 子句
        # 例如: SET nickname = ?, input_name = ?
        set_clause = ', '.join(f'{key} = ?' for key in kwargs.keys())
        values = list(kwargs.values())
        values.append(user_id)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f'UPDATE users SET {set_clause} WHERE id = ?',
                values
            )
            await db.commit()

        logger.info(f"更新用户 {user_id}: {kwargs}")
        return True

    async def update_user_cookies(self, user_id: int, cookies: dict):
        """更新用户 Cookie"""
        cookies_json = json.dumps(cookies, ensure_ascii=False)
        await self.update_user(user_id, cookies=cookies_json)

    async def update_last_checkin(self, user_id: int):
        """更新最后打卡时间"""
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        await self.update_user(user_id, last_checkin=now)

    async def delete_user(self, user_id: int) -> bool:
        """删除用户"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('DELETE FROM users WHERE id = ?', (user_id,))
            await db.commit()

        logger.info(f"删除用户: {user_id}")
        return True

    async def add_checkin_log(
        self,
        user_id: int,
        status: str,
        message: str = ""
    ) -> int:
        """
        添加打卡记录

        Args:
            user_id: 用户 ID
            status: 状态 (success/failed)
            message: 详细信息
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute('''
                INSERT INTO checkin_logs (user_id, status, message)
                VALUES (?, ?, ?)
            ''', (user_id, status, message))
            await db.commit()
            return cursor.lastrowid

    async def get_user_checkin_logs(
        self,
        user_id: int,
        limit: int = 10
    ) -> List[CheckinLog]:
        """
        获取用户的打卡记录

        SQLite 知识点：
        - ORDER BY created_at DESC: 按时间倒序（最新的在前）
        - LIMIT: 限制返回条数
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute('''
                SELECT * FROM checkin_logs
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            ''', (user_id, limit)) as cursor:
                rows = await cursor.fetchall()

        return [CheckinLog(
            id=row[0],
            user_id=row[1],
            status=row[2],
            message=row[3],
            created_at=row[4]
        ) for row in rows]

    def _row_to_user(self, row: tuple) -> User:
        """将数据库行转换为 User 对象"""
        return User(
            id=row[0],
            wps_uid=row[1],
            nickname=row[2],
            cookies=row[3],
            input_name=row[4],
            latitude=row[5],
            longitude=row[6],
            is_active=bool(row[7]),
            created_at=row[8],
            last_checkin=row[9],
            sendkey=row[10] if len(row) > 10 else "",
            checkin_hour=row[11] if len(row) > 11 else None,
            checkin_minute=row[12] if len(row) > 12 else None
        )

    # ==================== 打卡时间配置管理 ====================

    async def get_all_schedules(self) -> List[ScheduleConfig]:
        """获取所有打卡时间配置"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                'SELECT * FROM schedule_configs ORDER BY hour, minute'
            ) as cursor:
                rows = await cursor.fetchall()

        return [self._row_to_schedule(row) for row in rows]

    async def get_enabled_schedules(self) -> List[ScheduleConfig]:
        """获取所有启用的打卡时间配置"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                'SELECT * FROM schedule_configs WHERE is_enabled = 1 ORDER BY hour, minute'
            ) as cursor:
                rows = await cursor.fetchall()

        return [self._row_to_schedule(row) for row in rows]

    async def add_schedule(self, name: str, hour: int, minute: int) -> int:
        """
        添加打卡时间

        Args:
            name: 任务名称
            hour: 小时（0-23）
            minute: 分钟（0-59）

        Returns:
            新配置的 ID
        """
        # 验证时间范围
        if not (0 <= hour <= 23):
            raise ValueError("小时必须在 0-23 之间")
        if not (0 <= minute <= 59):
            raise ValueError("分钟必须在 0-59 之间")

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute('''
                INSERT INTO schedule_configs (name, hour, minute)
                VALUES (?, ?, ?)
            ''', (name, hour, minute))
            await db.commit()
            logger.info(f"添加打卡时间: {name} ({hour:02d}:{minute:02d})")
            return cursor.lastrowid

    async def update_schedule(self, schedule_id: int, **kwargs) -> bool:
        """
        更新打卡时间配置

        用法：
            await db.update_schedule(1, hour=9, minute=0)
            await db.update_schedule(1, is_enabled=False)
        """
        if not kwargs:
            return False

        # 验证时间范围
        if 'hour' in kwargs and not (0 <= kwargs['hour'] <= 23):
            raise ValueError("小时必须在 0-23 之间")
        if 'minute' in kwargs and not (0 <= kwargs['minute'] <= 59):
            raise ValueError("分钟必须在 0-59 之间")

        set_clause = ', '.join(f'{key} = ?' for key in kwargs.keys())
        values = list(kwargs.values())
        values.append(schedule_id)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f'UPDATE schedule_configs SET {set_clause} WHERE id = ?',
                values
            )
            await db.commit()

        logger.info(f"更新打卡时间 {schedule_id}: {kwargs}")
        return True

    async def delete_schedule(self, schedule_id: int) -> bool:
        """删除打卡时间配置"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'DELETE FROM schedule_configs WHERE id = ?',
                (schedule_id,)
            )
            await db.commit()

        logger.info(f"删除打卡时间: {schedule_id}")
        return True

    async def toggle_schedule(self, schedule_id: int) -> bool:
        """切换打卡时间的启用状态，返回新状态"""
        async with aiosqlite.connect(self.db_path) as db:
            # 获取当前状态
            async with db.execute(
                'SELECT is_enabled FROM schedule_configs WHERE id = ?',
                (schedule_id,)
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                return False

            new_status = not bool(row[0])

            # 更新状态
            await db.execute(
                'UPDATE schedule_configs SET is_enabled = ? WHERE id = ?',
                (new_status, schedule_id)
            )
            await db.commit()

        logger.info(f"切换打卡时间 {schedule_id} 状态为: {new_status}")
        return new_status

    def _row_to_schedule(self, row: tuple) -> ScheduleConfig:
        """将数据库行转换为 ScheduleConfig 对象"""
        return ScheduleConfig(
            id=row[0],
            name=row[1],
            hour=row[2],
            minute=row[3],
            is_enabled=bool(row[4]),
            created_at=row[5]
        )


# 全局数据库实例
db = Database()


async def test_database():
    """测试数据库功能"""
    import logging
    logging.basicConfig(level=logging.INFO)

    # 初始化
    await db.init()

    # 添加测试用户
    user_id = await db.add_user(
        wps_uid=123456,
        cookies={"rtk": "test_token", "wps_sid": "test_sid"},
        nickname="测试用户",
        input_name="1234&张三"
    )
    print(f"添加用户，ID: {user_id}")

    # 查询用户
    user = await db.get_user(user_id)
    print(f"查询用户: {user}")

    # 获取所有用户
    users = await db.get_all_users()
    print(f"所有用户: {len(users)} 个")

    # 添加打卡记录
    log_id = await db.add_checkin_log(user_id, "success", "打卡成功")
    print(f"添加打卡记录，ID: {log_id}")

    # 查询打卡记录
    logs = await db.get_user_checkin_logs(user_id)
    print(f"打卡记录: {logs}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_database())
