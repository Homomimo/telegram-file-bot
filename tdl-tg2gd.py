import os
import json
import shlex
import logging
import asyncio
import psutil
import humanize
import time
import subprocess
from telethon import TelegramClient, events
from telethon.sessions import MemorySession
from telethon.tl.types import MessageMediaDocument
from typing import Optional, Dict, Any

# 日志配置
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logging.getLogger('telethon').setLevel(logging.WARNING)
logging.getLogger('asyncio').setLevel(logging.WARNING)

# 基础配置
API_ID = 2        # 替换为你的 API ID
API_HASH = ''
BOT_TOKEN = ''

# 目标频道配置 (新增)
TARGET_CHANNEL = '2035522306'  # 替换为你想要转发到的目标频道ID (或 @username)

# 管理员配置
ADMIN_IDS = [6242875809]  # 替换为你的 Telegram ID

# 文件限制配置
MAX_FILE_SIZE = 4 * 1024 * 1024 * 1024  # 4GB
ALLOWED_TYPES = {
    'video/mp4', 'video/x-matroska', 'video/quicktime',
    'application/zip', 'application/x-rar-compressed',
    'application/x-7z-compressed', 'application/pdf',
    'image/jpeg', 'image/png', 'image/gif',
    'audio/mpeg', 'audio/mp4', 'audio/ogg'
}

# TDL 配置
TDL_PATH = "/usr/local/bin/tdl"  # TDL 可执行文件路径，如果在 PATH 中可以直接使用 "tdl"

