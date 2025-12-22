import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import pytz
import json
import os
import logging
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True
bot = commands.Bot(command_prefix='!', intents=intents)
TIMEZONE = pytz.timezone('America/New_York')
user_cooldowns = defaultdict(lambda: defaultdict(float))
pending_selections = defaultdict(dict)
HUNT_SCHEDULE = [{'day': 4, 'hour': 21, 'minute': 0, 'label': 'Friday 21:00'}, {'day': 5, 'hour': 21, 'minute': 0, 'label': 'Saturday 21:00'}, {'day': 6, 'hour': 21, 'minute': 0, 'label': 'Sunday 21:00'}]
PRIORITY_ROLES = ['Frontrunner', 'Envoy', 'Strategist', 'GM', 'Quartermaster', 'Administrator', 'Vice Master']
ROLE_ICONS = {'tank': '🛡️', 'support': '💚', 'dps': '⚔️'}

class HuntManager:
    def __init__(self):
        self.weekly_hunts, self.locks = {}, set()
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
        now, cutoff = datetime.now(TIMEZONE), datetime.now(TIMEZONE) - timedelta(days=2)
        for channel_id, weekly in list(self.weekly_hunts.items()):
            weekly['hunts'] = [h for h in weekly['hunts'] if datetime.fromisoformat(h['hunt_time']) > cutoff]
            if not weekly['hunts']:
                del self.weekly_hunts[channel_id]
        self.save_data()
    def create_weekly_hunts(self, channel_id):
        hunt_id, now, hunts = str(channel_id), datetime.now(TIMEZONE), []
        self.weekly_hunts.pop(hunt_id, None)
        for schedule in HUNT_SCHEDULE:
            days_ahead = schedule['day'] - now.weekday()
            if days_ahead < 0:
                days_ahead += 7
            elif days_ahead == 0:
                target_time = now.replace(hour=schedule['hour'], minute=schedule['minute'], second=0, microsecond=0)
                if now >= target_time:
                    days_ahead = 7
            hunt_time = (now + timedelta(days=days_ahead)).replace(hour=schedule['hour'], minute=schedule['minute'], second=0, microsecond=0)
            hunts.append({'hunt_time': hunt_time.isoformat(), 'label': schedule['label'], 'signed_up': [], 'tentative': [], 'parties': {str(i): {'tank': None, 'support': None, 'dps': []} for i in range(1, 9)}, 'message_id': None, 'reminder_sent_at': None})
        self.weekly_hunts[hunt_id] = {'channel_id': channel_id, 'hunts': hunts, 'created_at': now.isoformat(), 'last_reset': now.isoformat()}
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
    def join_party_with_role(self, channel_id, hunt_index, party_number, role, user_id, username, user_roles):
        lock_key = f"{channel_id}_{hunt_index}_{user_id}_{party_number}"
        if lock_key in self.locks:
            return False, "Please wait a moment before making changes."
        self.locks.add(lock_key)
        try:
            weekly = self.get_weekly_hunts(channel_id)
            if not weekly or hunt_index >= len(weekly['hunts']):
                return False, "Invalid hunt selection."
            hunt, party = weekly['hunts'][hunt_index], weekly['hunts'][hunt_index]['parties'].get(str(party_number))
            if not party:
                return False, "Invalid party number."
            if party_number in [1, 2] and not any(r in PRIORITY_ROLES for r in user_roles):
                return False, f"Party {party_number} is reserved for leadership roles only."
            is_in_party = (party['tank'] and party['tank']['id'] == user_id) or (party['support'] and party['support']['id'] == user_id) or any(d['id'] == user_id for d in party['dps'])
            if is_in_party:
                return False, f"You're already in Party {party_number}!"
            user_data = {'id': user_id, 'name': username, 'role': role}
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
            if not any(u['id'] == user_id for u in hunt['signed_up']):
                hunt['signed_up'].append(user_data)
            self.save_data()
            return True, f"Joined Party {party_number} as {role}! You can join another party as a filler if needed."
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
            self.remove_user_from_hunt(weekly['hunts'][hunt_index], user_id)
            self.save_data()
            return True, "Removed from hunt!"
        finally:
            self.locks.discard(lock_key)

hunt_manager = HuntManager()

