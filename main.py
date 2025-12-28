import discord
import aiohttp
import re
import os
import asyncio
import json
import sqlite3
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import pytz
import time
import io
import math
import random
from typing import Optional
from discord.ui import Button, View
from collections import defaultdict

# Get environment variables for Railway
TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
YOUR_DISCORD_ID = int(os.environ.get('ADMIN_DISCORD_ID', 1355605971858100249))
DEFAULT_CHANNEL_ID = int(os.environ.get('DEFAULT_CHANNEL_ID', 1435704878986039356))

# Exit if no token
if not TOKEN:
    print("❌ ERROR: DISCORD_BOT_TOKEN environment variable is required!")
    print("💡 Please set it in Railway Environment Variables")
    exit(1)

# Bot setup with correct intents
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# API Configuration
DETAILS_API_URL = "https://lostingness.site/KEY/Infox.php?type={value}"
TELEGRAM_API_URL = "https://my.lostingness.site/tgn.php?value={value}"
VEHICLE_API_URL = "https://botfiles.serv00.net/vehicle/api.php?key=Am&reg={value}"
FAM_API_URL = "https://my.lostingness.site/fam.php?upi={value}"

# Bot Invite Link
BOT_INVITE_LINK = "https://discord.com/oauth2/authorize?client_id=1429769934157905940&permissions=8&integration_type=0&scope=bot"

# Developer Information
DEVELOPER_INFO = {
    'discord': 'https://discord.gg/teamkorn',
    'telegram': 'https://t.me/Terex',
    'developer': '@Terex On Telegram',
    'phenion': '@phenion on Telegram'
}

# Service Prices
SERVICE_PRICES = {
    'mobile': 1,
    'aadhaar': 1,
    'email': 1,
    'telegram': 5,
    'vehicle': 2,
    'fam': 1
}

# Setup tracking
pending_setups = {}  # server_id: owner_id
admin_notification_tasks = {}
server_permission_checks = {}

# Rate limiting for bulk DMs
bulk_dm_rate_limit = {}
BULK_DM_LIMIT = 200  # 200 DMs per hour
BULK_DM_WINDOW = 3600  # 1 hour in seconds

# Voice chat time settings
voice_time_settings = {
    'global': 10,  # Default 10 minutes per credit
    'servers': {}  # server_id: minutes
}

