import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import pytz
import json
import os
import logging
from collections import defaultdict

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Timezone setup
TIMEZONE = pytz.timezone('America/New_York')

# Rate limiting tracker
user_cooldowns = defaultdict(lambda: defaultdict(float))

# Weekly guild hunt schedule
HUNT_SCHEDULE = [
    {'day': 4, 'hour': 21, 'minute': 0, 'label': 'Friday 21:00'},
    {'day': 5, 'hour': 21, 'minute': 0, 'label': 'Saturday 21:00'},
    {'day': 6, 'hour': 21, 'minute': 0, 'label': 'Sunday 21:00'}
]

# Priority roles that can access Party 1 and 2
PRIORITY_ROLES = ['Frontrunner', 'Envoy', 'Strategist', 'GM', 'Quartermaster', 'Administrator', 'Vice Master']

# Role icons for better UX
ROLE_ICONS = {
    'tank': '🛡️',
    'support': '💚',
    'dps': '⚔️'
}

# Guild Hunt data structure
class HuntManager:
    def __init__(self):
        self.weekly_hunts = {}
        self.locks = set()
        self.ensure_data_directory()
        self.load_data()
    
    def ensure_data_directory(self):
        os.makedirs('/app/data', exist_ok=True)
        if not os.path.exists('/app/data/hunts.json'):
            with open('/app/data/hunts.json', 'w') as f:
                json.dump({}, f)
    
    def load_data(self):
        try:
            with open('/app/data/hunts.json', 'r') as f:
                content = f.read().strip()
                self.weekly_hunts = json.loads(content) if content else {}
        except (json.JSONDecodeError, FileNotFoundError):
            logging.warning("hunts.json is corrupted or missing — resetting data")
            self.weekly_hunts = {}
        except Exception as e:
            logging.error(f"Error loading hunts.json: {e} — resetting data")
            self.weekly_hunts = {}
    
    def save_data(self):
        try:
            with open('/app/data/hunts.json', 'w') as f:
                json.dump(self.weekly_hunts, f, indent=4)
        except Exception as e:
            logging.error(f"Error saving hunts.json: {e}")
    
    def cleanup_old_hunts(self):
        now = datetime.now(TIMEZONE)
        cutoff = now - timedelta(days=2)
        
        for channel_id, weekly in list(self.weekly_hunts.items()):
            weekly['hunts'] = [
                h for h in weekly['hunts']
                if datetime.fromisoformat(h['hunt_time']) > cutoff
            ]
            if not weekly['hunts']:
                del self.weekly_hunts[channel_id]
        
        self.save_data()
    
    def create_weekly_hunts(self, channel_id):
        hunt_id = str(channel_id)
        self.weekly_hunts.pop(hunt_id, None)
        
        now = datetime.now(TIMEZONE)
        hunts = []
        
        for schedule in HUNT_SCHEDULE:
            days_ahead = schedule['day'] - now.weekday()
            if days_ahead < 0:
                days_ahead += 7
            elif days_ahead == 0:
                target_time = now.replace(hour=schedule['hour'], minute=schedule['minute'], second=0, microsecond=0)
                if now >= target_time:
                    days_ahead = 7
            
            hunt_time = now + timedelta(days=days_ahead)
            hunt_time = hunt_time.replace(hour=schedule['hour'], minute=schedule['minute'], second=0, microsecond=0)
            
            hunts.append({
                'hunt_time': hunt_time.isoformat(),
                'label': schedule['label'],
                'signed_up': [],
                'tentative': [],
                'parties': {
                    '1': {'tank': None, 'support': None, 'dps': []},
                    '2': {'tank': None, 'support': None, 'dps': []},
                    '3': {'tank': None, 'support': None, 'dps': []},
                    '4': {'tank': None, 'support': None, 'dps': []},
                    '5': {'tank': None, 'support': None, 'dps': []},
                    '6': {'tank': None, 'support': None, 'dps': []},
                    '7': {'tank': None, 'support': None, 'dps': []},
                    '8': {'tank': None, 'support': None, 'dps': []},
                },
                'message_id': None,
                'reminder_sent_at': None
            })
        
        self.weekly_hunts[hunt_id] = {
            'channel_id': channel_id,
            'hunts': hunts,
            'created_at': now.isoformat(),
            'last_reset': now.isoformat()
        }
        self.save_data()
        return hunt_id
    
    def get_weekly_hunts(self, channel_id):
        return self.weekly_hunts.get(str(channel_id))
    
    def get_hunt_by_message(self, message_id):
        for channel_id, weekly in self.weekly_hunts.items():
            for idx, hunt in enumerate(weekly['hunts']):
                if hunt.get('message_id') == message_id:
                    return channel_id, idx, hunt
        return None, None, None
    
    def get_user_party(self, channel_id, hunt_index, user_id):
        weekly = self.get_weekly_hunts(channel_id)
        if not weekly or hunt_index >= len(weekly['hunts']):
            return None, None
        
        hunt = weekly['hunts'][hunt_index]
        for party_num, party in hunt['parties'].items():
            if party['tank'] and party['tank']['id'] == user_id:
                return party_num, 'tank'
            if party['support'] and party['support']['id'] == user_id:
                return party_num, 'support'
            for dps in party['dps']:
                if dps['id'] == user_id:
                    return party_num, 'dps'
        return None, None
    
    def get_user_role(self, channel_id, hunt_index, user_id):
        """Get the role a user signed up as"""
        weekly = self.get_weekly_hunts(channel_id)
        if not weekly or hunt_index >= len(weekly['hunts']):
            return None
        
        hunt = weekly['hunts'][hunt_index]
        for user in hunt['signed_up']:
            if user['id'] == user_id:
                return user['role']
        return None
    
    def signup_role(self, channel_id, hunt_index, user_id, username, role):
        lock_key = f"{channel_id}_{hunt_index}_{user_id}"
        if lock_key in self.locks:
            return False, "Please wait a moment before making changes."
        
        self.locks.add(lock_key)
        try:
            weekly = self.get_weekly_hunts(channel_id)
            if not weekly or hunt_index >= len(weekly['hunts']):
                return False, "Invalid hunt selection."
            
            hunt = weekly['hunts'][hunt_index]
            
            # Remove user from all lists first
            self.remove_user_from_hunt(hunt, user_id)
            
            # Add to signed_up with role
            user_data = {'id': user_id, 'name': username, 'role': role}
            hunt['signed_up'].append(user_data)
            
            self.save_data()
            return True, f"Signed up as {role} for {hunt['label']}! Now react with a party number (1️⃣-8️⃣)."
        finally:
            self.locks.discard(lock_key)
    
    def join_party(self, channel_id, hunt_index, party_number, user_id, username, user_roles):
        lock_key = f"{channel_id}_{hunt_index}_{user_id}"
        if lock_key in self.locks:
            return False, "Please wait a moment before making changes."
        
        self.locks.add(lock_key)
        try:
            weekly = self.get_weekly_hunts(channel_id)
            if not weekly or hunt_index >= len(weekly['hunts']):
                return False, "Invalid hunt selection."
            
            hunt = weekly['hunts'][hunt_index]
            party = hunt['parties'].get(str(party_number))
            
            if not party:
                return False, "Invalid party number."
            
            # Check if user has signed up with a role
            user_signup = next((u for u in hunt['signed_up'] if u['id'] == user_id), None)
            if not user_signup:
                return False, "You must sign up with a role first (🛡️/💚/⚔️) before joining a party!"
            
            role = user_signup['role']
            
            # Check priority for parties 1 and 2
            if party_number in [1, 2]:
                has_priority = any(role_name in PRIORITY_ROLES for role_name in user_roles)
                if not has_priority:
                    return False, f"Party {party_number} is reserved for leadership roles only."
            
            # Remove user from all parties first
            for p in hunt['parties'].values():
                if p['tank'] and p['tank']['id'] == user_id:
                    p['tank'] = None
                if p['support'] and p['support']['id'] == user_id:
                    p['support'] = None
                p['dps'] = [d for d in p['dps'] if d['id'] != user_id]
            
            user_data = {'id': user_id, 'name': username}
            
            # Add to party based on role
            if role == 'tank':
                if party['tank']:
                    return False, f"Party {party_number} already has a tank."
                party['tank'] = user_data
            elif role == 'support':
                if party['support']:
                    return False, f"Party {party_number} already has a support."
                party['support'] = user_data
            elif role == 'dps':
                if len(party['dps']) >= 3:
                    return False, f"Party {party_number} already has 3 DPS."
                party['dps'].append(user_data)
            
            self.save_data()
            return True, f"Joined Party {party_number} as {role}!"
        finally:
            self.locks.discard(lock_key)
    
    def signup_tentative(self, channel_id, hunt_index, user_id, username):
        lock_key = f"{channel_id}_{hunt_index}_{user_id}"
        if lock_key in self.locks:
            return False, "Please wait a moment before making changes."
        
        self.locks.add(lock_key)
        try:
            weekly = self.get_weekly_hunts(channel_id)
            if not weekly or hunt_index >= len(weekly['hunts']):
                return False, "Invalid hunt selection."
            
            hunt = weekly['hunts'][hunt_index]
            self.remove_user_from_hunt(hunt, user_id)
            hunt['tentative'].append({'id': user_id, 'name': username})
            self.save_data()
            return True, f"Added to tentative for {hunt['label']}!"
        finally:
            self.locks.discard(lock_key)
    
    def remove_user_from_hunt(self, hunt, user_id):
        hunt['signed_up'] = [u for u in hunt['signed_up'] if u['id'] != user_id]
        hunt['tentative'] = [u for u in hunt['tentative'] if u['id'] != user_id]
        
        for p in hunt['parties'].values():
            if p['tank'] and p['tank']['id'] == user_id:
                p['tank'] = None
            if p['support'] and p['support']['id'] == user_id:
                p['support'] = None
            p['dps'] = [d for d in p['dps'] if d['id'] != user_id]
    
    def remove_user(self, channel_id, hunt_index, user_id):
        lock_key = f"{channel_id}_{hunt_index}_{user_id}"
        if lock_key in self.locks:
            return False, "Please wait a moment before making changes."
        
        self.locks.add(lock_key)
        try:
            weekly = self.get_weekly_hunts(channel_id)
            if not weekly or hunt_index >= len(weekly['hunts']):
                return False, "Invalid hunt selection."
            
            hunt = weekly['hunts'][hunt_index]
            self.remove_user_from_hunt(hunt, user_id)
            self.save_data()
            return True, "Removed from hunt!"
        finally:
            self.locks.discard(lock_key)