def create_hunt_embed(hunt, hunt_index):
    now, hunt_time = datetime.now(TIMEZONE), datetime.fromisoformat(hunt['hunt_time'])
    if hunt_time.tzinfo is None:
        hunt_time = TIMEZONE.localize(hunt_time)
    time_diff, is_locked = hunt_time - now, datetime.now(TIMEZONE) >= hunt_time
    if time_diff.total_seconds() > 0:
        days, hours, minutes = time_diff.days, time_diff.seconds // 3600, (time_diff.seconds % 3600) // 60
        time_str = f"⏰ {days}d {hours}h" if days > 0 else (f"⏰ {hours}h {minutes}m" if hours > 0 else f"⏰ {minutes}m")
    else:
        time_str = "✅ Completed"
    lock_indicator = "🔒 **Hunt Locked — In Progress**\n\n" if is_locked else ""
    description = f"{lock_indicator}**{hunt['label']}**\n📅 <t:{int(hunt_time.timestamp())}:F>\n{time_str} | Signed up: **{len(hunt['signed_up'])}**\n\n*Parties 1 & 2 reserved for leadership 👑*\n**Step 1:** React with party number (1️⃣-8️⃣)\n**Step 2:** Then select role (🛡️ Tank | 💚 Support | ⚔️ DPS)\n**Or:** ❓ for Tentative\n\n"
    for party_num in range(1, 9):
        party = hunt['parties'][str(party_num)]
        party_count = (1 if party['tank'] else 0) + (1 if party['support'] else 0) + len(party['dps'])
        indicator = "✅" if party_count == 5 else ("⚠️" if party_count > 0 else "❌")
        header = f"{indicator} **Party {party_num}**" + (" 👑" if party_num in [1, 2] else "") + f" ({party_count}/5): "
        tank, support = f"**{party['tank']['name']}**" if party['tank'] else "*Empty*", f"**{party['support']['name']}**" if party['support'] else "*Empty*"
        dps_names = ', '.join([f"**{d['name']}**" for d in party['dps']]) if party['dps'] else "*Empty*"
        description += f"{header}🛡️ {tank} | 💚 {support} | ⚔️ {dps_names}\n"
    description += "\n"
    if hunt['tentative']:
        description += f"**❓ Tentative:** {', '.join([u['name'] for u in hunt['tentative']])}\n"
    embed = discord.Embed(title=f"🏹 Guild Hunt - {hunt['label']} 🏹", description=description, color=discord.Color.green() if not is_locked else discord.Color.dark_grey())
    embed.set_footer(text="First select party, then select role | You can join multiple parties!" if not is_locked else "Hunt has started - signups closed")
    return embed

class HuntLeaveView(discord.ui.View):
    def __init__(self, hunt_index):
        super().__init__(timeout=None)
        self.hunt_index = hunt_index
        btn = discord.ui.Button(label="Leave Hunt", style=discord.ButtonStyle.secondary, emoji="❌", custom_id=f"hunt_{hunt_index}_leave")
        btn.callback = self.leave_callback
        self.add_item(btn)
    async def leave_callback(self, interaction: discord.Interaction):
        channel_id, _, _ = hunt_manager.get_hunt_by_message(interaction.message.id)
        if not channel_id:
            await interaction.response.send_message("Error: Hunt not found.", ephemeral=True)
            return
        success, message = hunt_manager.remove_user(int(channel_id), self.hunt_index, interaction.user.id)
        await interaction.response.send_message(message, ephemeral=True)
        if success:
            await update_hunt_message(int(channel_id), self.hunt_index)