class ForwardHistory:
    """转发历史记录类"""
    def __init__(self, history_file="forward_history.json"):
        self.history_file = history_file
        self.history = self._load_history()
        
    def _load_history(self) -> list:
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return []
        except Exception as e:
            logger.error(f"Error loading history: {e}")
            return []
            
    def _save_history(self):
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, ensure_ascii=False, indent=2)
            logger.info(f"成功保存历史记录，记录条数: {len(self.history)}")
        except Exception as e:
            logger.error(f"保存历史记录失败: {e}")
            
    def add_forward(self, file_id: int, filename: str, source_link: str, size: int, success: bool):
        record = {
            'id': len(self.history) + 1,
            'file_id': file_id,
            'filename': filename,
            'source_link': source_link,
            'size': size,
            'size_human': humanize.naturalsize(size),
            'success': success,
            'timestamp': time.time(),
            'date': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        self.history.append(record)
        self._save_history()
        return record['id']
        
    def get_all(self) -> list:
        return self.history
        
    def get_by_id(self, record_id: int) -> dict:
        for record in self.history:
            if record['id'] == record_id:
                return record
        return None
        
    def remove_by_id(self, record_id: int) -> bool:
        for i, record in enumerate(self.history):
            if record['id'] == record_id:
                del self.history[i]
                self._save_history()
                return True
        return False

class FileStats:
    """文件处理统计类"""
    def __init__(self):
        self.processed_count = 0
        self.total_size = 0
        self.start_time = time.time()
        self.successful_forwards = 0
        self.failed_forwards = 0

    def add_processed_file(self, size: int, success: bool):
        self.processed_count += 1
        self.total_size += size
        if success:
            self.successful_forwards += 1
        else:
            self.failed_forwards += 1

    def get_stats(self) -> Dict[str, Any]:
        return {
            'processed_count': self.processed_count,
            'total_size': humanize.naturalsize(self.total_size),
            'success_rate': f"{(self.successful_forwards/self.processed_count*100):.1f}%" if self.processed_count > 0 else "0%",
            'uptime': humanize.naturaldelta(time.time() - self.start_time)
        }

class TDLForwarder:
    """TDL 转发器类"""
    def __init__(self, tdl_path=TDL_PATH):
        self.tdl_path = tdl_path
        
    def verify_tdl(self):
        if not os.path.exists(self.tdl_path):
            raise FileNotFoundError(f"TDL可执行文件未找到: {self.tdl_path}")
        if not os.access(self.tdl_path, os.X_OK):
            raise PermissionError(f"TDL文件无执行权限: {self.tdl_path}")
        
    async def forward_file(self, message_link: str, progress_callback=None) -> bool:
        """使用 TDL 转发文件"""
        
        # 构建转发命令
        cmd = [
            self.tdl_path,
            "forward",
            "--from", message_link,
            "--to", str(TARGET_CHANNEL)
            "--mode", "clone"
        ]
        
        logger.info(f"Running TDL command: {' '.join(cmd)}")
        
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout_lines = []
            stderr_lines = []
            
            while True:
                stdout_line = await process.stdout.readline()
                if stdout_line:
                    stdout_lines.append(stdout_line)
                    line_str = stdout_line.decode('utf-8', errors='ignore').strip()
                    logger.debug(f"TDL stdout: {line_str}")
                    
                    # 转发通常很快，如果 TDL 仍输出进度，会在这里回调处理
                    if progress_callback:
                        await progress_callback(line_str)
                
                stderr_line = await process.stderr.readline()
                if stderr_line:
                    stderr_lines.append(stderr_line)
                    line_str = stderr_line.decode('utf-8', errors='ignore').strip()
                    logger.error(f"TDL stderr: {line_str}")
                
                if not stdout_line and not stderr_line:
                    if process.returncode is not None:
                        break
                    await asyncio.sleep(0.1)
            
            await process.wait()
            
            if process.returncode == 0:
                logger.info("TDL转发成功")
                return True
            else:
                logger.error(f"TDL转发失败，返回码: {process.returncode}")
                return False
                
        except Exception as e:
            logger.error(f"Error running TDL command: {str(e)}")
            return False

class TelegramBot:
    """Telegram 机器人主类"""
    def __init__(self):
        self.client = TelegramClient(MemorySession(), API_ID, API_HASH)
        self.stats = FileStats()
        self.history = ForwardHistory()
        self.start_time = time.time()
        self.tdl_forwarder = TDLForwarder()

    async def check_file(self, event) -> tuple[bool, Optional[str]]:
        if not event.message.media:
            return False, "不是文件"
            
        file_size = event.message.file.size
        if file_size > MAX_FILE_SIZE:
            return False, f"文件太大了！最大支持 {humanize.naturalsize(MAX_FILE_SIZE)}"
            
        mime_type = event.message.file.mime_type
        if mime_type not in ALLOWED_TYPES:
            return False, f"不支持的文件类型：{mime_type}"
            
        return True, None

    async def get_system_stats(self) -> Dict[str, Any]:
        process = psutil.Process()
        memory_info = process.memory_info()
        return {
            'memory_used': humanize.naturalsize(memory_info.rss),
            'cpu_percent': process.cpu_percent(),
            'uptime': humanize.naturaldelta(time.time() - self.start_time)
        }

    async def handle_admin_commands(self, event) -> bool:
        if event.sender_id not in ADMIN_IDS:
            return False
            
        command_text = event.message.text.lower()
        command_parts = command_text.split()
        command = command_parts[0]
        
        if command == '/stats':
            sys_stats = await self.get_system_stats()
            file_stats = self.stats.get_stats()
            
            await event.respond(
                f"📊 机器人状态\n\n"
                f"系统信息：\n"
                f"💾 内存使用：{sys_stats['memory_used']}\n"
                f"💻 CPU 使用：{sys_stats['cpu_percent']}%\n"
                f"⏱ 运行时间：{sys_stats['uptime']}\n\n"
                f"文件处理：\n"
                f"📁 总转发文件：{file_stats['processed_count']}\n"
                f"📦 总转发大小：{file_stats['total_size']}\n"
                f"✅ 成功率：{file_stats['success_rate']}"
            )
            return True
        elif command == '/restart':
            await event.respond("🔄 正在重启机器人...")
            await self.restart_bot()
            return True
        elif command == '/history':
            history = self.history.get_all()
            if not history:
                await event.respond("📝 暂无转发历史记录")
                return True
                
            history_text = "📝 转发历史记录：\n\n"
            for record in history[-10:]:
                history_text += (
                    f"ID: {record['id']}\n"
                    f"📁 文件名: {record['filename']}\n"
                    f"📦 大小: {record['size_human']}\n"
                    f"📅 日期: {record['date']}\n"
                    f"🔗 来源链接: {record['source_link']}\n\n"
                )
                
            history_text += f"共 {len(history)} 条记录，显示最近 {min(10, len(history))} 条"
            await event.respond(history_text)
            return True
        elif command == '/delete':
            if len(command_parts) < 2:
                await event.respond("❌ 用法: /delete <文件ID>")
                return True
                
            try:
                file_id = int(command_parts[1])
                record = self.history.get_by_id(file_id)
                
                if not record:
                    await event.respond(f"❌ 找不到ID为 {file_id} 的历史记录")
                    return True
                    
                self.history.remove_by_id(file_id)
                await event.respond(
                    f"✅ 已清除历史记录:\n"
                    f"📁 文件名: {record['filename']}"
                )
                    
            except ValueError:
                await event.respond("❌ 记录ID必须是数字")
                
            return True
            
        return False

    async def restart_bot(self):
        import sys
        script_path = os.path.abspath(sys.argv[0])
        try:
            await self.stop()
            current_dir = os.path.dirname(script_path)
            restart_cmd = f"cd {current_dir} && python3 {script_path} > google_bot.log 2>&1 &"
            subprocess.run(restart_cmd, shell=True)
            await asyncio.sleep(3)
            os._exit(0)
        except Exception as e:
            for admin_id in ADMIN_IDS:
                try:
                    await self.client.send_message(admin_id, f"🔄 重启失败: {str(e)}")
                except:
                    pass

    async def handle_file(self, event, processing_msg):
        """处理文件转发"""
        success = False
        file_size = event.message.file.size
        file_name = ""
        start_time = time.time()
        
        try:
            can_process, error_msg = await self.check_file(event)
            if not can_process:
                await processing_msg.edit(f"❌ {error_msg}")
                return

            if isinstance(event.message.media, MessageMediaDocument):
                orig_name = event.message.file.name if event.message.file.name else f"file_{event.message.id}"
                file_name = orig_name
            else:
                file_name = f"file_{event.message.id}"
            
            message_link = None
            
            # --- 原代码保留的链接提取逻辑 ---
            if event.message.forward:
                from_id = event.message.forward.from_id
                if hasattr(from_id, 'channel_id'):
                    channel_id = from_id.channel_id
                    forward_msg_id = event.message.forward.channel_post
                    try:
                        channel_entity = await event.client.get_entity(channel_id)
                        if hasattr(channel_entity, 'username') and channel_entity.username:
                            message_link = f"https://t.me/{channel_entity.username}/{forward_msg_id}"
                        else:
                            original_message = await event.client.get_messages(channel_id, ids=forward_msg_id)
                            if original_message and hasattr(original_message, 'id'):
                                original_chat = await event.client.get_entity(original_message.chat_id)
                                if hasattr(original_chat, 'username') and original_chat.username:
                                    message_link = f"https://t.me/{original_chat.username}/{original_message.id}"
                                else:
                                    message_link = original_message.link
                            else:
                                message_link = event.message.forward.link
                    except Exception as e:
                        message_link = event.message.forward.link
                elif hasattr(from_id, 'user_id'):
                    message_link = event.message.forward.link
            
            if not message_link:
                chat = await event.get_chat()
                if hasattr(chat, 'username') and chat.username:
                    message_link = f"https://t.me/{chat.username}/{event.message.id}"
                else:
                    chat_id = event.chat_id
                    if chat_id < 0:
                        try:
                            original_message = await event.client.get_messages(chat_id, ids=event.message.id)
                            if original_message and hasattr(original_message, 'id'):
                                original_chat = await event.client.get_entity(original_message.chat_id)
                                if hasattr(original_chat, 'username') and original_chat.username:
                                    message_link = f"https://t.me/{original_chat.username}/{original_message.id}"
                                else:
                                    message_link = original_message.link
                            else:
                                message_link = event.message.link
                        except:
                            message_link = event.message.link
                    else:
                        message_link = event.message.link
            # -------------------------------

            logger.info(f"开始转发文件，原链接: {message_link}")
            await processing_msg.edit(f"🔄 准备使用 TDL 转发文件...\n来源链接: {message_link}\n目标频道: {TARGET_CHANNEL}")
            
            # 简单的进度状态回调
            async def progress_cb(log_line):
                # 每隔3秒刷新一次状态，防止 API 限制
                if time.time() - getattr(progress_cb, 'last_update', 0) > 3:
                    try:
                        await processing_msg.edit(f"⏳ 正在转发中...\n来源链接: {message_link}\n日志: `{log_line[-30:]}`")
                        progress_cb.last_update = time.time()
                    except:
                        pass
            
            # 执行 TDL 转发
            success = await self.tdl_forwarder.forward_file(message_link, progress_cb)
            
            forward_time = time.time() - start_time
            time_text = f"{forward_time:.1f}秒" if forward_time < 60 else f"{forward_time/60:.1f}分钟"
            
            if success:
                record_id = self.history.add_forward(
                    event.message.id,
                    file_name,
                    message_link,
                    file_size,
                    True
                )
                
                await processing_msg.edit(
                    f"✅ **TDL 转发完成！**\n"
                    f"📁 文件名: {file_name}\n"
                    f"📦 文件大小: {humanize.naturalsize(file_size)}\n"
                    f"🎯 目标频道: `{TARGET_CHANNEL}`\n"
                    f"⏱️ 耗时: {time_text}\n"
                    f"🔢 历史记录ID: {record_id}"
                )
            else:
                await processing_msg.edit("❌ TDL 转发失败，请检查机器人日志或源链接权限。")
                
        except Exception as e:
            logger.error(f"处理文件时出错: {str(e)}", exc_info=True)
            await processing_msg.edit(f"❌ 处理出错: {str(e)}")
        finally:
            self.stats.add_processed_file(file_size, success)
            if not success and file_name and message_link:
                self.history.add_forward(event.message.id, file_name, message_link, file_size, False)

    async def start(self):
        """启动机器人"""
        await self.client.start(bot_token=BOT_TOKEN)
        
        start_message = (
            '🤖 机器人已启动！\n\n'
            '👋 你好！我是文件转发助手（TDL版）\n\n'
            '📥 发送任何文件给我，我会通过TDL帮你一键转发到目标频道\n\n'
            '✨ 支持任何类型的文件\n\n'
            '📌 管理员命令:\n'
            '/stats - 查看统计信息\n'
            '/restart - 重启机器人\n'
            '/history - 查看转发历史\n'
            '/delete  - 清除指定ID的历史记录'
        )
        
        for admin_id in ADMIN_IDS:
            try:
                await self.client.send_message(admin_id, start_message)
            except Exception as e:
                logger.error(f"Failed to send startup message to admin {admin_id}: {e}")
        
        @self.client.on(events.NewMessage(pattern='/start'))
        async def start_handler(event):
            await event.respond(start_message)

        @self.client.on(events.NewMessage)
        async def message_handler(event):
            try:
                if event.message.text and event.message.text.startswith('/'):
                    if await self.handle_admin_commands(event):
                        return

                if event.message.media:
                    if event.sender_id not in ADMIN_IDS:
                        await event.respond("❌ 抱歉，您没有权限使用此机器人。")
                        return
                        
                    processing_msg = await event.respond("⏳ 正在分析文件...")
                    asyncio.create_task(self.handle_file(event, processing_msg))
                    
            except Exception as e:
                logger.error(f"Message handling error: {e}")

if __name__ == '__main__':
    bot = TelegramBot()
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(bot.start())
        logger.info("Bot is running...")
        bot.client.run_until_disconnected()
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(bot.client.disconnect())
