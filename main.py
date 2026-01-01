import logging, os, csv, sqlite3, asyncio
from typing import Dict, Any, Optional, List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ConversationHandler, 
    MessageHandler, filters, ContextTypes
)
from telegram.helpers import escape_markdown

#CONFIG 
BOT_TOKEN = os.getenv("BOT_TOKEN", "...")
ADMIN_PASS = "..."
CHANNEL_ID = "..."
SUPPORT_ID = "..."
RECENT_COUNT = ...

#SUBJECTS 
SUBJECTS = (
    "Math", "English", "Physics", "Chemistry", "Biology", "IT", "Economics", 
    "History", "Geography", "Citizenship", "Amharic", "Kembatissa", 
    "Computer maintenance", "Web development", "Art", "HPE", "CTE", 
    "Social Studies", "General Science", "Marketing", "Accounting"
)

#STATES
(STUDENT_MENU, RESULTS_NAME, RESULTS_ID, SUPPORT_ISSUE, SUPPORT_NAME, 
 ADMIN_LOGIN, ADMIN_MENU, ADMIN_POST, ADMIN_EDIT, ADMIN_DELETE) = range(10)

#PLACEHOLDERS
SCHOOL_INFO = (
    "🏫 *About St. Anthony School*\n\n"
    "Founded in 1950 E.C in Shinshicho, Saint Anthony School provides KG–Grade 12 education "
    "for over 1,000 students. The school is known for discipline, academic excellence, and "
    "holistic development.\n\n"
    "📢 Join our official channel: https://t.me/saintanthonyschool "
)

BOT_INFO = (
    "🤖 *About This Bot*\n\n"
    "Saint Anthony School Bot is the school's secure digital system designed to provide "
    "official announcements and result checking for students and parents."
)

#ASYNC DB HELPERS
async def run_db(func, *args):
    return await asyncio.to_thread(func, *args)

#DATABASES
def init_dbs():
    for db, ddl in (
        ('users.db', 'CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY)'),
        ('posts.db', '''CREATE TABLE IF NOT EXISTS posts 
                        (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, 
                         message_id INTEGER, text TEXT, caption TEXT, file_id TEXT)'''),
    ):
        conn = sqlite3.connect(db)
        conn.execute(ddl)
        conn.commit()
        conn.close()

def save_user_sync(chat_id: int):
    with sqlite3.connect('users.db') as conn:
        conn.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))
        conn.commit()

def save_post_sync(chat_id: int, message_id: int, text: str, caption: str, file_id: str):
    with sqlite3.connect('posts.db') as conn:
        conn.execute(
            "INSERT INTO posts (chat_id, message_id, text, caption, file_id) VALUES (?, ?, ?, ?, ?)",
            (chat_id, message_id, text, caption, file_id))
        conn.commit()

def get_recent_posts_full_sync(limit: int = RECENT_COUNT) -> List[dict]:
    with sqlite3.connect('posts.db') as conn:
        rows = conn.execute(
            "SELECT id, chat_id, message_id, text, caption, file_id FROM posts ORDER BY id DESC LIMIT ?", 
            (limit,)
        ).fetchall()
        return [{'id': r[0], 'chat_id': r[1], 'message_id': r[2], 'text': r[3], 
                 'caption': r[4], 'file_id': r[5]} for r in rows]

def delete_post_by_id_sync(post_id: int):
    with sqlite3.connect('posts.db') as conn:
        conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
        conn.commit()

def get_all_users_sync() -> List[int]:
    with sqlite3.connect('users.db') as conn:
        return [row[0] for row in conn.execute("SELECT chat_id FROM users")]

def remove_user_sync(chat_id: int):
    with sqlite3.connect('users.db') as conn:
        conn.execute("DELETE FROM users WHERE chat_id = ?", (chat_id,))
        conn.commit()

#Main menu
def student_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Results", callback_data='results')],
        [InlineKeyboardButton("📢 Announcements", callback_data='announcements')],
        [InlineKeyboardButton("🏫 About School", callback_data='about_school')],
        [InlineKeyboardButton("🤖 About Bot", callback_data='about_bot')],
        [InlineKeyboardButton("🆘 Support (Ask)", callback_data='support')]
    ])

def student_sub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back')]])

def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Post", callback_data='post')],
        [InlineKeyboardButton("📝 Edit Post", callback_data='edit_post')],
        [InlineKeyboardButton("🗑️ Delete Post", callback_data='delete_post')],
        [InlineKeyboardButton("🔒 Logout", callback_data='logout')]
    ])