class BugReportModal(discord.ui.Modal, title="Report a Bug"):
    bug_title = discord.ui.TextInput(label="Bug Title", placeholder="Brief summary of the issue", max_length=100, required=True)
    bug_description = discord.ui.TextInput(label="Description", placeholder="Describe what happened, what you expected, and steps to reproduce", style=discord.TextStyle.paragraph, max_length=1000, required=True)
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        embed = discord.Embed(title=f"🐛 Bug Report: {self.bug_title.value}", description=self.bug_description.value, color=discord.Color.red(), timestamp=datetime.now(TIMEZONE))
        embed.add_field(name="Reported By", value=f"{interaction.user.mention} ({interaction.user.name})", inline=True)
        embed.add_field(name="User ID", value=str(interaction.user.id), inline=True)
        embed.add_field(name="Channel", value=f"#{interaction.channel.name}" if interaction.channel else "DM", inline=True)
        embed.add_field(name="Server", value=interaction.guild.name if interaction.guild else "N/A", inline=True)
        embed.set_footer(text=f"Report ID: {interaction.id}")
        dev_role = discord.utils.get(interaction.guild.roles, name="Bot Developer")
        if not dev_role:
            await interaction.followup.send("⚠️ Error: Bot Developer role not found. Please contact an administrator.", ephemeral=True)
            return
        developers = [m for m in interaction.guild.members if dev_role in m.roles]
        if not developers:
            await interaction.followup.send("⚠️ No Bot Developers found to send report to. Please contact an administrator.", ephemeral=True)
            return
        sent_count, failed_count = 0, 0
        for dev in developers:
            try:
                await dev.send(embed=embed)
                sent_count += 1
            except:
                failed_count += 1
        response_msg = f"✅ Bug report submitted successfully!\n\nYour report has been sent to {sent_count} Bot Developer(s)."
        if failed_count > 0:
            response_msg += f"\n⚠️ Could not reach {failed_count} developer(s) (DMs may be closed)."
        response_msg += f"\n\n**Report ID:** `{interaction.id}`\nDevelopers will review your report soon."
        await interaction.followup.send(response_msg, ephemeral=True)

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
        await message.edit(embed=create_hunt_embed(hunt, hunt_index), view=HuntLeaveView(hunt_index))
    except:
        pass

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
    now = datetime.now(TIMEZONE).timestamp()
    user_key = f"{payload.user_id}_{hunt_index}"
    if now - user_cooldowns[payload.user_id][user_key] < 3.0:
        return
    user_cooldowns[payload.user_id][user_key] = now
    hunt_time = datetime.fromisoformat(hunt['hunt_time'])
    if hunt_time.tzinfo is None:
        hunt_time = TIMEZONE.localize(hunt_time)
    if datetime.now(TIMEZONE) >= hunt_time:
        return
    emoji_str = str(payload.emoji)
    guild, member = bot.get_guild(payload.guild_id), None
    if guild:
        member = guild.get_member(payload.user_id)
    if not member:
        return
    if emoji_str == '❓':
        success, message = hunt_manager.signup_tentative(int(channel_id), hunt_index, payload.user_id, member.display_name)
        if success:
            await update_hunt_message(int(channel_id), hunt_index)
            pending_selections.pop(f"{channel_id}_{hunt_index}_{payload.user_id}", None)
        try:
            await member.send(f"**Hunt Signup:** {message}")
        except:
            pass
        return
    party_emojis = {'1️⃣': 1, '2️⃣': 2, '3️⃣': 3, '4️⃣': 4, '5️⃣': 5, '6️⃣': 6, '7️⃣': 7, '8️⃣': 8}
    if emoji_str in party_emojis:
        party_num = party_emojis[emoji_str]
        pending_selections[f"{channel_id}_{hunt_index}_{payload.user_id}"] = party_num
        try:
            await member.send(f"**Hunt Signup:** You selected Party {party_num}. Now react with your role: 🛡️ (Tank), 💚 (Support), or ⚔️ (DPS)")
        except:
            pass
        return
    role_emojis = {'🛡️': 'tank', '💚': 'support', '⚔️': 'dps'}
    if emoji_str in role_emojis:
        role = role_emojis[emoji_str]
        selection_key = f"{channel_id}_{hunt_index}_{payload.user_id}"
        if selection_key not in pending_selections:
            try:
                await member.send("**Hunt Signup:** Please select a party first (1️⃣-8️⃣), then select your role!")
            except:
                pass
            return
        party_num = pending_selections[selection_key]
        user_roles = [r.name for r in member.roles]
        success, message = hunt_manager.join_party_with_role(int(channel_id), hunt_index, party_num, role, payload.user_id, member.display_name, user_roles)
        if success:
            await update_hunt_message(int(channel_id), hunt_index)
            pending_selections.pop(selection_key, None)
        try:
            await member.send(f"**Hunt Signup:** {message}")
        except:
            pass

@bot.event
async def on_raw_reaction_remove(payload):
    if payload.user_id == bot.user.id:
        return
    channel_id, hunt_index, hunt = hunt_manager.get_hunt_by_message(payload.message_id)
    if not channel_id or not hunt or str(payload.emoji) != '❓':
        return
    success, _ = hunt_manager.remove_user(int(channel_id), hunt_index, payload.user_id)
    if success:
        await update_hunt_message(int(channel_id), hunt_index)

@tasks.loop(minutes=1)
async def check_weekly_reset():
    now = datetime.now(TIMEZONE)
    if now.weekday() == 0 and now.hour == 0 and now.minute < 5:
        for guild in bot.guilds:
            for channel in guild.text_channels:
                if channel.name == 'guild-hunt-organization':
                    weekly_data = hunt_manager.get_weekly_hunts(channel.id)
                    if not weekly_data or (datetime.now(TIMEZONE) - datetime.fromisoformat(weekly_data.get('last_reset', weekly_data['created_at']))).days >= 6:
                        await post_new_hunts_to_channel(channel)