hunt_manager = HuntManager()

def create_hunt_embed(hunt, hunt_index):
    now = datetime.now(TIMEZONE)
    hunt_time = datetime.fromisoformat(hunt['hunt_time'])
    if hunt_time.tzinfo is None:
        hunt_time = TIMEZONE.localize(hunt_time)
    
    time_diff = hunt_time - now
    is_locked = datetime.now(TIMEZONE) >= hunt_time
    
    if time_diff.total_seconds() > 0:
        days = time_diff.days
        hours = time_diff.seconds // 3600
        minutes = (time_diff.seconds % 3600) // 60
        
        if days > 0:
            time_remaining = f"{days}d {hours}h"
        elif hours > 0:
            time_remaining = f"{hours}h {minutes}m"
        else:
            time_remaining = f"{minutes}m"
        time_str = f"⏰ {time_remaining}"
    else:
        time_str = "✅ Completed"
    
    total_signups = len(hunt['signed_up'])
    timestamp = int(hunt_time.timestamp())
    
    lock_indicator = "🔒 **Hunt Locked — In Progress**\n\n" if is_locked else ""
    
    description = lock_indicator
    description += f"**{hunt['label']}**\n"
    description += f"📅 <t:{timestamp}:F>\n"
    description += f"{time_str} | Signed up: **{total_signups}**\n\n"
    description += "*Parties 1 & 2 reserved for leadership 👑*\n"
    description += "**React:** 🛡️ Tank | 💚 Support | ⚔️ DPS | ❓ Tentative\n"
    description += "**Then:** 1️⃣-8️⃣ to pick party\n\n"
    
    # Display parties in compact single-line format
    for party_num in range(1, 9):
        party = hunt['parties'][str(party_num)]
        party_count = (1 if party['tank'] else 0) + (1 if party['support'] else 0) + len(party['dps'])
        
        if party_count == 5:
            indicator = "✅"
        elif party_count > 0:
            indicator = "⚠️"
        else:
            indicator = "❌"
        
        header = f"{indicator} **Party {party_num}**"
        if party_num in [1, 2]:
            header += " 👑"
        header += f" ({party_count}/5): "
        
        tank = f"**{party['tank']['name']}**" if party['tank'] else "*Empty*"
        support = f"**{party['support']['name']}**" if party['support'] else "*Empty*"
        
        if party['dps']:
            dps_names = ', '.join([f"**{d['name']}**" for d in party['dps']])
        else:
            dps_names = "*Empty*"
        
        description += f"{header}🛡️ {tank} | 💚 {support} | ⚔️ {dps_names}\n"
    
    description += "\n"
    
    # Add users not in party
    not_in_party = []
    for user in hunt['signed_up']:
        user_id = user['id']
        in_party = False
        for p in hunt['parties'].values():
            if (p['tank'] and p['tank']['id'] == user_id) or \
               (p['support'] and p['support']['id'] == user_id) or \
               any(d['id'] == user_id for d in p['dps']):
                in_party = True
                break
        if not in_party:
            icon = ROLE_ICONS.get(user['role'], '')
            not_in_party.append(f"{icon} {user['name']}")
    
    if not_in_party:
        description += f"**📝 Not in party:** {', '.join(not_in_party)}\n"
    
    if hunt['tentative']:
        tent_names = ', '.join([u['name'] for u in hunt['tentative']])
        description += f"**❓ Tentative:** {tent_names}\n"
    
    embed = discord.Embed(
        title=f"🏹 Guild Hunt - {hunt['label']} 🏹",
        description=description,
        color=discord.Color.green() if not is_locked else discord.Color.dark_grey()
    )
    
    footer_text = "React with role, then party number" if not is_locked else "Hunt has started - signups closed"
    embed.set_footer(text=footer_text)
    return embed

