import asyncio
import aiosqlite
import json
from config import DB_PATH


class Database:
    _instance = None
    _lock = asyncio.Lock()

    def __init__(self):
        self.db_path = DB_PATH
        self.db = None

    @classmethod
    async def get_instance(cls):
        if cls._instance is not None and cls._instance.db is not None:
            # Fast path – check connection is still alive
            try:
                await cls._instance.db.execute("SELECT 1")
                return cls._instance
            except Exception:
                cls._instance = None
        # Slow path – initialise or reconnect under lock
        async with cls._lock:
            if cls._instance is not None and cls._instance.db is not None:
                try:
                    await cls._instance.db.execute("SELECT 1")
                    return cls._instance
                except Exception:
                    cls._instance = None
            inst = cls()
            await inst.connect()
            cls._instance = inst
        return cls._instance

    async def connect(self):
        self.db = await aiosqlite.connect(self.db_path)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA synchronous=NORMAL")
        await self.db.execute("PRAGMA cache_size=10000")
        await self.db.execute("PRAGMA busy_timeout=30000")
        await self.db.execute("PRAGMA temp_store=MEMORY")
        await self.db.execute("PRAGMA mmap_size=67108864")
        await self.create_tables()

    async def create_tables(self):
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                joined_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS polls (
                poll_id TEXT PRIMARY KEY,
                creator_id INTEGER,
                question TEXT,
                options TEXT,
                log_channel_id INTEGER DEFAULT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                is_active INTEGER DEFAULT 1,
                FOREIGN KEY (creator_id) REFERENCES users(user_id)
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                poll_id TEXT,
                user_id INTEGER,
                option_index INTEGER,
                voted_at TEXT DEFAULT (datetime('now')),
                UNIQUE(poll_id, user_id),
                FOREIGN KEY (poll_id) REFERENCES polls(poll_id),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_votes_poll ON votes(poll_id)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_votes_user ON votes(user_id)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_polls_creator ON polls(creator_id)")
        await self.db.commit()

    # ───────────── User operations ─────────────

    async def add_user(self, user_id: int, username: str, first_name: str, last_name: str):
        await self.db.execute(
            """INSERT INTO users (user_id, joined_at)
               VALUES (?, COALESCE(
                   (SELECT joined_at FROM users WHERE user_id = ?),
                   datetime('now')
               ))
               ON CONFLICT(user_id) DO NOTHING""",
            (user_id, user_id),
        )
        await self.db.commit()

    async def get_user(self, user_id: int):
        cursor = await self.db.execute(
            "SELECT user_id, joined_at FROM users WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_all_users(self, page: int = 1, per_page: int = 10):
        offset = (page - 1) * per_page
        cursor = await self.db.execute(
            "SELECT user_id, joined_at FROM users ORDER BY joined_at DESC LIMIT ? OFFSET ?",
            (per_page, offset),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_users_count(self) -> int:
        cursor = await self.db.execute("SELECT COUNT(*) FROM users")
        row = await cursor.fetchone()
        return row[0]

    async def get_all_user_ids(self) -> list[int]:
        cursor = await self.db.execute("SELECT user_id FROM users")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

    # ───────────── Poll operations ─────────────

    async def create_poll(
        self,
        poll_id: str,
        creator_id: int,
        question: str,
        options: list[str],
        log_channel_id: int | None = None,
    ):
        await self.db.execute(
            "INSERT INTO polls (poll_id, creator_id, question, options, log_channel_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (poll_id, creator_id, question, json.dumps(options, ensure_ascii=False), log_channel_id),
        )
        await self.db.commit()

    async def get_poll(self, poll_id: str) -> dict | None:
        cursor = await self.db.execute(
            "SELECT * FROM polls WHERE poll_id = ? AND is_active = 1", (poll_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_polls_by_creator(self, creator_id: int) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM polls WHERE creator_id = ? AND is_active = 1 ORDER BY created_at DESC",
            (creator_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_all_polls(self, page: int = 1, per_page: int = 10) -> list[dict]:
        offset = (page - 1) * per_page
        cursor = await self.db.execute(
            "SELECT * FROM polls WHERE is_active = 1 ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (per_page, offset),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_polls_count(self) -> int:
        cursor = await self.db.execute("SELECT COUNT(*) FROM polls WHERE is_active = 1")
        row = await cursor.fetchone()
        return row[0]

    async def delete_poll(self, poll_id: str, user_id: int | None = None) -> bool:
        if user_id:
            cursor = await self.db.execute(
                "UPDATE polls SET is_active = 0 WHERE poll_id = ? AND creator_id = ?",
                (poll_id, user_id),
            )
        else:
            cursor = await self.db.execute(
                "UPDATE polls SET is_active = 0 WHERE poll_id = ?", (poll_id,)
            )
        await self.db.commit()
        return cursor.rowcount > 0

    # ───────────── Vote operations ─────────────

    async def add_vote(self, poll_id: str, user_id: int, option_index: int) -> bool:
        for attempt in range(3):
            try:
                await self.db.execute(
                    "INSERT INTO votes (poll_id, user_id, option_index) VALUES (?, ?, ?)",
                    (poll_id, user_id, option_index),
                )
                await self.db.commit()
                return True
            except Exception as e:
                try:
                    await self.db.rollback()
                except Exception:
                    pass
                if "locked" in str(e).lower() and attempt < 2:
                    await asyncio.sleep(0.3 * (attempt + 1))
                    continue
                return False
        return False

    async def get_vote_number(self, poll_id: str, user_id: int) -> int:
        """Return the sequential vote number for this user in this poll (1-based)."""
        cursor = await self.db.execute(
            "SELECT COUNT(*) FROM votes WHERE poll_id = ? AND id <= "
            "(SELECT id FROM votes WHERE poll_id = ? AND user_id = ?)",
            (poll_id, poll_id, user_id),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def has_voted(self, poll_id: str, user_id: int) -> bool:
        cursor = await self.db.execute(
            "SELECT 1 FROM votes WHERE poll_id = ? AND user_id = ?",
            (poll_id, user_id),
        )
        return await cursor.fetchone() is not None

    async def get_vote_counts(self, poll_id: str) -> dict[int, int]:
        cursor = await self.db.execute(
            "SELECT option_index, COUNT(*) as cnt FROM votes WHERE poll_id = ? GROUP BY option_index",
            (poll_id,),
        )
        rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}

    async def get_total_votes(self, poll_id: str) -> int:
        cursor = await self.db.execute(
            "SELECT COUNT(*) FROM votes WHERE poll_id = ?", (poll_id,)
        )
        row = await cursor.fetchone()
        return row[0]

    async def get_user_votes(self, user_id: int, page: int = 1, per_page: int = 10) -> list[dict]:
        offset = (page - 1) * per_page
        cursor = await self.db.execute(
            """SELECT v.poll_id, v.option_index, v.voted_at, p.question, p.options, p.log_channel_id
               FROM votes v
               JOIN polls p ON v.poll_id = p.poll_id
               WHERE v.user_id = ?
               ORDER BY v.voted_at DESC
               LIMIT ? OFFSET ?""",
            (user_id, per_page, offset),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_user_votes_count(self, user_id: int) -> int:
        cursor = await self.db.execute(
            "SELECT COUNT(*) FROM votes WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0]

    async def get_log_channel_link(self, log_channel_id: int, bot) -> str | None:
        """Try to get a public link for a log channel."""
        try:
            chat = await bot.get_chat(log_channel_id)
            if chat.username:
                return f"https://t.me/{chat.username}"
            return chat.invite_link
        except Exception:
            return None

    async def get_log_channel_mention(self, log_channel_id: int, bot) -> str | None:
        """Get @username for a log channel, or fallback to link."""
        try:
            chat = await bot.get_chat(log_channel_id)
            if chat.username:
                return f"@{chat.username}"
            return chat.invite_link
        except Exception:
            return None

    # ───────────── Settings operations ─────────────

    async def set_setting(self, key: str, value: str):
        await self.db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value)),
        )
        await self.db.commit()

    async def set_settings_batch(self, settings: dict[str, str]):
        """Write multiple settings atomically in a single commit."""
        for key, value in settings.items():
            await self.db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, str(value)),
            )
        await self.db.commit()

    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        cursor = await self.db.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        return row[0] if row else default

    async def close(self):
        if self.db:
            await self.db.close()