@tasks.loop(minutes=1)
async def check_hunt_reminders():
    now = datetime.now(TIMEZONE)
    for channel_id, weekly in hunt_manager.weekly_hunts.items():
        channel = bot.get_channel(int(channel_id))
        if not channel:
            continue
        for idx, hunt in enumerate(weekly['hunts']):
            if hunt.get('reminder_sent_at'):
                continue
            hunt_time = datetime.fromisoformat(hunt['hunt_time'])
            if hunt_time.tzinfo is None:
                hunt_time = TIMEZONE.localize(hunt_time)
            minutes_until = (hunt_time - now).total_seconds() / 60
            if 29 <= minutes_until <= 31:
                try:
                    member_role = discord.utils.get(channel.guild.roles, name="Member")
                    embed = discord.Embed(title="⚠️ Guild Hunt Starting Soon! ⚠️", description=f"**{hunt['label']}** starts in **30 minutes**!\n\nMake sure you're in your party and ready to go!", color=discord.Color.orange())
                    party_info = ""
                    for party_num in range(1, 9):
                        party = hunt['parties'][str(party_num)]
                        if (1 if party['tank'] else 0) + (1 if party['support'] else 0) + len(party['dps']) > 0:
                            members = ([f"{party['tank']['name']} (Tank)"] if party['tank'] else []) + ([f"{party['support']['name']} (Support)"] if party['support'] else []) + [f"{dps['name']} (DPS)" for dps in party['dps']]
                            party_info += f"**Party {party_num}**: {', '.join(members)}\n"
                    if party_info:
                        embed.add_field(name="Active Parties", value=party_info, inline=False)
                    embed.set_footer(text=f"Total signed up: {len(hunt['signed_up'])}")
                    await channel.send(f"{member_role.mention}" if member_role else "@everyone", embed=embed)
                    hunt['reminder_sent_at'] = now.isoformat()
                    hunt_manager.save_data()
                except:
                    pass

@tasks.loop(hours=6)
async def cleanup_old_hunts_task():
    hunt_manager.cleanup_old_hunts()

@tasks.loop(minutes=5)
async def update_hunt_timers():
    for channel_id, weekly in hunt_manager.weekly_hunts.items():
        for idx in range(len(weekly['hunts'])):
            await update_hunt_message(int(channel_id), idx)

async def post_new_hunts_to_channel(channel):
    try:
        weekly_hunts_data = hunt_manager.get_weekly_hunts(channel.id)
        if weekly_hunts_data:
            for hunt in weekly_hunts_data.get('hunts', []):
                if hunt.get('message_id'):
                    try:
                        await (await channel.fetch_message(hunt['message_id'])).delete()
                    except:
                        pass
        hunt_manager.create_weekly_hunts(channel.id)
        weekly = hunt_manager.get_weekly_hunts(channel.id)
        weekly['last_reset'] = datetime.now(TIMEZONE).isoformat()
        hunt_manager.save_data()
        member_role = discord.utils.get(channel.guild.roles, name="Member")
        await channel.send(f"{member_role.mention} Guild Hunts for this week!" if member_role else "Guild Hunts for this week!")
        for idx, hunt in enumerate(weekly['hunts']):
            message = await channel.send(embed=create_hunt_embed(hunt, idx), view=HuntLeaveView(idx))
            for emoji in ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '🛡️', '💚', '⚔️', '❓']:
                await message.add_reaction(emoji)
            hunt['message_id'] = message.id
            hunt_manager.save_data()
    except Exception as e:
        logging.error(f"Error posting new hunts: {e}")

@bot.command()
@commands.has_permissions(administrator=True)
async def createweeklyhunts(ctx):
    await ctx.message.delete()
    await post_new_hunts_to_channel(ctx.channel)

@bot.command()
async def bugreport(ctx):
    member_role = discord.utils.get(ctx.guild.roles, name="Member")
    higher_roles = ['Frontrunner', 'Envoy', 'Strategist', 'GM', 'Quartermaster', 'Administrator', 'Vice Master', 'Bot Developer']
    if not member_role or (member_role not in ctx.author.roles and not any(discord.utils.get(ctx.guild.roles, name=r) in ctx.author.roles for r in higher_roles)):
        await ctx.send("❌ You need the Member role or above to report bugs.", delete_after=10)
        await ctx.message.delete(delay=10)
        return
    await ctx.message.delete()
    view = discord.ui.View(timeout=60)
    button = discord.ui.Button(label="Open Bug Report Form", style=discord.ButtonStyle.primary, emoji="🐛")
    async def button_callback(interaction: discord.Interaction):
        if interaction.user.id != ctx.author.id:
            await interaction.response.send_message("This button is not for you!", ephemeral=True)
            return
        await interaction.response.send_modal(BugReportModal())
    button.callback = button_callback
    view.add_item(button)
    await ctx.send(f"{ctx.author.mention} Click the button below to submit a bug report:", view=view, delete_after=60)

bot.run(os.getenv("BOT_TOKEN"))