class HuntLeaveView(discord.ui.View):
    def __init__(self, hunt_index):
        super().__init__(timeout=None)
        self.hunt_index = hunt_index
        
        leave_button = discord.ui.Button(
            label="Leave Hunt",
            style=discord.ButtonStyle.secondary,
            emoji="❌",
            custom_id=f"hunt_{hunt_index}_leave"
        )
        leave_button.callback = self.leave_callback
        self.add_item(leave_button)
    
    async def leave_callback(self, interaction: discord.Interaction):
        channel_id, _, _ = hunt_manager.get_hunt_by_message(interaction.message.id)
        if not channel_id:
            await interaction.response.send_message("Error: Hunt not found.", ephemeral=True)
            return
        
        success, message = hunt_manager.remove_user(int(channel_id), self.hunt_index, interaction.user.id)
        await interaction.response.send_message(message, ephemeral=True)
        if success:
            await update_hunt_message(int(channel_id), self.hunt_index)

async def update_hunt_message(channel_id, hunt_index):
    weekly = hunt_manager.get_weekly_hunts(channel_id)
    if not weekly or hunt_index >= len(weekly['hunts']):
        return
    
    hunt = weekly['hunts'][hunt_index]
    if not hunt.get('message_id'):
        return
    
    channel = bot.get_channel(channel_id)
    if not channel:
        return
    
    try:
        message = await channel.fetch_message(hunt['message_id'])
        embed = create_hunt_embed(hunt, hunt_index)
        view = HuntLeaveView(hunt_index)
        await message.edit(embed=embed, view=view)
    except discord.NotFound:
        hunt['message_id'] = None
        hunt_manager.save_data()
        logging.warning(f"Message not found for hunt {hunt_index} in channel {channel_id}.")
    except discord.Forbidden:
        logging.error(f"Missing permissions to edit message in channel {channel_id}")
    except Exception as e:
        logging.error(f"Error updating message: {e}")

