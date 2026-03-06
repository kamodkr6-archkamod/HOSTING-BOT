import os
import sys
import uuid
import shutil
import sqlite3
import subprocess
import zipfile
import psutil
import signal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ==================== CONFIGURATION ====================
# ⚠️ अपना बॉट टोकन यहाँ पेस्ट करें
TOKEN = "8218492153:AAG3LE7qQvEHopgSQl-mCaG7Xn9pJ-Swp7U" 

class Config:
    UPLOAD_FOLDER = "Hosting-Bot_data"
    CONTAINERS_DIR = "containers"
    DATABASE_FILE = "Hosting-Bot.db"
    
    @staticmethod
    def init():
        os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
        os.makedirs(Config.CONTAINERS_DIR, exist_ok=True)

# ==================== DATABASE ====================
class Database:
    def __init__(self):
        self.conn = sqlite3.connect(Config.DATABASE_FILE, check_same_thread=False)
        self.init_tables()
    
    def init_tables(self):
        c = self.conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS containers 
                     (id TEXT PRIMARY KEY, name TEXT, status TEXT DEFAULT 'stopped', 
                      main_file TEXT DEFAULT 'bot.py', pid INTEGER)''')
        self.conn.commit()
    
    def create_container(self, name: str) -> str:
        # ID generation fixed to be safe
        container_id = f"id{uuid.uuid4().hex[:8]}"
        c = self.conn.cursor()
        c.execute('INSERT INTO containers (id, name) VALUES (?, ?)', (container_id, name))
        self.conn.commit()
        os.makedirs(os.path.join(Config.CONTAINERS_DIR, container_id), exist_ok=True)
        return container_id
    
    def get_all_containers(self):
        c = self.conn.cursor()
        c.execute('SELECT * FROM containers')
        return c.fetchall()
    
    def get_container(self, container_id):
        c = self.conn.cursor()
        c.execute('SELECT * FROM containers WHERE id = ?', (container_id,))
        return c.fetchone()
    
    def update_status(self, container_id, status, pid=None):
        c = self.conn.cursor()
        c.execute('UPDATE containers SET status = ?, pid = ? WHERE id = ?', (status, pid, container_id))
        self.conn.commit()
    
    def update_main_file(self, container_id, filename):
        c = self.conn.cursor()
        c.execute('UPDATE containers SET main_file = ? WHERE id = ?', (filename, container_id))
        self.conn.commit()

    def delete_container(self, container_id):
        c = self.conn.cursor()
        c.execute('DELETE FROM containers WHERE id = ?', (container_id,))
        self.conn.commit()
        path = os.path.join(Config.CONTAINERS_DIR, container_id)
        if os.path.exists(path):
            shutil.rmtree(path)

# ==================== BOT ENGINE ====================
class BotEngine:
    def __init__(self):
        self.db = Database()
        self.processes = {}
    
    def handle_upload(self, file_path, file_name):
        name_clean = file_name.rsplit('.', 1)[0]
        ext = file_name.rsplit('.', 1)[-1].lower()
        
        container_id = self.db.create_container(name_clean)
        container_dir = os.path.join(Config.CONTAINERS_DIR, container_id)
        target_path = os.path.join(container_dir, file_name)
        shutil.move(file_path, target_path)
        
        msg = f"✅ **Container Created:** `{name_clean}`\n"
        
        if ext == 'zip':
            try:
                with zipfile.ZipFile(target_path, 'r') as zip_ref:
                    zip_ref.extractall(container_dir)
                os.remove(target_path) 
                
                # Find main python file
                files = os.listdir(container_dir)
                main_file = "bot.py"
                for f in files:
                    if f.endswith(".py"):
                        main_file = f
                        break
                
                self.db.update_main_file(container_id, main_file)
                msg += "📦 **ZIP Extracted!**\n"
                msg += f"🐍 **Main File set to:** `{main_file}`"
            except Exception as e:
                msg += f"❌ **Zip Error:** {str(e)}"
        elif ext == 'py':
            self.db.update_main_file(container_id, file_name)
            msg += f"🐍 **Script Uploaded:** `{file_name}`"

        return msg

    def run_bot(self, container_id):
        row = self.db.get_container(container_id)
        if not row: return "❌ Bot not found."
        
        cid, name, status, main_file, old_pid = row
        
        if status == 'running':
            return "⚠️ Bot is already running!"
            
        container_dir = os.path.join(Config.CONTAINERS_DIR, cid)
        main_path = os.path.join(container_dir, main_file)
        
        if not os.path.exists(main_path):
            return f"❌ File `{main_file}` missing!"
            
        log_file = open(os.path.join(container_dir, "logs.txt"), "a")
        req = os.path.join(container_dir, "requirements.txt")
        if os.path.exists(req):
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], cwd=container_dir)
        try:
            proc = subprocess.Popen(
                [sys.executable, main_file],
                cwd=container_dir,
                stdout=log_file,
                stderr=subprocess.STDOUT
            )
            self.processes[cid] = proc
            self.db.update_status(cid, 'running', proc.pid)
            return f"✅ **Started:** `{name}` (PID: {proc.pid})"
        except Exception as e:
            return f"❌ **Fail:** {str(e)}"

    def stop_bot(self, container_id):
        row = self.db.get_container(container_id)
        if not row: return "Bot not found"
        cid, name, status, main_file, pid = row
        
        if cid in self.processes:
            self.processes[cid].terminate()
            del self.processes[cid]
        elif pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except:
                pass
            
        self.db.update_status(cid, 'stopped', None)
        return f"🛑 **Stopped:** `{name}`"

    def delete_bot(self, container_id):
        self.stop_bot(container_id)
        self.db.delete_container(container_id)
        return "🗑 **Deleted Successfully.**"

    def get_logs(self, container_id):
        log_path = os.path.join(Config.CONTAINERS_DIR, container_id, "logs.txt")
        if os.path.exists(log_path):
            with open(log_path, 'r') as f:
                lines = f.readlines()
                return "".join(lines[-15:]) 
        return "📭 Log file is empty."

engine = BotEngine()

# ==================== TELEGRAM HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["📂 FILE MANAGER", "🚀 DEPLOY CONSOLE"],
        ["⏹ STOP INSTANCE", "📜 LIVE LOGS"],
        ["📊 SYSTEM HEALTH", "⚙️ SETTINGS"],
        ["🌐 SERVER INFO"]
    ]
    await update.message.reply_text(
        "👋 **Welcome to Hosting Bot Pro**",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )

# 1. FILE MANAGER BUTTON RESPONSE
async def file_manager_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📂 **File Manager**\n\n"
        "Please **upload your zip file** or python script now.\n"
        "कृपया अपनी .zip या .py फाइल अभी अपलोड करें।",
        parse_mode="Markdown"
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    file_name = doc.file_name
    status = await update.message.reply_text("⬇️ Processing...")
    
    file = await context.bot.get_file(doc.file_id)
    path = os.path.join(Config.UPLOAD_FOLDER, file_name)
    await file.download_to_drive(path)
    
    result = engine.handle_upload(path, file_name)
    await status.edit_text(result, parse_mode="Markdown")

async def deploy_console(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bots = engine.db.get_all_containers()
    if not bots:
        await update.message.reply_text("📭 No Bots Found.")
        return

    keyboard = []
    for bot in bots:
        status = "🟢" if bot[2] == "running" else "🔴"
        keyboard.append([InlineKeyboardButton(f"{status} {bot[1]}", callback_data=f"menu_{bot[0]}")])
    
    await update.message.reply_text("🚀 **Deploy Console**\nSelect a bot:", reply_markup=InlineKeyboardMarkup(keyboard))

# 2. STOP INSTANCE (ONLY SHOW RUNNING BOTS)
async def stop_instance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bots = engine.db.get_all_containers()
    running_bots = [b for b in bots if b[2] == 'running']
    
    if not running_bots:
        await update.message.reply_text("ℹ️ **No bots are currently running.**", parse_mode="Markdown")
        return

    keyboard = []
    for bot in running_bots:
        # Action 'stopdirect' will stop it immediately
        keyboard.append([InlineKeyboardButton(f"🛑 Stop: {bot[1]}", callback_data=f"stopdirect_{bot[0]}")])
    
    await update.message.reply_text("⏹ **Stop Instance**\nClick to stop immediately:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # Parse logic
    action, cid = data.split("_", 1)

    if action == "menu":
        row = engine.db.get_container(cid)
        if not row:
            await query.edit_message_text("❌ Bot no longer exists.")
            return
        
        text = f"🤖 **Bot:** {row[1]}\n📊 **Status:** {row[2].upper()}"
        
        buttons = [
            [
                InlineKeyboardButton("▶️ Run", callback_data=f"run_{cid}"),
                InlineKeyboardButton("⏹ Stop", callback_data=f"stop_{cid}")
            ],
            [
                InlineKeyboardButton("📜 Logs", callback_data=f"log_{cid}")
            ],
            [
                InlineKeyboardButton("🗑 Delete", callback_data=f"del_{cid}")
            ],
            [
                InlineKeyboardButton("🔙 Back", callback_data="back_list")
            ]
        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )

    elif action == "run":
        res = engine.run_bot(cid)
        await query.message.reply_text(res, parse_mode="Markdown")

    elif action == "stop":
        res = engine.stop_bot(cid)
        await query.message.reply_text(res, parse_mode="Markdown")

    elif action == "log":
        logs = engine.get_logs(cid)
        await query.message.reply_text(
            f"📜 Bot Logs:\n\n```\n{logs}\n```",
            parse_mode="Markdown"
        )

    elif action == "stopdirect":
        res = engine.stop_bot(cid)
        await query.message.reply_text(res, parse_mode="Markdown")
        await stop_instance_menu(query, context)

    elif action == "del":
        res = engine.delete_bot(cid)
        await query.message.reply_text(res, parse_mode="Markdown")
        await deploy_console(query, context)

    elif data == "back_list":
        await deploy_console(query, context)

async def live_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bots = engine.db.get_all_containers()
    running = [b for b in bots if b[2] == 'running']
    if not running:
        await update.message.reply_text("ℹ️ No running bots.")
        return
    for bot in running:
        logs = engine.get_logs(bot[0])
        await update.message.reply_text(f"📜 **{bot[1]}:**\n`{logs}`", parse_mode="Markdown")

async def system_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    await update.message.reply_text(f"📊 **CPU:** {cpu}% | **RAM:** {ram}%", parse_mode="Markdown")

async def server_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🌐 **Server:** Running on Python {sys.version.split()[0]}")

# ==================== MAIN ====================
def main():
    Config.init()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    
    # Handlers update
    app.add_handler(MessageHandler(filters.Regex("^📂 FILE MANAGER$"), file_manager_handler))
    app.add_handler(MessageHandler(filters.Regex("^⏹ STOP INSTANCE$"), stop_instance_menu))
    
    app.add_handler(MessageHandler(filters.Regex("^🚀 DEPLOY CONSOLE$"), deploy_console))
    app.add_handler(MessageHandler(filters.Regex("^📜 LIVE LOGS$"), live_logs))
    app.add_handler(MessageHandler(filters.Regex("^📊 SYSTEM HEALTH$"), system_health))
    app.add_handler(MessageHandler(filters.Regex("^🌐 SERVER INFO$"), server_info))
    
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("🤖 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
    