def admin_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_admin')]])

def join_channel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("➕ Join Channel", url=f"https://t.me/{CHANNEL_ID[1:]}"),
        InlineKeyboardButton("✅ I have joined", callback_data='check_join')
    ]])

#CLEANUP
def track_result(context: ContextTypes.DEFAULT_TYPE, msg_id: int, chat_id: int) -> None:
    context.user_data.setdefault('result_msgs', []).append((msg_id, chat_id))

async def delete_results(context: ContextTypes.DEFAULT_TYPE) -> None:
    for msg_id, chat_id in context.user_data.get('result_msgs', []):
        await safe_delete(chat_id, msg_id, context.bot)
    context.user_data.pop('result_msgs', None)

def track_message(context: ContextTypes.DEFAULT_TYPE, msg_id: int, chat_id: int) -> None:
    context.user_data.setdefault('all_messages', []).append((msg_id, chat_id))

async def cleanup_all_messages(context: ContextTypes.DEFAULT_TYPE) -> None:
    for msg_id, chat_id in context.user_data.get('all_messages', []):
        await safe_delete(chat_id, msg_id, context.bot)
    context.user_data.pop('all_messages', None)

async def wipe_admin_trail(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    for mid in context.user_data.get('to_clean', []):
        await safe_delete(chat_id, mid, context.bot)
    context.user_data['to_clean'] = []

async def wipe_everything(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """Master cleanup: wipes student msgs, results, AND admin trail"""
    await cleanup_all_messages(context)
    await delete_results(context)
    await wipe_admin_trail(context, chat_id)

async def clean_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, markup) -> None:
    await cleanup_all_messages(context)
    if update.callback_query:
        await update.callback_query.answer()
        try:
             await update.callback_query.message.delete()
        except: pass
        msg = await context.bot.send_message(update.effective_chat.id, text, parse_mode='Markdown', reply_markup=markup)
    else:
        msg = await update.message.reply_text(text, parse_mode='Markdown', reply_markup=markup)
    
    track_message(context, msg.message_id, msg.chat_id)

#STRAY-TEXT HANDLER
async def please_use_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await safe_delete(update.message.chat_id, update.message.message_id, context.bot)
    kb = student_main_kb()
    await clean_and_send(update, context, "👇 Please use the buttons below:", kb)
    return STUDENT_MENU

#HELPERS
def get_student_results(name: str, st_id: str) -> Optional[Dict[str, Any]]:
    try:
        with open('results.csv', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                if row['name'].strip().lower() == name.strip().lower() and row['student_id'].strip().lower() == st_id.strip().lower():
                    subs, scores = {}, []
                    for s in SUBJECTS:
                        val = row.get(s, '').strip()
                        if val:
                            try:
                                score = float(val.replace('%', ''))
                                subs[s] = score
                                scores.append(score)
                            except ValueError: pass
                    total = round(sum(scores), 2) if scores else 0
                    avg = round(total / len(scores), 2) if scores else 0
                    return {'subs': subs, 'total': total, 'avg': avg, 'count': len(scores)}
    except FileNotFoundError:
        logging.error("results.csv missing")
    return None

async def safe_delete(chat_id: int, msg_id: int, bot) -> None:
    try:
        await bot.delete_message(chat_id, msg_id)
    except Exception:
        pass

def wipe_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear memory but preserve Admin flag if set"""
    admin = context.user_data.get('admin', False)
    context.user_data.clear()
    if admin: context.user_data['admin'] = True

#MEMBERSHIP CHECK
async def is_member(user_id: int, bot: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in {'member', 'administrator', 'creator'}
    except Exception:
        return False

async def get_bot_announcements(bot) -> List[str]:
    rows = await run_db(get_recent_posts_full_sync, 5) 
    return [(r['text'] or r['caption'] or '').strip() for r in rows if r['text'] or r['caption']]

#STUDENT HANDLERS
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await run_db(save_user_sync, update.effective_chat.id)
    # FIX: Ensure we wipe everything including potential admin leftovers on start
    await wipe_everything(context, update.effective_chat.id)
    wipe_context(context)
    
    text = "👋 Welcome to **St. Anthony School Bot!**\n\nPlease join our official channel first to continue:"
    await clean_and_send(update, context, text, join_channel_kb())
    return STUDENT_MENU

async def student_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    if not await is_member(q.from_user.id, context.bot):
        await clean_and_send(update, context, "⛔ You must join the channel first!", join_channel_kb())
        return STUDENT_MENU
    
    await delete_results(context)

    match q.data:
        case 'results':
            await clean_and_send(update, context, "✏️ Enter your full name:", student_sub_kb())
            return RESULTS_NAME
        case 'announcements':
            announcements = await get_bot_announcements(update.get_bot())
            text = "📢 *Latest Announcements*\n\n" + "\n\n".join(f"*{i+1}.* {ann}" for i, ann in enumerate(announcements)) if announcements else "No announcements found."
            await clean_and_send(update, context, text, student_sub_kb())
            return STUDENT_MENU
        case 'about_school':
            await clean_and_send(update, context, SCHOOL_INFO, student_sub_kb())
            return STUDENT_MENU
        case 'about_bot':
            await clean_and_send(update, context, BOT_INFO, student_sub_kb())
            return STUDENT_MENU
        case 'support':
            await clean_and_send(update, context, "✏️ Please describe your problem:", student_sub_kb())
            return SUPPORT_ISSUE
        case 'back':
            await wipe_everything(context, q.message.chat_id)
            await clean_and_send(update, context, "Main menu", student_main_kb())
            return STUDENT_MENU
    return STUDENT_MENU

async def results_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await safe_delete(update.message.chat_id, update.message.message_id, context.bot) 
    context.user_data['name'] = update.message.text
    await clean_and_send(update, context, "✅ Now enter your student ID:", student_sub_kb())
    return RESULTS_ID

async def results_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await safe_delete(update.message.chat_id, update.message.message_id, context.bot) 
    name, st_id = context.user_data['name'], update.message.text
    
    prog = await update.message.reply_text("🔍 Searching...")
    track_result(context, prog.message_id, prog.chat_id)
    
    res = get_student_results(name, st_id)
    
    if not res:
        await clean_and_send(update, context, "❌ No results found. Check name/ID.", student_sub_kb())
        return STUDENT_MENU
    
    txt = f"📊 *Results for {name}*\n\n" + "\n".join(f"• {s}: *{score}*" for s, score in res['subs'].items()) + f"\n\n📈 Total: {res['total']} 📊 Average: {res['avg']}%"
    
    await cleanup_all_messages(context) 
    msg = await update.message.reply_text(txt, parse_mode='Markdown', reply_markup=student_sub_kb())
    track_result(context, msg.message_id, msg.chat_id)
    
    asyncio.create_task(self_destruct_results(context, 60))
    return STUDENT_MENU

async def support_issue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await safe_delete(update.message.chat_id, update.message.message_id, context.bot) 
    context.user_data['support_issue'] = update.message.text
    await clean_and_send(update, context, "✅ Thank you!\n\nPlease enter your **full name** so we can contact you:", student_sub_kb())
    return SUPPORT_NAME

async def support_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await safe_delete(update.message.chat_id, update.message.message_id, context.bot) 
    issue, name = context.user_data['support_issue'], update.message.text
    user = update.effective_user
    username = f"@{user.username}" if user.username else str(user.id)
    
    support_text = (
        f"🆘 *NEW SUPPORT TICKET*\n\n"
        f"*Student:* {escape_markdown(name, version=1)}\n"
        f"*Username:* {escape_markdown(username, version=1)}\n\n"
        f"*Problem:*\n{escape_markdown(issue, version=1)}"
    )
    
    try:
        await update.get_bot().send_message(SUPPORT_ID, support_text, parse_mode='Markdown')
        await clean_and_send(update, context, "✅ Your issue has been sent to the IT team!", student_main_kb())
    except Exception as e:
        logging.error("support send failed: %s", e)
        await clean_and_send(update, context, "❌ Failed to send. Please try again later.", student_main_kb())
    
    context.user_data.clear()
    return STUDENT_MENU

async def check_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if await is_member(q.from_user.id, context.bot):
        await clean_and_send(update, context, "✅ Welcome! You can now use the bot.", student_main_kb())
        return STUDENT_MENU
    await clean_and_send(update, context, "⛔ You must join the channel first!", join_channel_kb())
    return STUDENT_MENU

#ADMIN
async def force_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # KEY FIX: Cleanup MUST happen BEFORE wipe_context, or we lose the IDs to delete
    await safe_delete(update.message.chat_id, update.message.message_id, context.bot)
    await wipe_everything(context, update.message.chat_id)
    wipe_context(context)
    
    m = await update.message.reply_text("🔐 Enter admin password:")
    context.user_data.setdefault('to_clean', []).append(m.message_id)
    return ADMIN_LOGIN

#ADMIN HANDLERS
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    m = await update.message.reply_text("🔐 Enter admin password:")
    context.user_data.setdefault('to_clean', []).append(m.message_id)
    return ADMIN_LOGIN

async def admin_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await safe_delete(update.message.chat_id, update.message.message_id, context.bot)
    if update.message.text == ADMIN_PASS:
        # Success: Wipe the "Enter password" message and any lingering student stuff
        await wipe_admin_trail(context, update.message.chat_id)
        await wipe_everything(context, update.message.chat_id)
        
        context.user_data['admin'] = True
        
        m = await update.message.reply_text("✅ Admin access granted.", reply_markup=admin_kb())
        context.user_data.setdefault('to_clean', []).append(m.message_id)
        return ADMIN_MENU
    
    m = await update.message.reply_text("❌ Wrong password. Try again or /cancel:")
    context.user_data.setdefault('to_clean', []).append(m.message_id)
    return ADMIN_LOGIN

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    match q.data:
        case 'back_admin':
            await safe_delete(q.message.chat_id, q.message.message_id, context.bot)
            msg = await context.bot.send_message(q.from_user.id, "Admin menu", reply_markup=admin_kb())
            context.user_data['to_clean'] = [msg.message_id]
            return ADMIN_MENU
        case 'post':
            await q.message.reply_text("✍️ Send your announcement text (or photo + caption):", reply_markup=ReplyKeyboardRemove())
            context.user_data['post_gather'] = True
            return ADMIN_POST
        case 'edit_post':
            recent = await run_db(get_recent_posts_full_sync, RECENT_COUNT)
            if not recent:
                await clean_and_send(update, context, "❌ No posts to edit.", admin_kb())
                return ADMIN_MENU
            text = "📝 *Choose a post to edit:*\n\n" + "\n".join(f"{i+1}. {r['text'] or r['caption'][:50]}" for i, r in enumerate(recent))
            await clean_and_send(update, context, text, None)
            context.user_data['recent_posts'] = recent
            return ADMIN_EDIT
        case 'delete_post':
            recent = await run_db(get_recent_posts_full_sync, RECENT_COUNT)
            if not recent:
                await clean_and_send(update, context, "❌ No posts to delete.", admin_kb())
                return ADMIN_MENU
            text = "🗑️ *Choose a post to delete:*\n\n" + "\n".join(f"{i+1}. {r['text'] or r['caption'][:50]}" for i, r in enumerate(recent))
            await clean_and_send(update, context, text, None)
            context.user_data['recent_posts'] = recent
            return ADMIN_DELETE
        case 'logout':
            # FIX: Explicitly delete the menu button clicked immediately
            try: await q.message.delete()
            except: pass
            
            await wipe_everything(context, q.message.chat_id)
            wipe_context(context)
            await context.bot.send_message(q.from_user.id, "🔒 Logged out.\n👋 Welcome back to student mode.", reply_markup=student_main_kb())
            return STUDENT_MENU
    return ADMIN_MENU

async def admin_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if 'post_gather' not in context.user_data:
        await update.message.reply_text("✍️ Send your announcement text (or photo + caption):", reply_markup=ReplyKeyboardRemove())
        context.user_data['post_gather'] = True
        return ADMIN_POST

    text = update.message.text_html if not update.message.photo else ""
    caption = update.message.caption_html if update.message.caption else ""
    file_id = update.message.photo[-1].file_id if update.message.photo else None

    if file_id:
        channel_msg = await context.bot.send_photo(chat_id=CHANNEL_ID, photo=file_id, caption=caption or text, parse_mode="HTML")
    else:
        channel_msg = await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="HTML")

    await run_db(save_post_sync, CHANNEL_ID, channel_msg.message_id, text if not file_id else "", caption or text if file_id else "", file_id)
    
    users = await run_db(get_all_users_sync)
    broadcast_text = f"📢 *New Announcement*\n\n{text or caption}"
    
    for chat_id in users:
        try:
            await context.bot.send_message(chat_id=chat_id, text=broadcast_text, parse_mode="HTML")
        except Exception as e:
            await run_db(remove_user_sync, chat_id)

    context.user_data.pop('post_gather', None)
    await update.message.reply_text(f"✅ Posted to channel and broadcast to {len(users)} users!", reply_markup=admin_kb())
    return ADMIN_MENU

async def admin_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    recent = context.user_data.get('recent_posts', [])
    if not update.message.text or not update.message.text.isdigit():
        m = await update.message.reply_text("❌ Send the **number** of the post to delete.")
        context.user_data.setdefault('to_clean', []).append(m.message_id)
        return ADMIN_DELETE
        
    idx = int(update.message.text) - 1
    if idx < 0 or idx >= len(recent):
        m = await update.message.reply_text("❌ Invalid number.")
        context.user_data.setdefault('to_clean', []).append(m.message_id)
        return ADMIN_DELETE
        
    post = recent[idx]
    await safe_delete(CHANNEL_ID, post['message_id'], context.bot)
    await run_db(delete_post_by_id_sync, post['id'])
    
    ms = await update.message.reply_text("✅ Post deleted.", reply_markup=admin_kb())
    context.user_data.setdefault('to_clean', []).append(ms.message_id)
    return ADMIN_MENU

async def admin_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    recent = context.user_data.get('recent_posts', [])
    if update.message.text and update.message.text.isdigit():
        idx = int(update.message.text) - 1
        if idx < 0 or idx >= len(recent):
            m = await update.message.reply_text("❌ Invalid number.")
            context.user_data.setdefault('to_clean', []).append(m.message_id)
            return ADMIN_EDIT
            
        context.user_data['edit_post'] = recent[idx]
        m = await update.message.reply_text("✏️ Send the new text for this post:", reply_markup=admin_back_kb())
        context.user_data.setdefault('to_clean', []).append(m.message_id)
        return ADMIN_EDIT

    post = context.user_data.get('edit_post')
    if not post:
        m = await update.message.reply_text("❌ No post selected. /admin to restart.", reply_markup=admin_kb())
        return ADMIN_MENU
        
    new_text = update.message.text_html
    await context.bot.edit_message_text(chat_id=CHANNEL_ID, message_id=post['message_id'], text=new_text, parse_mode="HTML")
    
    with sqlite3.connect('posts.db') as conn:
        conn.execute("UPDATE posts SET text = ?, caption = '' WHERE id = ?", (new_text, post['id']))
        conn.commit()
        
    ms = await update.message.reply_text("✅ Post edited!", reply_markup=admin_kb())
    context.user_data.setdefault('to_clean', []).append(ms.message_id)
    context.user_data.pop('edit_post', None)
    return ADMIN_MENU

async def self_destruct_results(context: ContextTypes.DEFAULT_TYPE, delay: int = 60) -> None:
    await asyncio.sleep(delay)
    await delete_results(context)

CONVERSATION
def build_conv() -> ConversationHandler:
    back_handler = CallbackQueryHandler(student_menu, pattern='^back$')

    return ConversationHandler(
        entry_points=[CommandHandler('start', start), CommandHandler('admin', admin_cmd)],
        states={
            STUDENT_MENU: [
                CallbackQueryHandler(student_menu, pattern='^(results|announcements|about_school|about_bot|support|back)$'),
                CallbackQueryHandler(check_join, pattern='^check_join$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, please_use_buttons)
            ],
            RESULTS_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, results_name), back_handler],
            RESULTS_ID:   [MessageHandler(filters.TEXT & ~filters.COMMAND, results_id), back_handler],
            SUPPORT_ISSUE:[MessageHandler(filters.TEXT & ~filters.COMMAND, support_issue), back_handler],
            SUPPORT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, support_name), back_handler],
            
            ADMIN_LOGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_login)],
            ADMIN_MENU: [CallbackQueryHandler(admin_menu)],
            ADMIN_POST: [MessageHandler(filters.TEXT | filters.PHOTO, admin_post)],
            ADMIN_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit)],
            ADMIN_DELETE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_delete)],
        },
        fallbacks=[
            CommandHandler('cancel', lambda u, c: c.bot.send_message(u.effective_chat.id, "Cancelled.", reply_markup=student_main_kb()) or STUDENT_MENU),
            CommandHandler('admin', force_admin)
        ],
        per_chat=True,
        per_message=False
    )

#MAIN
async def global_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)

def main() -> None:
    init_dbs()
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logging.error("BOT_TOKEN not set in .env")
        return
        
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(build_conv())
    app.add_handler(CommandHandler('start', global_start))
    app.add_error_handler(lambda u, c: logging.exception(c.error))
    
    if not os.path.exists('results.csv'):
        with open('results.csv', 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['student_id', 'name', *SUBJECTS])
            w.writerow(['STD001', 'Abel Tesfaye', '95', '88', '92', '90', '87'] + [''] * (len(SUBJECTS) - 5))
    
    logging.info("Bot starting...")
    app.run_polling()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    main()