@bot.event
async def on_ready():
    logging.info(f'{bot.user} is now online!')
    
    for weekly in hunt_manager.weekly_hunts.values():
        for idx, hunt in enumerate(weekly['hunts']):
            if hunt.get('message_id'):
                bot.add_view(HuntLeaveView(idx), message_id=hunt['message_id'])
    
    check_weekly_reset.start()
    update_hunt_timers.start()
    check_hunt_reminders.start()
    cleanup_old_hunts_task.start()

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return
    
    channel_id, hunt_index, hunt = hunt_manager.get_hunt_by_message(payload.message_id)
    if not channel_id or not hunt:
        return
    
    # Rate limiting
    now = datetime.now(TIMEZONE).timestamp()
    user_key = f"{payload.user_id}_{hunt_index}"
    last_interaction = user_cooldowns[payload.user_id][user_key]
    
    if now - last_interaction < 3.0:
        return
    
    user_cooldowns[payload.user_id][user_key] = now
    
    # Check if hunt is locked
    hunt_time = datetime.fromisoformat(hunt['hunt_time'])
    if hunt_time.tzinfo is None:
        hunt_time = TIMEZONE.localize(hunt_time)
    if datetime.now(TIMEZONE) >= hunt_time:
        channel = bot.get_channel(payload.channel_id)
        if channel:
            try:
                message = await channel.fetch_message(payload.message_id)
                await message.remove_reaction(payload.emoji, payload.member)
            except:
                pass
        return
    
    emoji_str = str(payload.emoji)
    
    # Map emojis to roles
    role_emojis = {
        '🛡️': 'tank',
        '💚': 'support',
        '⚔️': 'dps',
        '❓': 'tentative'
    }
    
    # Map number emojis to party numbers
    party_emojis = {
        '1️⃣': 1, '2️⃣': 2, '3️⃣': 3, '4️⃣': 4,
        '5️⃣': 5, '6️⃣': 6, '7️⃣': 7, '8️⃣': 8
    }
    
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    
    member = guild.get_member(payload.user_id)
    if not member:
        return
    
    # Handle role signup
    if emoji_str in role_emojis:
        role = role_emojis[emoji_str]
        
        if role == 'tentative':
            success, message = hunt_manager.signup_tentative(
                int(channel_id), hunt_index, payload.user_id, member.display_name
            )
        else:
            success, message = hunt_manager.signup_role(
                int(channel_id), hunt_index, payload.user_id, member.display_name, role
            )
        
        if success:
            await update_hunt_message(int(channel_id), hunt_index)
        
        try:
            await member.send(f"**Hunt Signup:** {message}")
        except:
            pass
    
    # Handle party selection
    elif emoji_str in party_emojis:
        party_num = party_emojis[emoji_str]
        user_roles = [role.name for role in member.roles]
        
        success, message = hunt_manager.join_party(
            int(channel_id), hunt_index, party_num, payload.user_id, member.display_name, user_roles
        )
        
        if success:
            await update_hunt_message(int(channel_id), hunt_index)
        
        try:
            await member.send(f"**Hunt Signup:** {message}")
        except:
            pass