# Database setup
def init_db():
    conn = sqlite3.connect('kornfinder.db')
    c = conn.cursor()
    
    # Users table for credits and levels
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            credits INTEGER DEFAULT 0,
            level INTEGER DEFAULT 0,
            total_voice_minutes INTEGER DEFAULT 0,
            unlimited INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Voice sessions table for tracking active voice time
    c.execute('''
        CREATE TABLE IF NOT EXISTS voice_sessions (
            user_id INTEGER PRIMARY KEY,
            join_time TEXT,
            guild_id INTEGER,
            channel_id INTEGER,
            last_check_time TEXT
        )
    ''')
    
    # Allowed channels table
    c.execute('''
        CREATE TABLE IF NOT EXISTS allowed_channels (
            channel_id INTEGER PRIMARY KEY,
            guild_id INTEGER,
            added_by INTEGER,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Global admins table (full access)
    c.execute('''
        CREATE TABLE IF NOT EXISTS global_admins (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Server admins table (limited access - only their server)
    c.execute('''
        CREATE TABLE IF NOT EXISTS server_admins (
            server_id INTEGER,
            user_id INTEGER,
            added_by INTEGER,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (server_id, user_id)
        )
    ''')
    
    # Server setup tracking
    c.execute('''
        CREATE TABLE IF NOT EXISTS server_setup (
            server_id INTEGER PRIMARY KEY,
            setup_complete INTEGER DEFAULT 0,
            setup_channel_id INTEGER,
            last_notification TEXT,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Bot joins tracking table
    c.execute('''
        CREATE TABLE IF NOT EXISTS bot_joins (
            server_id INTEGER PRIMARY KEY,
            server_name TEXT,
            server_owner_id INTEGER,
            join_date TEXT,
            added_by INTEGER,
            notification_sent INTEGER DEFAULT 0
        )
    ''')
    
    # Service prices table
    c.execute('''
        CREATE TABLE IF NOT EXISTS service_prices (
            service_name TEXT PRIMARY KEY,
            price INTEGER DEFAULT 1,
            updated_by INTEGER,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Voice time settings table
    c.execute('''
        CREATE TABLE IF NOT EXISTS voice_time_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER DEFAULT 0,
            minutes_per_credit INTEGER DEFAULT 10,
            updated_by INTEGER,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Initialize service prices
    for service, price in SERVICE_PRICES.items():
        c.execute('''
            INSERT OR REPLACE INTO service_prices (service_name, price, updated_by) 
            VALUES (?, ?, ?)
        ''', (service, price, YOUR_DISCORD_ID))
    
    # Initialize global voice time setting
    c.execute('''
        INSERT OR IGNORE INTO voice_time_settings (server_id, minutes_per_credit, updated_by) 
        VALUES (0, 10, ?)
    ''', (YOUR_DISCORD_ID,))
    
    # Add default global admin
    c.execute('INSERT OR IGNORE INTO global_admins (user_id, added_by) VALUES (?, ?)', (YOUR_DISCORD_ID, YOUR_DISCORD_ID))
    
    conn.commit()
    conn.close()

# Initialize database
init_db()

class PremiumStyles:
    # Premium Colors
    PRIMARY = 0x5865F2
    SUCCESS = 0x57F287
    ERROR = 0xED4245
    WARNING = 0xFEE75C
    INFO = 0x3498DB
    PREMIUM = 0x9B59B6

# Global variables for stats
bot.start_time = datetime.now(timezone.utc)
search_count = 0

def get_db_connection():
    return sqlite3.connect('kornfinder.db', check_same_thread=False)

def is_allowed_channel():
    async def predicate(ctx):
        # Global admins can use commands anywhere
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT user_id FROM global_admins WHERE user_id = ?', (ctx.author.id,))
        is_global_admin = c.fetchone()
        conn.close()
        
        if is_global_admin:
            return True
        
        # First check if bot has admin permissions in the server
        if ctx.guild and not ctx.guild.me.guild_permissions.administrator:
            embed = discord.Embed(
                title="⚠️ ADMIN PERMISSION REQUIRED ⚠️",
                description="This bot requires **Administrator Permissions** to function properly in this server!",
                color=0xED4245
            )
            embed.add_field(
                name="🔧 **Please grant Administrator Permission**",
                value="The bot will not work until it has Administrator permissions.\nServer admins will receive notifications until permissions are granted.",
                inline=False
            )
            await ctx.send(embed=embed, delete_after=30)
            return False
        
        # Then check if channel is allowed
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT channel_id FROM allowed_channels WHERE channel_id = ?', (ctx.channel.id,))
        result = c.fetchone()
        conn.close()
        
        if not result:
            embed = discord.Embed(
                title="🚫 Channel Restricted",
                description="This bot can only be used in authorized channels.",
                color=0xED4245
            )
            await ctx.send(embed=embed, delete_after=10)
            return False
        return True
    return commands.check(predicate)

def is_global_admin():
    async def predicate(ctx):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT user_id FROM global_admins WHERE user_id = ?', (ctx.author.id,))
        result = c.fetchone()
        conn.close()
        
        if not result:
            embed = discord.Embed(
                title="🚫 Global Admin Access Required",
                description="You need global administrator privileges to use this command.",
                color=0xED4245
            )
            await ctx.send(embed=embed, delete_after=10)
            return False
        return True
    return commands.check(predicate)

def is_server_admin():
    async def predicate(ctx):
        # Check if global admin first
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT user_id FROM global_admins WHERE user_id = ?', (ctx.author.id,))
        global_admin = c.fetchone()
        
        if global_admin:
            conn.close()
            return True
        
        # Check if server admin for this server
        if ctx.guild:
            c.execute('SELECT user_id FROM server_admins WHERE server_id = ? AND user_id = ?', (ctx.guild.id, ctx.author.id))
            server_admin = c.fetchone()
            conn.close()
            
            if server_admin:
                return True
        
        embed = discord.Embed(
            title="🚫 Admin Access Required",
            description="You need administrator privileges to use this command.",
            color=0xED4245
        )
        await ctx.send(embed=embed, delete_after=10)
        return False
    return commands.check(predicate)

def get_user_data(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = c.fetchone()
    
    if not user:
        # Create new user with 0 credits
        c.execute('''
            INSERT INTO users (user_id, credits, level, total_voice_minutes, unlimited)
            VALUES (?, 0, 0, 0, 0)
        ''', (user_id,))
        conn.commit()
        c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        user = c.fetchone()
    
    conn.close()
    return user

def has_unlimited_access(user_id):
    """Check if user has unlimited access"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT unlimited FROM users WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    return result and result[0] == 1

def update_user_credits(user_id, credits_change):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE users SET credits = credits + ? WHERE user_id = ?', (credits_change, user_id))
    conn.commit()
    conn.close()

def set_user_credits(user_id, credits):
    """Set user credits to specific amount"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE users SET credits = ? WHERE user_id = ?', (credits, user_id))
    conn.commit()
    conn.close()

def update_user_level(user_id, level):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE users SET level = ? WHERE user_id = ?', (level, user_id))
    conn.commit()
    conn.close()

def update_voice_minutes(user_id, minutes):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE users SET total_voice_minutes = total_voice_minutes + ? WHERE user_id = ?', (minutes, user_id))
    conn.commit()
    conn.close()

def get_voice_session(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM voice_sessions WHERE user_id = ?', (user_id,))
    session = c.fetchone()
    conn.close()
    return session

def start_voice_session(user_id, guild_id, channel_id):
    conn = get_db_connection()
    c = conn.cursor()
    current_time = datetime.now().isoformat()
    c.execute('''
        INSERT OR REPLACE INTO voice_sessions (user_id, join_time, guild_id, channel_id, last_check_time)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, current_time, guild_id, channel_id, current_time))
    conn.commit()
    conn.close()

def update_voice_check_time(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE voice_sessions SET last_check_time = ? WHERE user_id = ?', (datetime.now().isoformat(), user_id))
    conn.commit()
    conn.close()

def end_voice_session(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('DELETE FROM voice_sessions WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_indian_time():
    """Get current Indian time"""
    ist = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist).strftime("%d %b %Y • %I:%M %p IST")

def clean_mobile_number(mobile_str):
    """Clean mobile number - remove spaces, country code, etc."""
    # Remove all non-digit characters
    digits = re.sub(r'\D', '', mobile_str)
    
    # Check if it's a valid Indian mobile number
    if len(digits) >= 10:
        # Take last 10 digits (in case of country code)
        cleaned = digits[-10:]
        
        # Check if it starts with 6,7,8,9 (valid Indian mobile prefixes)
        if cleaned[0] in '6789':
            return cleaned
    
    return None

async def resolve_user(ctx, user_input):
    """Resolve user input to user object"""
    try:
        # Check if input is a user ID
        if user_input.isdigit():
            user_id = int(user_input)
            try:
                user = await bot.fetch_user(user_id)
                return user
            except:
                pass
        
        # Check if input is a mention
        if user_input.startswith('<@') and user_input.endswith('>'):
            user_id = int(re.sub(r'\D', '', user_input))
            try:
                user = await bot.fetch_user(user_id)
                return user
            except:
                pass
        
        # Check in current guild
        if ctx.guild:
            # Remove @ if present
            if user_input.startswith('@'):
                user_input = user_input[1:]
            
            # Try to find by username#discriminator
            if '#' in user_input:
                try:
                    username, discriminator = user_input.split('#')
                    user = discord.utils.get(ctx.guild.members, name=username, discriminator=discriminator)
                    if user:
                        return user
                except:
                    pass
            
            # Try to find by username
            user = discord.utils.get(ctx.guild.members, name=user_input)
            if user:
                return user
            
            # Try to find by nickname
            user = discord.utils.get(ctx.guild.members, display_name=user_input)
            if user:
                return user
            
            # Try to find by partial name
            for member in ctx.guild.members:
                if user_input.lower() in member.name.lower() or (member.nick and user_input.lower() in member.nick.lower()):
                    return member
        
        return None
    except Exception as e:
        print(f"Error resolving user {user_input}: {e}")
        return None

def clean_text(text):
    """Advanced text cleaning"""
    if not text or str(text).strip() in ["", "null", "None", "N/A", "NA"]:
        return "**Not Available**"
    
    text = str(text).strip()
    text = re.sub(r'[!@#$%^&*()_+=`~\[\]{}|\\:;"<>?]', ' ', text)
    text = re.sub(r'[.!]+$', '', text)
    text = re.sub(r'\s+', ' ', text)
    
    if '@' not in text:
        words = text.split()
        cleaned_words = []
        for word in words:
            if word.upper() in ['II', 'III', 'IV', 'VI', 'VII', 'VIII']:
                cleaned_words.append(word.upper())
            elif len(word) > 1:
                cleaned_words.append(word[0].upper() + word[1:].lower())
            else:
                cleaned_words.append(word.upper())
        text = ' '.join(cleaned_words)
    
    return f"**{text}**"

def format_address(address):
    """Premium address formatting"""
    if not address or str(address).strip() in ["", "null", "None", "N/A"]:
        return "**Address Not Available**"
    
    address = str(address)
    address = re.sub(r'[.!*#-]+', ', ', address)
    address = re.sub(r'\s*,\s*', ', ', address)
    address = re.sub(r'\s+', ' ', address)
    address = re.sub(r'\b(c/o|C/O)\s*:?\s*', '**C/O:** ', address, flags=re.IGNORECASE)
    address = address.strip().strip(',')
    
    parts = [part.strip() for part in address.split(',') if part.strip()]
    formatted_parts = []
    
    for part in parts:
        if part.upper() in ['DELHI', 'MUMBAI', 'KOLKATA', 'CHENNAI', 'BANGALORE', 'HYDERABAD']:
            formatted_parts.append(f"**{part.upper()}**")
        else:
            formatted_parts.append(f"**{part.title()}**")
    
    return ', '.join(formatted_parts)

def get_voice_time_settings(server_id=None):
    """Get voice time settings for a server or global"""
    conn = get_db_connection()
    c = conn.cursor()
    
    if server_id:
        c.execute('SELECT minutes_per_credit FROM voice_time_settings WHERE server_id = ?', (server_id,))
        result = c.fetchone()
        if result:
            conn.close()
            return result[0]
    
    # Return global setting
    c.execute('SELECT minutes_per_credit FROM voice_time_settings WHERE server_id = 0')
    result = c.fetchone()
    conn.close()
    
    if result:
        return result[0]
    return 10  # Default

def set_voice_time_settings(server_id, minutes, updated_by):
    """Set voice time settings"""
    conn = get_db_connection()
    c = conn.cursor()
    
    if server_id == 0:
        # Update global setting
        c.execute('UPDATE voice_time_settings SET minutes_per_credit = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP WHERE server_id = 0', 
                 (minutes, updated_by))
    else:
        # Check if server setting exists
        c.execute('SELECT 1 FROM voice_time_settings WHERE server_id = ?', (server_id,))
        if c.fetchone():
            c.execute('UPDATE voice_time_settings SET minutes_per_credit = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP WHERE server_id = ?', 
                     (minutes, updated_by, server_id))
        else:
            c.execute('INSERT INTO voice_time_settings (server_id, minutes_per_credit, updated_by) VALUES (?, ?, ?)', 
                     (server_id, minutes, updated_by))
    
    conn.commit()
    conn.close()

async def check_voice_rewards(user_id, minutes_added, guild_id=None):
    """Check and give voice rewards"""
    user_data = get_user_data(user_id)
    old_minutes = user_data[3] - minutes_added
    new_minutes = user_data[3]
    
    # Get voice time settings for this server
    minutes_per_credit = get_voice_time_settings(guild_id)
    
    # Calculate how many credit intervals passed
    old_credits = old_minutes // minutes_per_credit
    new_credits = new_minutes // minutes_per_credit
    
    # Give credits for completed intervals
    credits_diff = new_credits - old_credits
    if credits_diff > 0:
        update_user_credits(user_id, credits_diff)
        
        # Notify user about credits earned
        user = bot.get_user(user_id)
        if user:
            try:
                embed = discord.Embed(
                    title="💰 CREDITS EARNED! 💰",
                    description=f"**{user.mention} earned {credits_diff} credits from voice chat!**",
                    color=0x57F287
                )
                embed.add_field(
                    name="🎧 Voice Activity",
                    value=f"**Minutes Talked:** {minutes_added} minutes",
                    inline=True
                )
                embed.add_field(
                    name="💎 Total Credits",
                    value=f"**{user_data[1] + credits_diff} credits**",
                    inline=True
                )
                embed.set_footer(text="Keep staying active in voice chat to earn more credits! 💫")
                await user.send(embed=embed)
            except:
                pass
    
    # Level up for every 2 credits earned
    if credits_diff >= 2:
        new_level = user_data[2] + (credits_diff // 2)
        update_user_level(user_id, new_level)
        
        # Notify user about level up
        user = bot.get_user(user_id)
        if user:
            try:
                embed = discord.Embed(
                    title="🎉 LEVEL UP! 🎉",
                    description=f"**{user.mention} just reached Level {new_level}!**",
                    color=0xFFD700
                )
                embed.add_field(
                    name="🎧 Voice Activity",
                    value=f"**Total Time:** {new_minutes} minutes",
                    inline=True
                )
                embed.add_field(
                    name="💰 Credits Earned",
                    value=f"**+{credits_diff} credits** this session\n**Total Credits:** {user_data[1] + credits_diff}",
                    inline=True
                )
                embed.set_footer(text="Keep staying active in voice chat to earn more credits! 💫")
                await user.send(embed=embed)
            except:
                pass

@bot.event
async def on_ready():
    print("🚀 KornFinder Premium Mobile Search Bot Online!")
    print(f"💎 Admin ID: {YOUR_DISCORD_ID}")
    print(f"📢 Default Channel: {DEFAULT_CHANNEL_ID}")
    print("⚡ Voice Chat Credit System Enabled!")
    print("🌐 API: Lostingness Premium")
    print("💰 10 minutes = 1 credit, 20 minutes = 2 credits + level up")
    print("📱 Services: Number, Aadhaar, Email, Telegram, Vehicle, FamPay")
    print(f"🔗 Bot Invite Link: {BOT_INVITE_LINK}")
    
    activity = discord.Activity(
        type=discord.ActivityType.watching,
        name="Mobile Numbers | Auto Detect Active"
    )
    await bot.change_presence(activity=activity)
    
    # Add default channel if exists
    await add_default_channel()
    
    # Load active voice sessions
    await load_voice_sessions()
    
    # Start background tasks
    asyncio.create_task(voice_monitoring_task())
    asyncio.create_task(cleanup_voice_sessions_task())
    asyncio.create_task(daily_report_task())
    
    # Start server permission checks
    for guild in bot.guilds:
        asyncio.create_task(start_server_permission_check(guild))
    
    print(f"✅ Bot is online in {len(bot.guilds)} servers!")
    print("✅ Auto-Detection System: ACTIVE")
    print("✅ Just type any search value and bot will auto-detect!")

async def add_default_channel():
    """Add default channel to database if it exists"""
    try:
        channel = bot.get_channel(DEFAULT_CHANNEL_ID)
        if channel and channel.guild:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute('INSERT OR IGNORE INTO allowed_channels (channel_id, guild_id, added_by) VALUES (?, ?, ?)', 
                      (DEFAULT_CHANNEL_ID, channel.guild.id, YOUR_DISCORD_ID))
            conn.commit()
            conn.close()
            print(f"✅ Default channel added: #{channel.name} in {channel.guild.name}")
    except Exception as e:
        print(f"⚠️ Could not add default channel: {e}")

async def load_voice_sessions():
    """Load active voice sessions from database on startup"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT * FROM voice_sessions')
        sessions = c.fetchall()
        conn.close()
        
        for session in sessions:
            user_id, join_time_str, guild_id, channel_id, last_check_str = session
            guild = bot.get_guild(guild_id)
            if guild:
                member = guild.get_member(user_id)
                if member and member.voice and member.voice.channel and member.voice.channel.id == channel_id:
                    print(f"✅ Restored voice session for {member.display_name}")
                else:
                    # Clean up stale session
                    end_voice_session(user_id)
            else:
                # Guild not found, clean up session
                end_voice_session(user_id)
    except Exception as e:
        print(f"Error loading voice sessions: {e}")

async def voice_monitoring_task():
    """Improved 24x7 Voice monitoring for automatic rewards"""
    print("🎧 Voice Monitoring Task Started...")
    
    while True:
        try:
            # Check all active voice sessions every 30 seconds
            conn = get_db_connection()
            c = conn.cursor()
            c.execute('SELECT * FROM voice_sessions')
            active_sessions = c.fetchall()
            conn.close()
            
            current_time = datetime.now()
            
            for session in active_sessions:
                user_id, join_time_str, guild_id, channel_id, last_check_str = session
                
                # Get the guild and member
                guild = bot.get_guild(guild_id)
                if not guild:
                    end_voice_session(user_id)
                    continue
                
                member = guild.get_member(user_id)
                if not member:
                    end_voice_session(user_id)
                    continue
                
                # Check if member is actually in a voice channel
                if not member.voice or not member.voice.channel or member.voice.channel.id != channel_id:
                    # Member is not in the tracked voice channel, end session
                    session_data = get_voice_session(user_id)
                    if session_data:
                        join_time = datetime.fromisoformat(session_data[1])
                        last_check = datetime.fromisoformat(session_data[4])
                        time_spent = (last_check - join_time).total_seconds() / 60
                        time_spent = int(time_spent)
                        
                        if time_spent > 0:
                            # Final update for remaining time
                            update_voice_minutes(user_id, time_spent)
                            
                            # Check for rewards
                            await check_voice_rewards(user_id, time_spent, guild_id)
                    
                    end_voice_session(user_id)
                    print(f"🎤 Voice session ended (left): {member.display_name}")
                    continue
                
                # Member is in voice channel, update minutes
                last_check = datetime.fromisoformat(last_check_str)
                time_since_last_check = (current_time - last_check).total_seconds()
                
                if time_since_last_check >= 60:  # 1 minute
                    minutes_to_add = int(time_since_last_check / 60)
                    
                    # Update voice minutes
                    update_voice_minutes(user_id, minutes_to_add)
                    
                    # Check for rewards
                    await check_voice_rewards(user_id, minutes_to_add, guild_id)
                    
                    # Update last check time
                    update_voice_check_time(user_id)
                    
                    # Update user's presence
                    user_data = get_user_data(user_id)
                    total_minutes = user_data[3]
                    minutes_per_credit = get_voice_time_settings(guild_id)
                    next_credit = minutes_per_credit - (total_minutes % minutes_per_credit)
                    
                    # Update bot status with user's progress
                    if member.id == YOUR_DISCORD_ID:  # Only update for admin
                        activity = discord.Activity(
                            type=discord.ActivityType.watching,
                            name=f"VC: {total_minutes}m (+{minutes_to_add})"
                        )
                        await bot.change_presence(activity=activity)
            
            await asyncio.sleep(30)  # Check every 30 seconds
            
        except Exception as e:
            print(f"Voice monitoring error: {e}")
            await asyncio.sleep(60)

async def cleanup_voice_sessions_task():
    """Clean up stale voice sessions"""
    while True:
        try:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute('SELECT * FROM voice_sessions')
            sessions = c.fetchall()
            
            for session in sessions:
                user_id, join_time_str, guild_id, channel_id, last_check_str = session
                last_check = datetime.fromisoformat(last_check_str)
                current_time = datetime.now()
                
                # If no check for 2 minutes, assume user left
                if (current_time - last_check).total_seconds() > 120:
                    c.execute('DELETE FROM voice_sessions WHERE user_id = ?', (user_id,))
                    print(f"🧹 Cleaned stale voice session for user {user_id}")
            
            conn.commit()
            conn.close()
            await asyncio.sleep(120)  # Check every 2 minutes
            
        except Exception as e:
            print(f"Cleanup error: {e}")
            await asyncio.sleep(120)

async def daily_report_task():
    """Send daily report to admin"""
    while True:
        try:
            # Wait 24 hours
            await asyncio.sleep(86400)  # 24 hours in seconds
            
            # Generate and send report
            await send_daily_report()
            
        except Exception as e:
            print(f"Daily report error: {e}")
            await asyncio.sleep(3600)  # Wait 1 hour before retrying

async def send_daily_report():
    """Send daily report to admin"""
    try:
        admin_user = bot.get_user(YOUR_DISCORD_ID)
        
        if not admin_user:
            print("❌ Admin user not found!")
            return
        
        # Generate report
        report_content = await generate_server_report()
        
        # Create text file
        file_content = f"KornFinder Bot - Daily Report\n"
        file_content += f"Generated on: {get_indian_time()}\n"
        file_content += f"Total Servers: {len(bot.guilds)}\n"
        file_content += "=" * 50 + "\n\n"
        file_content += report_content
        
        # Send as file
        file = discord.File(io.BytesIO(file_content.encode('utf-8')), filename=f"kornfinder_report_{datetime.now().strftime('%Y%m%d')}.txt")
        
        embed = discord.Embed(
            title="📊 Daily Server Report",
            description=f"**KornFinder Bot Daily Report**\nGenerated on {get_indian_time()}",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc)
        )
        
        embed.add_field(
            name="📈 Server Statistics",
            value=f"**Total Servers:** {len(bot.guilds)}\n**Total Users:** {sum(guild.member_count for guild in bot.guilds):,}\n**Bot Uptime:** {str(datetime.now(timezone.utc) - bot.start_time).split('.')[0]}",
            inline=False
        )
        
        embed.set_footer(text="Automated Daily Report • KornFinder Bot")
        
        await admin_user.send(embed=embed, file=file)
        print(f"📊 Daily report sent to admin {admin_user.name}")
        
    except Exception as e:
        print(f"Error sending daily report: {e}")

async def generate_server_report():
    """Generate server report text"""
    report = ""
    
    for guild in bot.guilds:
        report += f"Server: {guild.name}\n"
        report += f"ID: {guild.id}\n"
        report += f"Owner: {guild.owner.name if guild.owner else 'Unknown'}\n"
        report += f"Members: {guild.member_count}\n"
        report += f"Created: {guild.created_at.strftime('%Y-%m-%d')}\n"
        
        # Get allowed channels for this server
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('SELECT channel_id FROM allowed_channels WHERE guild_id = ?', (guild.id,))
        allowed_channels = c.fetchall()
        conn.close()
        
        if allowed_channels:
            report += "Allowed Channels:\n"
            for channel_row in allowed_channels:
                channel_id = channel_row[0]
                channel = guild.get_channel(channel_id)
                if channel:
                    report += f"  - #{channel.name} (ID: {channel.id})\n"
                else:
                    report += f"  - Unknown Channel (ID: {channel_id})\n"
        else:
            report += "Allowed Channels: None\n"
        
        # Check if bot has admin permissions
        has_admin = guild.me.guild_permissions.administrator
        report += f"Bot Has Admin Permissions: {'✅ Yes' if has_admin else '❌ No'}\n"
        
        # Try to get invite link
        invite_link = "No invite available"
        try:
            if guild.vanity_url_code:
                invite_link = f"https://discord.gg/{guild.vanity_url_code}"
            else:
                # Try to create an invite
                for channel in guild.text_channels:
                    if channel.permissions_for(guild.me).create_instant_invite:
                        try:
                            invite = await channel.create_invite(max_age=300, max_uses=1, reason="Daily report")
                            invite_link = invite.url
                            break
                        except:
                            continue
        except:
            pass
        
        report += f"Invite Link: {invite_link}\n"
        report += "-" * 40 + "\n\n"
    
    return report

@bot.event
async def on_voice_state_update(member, before, after):
    """Improved voice channel tracking"""
    if member.bot:
        return
    
    user_id = member.id
    
    # User joined a voice channel
    if before.channel is None and after.channel is not None:
        # Check if there's an existing session (shouldn't happen, but just in case)
        existing_session = get_voice_session(user_id)
        if existing_session:
            end_voice_session(user_id)
        
        # Start new session
        start_voice_session(user_id, after.channel.guild.id, after.channel.id)
        
        # Send notification to user
        try:
            embed = discord.Embed(
                title="🎤 Voice Chat Started!",
                description=f"**{member.mention}, you're now earning credits in voice chat!**",
                color=0x57F287
            )
            embed.add_field(
                name="💰 How to Earn",
                value="Stay active in voice chat to earn:\n• **1 credit** every 10 minutes\n• **Level up** every 20 minutes",
                inline=False
            )
            embed.set_footer(text="Credits are GLOBAL - use them in any server!")
            await member.send(embed=embed)
        except:
            pass
        
        print(f"🎤 Voice session started: {member.display_name} in #{after.channel.name}")
    
    # User left a voice channel
    elif before.channel is not None and after.channel is None:
        session = get_voice_session(user_id)
        if session:
            join_time = datetime.fromisoformat(session[1])
            last_check = datetime.fromisoformat(session[4])
            time_spent = (last_check - join_time).total_seconds() / 60
            time_spent = int(time_spent)
            
            if time_spent > 0:
                # Final update for remaining time
                update_voice_minutes(user_id, time_spent)
                
                # Check for rewards
                await check_voice_rewards(user_id, time_spent, session[2])
                
                # Send summary to user
                try:
                    user_data = get_user_data(user_id)
                    embed = discord.Embed(
                        title="🎤 Voice Session Complete",
                        description=f"**{member.mention}, your voice session has ended!**",
                        color=0x3498DB
                    )
                    embed.add_field(
                        name="📊 Session Summary",
                        value=f"**Time Spent:** {time_spent} minutes\n**Total Voice Minutes:** {user_data[3]}\n**Credits Earned:** Check with `!credits`",
                        inline=False
                    )
                    await member.send(embed=embed)
                except:
                    pass
            
            end_voice_session(user_id)
            print(f"🎤 Voice session ended: {member.display_name} spent {time_spent} minutes")
    
    # User moved between channels
    elif before.channel is not None and after.channel is not None and before.channel.id != after.channel.id:
        # End old session and start new one
        session = get_voice_session(user_id)
        if session:
            join_time = datetime.fromisoformat(session[1])
            last_check = datetime.fromisoformat(session[4])
            time_spent = (last_check - join_time).total_seconds() / 60
            time_spent = int(time_spent)
            
            if time_spent > 0:
                update_voice_minutes(user_id, time_spent)
                await check_voice_rewards(user_id, time_spent, session[2])
            
            end_voice_session(user_id)
        
        start_voice_session(user_id, after.channel.guild.id, after.channel.id)
        print(f"🎤 Voice session moved: {member.display_name} to #{after.channel.name}")

async def start_server_permission_check(guild):
    """Start periodic permission checks for a server"""
    if guild.id in server_permission_checks:
        return
    
    server_permission_checks[guild.id] = True
    
    # Initial check
    await check_server_admin_permissions(guild)
    
    # Periodic checks every 2-3 hours
    while True:
        try:
            # Random interval between 2-3 hours (7200-10800 seconds)
            wait_time = random.randint(7200, 10800)
            await asyncio.sleep(wait_time)
            
            # Check if bot still in server
            if not bot.get_guild(guild.id):
                del server_permission_checks[guild.id]
                break
            
            await check_server_admin_permissions(guild)
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Permission check error for {guild.name}: {e}")
            await asyncio.sleep(3600)

async def check_server_admin_permissions(guild):
    """Check if bot has admin permissions and notify admins if not"""
    if not guild.me.guild_permissions.administrator:
        # Get all admins in the server
        admins = []
        for member in guild.members:
            if member.guild_permissions.administrator and not member.bot:
                admins.append(member)
        
        if not admins:
            # If no admins found, try to get server owner
            if guild.owner and not guild.owner.bot:
                admins = [guild.owner]
        
        # Send notification to all admins
        for admin in admins:
            try:
                await send_admin_permission_notification(admin, guild)
                print(f"⚠️ Admin notification sent to {admin.name} in {guild.name}")
            except Exception as e:
                print(f"Could not send admin notification to {admin.name}: {e}")
        
        return False
    return True

async def send_admin_permission_notification(admin, guild):
    """Send admin permission notification to an admin"""
    try:
        # Create button for bot invite
        button = Button(
            label="🔗 Invite KornFinder Bot",
            url=BOT_INVITE_LINK,
            style=discord.ButtonStyle.link
        )
        
        # Create view with button
        view = View()
        view.add_item(button)
        
        embed = discord.Embed(
            title="⚠️ URGENT: ADMINISTRATOR PERMISSIONS REQUIRED ⚠️",
            description=f"Hello {admin.mention}! **KornFinder Bot** needs **Administrator Permissions** in **{guild.name}** to function properly!",
            color=0xED4245,
            timestamp=datetime.now(timezone.utc)
        )
        
        embed.add_field(
            name="🔧 **Why Admin Permissions?**",
            value="Administrator permissions allow the bot to:\n• Manage channels and messages\n• Track voice chat activity\n• Provide seamless user experience\n• Access all necessary features\n• **Without admin permissions, the bot WILL NOT WORK!**",
            inline=False
        )
        
        embed.add_field(
            name="🚀 **Premium Features You're Missing**",
            value="• **24/7 Voice Chat Credit System** 🎤\n• **Advanced Search Features** 🔍\n• **Auto-delete for privacy** 🛡️\n• **User management tools** 👥\n• **Server analytics** 📊\n• **Telegram to Mobile Search** 📲\n• **Vehicle Number Search** 🚗\n• **FamPay to Mobile Search** 💳",
            inline=False
        )
        
        embed.add_field(
            name="⚡ **How to Grant Admin**",
            value="1. Go to **Server Settings** ⚙️\n2. Click **Roles** 👑\n3. Select **KornFinder Bot** role\n4. Enable **Administrator** permission\n5. Save changes 💾\n\n**Or drag the KornFinder Bot role ABOVE other roles!**",
            inline=False
        )
        
        embed.add_field(
            name="📞 **Need Help?**",
            value=f"**Developer:** {DEVELOPER_INFO['developer']}\n**Discord Server:** [Join Here]({DEVELOPER_INFO['discord']}) 👥\n**Telegram:** [Contact]({DEVELOPER_INFO['telegram']}) 📲\n**API Provider:** {DEVELOPER_INFO['phenion']} 🔗",
            inline=False
        )
        
        embed.set_footer(text="This notification will repeat every 2-3 hours until admin permissions are granted!")
        
        await admin.send(embed=embed, view=view)
        
    except Exception as e:
        print(f"Failed to send admin notification: {e}")

@bot.event
async def on_guild_join(guild):
    """Send notification to server owner when bot is added"""
    # Store bot join information
    conn = get_db_connection()
    c = conn.cursor()
    
    # Record bot join
    c.execute('''
        INSERT OR REPLACE INTO bot_joins (server_id, server_name, server_owner_id, join_date, added_by, notification_sent)
        VALUES (?, ?, ?, ?, ?, 0)
    ''', (guild.id, guild.name, guild.owner.id if guild.owner else 0, datetime.now().isoformat(), guild.owner.id if guild.owner else 0))
    
    # Add to server setup tracking
    c.execute('INSERT OR IGNORE INTO server_setup (server_id, setup_complete) VALUES (?, 0)', (guild.id,))
    
    conn.commit()
    conn.close()
    
    # Start permission checks
    asyncio.create_task(start_server_permission_check(guild))
    
    # Send setup message to server owner
    owner = guild.owner
    if owner:
        try:
            # Create button for bot invite
            button = Button(
                label="🔗 Invite KornFinder to More Servers",
                url=BOT_INVITE_LINK,
                style=discord.ButtonStyle.link
            )
            
            view = View()
            view.add_item(button)
            
            embed = discord.Embed(
                title="🎉 WELCOME TO KORNFINDER BOT! 🎉",
                description=f"Hello {owner.mention}! Thanks for adding **KornFinder Bot** to **{guild.name}**!",
                color=0x5865F2,
                timestamp=datetime.now(timezone.utc)
            )
            
            embed.add_field(
                name="🚀 **Quick Setup Guide**",
                value="To get started, please follow these steps:",
                inline=False
            )
            
            embed.add_field(
                name="📌 **Step 1: Grant Admin Permissions**",
                value="**IMPORTANT:** The bot REQUIRES Administrator permissions to function!\nGo to Server Settings → Roles → KornFinder Bot → Enable Administrator",
                inline=False
            )
            
            embed.add_field(
                name="🔧 **Step 2: Create a Channel**",
                value="Create a new text channel or use an existing one where you want to use the bot.",
                inline=False
            )
            
            embed.add_field(
                name="📨 **Step 3: Send Channel ID**",
                value="**Reply to this message** with the Channel ID to complete setup.",
                inline=False
            )
            
            embed.add_field(
                name="💎 **Bot Features**",
                value="• **Mobile Number Lookup** 📱\n• **Aadhaar Card Search** 🪪\n• **Email Address Search** 📧\n• **Telegram to Mobile** 📲\n• **Vehicle Number Search** 🚗\n• **FamPay UPI Search** 💳\n• **Voice Chat Credit System** 🎤\n• **Auto-delete for privacy** 🛡️\n• **AUTO-DETECTION** - Just type and search!",
                inline=False
            )
            
            embed.add_field(
                name="📞 **Support & Links**",
                value=f"**Developer:** {DEVELOPER_INFO['developer']}\n**Discord Server:** [Join Here]({DEVELOPER_INFO['discord']}) 👥\n**Telegram:** [Contact]({DEVELOPER_INFO['telegram']}) 📲\n**API Provider:** {DEVELOPER_INFO['phenion']} 🔗",
                inline=False
            )
            
            embed.set_footer(text="Reply to this message with the Channel ID to complete setup! ✅")
            
            setup_msg = await owner.send(embed=embed, view=view)
            
            # Store the setup message ID for reply tracking
            pending_setups[guild.id] = {
                'owner_id': owner.id,
                'setup_msg_id': setup_msg.id,
                'channel_id': None
            }
            
            print(f"📥 Bot added to new server: {guild.name} (ID: {guild.id})")
            
        except Exception as e:
            print(f"Could not send setup message to server owner: {e}")
    
    # Send notification to bot admin
    await notify_admin_about_join(guild)

@bot.event
async def on_message(message):
    """Handle messages with auto-detection"""
    # Ignore bot messages
    if message.author.bot:
        return
    
    # Process commands first
    await bot.process_commands(message)
    
    # Check if message is a DM for setup
    if isinstance(message.channel, discord.DMChannel):
        await handle_dm_setup(message)
        return
    
    # Check if channel is allowed
    if not await is_channel_allowed(message):
        return
    
    # Check if message is a command
    if message.content.startswith('!'):
        return
    
    # Auto-detect search type and process
    search_value = message.content.strip()
    if search_value:
        await auto_detect_and_search(message, search_value)

async def handle_dm_setup(message):
    """Handle DM messages for setup"""
    # Check if this is a reply to our setup message
    if message.reference and message.reference.message_id:
        # Check if user is a server owner with pending setup
        for server_id, setup_info in list(pending_setups.items()):
            if (message.author.id == setup_info['owner_id'] and 
                message.reference.message_id == setup_info['setup_msg_id']):
                
                # This is a setup message reply
                content = message.content.strip()
                
                # Check if it's a channel ID (numeric)
                if content.isdigit():
                    channel_id = int(content)
                    guild = bot.get_guild(server_id)
                    
                    if guild:
                        channel = guild.get_channel(channel_id)
                        if channel:
                            # Add channel to allowed channels
                            conn = get_db_connection()
                            c = conn.cursor()
                            c.execute('INSERT OR REPLACE INTO allowed_channels (channel_id, guild_id, added_by) VALUES (?, ?, ?)', 
                                     (channel_id, guild.id, message.author.id))
                            c.execute('UPDATE server_setup SET setup_complete = 1, setup_channel_id = ? WHERE server_id = ?', 
                                     (channel_id, guild.id))
                            conn.commit()
                            conn.close()
                            
                            # Remove from pending setups
                            del pending_setups[server_id]
                            
                            # Send success message
                            await send_setup_success(message, guild, channel)
                            return

async def is_channel_allowed(message):
    """Check if channel is allowed for commands"""
    # Global admins can use commands anywhere
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT user_id FROM global_admins WHERE user_id = ?', (message.author.id,))
    is_global_admin = c.fetchone()
    
    if is_global_admin:
        conn.close()
        return True
    
    # Check if channel is allowed
    c.execute('SELECT channel_id FROM allowed_channels WHERE channel_id = ?', (message.channel.id,))
    result = c.fetchone()
    conn.close()
    
    return result is not None

async def auto_detect_and_search(message, search_value):
    """Auto-detect search type and process"""
    # Detect search type
    search_type, cleaned_value = detect_search_type(search_value)
    
    if not search_type:
        return
    
    # Create a context object for the search
    ctx = await bot.get_context(message)
    ctx.author = message.author
    ctx.channel = message.channel
    ctx.guild = message.guild
    
    # Process based on detected type
    if search_type == "mobile":
        await number(ctx, mobile_number=cleaned_value)
    elif search_type == "aadhaar":
        await aadhaar(ctx, aadhaar_number=cleaned_value)
    elif search_type == "email":
        await email(ctx, email_address=cleaned_value)
    elif search_type == "telegram":
        await tg(ctx, telegram_input=cleaned_value)
    elif search_type == "vehicle":
        await vehicle(ctx, vehicle_number=cleaned_value)
    elif search_type == "fam":
        await fam(ctx, fampay_id=cleaned_value)

def detect_search_type(search_value):
    """Detect the type of search from input"""
    # Check for FamPay UPI ID
    if '@fam' in search_value.lower():
        # Extract the fampay ID
        match = re.search(r'[\w\.]+@fam', search_value, re.IGNORECASE)
        if match:
            return "fam", match.group()
    
    # Check for email
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if re.match(email_pattern, search_value):
        return "email", search_value
    
    # Check for vehicle number
    vehicle_pattern = r'^[A-Z]{2}[0-9]{1,2}[A-Z]{1,2}[0-9]{4}$'
    cleaned_vehicle = re.sub(r'[^A-Z0-9]', '', search_value.upper())
    if re.match(vehicle_pattern, cleaned_vehicle):
        return "vehicle", cleaned_vehicle
    
    # Check for mobile number
    cleaned_mobile = clean_mobile_number(search_value)
    if cleaned_mobile:
        return "mobile", cleaned_mobile
    
    # Check for Aadhaar number
    aadhaar_match = re.search(r'\d{12}', search_value)
    if aadhaar_match:
        return "aadhaar", aadhaar_match.group()
    
    # Check for Telegram (numeric ID or username)
    if search_value.isdigit() or (search_value.startswith('@') and len(search_value) > 1):
        return "telegram", search_value
    
    return None, None

async def notify_admin_about_join(guild):
    """Notify admin about new server join"""
    try:
        admin_user = bot.get_user(YOUR_DISCORD_ID)
        
        if not admin_user:
            print("❌ Admin user not found!")
            return
        
        # Create invite for the server (try to get existing invite or create one)
        invite_link = "Could not create invite"
        try:
            # Try to get text channels
            text_channels = [channel for channel in guild.text_channels if channel.permissions_for(guild.me).create_instant_invite]
            
            if text_channels:
                # Use the first text channel we have permission in
                invite = await text_channels[0].create_invite(max_age=604800, max_uses=1, reason="Bot join notification")
                invite_link = invite.url
        except:
            pass
        
        # Send notification to admin
        embed = discord.Embed(
            title="📥 BOT ADDED TO NEW SERVER!",
            description=f"**KornFinder Bot** has been added to a new server!",
            color=0x57F287,
            timestamp=datetime.now(timezone.utc)
        )
        
        embed.add_field(name="🏢 Server Name", value=f"**{guild.name}**", inline=True)
        embed.add_field(name="🆔 Server ID", value=f"`{guild.id}`", inline=True)
        embed.add_field(name="👑 Server Owner", value=f"{guild.owner.mention if guild.owner else 'Unknown'}", inline=True)
        embed.add_field(name="👥 Member Count", value=f"**{guild.member_count}** members", inline=True)
        embed.add_field(name="📅 Joined On", value=f"{get_indian_time()}", inline=True)
        embed.add_field(name="🔗 Server Invite", value=f"[Join Server]({invite_link})", inline=True)
        
        embed.set_footer(text="Bot Join Notification • KornFinder")
        
        await admin_user.send(embed=embed)
        
        # Mark notification as sent
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('UPDATE bot_joins SET notification_sent = 1 WHERE server_id = ?', (guild.id,))
        conn.commit()
        conn.close()
        
        print(f"📢 Admin notified about new server: {guild.name}")
        
    except Exception as e:
        print(f"Error notifying admin: {e}")

def get_service_price(service_name):
    """Get price for a service from database"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT price FROM service_prices WHERE service_name = ?', (service_name,))
    result = c.fetchone()
    conn.close()
    
    if result:
        return result[0]
    else:
        # Default prices if not found
        return SERVICE_PRICES.get(service_name, 1)

def check_credits(user_id, service_name):
    """Check if user has credits for search"""
    if has_unlimited_access(user_id):
        return True, "unlimited"
    
    price = get_service_price(service_name)
    user_data = get_user_data(user_id)
    credits = user_data[1]
    
    if credits >= price:
        return True, "credit"
    else:
        return False, "no_credits"

def use_credit(user_id, service_name):
    """Use credits for search"""
    if has_unlimited_access(user_id):
        return True
    
    price = get_service_price(service_name)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE users SET credits = credits - ? WHERE user_id = ?', (price, user_id))
    conn.commit()
    conn.close()
    return True

def refund_credit(user_id, service_name):
    """Refund credits if no records found"""
    if has_unlimited_access(user_id):
        return
    
    price = get_service_price(service_name)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE users SET credits = credits + ? WHERE user_id = ?', (price, user_id))
    conn.commit()
    conn.close()

async def make_api_request(url, max_retries=3):
    """Make API request with retry mechanism and better error handling"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
        'Connection': 'keep-alive',
        'Cache-Control': 'no-cache'
    }
    
    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=30, ssl=False) as response:
                    if response.status == 200:
                        try:
                            return await response.json()
                        except Exception as e:
                            # Try to read as text if JSON fails
                            text = await response.text()
                            print(f"⚠️ JSON parse failed, got text: {text[:100]}")
                            return {"text": text, "error": str(e)}
                    elif response.status in [502, 503, 504]:
                        print(f"⚠️ Server error {response.status}, attempt {attempt + 1}/{max_retries}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2 ** attempt)  # Exponential backoff
                            continue
                        else:
                            raise Exception(f"API server error after {max_retries} attempts: {response.status}")
                    elif response.status == 403:
                        raise Exception("API access forbidden. Please check API key or permissions.")
                    elif response.status == 404:
                        raise Exception("API endpoint not found.")
                    else:
                        raise Exception(f"API returned status {response.status}")
        except asyncio.TimeoutError:
            print(f"⏰ Timeout, attempt {attempt + 1}/{max_retries}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            else:
                raise Exception("Request timed out after multiple attempts")
        except aiohttp.ClientError as e:
            print(f"🌐 Network error: {e}, attempt {attempt + 1}/{max_retries}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            else:
                raise Exception(f"Network error: {str(e)}")
    
    raise Exception("Max retries exceeded")

async def process_api_search(ctx, api_url, search_value, user_id, service_name, search_type="mobile"):
    """Process API search with credit system"""
    global search_count
    
    # First check if bot has admin permissions in the server
    if ctx.guild and not ctx.guild.me.guild_permissions.administrator:
        embed = discord.Embed(
            title="⚠️ ADMIN PERMISSION REQUIRED ⚠️",
            description="**This bot requires Administrator Permissions to function!**\n\nServer admins have been notified. Please wait until admin permissions are granted.",
            color=0xED4245
        )
        embed.add_field(
            name="🔧 **Current Status**",
            value="The bot will not work until it has Administrator permissions in this server.",
            inline=False
        )
        await ctx.send(embed=embed, delete_after=30)
        return None
    
    price = get_service_price(service_name)
    has_credits, credit_type = check_credits(user_id, service_name)
    
    if not has_credits:
        user_data = get_user_data(user_id)
        level = user_data[2]
        voice_minutes = user_data[3]
        
        embed = discord.Embed(
            title="💰 Insufficient Credits!",
            description=f"**{ctx.author.mention}, you need {price} credit(s) for this search!**",
            color=0xED4245
        )
        
        embed.add_field(
            name="📊 Your Stats",
            value=f"**Current Credits:** {user_data[1]}\n**Level:** {level}\n**Voice Minutes:** {voice_minutes}",
            inline=True
        )
        
        embed.add_field(
            name="🎧 Earn Credits",
            value=f"**10 minutes in VC** = 1 credit\n**20 minutes in VC** = 2 credits + level up\nJoin any voice channel to start earning!",
            inline=True
        )
        
        embed.set_footer(text="Voice activity = Search power! 🔋")
        
        message = await ctx.send(embed=embed)
        await asyncio.sleep(30)
        try:
            await message.delete()
        except:
            pass
        return None
    
    # Show searching embed
    search_embed = discord.Embed(
        title="🔍 Launching Premium Search",
        description=f"**Searching for:** `{search_value}`\n**Type:** {search_type.upper()}",
        color=0x5865F2
    )
    
    search_embed.add_field(name="💰 Cost", value=f"**{price} credit(s)**", inline=True)
    search_embed.add_field(name="👤 User", value=f"{ctx.author.mention}", inline=True)
    search_embed.add_field(name="🌐 API Status", value="**Connecting...** 🔄", inline=True)
    search_embed.set_footer(text="Powered by Advanced OSINT Technology")
    search_msg = await ctx.send(embed=search_embed)
    
    # Use credits
    use_credit(user_id, service_name)
    
    try:
        # Update status
        search_embed.set_field_at(2, name="🌐 API Status", value="**Processing...** ⚡", inline=True)
        await search_msg.edit(embed=search_embed)
        
        # Make API request with retry mechanism
        data = await make_api_request(api_url)
        
        # Update status to success
        search_embed.set_field_at(2, name="🌐 API Status", value="**Success!** ✅", inline=True)
        await search_msg.edit(embed=search_embed)
        
        # Wait a moment then delete
        await asyncio.sleep(1)
        await search_msg.delete()
        return data
        
    except Exception as e:
        # Refund credits on error
        refund_credit(user_id, service_name)
        
        error_embed = discord.Embed(
            title="❌ Search Failed",
            description=f"Could not search for `{search_value}`",
            color=0xED4245
        )
        
        # Provide specific error messages
        error_msg = str(e)
        if "403" in error_msg:
            error_detail = "**API Access Forbidden**\nThe API server denied access. This could be due to:\n• Invalid or expired API key\n• IP blocking\n• Rate limiting"
        elif "502" in error_msg or "503" in error_msg or "504" in error_msg:
            error_detail = "**Server Error**\nThe API server is currently experiencing issues:\n• Server may be down\n• High traffic\n• Maintenance in progress"
        elif "timed out" in error_msg.lower():
            error_detail = "**Connection Timeout**\nThe request took too long:\n• Slow network connection\n• API server overloaded\n• Try again later"
        else:
            error_detail = f"**Error:** {error_msg[:150]}"
        
        error_embed.add_field(
            name="📝 Error Details",
            value=f"{error_detail}\n**Credits refunded:** {price}",
            inline=False
        )
        
        error_embed.add_field(
            name="🔄 Solution",
            value="• Try again in a few minutes\n• Check if API service is working\n• Contact support if issue persists",
            inline=False
        )
        
        error_msg = await ctx.send(embed=error_embed)
        await asyncio.sleep(120)
        try:
            await error_msg.delete()
        except:
            pass
        return None

async def send_premium_results(ctx, search_value, data, search_type="mobile"):
    """Send formatted search results"""
    
    # Check for None data
    if data is None:
        embed = discord.Embed(
            title="❌ Search Failed",
            description="No data received from the API.",
            color=0xED4245
        )
        await ctx.send(embed=embed, delete_after=30)
        return
    
    # Handle string responses
    if isinstance(data, str):
        embed = discord.Embed(
            title="📭 No Records Found",
            description=f"No records found for: `{search_value}`",
            color=0xFEE75C
        )
        embed.add_field(name="👤 User", value=f"{ctx.author.mention}", inline=True)
        message = await ctx.send(embed=embed)
        await asyncio.sleep(180)
        try:
            await message.delete()
        except:
            pass
        return
    
    # Handle dictionary responses
    if isinstance(data, dict):
        # Check for text response (failed JSON parse)
        if "text" in data and "error" in data:
            embed = discord.Embed(
                title="❌ Invalid API Response",
                description="The API returned an invalid response format.",
                color=0xED4245
            )
            await ctx.send(embed=embed, delete_after=30)
            return
        
        # Vehicle search response
        if search_type == "vehicle":
            await handle_vehicle_response(ctx, search_value, data)
            return
        
        # Telegram search response
        if search_type == "telegram":
            await handle_telegram_response(ctx, search_value, data)
            return
        
        # FamPay search response
        if search_type == "fam":
            await handle_fam_response(ctx, search_value, data)
            return
        
        # Standard details API response
        await handle_standard_response(ctx, search_value, data, search_type)
        return
    
    # Handle list responses
    elif isinstance(data, list):
        await handle_list_response(ctx, search_value, data, search_type)
        return
    
    # Unknown response format
    embed = discord.Embed(
        title="❌ Unexpected Response Format",
        description="The API returned an unexpected response format.",
        color=0xED4245
    )
    await ctx.send(embed=embed, delete_after=30)

async def handle_fam_response(ctx, search_value, data):
    """Handle FamPay search response"""
    # Check for error response
    if "error" in data:
        embed = discord.Embed(
            title="💳 FamPay Search Failed",
            description=f"Could not find details for FamPay ID: `{search_value}`",
            color=0xED4245
        )
        embed.add_field(
            name="📝 Error Details",
            value=f"**{data.get('error', 'Unknown error')}**",
            inline=False
        )
        embed.add_field(
            name="💡 Possible Reasons",
            value="• FamPay UPI ID is incorrect\n• Account not found in database\n• API service temporary unavailable",
            inline=False
        )
        embed.add_field(name="👤 User", value=f"{ctx.author.mention}", inline=True)
        
        message = await ctx.send(embed=embed)
        await asyncio.sleep(180)
        try:
            await message.delete()
        except:
            pass
        return
    
    # Check if status is false
    if data.get("status") == False:
        embed = discord.Embed(
            title="💳 FamPay Search Results",
            description=f"**FamPay UPI ID:** `{data.get('fam_id', search_value)}`",
            color=0xFEE75C
        )
        embed.add_field(
            name="📱 Mobile Number",
            value="**Not Found** ❌",
            inline=True
        )
        embed.add_field(
            name="💡 Information",
            value="Mobile number not available for this FamPay account in the database.",
            inline=False
        )
        embed.add_field(name="👤 User", value=f"{ctx.author.mention}", inline=True)
        
        message = await ctx.send(embed=embed)
        await asyncio.sleep(180)
        try:
            await message.delete()
        except:
            pass
        return
    
    # Success response with mobile number
    phone = data.get("phone", "").strip()
    name = data.get("name", "N/A").strip()
    
    embed = discord.Embed(
        title="✅ FamPay Search Successful!",
        description=f"**Found details for FamPay UPI ID:** `{data.get('fam_id', search_value)}`",
        color=0x57F287,
        timestamp=datetime.now(timezone.utc)
    )
    
    embed.add_field(
        name="💳 **FAMPAY INFORMATION**",
        value=(
            f"**UPI ID:** ```{data.get('fam_id', search_value)}```\n"
            f"**Name:** ```{name}```\n"
            f"**Source:** {data.get('source', 'N/A')}"
        ),
        inline=False
    )
    
    embed.add_field(
        name="📱 **MOBILE NUMBER FOUND**",
        value=f"```{phone}```",
        inline=False
    )
    
    embed.add_field(
        name="💰 **Search Cost**",
        value="**1 credit** (FamPay to Mobile Search)",
        inline=True
    )
    
    embed.add_field(name="👤 **Requested By**", value=f"{ctx.author.mention}", inline=True)
    
    embed.set_footer(text=f"FamPay UPI Search • {get_indian_time()}")
    
    message = await ctx.send(embed=embed)
    await asyncio.sleep(180)
    try:
        await message.delete()
    except:
        pass

async def handle_vehicle_response(ctx, search_value, data):
    """Handle vehicle search response"""
    # Check for error response
    if "error" in data:
        embed = discord.Embed(
            title="🚗 Vehicle Search Failed",
            description=f"Could not find details for vehicle: `{search_value}`",
            color=0xED4245
        )
        embed.add_field(
            name="📝 Error Details",
            value=f"**{data.get('error', 'Unknown error')}**",
            inline=False
        )
        embed.add_field(
            name="💡 Possible Reasons",
            value="• Vehicle number is incorrect\n• Vehicle not found in database\n• API service temporary unavailable",
            inline=False
        )
        embed.add_field(name="👤 User", value=f"{ctx.author.mention}", inline=True)
        
        message = await ctx.send(embed=embed)
        await asyncio.sleep(180)
        try:
            await message.delete()
        except:
            pass
        return
    
    # Check for mobile number
    mobile_no = data.get("mobile_no", "").strip()
    if not mobile_no or mobile_no.lower() == "null":
        embed = discord.Embed(
            title="🚗 Vehicle Search Results",
            description=f"**Vehicle Number:** `{data.get('vehicle_no', search_value)}`",
            color=0xFEE75C
        )
        embed.add_field(
            name="📱 Mobile Number",
            value="**Not Found** ❌",
            inline=True
        )
        embed.add_field(
            name="💡 Information",
            value="Mobile number not available for this vehicle in the database.",
            inline=False
        )
        embed.add_field(name="👤 User", value=f"{ctx.author.mention}", inline=True)
        
        message = await ctx.send(embed=embed)
        await asyncio.sleep(180)
        try:
            await message.delete()
        except:
            pass
        return
    
    # Success response with mobile number
    embed = discord.Embed(
        title="✅ Vehicle Search Successful!",
        description=f"**Found mobile number for vehicle:** `{data.get('vehicle_no', search_value)}`",
        color=0x57F287,
        timestamp=datetime.now(timezone.utc)
    )
    
    embed.add_field(
        name="🚗 **VEHICLE INFORMATION**",
        value=(
            f"**Vehicle Number:** ```{data.get('vehicle_no', search_value)}```\n"
            f"**Source:** {data.get('source', 'N/A')}"
        ),
        inline=False
    )
    
    embed.add_field(
        name="📱 **MOBILE NUMBER FOUND**",
        value=f"```{mobile_no}```",
        inline=False
    )
    
    embed.add_field(
        name="💰 **Search Cost**",
        value="**2 credits** (Vehicle to Mobile Search)",
        inline=True
    )
    
    embed.add_field(name="👤 **Requested By**", value=f"{ctx.author.mention}", inline=True)
    
    embed.set_footer(text=f"Vehicle Number Search • {get_indian_time()}")
    
    message = await ctx.send(embed=embed)
    await asyncio.sleep(180)
    try:
        await message.delete()
    except:
        pass

async def handle_telegram_response(ctx, search_value, data):
    """Handle Telegram search response"""
    # Check if success is false
    if data.get("success") == False:
        embed = discord.Embed(
            title="📭 Telegram Details Not Found",
            description=f"No Telegram details found for: `{search_value}`",
            color=0xFEE75C
        )
        embed.add_field(
            name="💡 Information",
            value=data.get("msg", "No data found for this Telegram ID"),
            inline=False
        )
        embed.add_field(name="👤 User", value=f"{ctx.author.mention}", inline=True)
        
        message = await ctx.send(embed=embed)
        await asyncio.sleep(180)
        try:
            await message.delete()
        except:
            pass
        return
    
    # Check if phone info is available
    phone_info = data.get("phone_info")
    if not phone_info:
        embed = discord.Embed(
            title="📱 Telegram Search Results",
            description=f"**Telegram ID:** `{search_value}`",
            color=0xFEE75C
        )
        embed.add_field(
            name="📞 **Mobile Number**",
            value="**Not Found** ❌\nNo mobile number linked to this Telegram account",
            inline=False
        )
        
        # Add account info if available
        account_info = data.get("account_info", {})
        if account_info:
            embed.add_field(
                name="👤 **Account Information**",
                value=(
                    f"**Name:** {account_info.get('first_name', 'N/A')} {account_info.get('last_name', '')}\n"
                    f"**Status:** {'✅ Active' if account_info.get('is_active') else '❌ Inactive'}\n"
                    f"**Type:** {'🤖 Bot' if account_info.get('is_bot') else '👤 User'}"
                ),
                inline=False
            )
        
        embed.add_field(name="👤 **Requested By**", value=f"{ctx.author.mention}", inline=True)
        embed.set_footer(text=f"Telegram ID Search • {get_indian_time()}")
        
        message = await ctx.send(embed=embed)
        await asyncio.sleep(180)
        try:
            await message.delete()
        except:
            pass
        return
    
    # Success response with phone info
    embed = discord.Embed(
        title="✅ Telegram Search Successful!",
        description=f"**Found details for Telegram ID:** `{search_value}`",
        color=0x57F287,
        timestamp=datetime.now(timezone.utc)
    )
    
    # Phone info section
    if phone_info:
        embed.add_field(
            name="📱 **PHONE INFORMATION**",
            value=(
                f"**Country:** {phone_info.get('country', 'N/A')}\n"
                f"**Country Code:** {phone_info.get('country_code', 'N/A')}\n"
                f"**Number:** {phone_info.get('number', 'N/A')}\n"
                f"**Full Number:** {phone_info.get('full_number', 'N/A')}"
            ),
            inline=False
        )
    
    # Account info section
    account_info = data.get("account_info", {})
    if account_info:
        account_status = "✅ Active" if account_info.get('is_active') else "❌ Inactive"
        bot_status = "🤖 Bot" if account_info.get('is_bot') else "👤 User"
        
        embed.add_field(
            name="👤 **ACCOUNT INFORMATION**",
            value=(
                f"**Status:** {account_status}\n"
                f"**Type:** {bot_status}\n"
                f"**First Name:** {account_info.get('first_name', 'N/A')}\n"
                f"**Last Name:** {account_info.get('last_name', 'N/A')}"
            ),
            inline=False
        )
    
    embed.add_field(
        name="💰 **Search Cost**",
        value="**5 credits** (Telegram to Mobile Search)",
        inline=True
    )
    
    embed.add_field(name="👤 **Requested By**", value=f"{ctx.author.mention}", inline=True)
    
    embed.set_footer(text=f"Telegram ID Search • {get_indian_time()}")
    
    message = await ctx.send(embed=embed)
    await asyncio.sleep(180)
    try:
        await message.delete()
    except:
        pass

async def handle_standard_response(ctx, search_value, data, search_type):
    """Handle standard API response"""
    # Check for no records
    if data.get("message") == "No records found" or not data:
        embed = discord.Embed(
            title="📭 No Records Found",
            description=f"No records found for: `{search_value}`",
            color=0xFEE75C
        )
        embed.add_field(name="👤 User", value=f"{ctx.author.mention}", inline=True)
        
        message = await ctx.send(embed=embed)
        await asyncio.sleep(180)
        try:
            await message.delete()
        except:
            pass
        return
    
    # Single record response
    embed = create_record_embed(data, 1, 1, search_value, search_type)
    embed.title = "✅ Search Result"
    message = await ctx.send(embed=embed)
    await asyncio.sleep(180)
    try:
        await message.delete()
    except:
        pass

async def handle_list_response(ctx, search_value, data, search_type):
    """Handle list API response"""
    if not data or len(data) == 0:
        embed = discord.Embed(
            title="📭 No Records Found",
            description=f"No records found for: `{search_value}`",
            color=0xFEE75C
        )
        embed.add_field(name="👤 User", value=f"{ctx.author.mention}", inline=True)
        
        message = await ctx.send(embed=embed)
        await asyncio.sleep(180)
        try:
            await message.delete()
        except:
            pass
        return
    
    total_records = len(data)
    
    summary_embed = discord.Embed(
        title="✅ SEARCH SUCCESSFUL!",
        description=f"**Found {total_records} Record(s) for `{search_value}`**",
        color=0x57F287,
        timestamp=datetime.now(timezone.utc)
    )
    
    summary_embed.add_field(
        name="📊 Search Summary",
        value=f"**Type:** {search_type.upper()}\n**Value:** `{search_value}`\n**Records:** {total_records}\n**Time:** {get_indian_time()}",
        inline=False
    )
    
    summary_embed.add_field(name="👤 User", value=f"{ctx.author.mention}", inline=True)
    
    summary_embed.add_field(
        name="⏰ Auto-Delete",
        value="**This message will be automatically deleted in 3 minutes!**\nSave important information before it disappears.",
        inline=False
    )
    
    summary_message = await ctx.send(embed=summary_embed)
    
    # Send individual records
    messages_to_delete = [summary_message]
    
    for index, record in enumerate(data[:5], 1):
        if isinstance(record, dict):
            record_embed = create_record_embed(record, index, min(5, total_records), search_value, search_type)
            record_message = await ctx.send(embed=record_embed)
            messages_to_delete.append(record_message)
            await asyncio.sleep(0.5)
    
    if total_records > 5:
        note_embed = discord.Embed(
            title="📋 Note",
            description=f"Showing 5 of {total_records} records for better readability.",
            color=0xFEE75C
        )
        note_message = await ctx.send(embed=note_embed)
        messages_to_delete.append(note_message)
    
    # Auto-delete after 3 minutes
    await asyncio.sleep(180)
    for msg in messages_to_delete:
        try:
            await msg.delete()
        except:
            pass

def create_record_embed(record, current_index, total_records, search_value, search_type):
    """Create premium embed for record"""
    embed = discord.Embed(
        title=f"📄 RECORD {current_index} of {total_records}",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc)
    )
    
    # Add all available fields with bold formatting
    if 'mobile' in record and record['mobile']:
        embed.add_field(name="📱 **MOBILE NUMBER**", value=f"```{record['mobile']}```", inline=True)
    
    if 'name' in record and record['name']:
        embed.add_field(name="👤 **FULL NAME**", value=f"```{record['name']}```", inline=True)
    
    father_name = None
    if 'father_name' in record and record['father_name']:
        father_name = record['father_name']
    elif 'fathersname' in record and record['fathersname']:
        father_name = record['fathersname']
    
    if father_name:
        embed.add_field(name="👨‍👦 **FATHER'S NAME**", value=f"```{father_name}```", inline=True)
    
    if 'address' in record and record['address']:
        address = format_address(record['address'])
        embed.add_field(name="🏠 **COMPLETE ADDRESS**", value=address, inline=False)
    
    if 'circle' in record and record['circle']:
        embed.add_field(name="🌍 **TELECOM CIRCLE**", value=f"```{record['circle']}```", inline=True)
    
    id_number = None
    if 'id_number' in record and record['id_number']:
        id_number = record['id_number']
    elif 'idnumber' in record and record['idnumber']:
        id_number = record['idnumber']
    
    if id_number:
        embed.add_field(name="🪪 **ID NUMBER**", value=f"```{id_number}```", inline=True)
    
    if 'email' in record and record['email']:
        embed.add_field(name="📧 **EMAIL ADDRESS**", value=f"```{record['email']}```", inline=True)
    
    if 'alt_mobile' in record and record['alt_mobile']:
        embed.add_field(name="📞 **ALTERNATE MOBILE**", value=f"```{record['alt_mobile']}```", inline=True)
    
    # Add search value if not already in fields
    if search_type == "mobile" and 'mobile' not in record:
        embed.add_field(name="🔍 **SEARCHED FOR**", value=f"```{search_value}```", inline=False)
    
    embed.set_footer(text=f"Record {current_index}/{total_records} • {get_indian_time()}")
    
    return embed

# ============================
# AUTO-DETECT COMMAND
# ============================

@bot.command()
@is_allowed_channel()
async def info(ctx, *, search_value: str = None):
    """Auto-detect search type or show bot info"""
    if not search_value:
        # Show bot information
        await show_bot_info(ctx)
        return
    
    # Auto-detect search type
    search_type, cleaned_value = detect_search_type(search_value)
    
    if not search_type:
        embed = discord.Embed(
            title="❌ Could Not Auto-Detect",
            description=f"Could not detect the type of: `{search_value}`",
            color=0xED4245
        )
        embed.add_field(
            name="💡 Supported Formats",
            value=(
                "• **Mobile Number:** 9876543210\n"
                "• **Aadhaar Card:** 123456789012\n"
                "• **Email:** example@gmail.com\n"
                "• **Telegram:** @username or 123456789\n"
                "• **Vehicle:** GJ01AB1234\n"
                "• **FamPay:** username@fam"
            ),
            inline=False
        )
        await ctx.send(embed=embed)
        return
    
    # Process based on detected type
    if search_type == "mobile":
        await number(ctx, mobile_number=cleaned_value)
    elif search_type == "aadhaar":
        await aadhaar(ctx, aadhaar_number=cleaned_value)
    elif search_type == "email":
        await email(ctx, email_address=cleaned_value)
    elif search_type == "telegram":
        await tg(ctx, telegram_input=cleaned_value)
    elif search_type == "vehicle":
        await vehicle(ctx, vehicle_number=cleaned_value)
    elif search_type == "fam":
        await fam(ctx, fampay_id=cleaned_value)

async def show_bot_info(ctx):
    """Show bot information"""
    # Create buttons
    invite_button = Button(
        label="🚀 Add to Your Server",
        url=BOT_INVITE_LINK,
        style=discord.ButtonStyle.link
    )
    
    support_button = Button(
        label="💬 Join Support Server",
        url=DEVELOPER_INFO['discord'],
        style=discord.ButtonStyle.link
    )
    
    view = View()
    view.add_item(invite_button)
    view.add_item(support_button)
    
    embed = discord.Embed(
        title="🤖 KORNFINDER BOT - Complete Information",
        description="**Advanced OSINT Search Bot with Voice Chat Credit System** 🔍",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc)
    )
    
    # Bot Statistics
    uptime = datetime.now(timezone.utc) - bot.start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    # Server count
    server_count = len(bot.guilds)
    
    embed.add_field(
        name="📊 **Bot Statistics**",
        value=(
            f"**Uptime:** {days}d {hours}h {minutes}m\n"
            f"**Servers:** {server_count} servers\n"
            f"**Developer:** {DEVELOPER_INFO['developer']}\n"
            f"**Version:** Premium v6.0"
        ),
        inline=False
    )
    
    # Get current service prices
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT service_name, price FROM service_prices')
    prices = c.fetchall()
    conn.close()
    
    price_text = ""
    for service_name, price in prices:
        service_display = {
            'mobile': '📱 Mobile Number',
            'aadhaar': '🪪 Aadhaar Card',
            'email': '📧 Email Address',
            'telegram': '📲 Telegram ID',
            'vehicle': '🚗 Vehicle Number',
            'fam': '💳 FamPay UPI'
        }.get(service_name, service_name.title())
        
        price_text += f"• **{service_display}:** {price} credit{'s' if price > 1 else ''}\n"
    
    # Search Features
    embed.add_field(
        name="🔍 **Search Features & Prices**",
        value=price_text,
        inline=False
    )
    
    # Auto-Detection Feature
    embed.add_field(
        name="⚡ **AUTO-DETECTION SYSTEM**",
        value=(
            "**Just type and search!** No commands needed!\n\n"
            "**Examples:**\n"
            "• `9876543210` - Mobile number\n"
            "• `example@gmail.com` - Email\n"
            "• `GJ01AB1234` - Vehicle number\n"
            "• `username@fam` - FamPay UPI\n"
            "• `@username` - Telegram\n"
            "• `123456789012` - Aadhaar"
        ),
        inline=False
    )
    
    # Credit System
    embed.add_field(
        name="💰 **Credit System**",
        value=(
            "**Earn Credits in Voice Chat:** 🎤\n"
            "• **10 minutes** = 1 credit 💎\n"
            "• **20 minutes** = 2 credits + Level Up ⭐\n"
            "• **Credits are GLOBAL** - Use in any server!\n"
            "• **No daily limits** - Earn unlimited! 🔥\n"
            "• **Auto-tracking** - Join VC and earn automatically"
        ),
        inline=False
    )
    
    # Quick Commands
    embed.add_field(
        name="⚡ **Quick Commands**",
        value=(
            "`!info` - Show this message\n"
            "`!credits` - Check your balance\n"
            "`!voice` - Voice chat status\n"
            "`!level` - Check your level\n"
            "`!leader` - View top users leaderboard\n"
            "`!addchannel #channel` - Add allowed channel (admin)\n"
            "`!listchannels` - List allowed channels"
        ),
        inline=False
    )
    
    # Support Information
    embed.add_field(
        name="📞 **Support & Links**",
        value=(
            f"**Developer:** {DEVELOPER_INFO['developer']}\n"
            f"**Discord Server:** [Join Here]({DEVELOPER_INFO['discord']}) 👥\n"
            f"**Telegram:** [Contact]({DEVELOPER_INFO['telegram']}) 📲\n"
            f"**API Provider:** {DEVELOPER_INFO['phenion']} 🔗"
        ),
        inline=False
    )
    
    # Footer with tips
    embed.set_footer(
        text=f"💡 Pro Tip: Just type any value to search! Auto-detection is ACTIVE! • {get_indian_time()}"
    )
    
    await ctx.send(embed=embed, view=view)

# ============================
# MAIN SEARCH COMMANDS
# ============================

@bot.command(aliases=['num'])
@is_allowed_channel()
async def number(ctx, *, mobile_number: str = None):
    """Search mobile number"""
    if not mobile_number:
        embed = discord.Embed(
            title="📱 Mobile Number Search",
            description="**Usage:** `!num 9876543210` or just type `9876543210`\n**Cost:** 1 credit per search\n**Format:** 10-digit Indian number",
            color=0x3498DB
        )
        await ctx.send(embed=embed)
        return
    
    # Clean the mobile number
    cleaned_number = clean_mobile_number(mobile_number)
    
    if not cleaned_number:
        embed = discord.Embed(
            title="❌ Invalid Mobile Number",
            description="Please provide a valid 10-digit Indian mobile number!",
            color=0xED4245
        )
        
        embed.add_field(
            name="💡 Accepted Formats",
            value=(
                "• `9876543210`\n"
                "• `98765 43210`\n"
                "• `+91 9876543210`\n"
                "• `+91 98765 43210`\n"
                "• `919876543210`"
            ),
            inline=False
        )
        
        embed.add_field(
            name="🔧 **Auto-Cleaning Feature**",
            value="The bot automatically cleans numbers by removing spaces, country codes, and taking the last 10 digits.",
            inline=False
        )
        
        await ctx.send(embed=embed, delete_after=30)
        return
    
    api_url = DETAILS_API_URL.format(value=cleaned_number)
    
    data = await process_api_search(ctx, api_url, cleaned_number, ctx.author.id, "mobile", "mobile")
    if data is not None:
        await send_premium_results(ctx, cleaned_number, data, "mobile")

@bot.command(aliases=['card'])
@is_allowed_channel()
async def aadhaar(ctx, *, aadhaar_number: str = None):
    """Search Aadhaar number"""
    if not aadhaar_number:
        embed = discord.Embed(
            title="🪪 Aadhaar Card Search",
            description="**Usage:** `!card 123456789012` or just type `123456789012`\n**Cost:** 1 credit per search\n**Format:** 12-digit Aadhaar",
            color=0x3498DB
        )
        await ctx.send(embed=embed)
        return
    
    # Extract 12-digit Aadhaar
    aadhaar_match = re.search(r'\d{12}', aadhaar_number)
    if not aadhaar_match:
        await ctx.send("❌ Please provide a valid 12-digit Aadhaar number!")
        return
    
    aadhaar = aadhaar_match.group()
    api_url = DETAILS_API_URL.format(value=aadhaar)
    
    data = await process_api_search(ctx, api_url, aadhaar, ctx.author.id, "aadhaar", "aadhaar")
    if data is not None:
        await send_premium_results(ctx, aadhaar, data, "aadhaar")

@bot.command()
@is_allowed_channel()
async def email(ctx, *, email_address: str = None):
    """Search email address"""
    if not email_address:
        embed = discord.Embed(
            title="📧 Email Address Search",
            description="**Usage:** `!email example@domain.com` or just type `example@domain.com`\n**Cost:** 1 credit per search\n**Format:** Valid email address",
            color=0x3498DB
        )
        await ctx.send(embed=embed)
        return
    
    # Basic email validation
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email_address.strip()):
        await ctx.send("❌ Please provide a valid email address!")
        return
    
    email_addr = email_address.strip()
    api_url = DETAILS_API_URL.format(value=email_addr)
    
    data = await process_api_search(ctx, api_url, email_addr, ctx.author.id, "email", "email")
    if data is not None:
        await send_premium_results(ctx, email_addr, data, "email")

@bot.command()
@is_allowed_channel()
async def tg(ctx, *, telegram_input: str = None):
    """Search Telegram to Mobile"""
    if not telegram_input:
        embed = discord.Embed(
            title="📲 Telegram to Mobile Search",
            description="**Usage:** `!tg 123456789` or just type `@username`\n**Cost:** 5 credits per search\n**Note:** Searches for mobile linked to Telegram",
            color=0x9B59B6
        )
        await ctx.send(embed=embed)
        return
    
    telegram_value = telegram_input.strip()
    api_url = TELEGRAM_API_URL.format(value=telegram_value)
    
    data = await process_api_search(ctx, api_url, telegram_value, ctx.author.id, "telegram", "telegram")
    if data is not None:
        await send_premium_results(ctx, telegram_value, data, "telegram")

@bot.command()
@is_allowed_channel()
async def vehicle(ctx, *, vehicle_number: str = None):
    """Search vehicle number to mobile"""
    if not vehicle_number:
        embed = discord.Embed(
            title="🚗 Vehicle Number Search",
            description="**Usage:** `!vehicle GJ01AB1234` or just type `GJ01AB1234`\n**Cost:** 2 credits per search\n**Format:** Indian vehicle registration number",
            color=0x3498DB
        )
        embed.add_field(
            name="💡 Examples",
            value="• `GJ01AB1234`\n• `DL3CAB1234`\n• `MH12DE5678`\n• `KA05MF9999`",
            inline=False
        )
        await ctx.send(embed=embed)
        return
    
    # Clean vehicle number
    vehicle_num = vehicle_number.strip().upper()
    # Remove spaces and special characters
    vehicle_num = re.sub(r'[^A-Z0-9]', '', vehicle_num)
    
    if len(vehicle_num) < 8:
        await ctx.send("❌ Please provide a valid vehicle number!")
        return
    
    api_url = VEHICLE_API_URL.format(value=vehicle_num)
    
    data = await process_api_search(ctx, api_url, vehicle_num, ctx.author.id, "vehicle", "vehicle")
    if data is not None:
        await send_premium_results(ctx, vehicle_num, data, "vehicle")

@bot.command()
@is_allowed_channel()
async def fam(ctx, *, fampay_id: str = None):
    """Search FamPay UPI to Mobile"""
    if not fampay_id:
        embed = discord.Embed(
            title="💳 FamPay to Mobile Search",
            description="**Usage:** `!fam username@fam` or just type `username@fam`\n**Cost:** 1 credit per search\n**Format:** FamPay UPI ID",
            color=0x9B59B6
        )
        embed.add_field(
            name="💡 Examples",
            value="• `anshapi@fam`\n• `john@fam`\n• `jane.doe@fam`",
            inline=False
        )
        await ctx.send(embed=embed)
        return
    
    # Clean fampay ID
    fampay_id = fampay_id.strip().lower()
    
    # Validate fampay ID format
    if not re.match(r'^[\w\.]+@fam$', fampay_id):
        await ctx.send("❌ Please provide a valid FamPay UPI ID (example: username@fam)!")
        return
    
    api_url = FAM_API_URL.format(value=fampay_id)
    
    data = await process_api_search(ctx, api_url, fampay_id, ctx.author.id, "fam", "fam")
    if data is not None:
        await send_premium_results(ctx, fampay_id, data, "fam")

# ============================
# USER COMMANDS
# ============================

@bot.command()
@is_allowed_channel()
async def credits(ctx):
    """Check your credits"""
    user_data = get_user_data(ctx.author.id)
    credits = user_data[1]
    level = user_data[2]
    voice_minutes = user_data[3]
    unlimited = user_data[4]
    
    embed = discord.Embed(
        title="💰 Your Credit Balance",
        description=f"**{ctx.author.mention}, here are your current stats:**",
        color=0x9B59B6
    )
    
    if unlimited == 1:
        embed.add_field(name="✨ **UNLIMITED ACCESS**", value="**You have unlimited credits!** 🎉", inline=False)
    else:
        embed.add_field(name="💎 **Credits Available**", value=f"**{credits}** credits", inline=True)
    
    embed.add_field(name="⭐ **Level**", value=f"**{level}**", inline=True)
    embed.add_field(name="🎧 **Voice Minutes**", value=f"**{voice_minutes}** minutes", inline=True)
    
    # Calculate next rewards based on server settings
    minutes_per_credit = get_voice_time_settings(ctx.guild.id if ctx.guild else None)
    next_credit = minutes_per_credit - (voice_minutes % minutes_per_credit)
    next_level = (minutes_per_credit * 2) - (voice_minutes % (minutes_per_credit * 2))
    
    embed.add_field(
        name="🎯 **Next Rewards**",
        value=f"**{next_credit} minutes** → 1 credit\n**{next_level} minutes** → 2 credits + level up",
        inline=False
    )
    
    embed.add_field(
        name="🌍 **Global Credits**",
        value="Your credits are **GLOBAL** - use them in any server where this bot is present!",
        inline=False
    )
    
    await ctx.send(embed=embed)

@bot.command()
@is_allowed_channel()
async def voice(ctx):
    """Check voice chat status"""
    user_data = get_user_data(ctx.author.id)
    voice_minutes = user_data[3]
    level = user_data[2]
    
    session = get_voice_session(ctx.author.id)
    
    # Get voice time settings for this server
    minutes_per_credit = get_voice_time_settings(ctx.guild.id if ctx.guild else None)
    
    embed = discord.Embed(
        title="🎧 Voice Chat Status",
        description=f"**{ctx.author.mention}, here's your voice activity:**",
        color=0x3498DB
    )
    
    if session:
        join_time = datetime.fromisoformat(session[1])
        last_check = datetime.fromisoformat(session[4])
        time_spent = (last_check - join_time).total_seconds() / 60
        time_spent = int(time_spent)
        
        # Get current time for more accurate calculation
        current_time = datetime.now()
        actual_time = (current_time - join_time).total_seconds() / 60
        actual_time = int(actual_time)
        
        embed.add_field(
            name="🔴 **Live Session Active**",
            value=(
                f"**Current Session:** {actual_time} minutes\n"
                f"**Tracked Time:** {time_spent} minutes\n"
                f"**Total Time:** {voice_minutes} minutes\n"
                f"**Level:** {level}"
            ),
            inline=False
        )
        
        # Show which channel
        guild = bot.get_guild(session[2])
        channel = guild.get_channel(session[3]) if guild else None
        if channel:
            embed.add_field(
                name="📢 **In Channel**",
                value=f"**{channel.name}** in **{guild.name}**",
                inline=True
            )
    else:
        embed.add_field(
            name="🟢 **Ready to Earn**",
            value="Join any voice channel to start earning credits!",
            inline=False
        )
    
    # Calculate next rewards based on server settings
    next_credit = minutes_per_credit - (voice_minutes % minutes_per_credit)
    next_level = (minutes_per_credit * 2) - (voice_minutes % (minutes_per_credit * 2))
    
    embed.add_field(
        name="🎯 **Next Rewards**",
        value=(
            f"**{next_credit} minutes** → **1 credit** 💎\n"
            f"**{next_level} minutes** → **2 credits + level up** ⭐"
        ),
        inline=False
    )
    
    embed.add_field(
        name="⚙️ **Server Settings**",
        value=f"**{minutes_per_credit} minutes** = 1 credit\n*(Global admins can change this with !setvc)*",
        inline=False
    )
    
    embed.add_field(
        name="💡 **Pro Tips**",
        value=(
            "• Join voice channels with friends 🎤\n"
            "• Background music sessions count 🎵\n"
            "• Every minute brings you closer to credits ⏰\n"
            "• Credits are GLOBAL - use anywhere! 🌍"
        ),
        inline=False
    )
    
    await ctx.send(embed=embed)

@bot.command()
@is_allowed_channel()
async def level(ctx):
    """Check your level"""
    user_data = get_user_data(ctx.author.id)
    level = user_data[2]
    voice_minutes = user_data[3]
    
    # Get voice time settings for this server
    minutes_per_credit = get_voice_time_settings(ctx.guild.id if ctx.guild else None)
    
    embed = discord.Embed(
        title="⭐ Your Level Stats",
        description=f"**{ctx.author.mention}, here's your level progression:**",
        color=0xFFD700
    )
    
    embed.add_field(name="🏆 **Current Level**", value=f"**{level}**", inline=True)
    embed.add_field(name="🎧 **Total Voice Minutes**", value=f"**{voice_minutes}** minutes", inline=True)
    
    # Calculate progress to next level (2 credits = 1 level)
    minutes_per_level = minutes_per_credit * 2
    minutes_in_current_level = voice_minutes % minutes_per_level
    minutes_to_next_level = minutes_per_level - minutes_in_current_level
    
    # Progress bar
    progress_percentage = (minutes_in_current_level / minutes_per_level) * 100
    progress_bar = "🟩" * int(progress_percentage / 10) + "⬜" * (10 - int(progress_percentage / 10))
    
    embed.add_field(
        name="📊 **Progress to Level {next_level}**".format(next_level=level + 1),
        value=f"{progress_bar} {progress_percentage:.0f}%\n**{minutes_to_next_level} minutes needed**",
        inline=False
    )
    
    embed.add_field(
        name="🎯 **Level Up Rewards**",
        value=f"Every **{minutes_per_level} minutes** in voice chat gives you:\n• **2 credits** 💎\n• **1 level up** ⭐",
        inline=False
    )
    
    await ctx.send(embed=embed)

@bot.command()
@is_allowed_channel()
async def leader(ctx):
    """Show global leaderboard"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        SELECT user_id, level, credits, total_voice_minutes 
        FROM users 
        WHERE total_voice_minutes > 0
        ORDER BY credits DESC, level DESC, total_voice_minutes DESC 
        LIMIT 10
    ''')
    top_users = c.fetchall()
    conn.close()
    
    embed = discord.Embed(
        title="🏆 Global Leaderboard - Top 10 Users",
        description="**Ranked by credits and voice activity**",
        color=0xFFD700,
        timestamp=datetime.now(timezone.utc)
    )
    
    if not top_users:
        embed.add_field(
            name="No Users Yet",
            value="Be the first to join voice chat and earn credits! 🎤",
            inline=False
        )
    else:
        leaderboard_text = ""
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        
        for idx, (user_id, level, credits, voice_minutes) in enumerate(top_users):
            try:
                user = await bot.fetch_user(user_id)
                username = user.name
            except:
                username = f"User {user_id}"
            
            medal = medals[idx] if idx < len(medals) else f"{idx+1}."
            
            leaderboard_text += (
                f"{medal} **{username}**\n"
                f"   💰 **{credits} credits** | ⭐ Level {level} | 🎧 {voice_minutes} mins\n\n"
            )
        
        embed.add_field(name="🏅 Top Users", value=leaderboard_text, inline=False)
    
    # Add user's rank if available
    all_users = get_db_connection().execute(
        'SELECT user_id FROM users WHERE total_voice_minutes > 0 ORDER BY credits DESC, level DESC, total_voice_minutes DESC'
    ).fetchall()
    
    user_rank = None
    for rank, (uid,) in enumerate(all_users, 1):
        if uid == ctx.author.id:
            user_rank = rank
            break
    
    if user_rank:
        user_data = get_user_data(ctx.author.id)
        embed.add_field(
            name="📈 Your Rank",
            value=(
                f"**You are ranked #{user_rank} globally!**\n"
                f"💰 **Credits:** {user_data[1]}\n"
                f"⭐ **Level:** {user_data[2]}\n"
                f"🎧 **Voice Minutes:** {user_data[3]}"
            ),
            inline=False
        )
    
    embed.set_footer(text=f"Updated • {get_indian_time()}")
    await ctx.send(embed=embed)

# ============================
# ADMIN COMMANDS
# ============================

@bot.command()
@is_global_admin()
async def addcredits(ctx, user_input: str, credit_amount: int):
    """Add credits to a user (global admin only)"""
    # Try to resolve user input
    user = await resolve_user(ctx, user_input)
    
    if not user:
        await ctx.send("❌ User not found! Please provide a valid user ID, mention, or username.")
        return
    
    if credit_amount <= 0:
        await ctx.send("❌ Credit amount must be positive!")
        return
    
    # Add credits
    update_user_credits(user.id, credit_amount)
    
    # Get updated user data
    user_data = get_user_data(user.id)
    
    embed = discord.Embed(
        title="💰 Credits Added!",
        description=f"**Successfully added {credit_amount} credits to {user.mention}!**",
        color=0x57F287
    )
    
    embed.add_field(name="👤 User", value=f"{user.mention}\n(ID: `{user.id}`)", inline=True)
    embed.add_field(name="💎 Credits Added", value=f"**{credit_amount} credits**", inline=True)
    embed.add_field(name="📊 New Balance", value=f"**{user_data[1]} credits**", inline=True)
    embed.add_field(name="👤 Added By", value=f"{ctx.author.mention}", inline=True)
    
    await ctx.send(embed=embed)
    
    # Notify the user
    try:
        user_notification = discord.Embed(
            title="🎉 Credits Added!",
            description=f"You received **{credit_amount} credits** from {ctx.author.mention}",
            color=0x57F287
        )
        user_notification.add_field(name="📊 New Balance", value=f"**{user_data[1]} credits**", inline=True)
        user_notification.add_field(name="🌍 Global Credits", value="These credits can be used in ANY server with KornFinder Bot!", inline=False)
        await user.send(embed=user_notification)
    except:
        pass

@bot.command()
@is_server_admin()
async def addadmin(ctx, user_input: str):
    """Add server admin (server admins only)"""
    # Try to resolve user input
    user = await resolve_user(ctx, user_input)
    
    if not user:
        await ctx.send("❌ User not found! Please provide a valid user ID, mention, or username.")
        return
    
    # Check if user is already a server admin
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT user_id FROM server_admins WHERE server_id = ? AND user_id = ?', (ctx.guild.id, user.id))
    existing = c.fetchone()
    
    if existing:
        await ctx.send(f"❌ {user.mention} is already a server admin!")
        conn.close()
        return
    
    # Add as server admin
    c.execute('INSERT INTO server_admins (server_id, user_id, added_by) VALUES (?, ?, ?)', 
              (ctx.guild.id, user.id, ctx.author.id))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(
        title="✅ Server Admin Added!",
        description=f"**{user.mention} is now a server admin!**",
        color=0x57F287
    )
    
    embed.add_field(name="👤 New Admin", value=f"{user.mention}\n(ID: `{user.id}`)", inline=True)
    embed.add_field(name="🏢 Server", value=f"**{ctx.guild.name}**", inline=True)
    embed.add_field(name="👑 Added By", value=f"{ctx.author.mention}", inline=True)
    
    # Notify the new admin
    try:
        admin_notification = discord.Embed(
            title="🎉 You're Now a Server Admin!",
            description=f"You have been granted **server admin access** by {ctx.author.mention} in **{ctx.guild.name}**",
            color=0x57F287
        )
        admin_notification.add_field(
            name="🔧 Admin Commands",
            value="You can now use server admin commands:\n• `!addchannel #channel`\n• `!listchannels`",
            inline=False
        )
        await user.send(embed=admin_notification)
    except:
        pass
    
    await ctx.send(embed=embed)

@bot.command()
@is_server_admin()
async def addchannel(ctx, channel: discord.TextChannel = None):
    """Add channel to allowed list (server admins only)"""
    if not channel:
        # Try to get channel from mention
        if ctx.message.channel_mentions:
            channel = ctx.message.channel_mentions[0]
        else:
            await ctx.send("❌ Please mention a channel! Example: `!addchannel #general`")
            return
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO allowed_channels (channel_id, guild_id, added_by) VALUES (?, ?, ?)', 
              (channel.id, ctx.guild.id, ctx.author.id))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(
        title="✅ Channel Added!",
        description=f"**{channel.mention} is now an allowed channel!**",
        color=0x57F287
    )
    
    embed.add_field(name="📢 Channel", value=f"{channel.mention}\n(ID: `{channel.id}`)", inline=True)
    embed.add_field(name="🏢 Server", value=f"**{ctx.guild.name}**", inline=True)
    embed.add_field(name="👤 Added By", value=f"{ctx.author.mention}", inline=True)
    
    await ctx.send(embed=embed)

@bot.command()
@is_server_admin()
async def listchannels(ctx):
    """List all allowed channels in this server"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT channel_id FROM allowed_channels WHERE guild_id = ?', (ctx.guild.id,))
    channels = c.fetchall()
    conn.close()
    
    if not channels:
        embed = discord.Embed(
            title="📋 Allowed Channels",
            description="❌ No channels configured for this server!\nUse `!addchannel #channel` to add one.",
            color=0xED4245
        )
    else:
        channel_list = []
        for channel_row in channels:
            channel_id = channel_row[0]
            channel = ctx.guild.get_channel(channel_id)
            if channel:
                channel_list.append(f"• {channel.mention} (ID: `{channel.id}`)")
            else:
                channel_list.append(f"• Unknown Channel (ID: `{channel_id}`)")
        
        embed = discord.Embed(
            title="📋 Allowed Channels",
            description="**Channels where bot commands can be used:**\n\n" + "\n".join(channel_list),
            color=0x3498DB
        )
    
    embed.set_footer(text=f"Server: {ctx.guild.name}")
    await ctx.send(embed=embed)

# ============================
# OTHER ADMIN COMMANDS
# ============================

@bot.command()
@is_global_admin()
async def setprice(ctx, service_name: str, price: int):
    """Set price for a service"""
    valid_services = ['mobile', 'aadhaar', 'email', 'telegram', 'vehicle', 'fam']
    
    if service_name not in valid_services:
        await ctx.send(f"❌ Invalid service name! Valid services: {', '.join(valid_services)}")
        return
    
    if price < 1:
        await ctx.send("❌ Price must be at least 1 credit!")
        return
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE service_prices SET price = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP WHERE service_name = ?', 
              (price, ctx.author.id, service_name))
    conn.commit()
    
    # Update SERVICE_PRICES dict
    SERVICE_PRICES[service_name] = price
    
    conn.close()
    
    service_display = {
        'mobile': 'Mobile Number Search',
        'aadhaar': 'Aadhaar Card Search',
        'email': 'Email Address Search',
        'telegram': 'Telegram ID Search',
        'vehicle': 'Vehicle Number Search',
        'fam': 'FamPay UPI Search'
    }.get(service_name, service_name.title())
    
    embed = discord.Embed(
        title="✅ Service Price Updated!",
        description=f"**{service_display} price has been updated!**",
        color=0x57F287
    )
    
    embed.add_field(name="💰 **New Price**", value=f"**{price} credit{'s' if price > 1 else ''}**", inline=True)
    embed.add_field(name="🔧 **Service**", value=f"**{service_display}**", inline=True)
    embed.add_field(name="👤 **Updated By**", value=f"{ctx.author.mention}", inline=True)
    
    await ctx.send(embed=embed)

@bot.command()
@is_global_admin()
async def setvctime(ctx, minutes: int):
    """Set global voice chat time per credit"""
    if minutes < 1 or minutes > 60:
        await ctx.send("❌ Minutes must be between 1 and 60!")
        return
    
    # Update global setting
    set_voice_time_settings(0, minutes, ctx.author.id)
    
    embed = discord.Embed(
        title="✅ Global Voice Chat Time Updated!",
        description=f"**Global voice chat time per credit has been set to {minutes} minutes!**",
        color=0x57F287,
        timestamp=datetime.now(timezone.utc)
    )
    
    embed.add_field(name="⏰ **New Setting**", value=f"**{minutes} minutes** = 1 credit", inline=True)
    embed.add_field(name="👤 **Updated By**", value=f"{ctx.author.mention}", inline=True)
    embed.add_field(name="📊 **Affects**", value="All servers (unless overridden by server-specific setting)", inline=False)
    
    await ctx.send(embed=embed)

@bot.command()
@is_global_admin()
async def setvc(ctx, server_id: int, minutes: int):
    """Set voice chat time for specific server"""
    if minutes < 1 or minutes > 60:
        await ctx.send("❌ Minutes must be between 1 and 60!")
        return
    
    # Check if server exists
    server = bot.get_guild(server_id)
    if not server:
        await ctx.send("❌ Server not found!")
        return
    
    # Update server setting
    set_voice_time_settings(server_id, minutes, ctx.author.id)
    
    embed = discord.Embed(
        title="✅ Server Voice Chat Time Updated!",
        description=f"**Voice chat time per credit has been set to {minutes} minutes for {server.name}!**",
        color=0x57F287,
        timestamp=datetime.now(timezone.utc)
    )
    
    embed.add_field(name="⏰ **New Setting**", value=f"**{minutes} minutes** = 1 credit", inline=True)
    embed.add_field(name="🏢 **Server**", value=f"**{server.name}**\n(ID: `{server.id}`)", inline=True)
    embed.add_field(name="👤 **Updated By**", value=f"{ctx.author.mention}", inline=True)
    embed.add_field(name="ℹ️ **Note**", value="This setting overrides the global setting for this server only", inline=False)
    
    await ctx.send(embed=embed)

@bot.command()
@is_global_admin()
async def servers(ctx):
    """List all servers the bot is in"""
    embed = discord.Embed(
        title="🏢 Bot Servers List",
        description=f"**Total Servers:** {len(bot.guilds)}",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc)
    )
    
    if not bot.guilds:
        embed.add_field(
            name="No Servers",
            value="The bot is not in any servers yet.",
            inline=False
        )
    else:
        # Sort servers by member count
        sorted_guilds = sorted(bot.guilds, key=lambda g: g.member_count, reverse=True)
        
        server_list = ""
        for i, guild in enumerate(sorted_guilds[:25], 1):
            owner_name = guild.owner.name if guild.owner else "Unknown"
            has_admin = guild.me.guild_permissions.administrator
            admin_status = "✅" if has_admin else "❌"
            
            server_list += f"{i}. **{guild.name}** {admin_status}\n   👑 {owner_name} | 👥 {guild.member_count} | 🆔 `{guild.id}`\n"
        
        embed.add_field(
            name=f"📋 Servers ({len(sorted_guilds)})",
            value=server_list,
            inline=False
        )
    
    embed.set_footer(text=f"Requested by {ctx.author.name} • {get_indian_time()}")
    await ctx.send(embed=embed)

# ============================
# ERROR HANDLING
# ============================

@bot.event
async def on_command_error(ctx, error):
    """Global error handler"""
    if isinstance(error, commands.CommandNotFound):
        return
    
    if isinstance(error, commands.CheckFailure):
        return
    
    # Log the error
    print(f"Command error: {error}")
    
    # Send error message
    embed = discord.Embed(
        title="❌ Command Error",
        description="An error occurred while processing the command.",
        color=0xED4245
    )
    
    # Add more specific error information
    if hasattr(error, 'original'):
        error_msg = str(error.original)[:200]
        embed.add_field(name="Error Details", value=f"```{error_msg}```", inline=False)
    else:
        embed.add_field(name="Error Details", value=f"```{str(error)[:200]}```", inline=False)
    
    embed.add_field(
        name="🔄 Solution",
        value="• Check your command syntax\n• Make sure you have required permissions\n• Try again in a few moments",
        inline=False
    )
    
    await ctx.send(embed=embed, delete_after=30)

async def send_setup_success(message, guild, channel):
    """Send setup success message"""
    success_embed = discord.Embed(
        title="✅ SETUP COMPLETE! 🎉",
        description=f"**KornFinder Bot has been successfully set up in {guild.name}!**",
        color=0x57F287,
        timestamp=datetime.now(timezone.utc)
    )
    
    success_embed.add_field(
        name="📢 **Setup Successful!**",
        value=f"**Channel:** #{channel.name}\n**Server:** {guild.name}\n**Status:** ✅ **ACTIVE 24/7**",
        inline=False
    )
    
    success_embed.add_field(
        name="⚡ **AUTO-DETECTION FEATURE**",
        value="**Just type and search!** No commands needed!\nExamples:\n• `9876543210` - Mobile number\n• `example@gmail.com` - Email\n• `GJ01AB1234` - Vehicle\n• `username@fam` - FamPay",
        inline=False
    )
    
    success_embed.add_field(
        name="🔧 **Manual Commands**",
        value=(
            "`!num 9876543210` - Mobile number\n"
            "`!card 123456789012` - Aadhaar card\n"
            "`!email example@domain.com` - Email\n"
            "`!tg username` - Telegram ID\n"
            "`!vehicle GJ01AB1234` - Vehicle\n"
            "`!fam username@fam` - FamPay UPI"
        ),
        inline=False
    )
    
    success_embed.add_field(
        name="🎧 **Voice Chat Rewards**",
        value=(
            "**Earn credits by staying in voice chat:**\n"
            "• **10 minutes** = 1 credit 💎\n"
            "• **20 minutes** = 2 credits + level up ⭐\n"
            "• **Stay active** = Unlimited credits! 🔥"
        ),
        inline=False
    )
    
    success_embed.add_field(
        name="📞 **Support & Links**",
        value=f"**Developer:** {DEVELOPER_INFO['developer']}\n**Discord Server:** [Join Here]({DEVELOPER_INFO['discord']}) 👥\n**Telegram:** [Contact]({DEVELOPER_INFO['telegram']}) 📲\n**API Provider:** {DEVELOPER_INFO['phenion']} 🔗",
        inline=False
    )
    
    success_embed.set_footer(text="Enjoy using KornFinder Bot! 🚀")
    
    await message.channel.send(embed=success_embed)
    
    # Also send message to the setup channel
    try:
        channel_embed = discord.Embed(
            title="🤖 KORNFINDER BOT - READY TO USE! 🎉",
            description=(
                "**This channel has been set up for KornFinder Bot!**\n\n"
                "**⚡ AUTO-DETECTION ACTIVE:** Just type any value to search!\n"
                "• Mobile numbers, emails, vehicle numbers, etc.\n\n"
                "Use `!info` to see all available commands."
            ),
            color=0x57F287
        )
        channel_embed.set_footer(text="Setup completed successfully! ✅")
        await channel.send(embed=channel_embed)
    except:
        pass
    
    print(f"✅ Setup completed for server: {guild.name} (Channel: #{channel.name})")

# ============================
# RUN THE BOT
# ============================

if __name__ == "__main__":
    print("=" * 70)
    print("🚀 STARTING KORNFINDER PREMIUM BOT v6.0")
    print("=" * 70)
    print(f"💎 Admin ID: {YOUR_DISCORD_ID}")
    print(f"📢 Default Channel: {DEFAULT_CHANNEL_ID}")
    print(f"🔗 Bot Invite Link: {BOT_INVITE_LINK}")
    print(f"🔗 Discord Server: {DEVELOPER_INFO['discord']}")
    print(f"📱 Telegram: {DEVELOPER_INFO['telegram']}")
    print(f"👤 Developer: {DEVELOPER_INFO['developer']}")
    print(f"🔗 API Provider: {DEVELOPER_INFO['phenion']}")
    print("💰 Credit System: Voice Chat Only")
    print("🎯 10 minutes = 1 credit, 20 minutes = 2 credits + level up")
    print("📱 Services: Number, Card, Email, Telegram, Vehicle, FamPay")
    print("🔧 API Features: 3x Retry, Error Handling, Auto-Refund")
    print("⚡ AUTO-DETECTION: Just type any value to search!")
    print("📲 Telegram Search: Improved error handling")
    print("🚀 Railway.com Compatible: YES")
    print("=" * 60)
    print("✅ Bot is ready to launch!")
    
    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        print("❌ Invalid bot token! Check your DISCORD_BOT_TOKEN environment variable.")
    except Exception as e:
        print(f"❌ Bot error: {e}")