@bot.event
async def on_raw_reaction_remove(payload):
    if payload.user_id == bot.user.id:
        return
    
    channel_id, hunt_index, hunt = hunt_manager.get_hunt_by_message(payload.message_id)
    if not channel_id or not hunt:
        return
    
    emoji_str = str(payload.emoji)
    
    # If they remove role reaction, remove them from hunt
    role_emojis = {'🛡️': 'tank', '💚': 'support', '⚔️': 'dps', '❓': 'tentative'}
    
    if emoji_str in role_emojis:
        success, message = hunt_manager.remove_user(int(channel_id), hunt_index, payload.user_id)
        
        if success:
            await update_hunt_message(int(channel_id), hunt_index)

@tasks.loop(minutes=1)
async def check_weekly_reset():
    now = datetime.now(TIMEZONE)
    
    # Check if it's Monday between 00:00 and 00:05 (5 minute window)
    if now.weekday() == 0 and now.hour == 0 and now.minute < 5:
        # Check if we've already reset this week
        for guild in bot.guilds:
            for channel in guild.text_channels:
                if channel.name == 'guild-hunt-organization':
                    weekly_data = hunt_manager.get_weekly_hunts(channel.id)
                    
                    # Only reset if data is old or doesn't exist
                    if not weekly_data:
                        logging.info(f"Running weekly reset for {channel.name} - no data found")
                        await post_new_hunts_to_channel(channel)
                    else:
                        last_reset = datetime.fromisoformat(weekly_data.get('last_reset', weekly_data['created_at']))
                        if last_reset.tzinfo is None:
                            last_reset = TIMEZONE.localize(last_reset)
                        
                        # Reset if last reset was more than 6 days ago
                        days_since_reset = (now - last_reset).days
                        if days_since_reset >= 6:
                            logging.info(f"Running weekly reset for {channel.name} - {days_since_reset} days since last reset")
                            await post_new_hunts_to_channel(channel)

@tasks.loop(minutes=1)
async def check_hunt_reminders():
    now = datetime.now(TIMEZONE)
    
    for channel_id, weekly in hunt_manager.weekly_hunts.items():
        channel = bot.get_channel(int(channel_id))
        if not channel:
            continue
        
        for idx, hunt in enumerate(weekly['hunts']):
            if hunt.get('reminder_sent_at') is not None:
                continue
            
            hunt_time = datetime.fromisoformat(hunt['hunt_time'])
            if hunt_time.tzinfo is None:
                hunt_time = TIMEZONE.localize(hunt_time)
            
            time_diff = hunt_time - now
            minutes_until = time_diff.total_seconds() / 60
            
            if 29 <= minutes_until <= 31:
                try:
                    member_role = discord.utils.get(channel.guild.roles, name="Member")
                    ping_text = f"{member_role.mention}" if member_role else "@everyone"
                    
                    embed = discord.Embed(
                        title="⚠️ Guild Hunt Starting Soon! ⚠️",
                        description=f"**{hunt['label']}** starts in **30 minutes**!\n\nMake sure you're in your party and ready to go!",
                        color=discord.Color.orange()
                    )
                    
                    party_info = ""
                    for party_num in range(1, 9):
                        party = hunt['parties'][str(party_num)]
                        party_count = (1 if party['tank'] else 0) + (1 if party['support'] else 0) + len(party['dps'])
                        
                        if party_count > 0:
                            members = []
                            if party['tank']:
                                members.append(f"{party['tank']['name']} (Tank)")
                            if party['support']:
                                members.append(f"{party['support']['name']} (Support)")
                            for dps in party['dps']:
                                members.append(f"{dps['name']} (DPS)")
                            
                            party_info += f"**Party {party_num}** ({party_count}/5): {', '.join(members)}\n"
                    
                    if party_info:
                        embed.add_field(name="Active Parties", value=party_info, inline=False)
                    
                    total_signed = len(hunt['signed_up'])
                    embed.set_footer(text=f"Total signed up: {total_signed}")
                    
                    await channel.send(ping_text, embed=embed)
                    
                    hunt['reminder_sent_at'] = now.isoformat()
                    hunt_manager.save_data()
                except discord.Forbidden:
                    logging.error(f"Missing permissions to send reminder in channel {channel.name}")
                except Exception as e:
                    logging.error(f"Error sending hunt reminder: {e}")

@tasks.loop(hours=6)
async def cleanup_old_hunts_task():
    logging.info("Running cleanup of old hunts...")
    hunt_manager.cleanup_old_hunts()

@tasks.loop(minutes=5)
async def update_hunt_timers():
    for channel_id, weekly in hunt_manager.weekly_hunts.items():
        for idx in range(len(weekly['hunts'])):
            await update_hunt_message(int(channel_id), idx)

async def post_new_hunts_to_channel(channel):
    try:
        weekly_hunts_data = hunt_manager.get_weekly_hunts(channel.id)
        old_message_ids = []
        if weekly_hunts_data:
            for hunt in weekly_hunts_data.get('hunts', []):
                if hunt.get('message_id'):
                    old_message_ids.append(hunt['message_id'])
        
        hunt_manager.create_weekly_hunts(channel.id)
        weekly = hunt_manager.get_weekly_hunts(channel.id)
        
        # Update last_reset timestamp
        weekly['last_reset'] = datetime.now(TIMEZONE).isoformat()
        hunt_manager.save_data()
        
        for msg_id in old_message_ids:
            try:
                message = await channel.fetch_message(msg_id)
                await message.delete()
            except discord.NotFound:
                pass
            except discord.Forbidden:
                logging.error(f"Missing permissions to delete message in {channel.name}")
            except Exception as e:
                logging.error(f"Error deleting old message {msg_id}: {e}")
        
        member_role = discord.utils.get(channel.guild.roles, name="Member")
        ping_text = f"{member_role.mention} Guild Hunts for this week!" if member_role else "Guild Hunts for this week!"
        
        await channel.send(ping_text)
        
        for idx, hunt in enumerate(weekly['hunts']):
            embed = create_hunt_embed(hunt, idx)
            view = HuntLeaveView(idx)
            message = await channel.send(embed=embed, view=view)
            
            # Add reaction emojis
            await message.add_reaction('🛡️')  # Tank
            await message.add_reaction('💚')  # Support
            await message.add_reaction('⚔️')  # DPS
            await message.add_reaction('❓')  # Tentative
            await message.add_reaction('1️⃣')  # Party 1
            await message.add_reaction('2️⃣')  # Party 2
            await message.add_reaction('3️⃣')  # Party 3
            await message.add_reaction('4️⃣')  # Party 4
            await message.add_reaction('5️⃣')  # Party 5
            await message.add_reaction('6️⃣')  # Party 6
            await message.add_reaction('7️⃣')  # Party 7
            await message.add_reaction('8️⃣')  # Party 8
            
            hunt['message_id'] = message.id
            hunt_manager.save_data()
    except Exception as e:
        logging.error(f"Error posting new hunts to channel {channel.name}: {e}")

@bot.command()
@commands.has_permissions(administrator=True)
async def createweeklyhunts(ctx):
    """Create weekly hunt signups (Admin only)"""
    await ctx.message.delete()
    await post_new_hunts_to_channel(ctx.channel)

bot.run(os.getenv("BOT_TOKEN"))
