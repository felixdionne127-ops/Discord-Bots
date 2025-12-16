import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import pytz
import json
import os
from collections import defaultdict

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Timezone setup
TIMEZONE = pytz.timezone('America/New_York')

# Rate limiting tracker
user_cooldowns = defaultdict(lambda: defaultdict(float))

# Weekly raid schedule
RAID_SCHEDULE = [
    {'day': 2, 'hour': 21, 'minute': 0, 'label': 'Wednesday 21:00'},
    {'day': 4, 'hour': 19, 'minute': 30, 'label': 'Friday 19:30'},
    {'day': 6, 'hour': 22, 'minute': 0, 'label': 'Sunday 22:00 (Optional)'}
]

# Roundtable channel ID for audit logs
ROUNDTABLE_CHANNEL_ID = 1432166014979674123

class RaidManager:
    def __init__(self):
        self.weekly_raids = {}
        self.load_data()
    
    def load_data(self):
        if os.path.exists('raids.json'):
            try:
                with open('raids.json', 'r') as f:
                    self.weekly_raids = json.load(f)
            except json.JSONDecodeError:
                print("raids.json is corrupted or empty — resetting data")
                self.weekly_raids = {}
            except Exception as e:
                print(f"Error loading raids.json: {e} — resetting data")
                self.weekly_raids = {}
    
    def save_data(self):
        with open('raids.json', 'w') as f:
            json.dump(self.weekly_raids, f, indent=4)
    
    def create_weekly_raids(self, channel_id):
        raid_id = str(channel_id)
        self.weekly_raids.pop(raid_id, None)
        
        now = datetime.now(TIMEZONE)
        raids = []
        
        for schedule in RAID_SCHEDULE:
            days_ahead = schedule['day'] - now.weekday()
            if days_ahead < 0:
                days_ahead += 7
            elif days_ahead == 0:
                target_time = now.replace(hour=schedule['hour'], minute=schedule['minute'], second=0, microsecond=0)
                if now >= target_time:
                    days_ahead = 7
            
            raid_time = now + timedelta(days=days_ahead)
            raid_time = raid_time.replace(hour=schedule['hour'], minute=schedule['minute'], second=0, microsecond=0)
            
            raids.append({
                'raid_time': raid_time.isoformat(),
                'label': schedule['label'],
                'tanks': [],
                'supports': [],
                'dps': [],
                'bench': [],
                'tentative': [],
                'cleared': [],
                'message_id': None,
                'reminder_30min_sent': False,
                'reminder_start_sent': False,
                'blocked_users': [],
                'blocked': []
            })
        
        self.weekly_raids[raid_id] = {
            'channel_id': channel_id,
            'raids': raids,
            'created_at': now.isoformat()
        }
        self.save_data()
        return raid_id
    
    def get_weekly_raids(self, channel_id):
        return self.weekly_raids.get(str(channel_id))
    
    def get_raid_by_message(self, message_id):
        for channel_id, weekly in self.weekly_raids.items():
            for idx, raid in enumerate(weekly['raids']):
                if raid.get('message_id') == message_id:
                    return channel_id, idx, raid
        return None, None, None
    
    def signup(self, channel_id, raid_index, user_id, username, role):
        weekly = self.get_weekly_raids(channel_id)
        if not weekly or raid_index >= len(weekly['raids']):
            return False, "Invalid raid selection."
        
        raid = weekly['raids'][raid_index]
        
        if user_id in raid.get('blocked_users', []) or any(u['id'] == user_id for u in raid.get('blocked', [])):
            return False, "🚫 You are currently **blocked** from signing up for this raid."
        
        self.remove_user_from_raid(raid, user_id)
        user_data = {'id': user_id, 'name': username}
        location = "Main Raid"
        
        if role == 'tank':
            if len(raid['tanks']) < 2:
                raid['tanks'].append(user_data)
            else:
                raid['bench'].append({**user_data, 'role': 'tank'})
                location = "Bench (Tank)"
        elif role == 'support':
            if len(raid['supports']) < 4:
                raid['supports'].append(user_data)
            else:
                raid['bench'].append({**user_data, 'role': 'support'})
                location = "Bench (Support)"
        elif role == 'dps':
            if len(raid['dps']) < 14:
                raid['dps'].append(user_data)
            else:
                raid['bench'].append({**user_data, 'role': 'dps'})
                location = "Bench (DPS)"
        
        self.save_data()
        return True, f"Signed up as {role} for {raid['label']}!\nStatus: **{location}**"
    
    def promote_from_bench(self, raid, role):
        for user in raid['bench']:
            if user['role'] == role:
                raid['bench'].remove(user)
                user_data = {'id': user['id'], 'name': user['name']}
                
                if role == 'tank':
                    raid['tanks'].append(user_data)
                elif role == 'support':
                    raid['supports'].append(user_data)
                elif role == 'dps':
                    raid['dps'].append(user_data)
                
                self.save_data()
                return user
        return None
    
    def signup_tentative(self, channel_id, raid_index, user_id, username):
        weekly = self.get_weekly_raids(channel_id)
        if not weekly or raid_index >= len(weekly['raids']):
            return False, "Invalid raid selection."
        
        raid = weekly['raids'][raid_index]
        
        if user_id in raid.get('blocked_users', []) or any(u['id'] == user_id for u in raid.get('blocked', [])):
            return False, "🚫 You are currently **blocked** from signing up for this raid."
        
        self.remove_user_from_raid(raid, user_id)
        raid['tentative'].append({'id': user_id, 'name': username})
        self.save_data()
        return True, f"Added to tentative for {raid['label']}!"
    
    def mark_cleared(self, channel_id, raid_index, user_id, username):
        weekly = self.get_weekly_raids(channel_id)
        if not weekly or raid_index >= len(weekly['raids']):
            return False, "Invalid raid selection."
        
        raid = weekly['raids'][raid_index]
        
        if any(u['id'] == user_id for u in raid['cleared']):
            raid['cleared'] = [u for u in raid['cleared'] if u['id'] != user_id]
            self.save_data()
            return True, f"Removed cleared status for {raid['label']}"
        else:
            raid['cleared'].append({'id': user_id, 'name': username})
            self.save_data()
            return True, f"Marked as cleared for {raid['label']}!"
    
    def remove_user_from_raid(self, raid, user_id):
        raid['tanks'] = [u for u in raid['tanks'] if u['id'] != user_id]
        raid['supports'] = [u for u in raid['supports'] if u['id'] != user_id]
        raid['dps'] = [u for u in raid['dps'] if u['id'] != user_id]
        raid['bench'] = [u for u in raid['bench'] if u['id'] != user_id]
        raid['tentative'] = [u for u in raid['tentative'] if u['id'] != user_id]
        raid['cleared'] = [u for u in raid['cleared'] if u['id'] != user_id]
    
    def remove_user(self, channel_id, raid_index, user_id):
        weekly = self.get_weekly_raids(channel_id)
        if not weekly or raid_index >= len(weekly['raids']):
            return False, "Invalid raid selection."
        
        raid = weekly['raids'][raid_index]
        user_role = None
        
        if any(u['id'] == user_id for u in raid['tanks']):
            user_role = 'tank'
        elif any(u['id'] == user_id for u in raid['supports']):
            user_role = 'support'
        elif any(u['id'] == user_id for u in raid['dps']):
            user_role = 'dps'
        
        self.remove_user_from_raid(raid, user_id)
        
        if user_role:
            promoted = self.promote_from_bench(raid, user_role)
            if promoted:
                self.save_data()
                return True, f"Removed from raid!\n✅ {promoted['name']} promoted from bench to {user_role}!"
        
        self.save_data()
        return True, "Removed from raid!"
    
    def admin_remove_user(self, channel_id, raid_index, user_id):
        return self.remove_user(channel_id, raid_index, user_id)
    
    def admin_move_user(self, channel_id, raid_index, user_id, username, new_role, bench_role=None):
        weekly = self.get_weekly_raids(channel_id)
        if not weekly or raid_index >= len(weekly['raids']):
            return False, "Invalid raid selection."
        
        raid = weekly['raids'][raid_index]
        current_bench_role = None
        
        for user in raid['bench']:
            if user['id'] == user_id:
                current_bench_role = user['role']
                break
        
        self.remove_user_from_raid(raid, user_id)
        user_data = {'id': user_id, 'name': username}
        
        if new_role == 'tank':
            if len(raid['tanks']) < 2:
                raid['tanks'].append(user_data)
            else:
                return False, "Tank slots are full."
        elif new_role == 'support':
            if len(raid['supports']) < 4:
                raid['supports'].append(user_data)
            else:
                return False, "Support slots are full."
        elif new_role == 'dps':
            if len(raid['dps']) < 14:
                raid['dps'].append(user_data)
            else:
                return False, "DPS slots are full."
        elif new_role == 'bench':
            role_for_bench = bench_role or current_bench_role or 'dps'
            raid['bench'].append({**user_data, 'role': role_for_bench})
        else:
            return False, "Invalid role."
        
        self.save_data()
        return True, f"Moved {username} to {new_role}."
    
    def admin_promote_user(self, channel_id, raid_index, user_id, username, to_role):
        weekly = self.get_weekly_raids(channel_id)
        if not weekly or raid_index >= len(weekly['raids']):
            return False, "Invalid raid selection."
        
        raid = weekly['raids'][raid_index]
        
        if not any(u['id'] == user_id for u in raid['bench']):
            return False, f"{username} is not on the bench."
        
        raid['bench'] = [u for u in raid['bench'] if u['id'] != user_id]
        user_data = {'id': user_id, 'name': username}
        
        if to_role == 'tank':
            if len(raid['tanks']) >= 2:
                raid['bench'].append({**user_data, 'role': 'tank'})
                return False, "Tank slots are full."
            raid['tanks'].append(user_data)
        elif to_role == 'support':
            if len(raid['supports']) >= 4:
                raid['bench'].append({**user_data, 'role': 'support'})
                return False, "Support slots are full."
            raid['supports'].append(user_data)
        elif to_role == 'dps':
            if len(raid['dps']) >= 14:
                raid['bench'].append({**user_data, 'role': 'dps'})
                return False, "DPS slots are full."
            raid['dps'].append(user_data)
        else:
            raid['bench'].append({**user_data, 'role': 'dps'})
            return False, "Invalid role."
        
        self.save_data()
        return True, f"Promoted {username} from bench to {to_role}."
    
    def admin_demote_user(self, channel_id, raid_index, user_id, username):
        weekly = self.get_weekly_raids(channel_id)
        if not weekly or raid_index >= len(weekly['raids']):
            return False, "Invalid raid selection."
        
        raid = weekly['raids'][raid_index]
        current_role = None
        
        if any(u['id'] == user_id for u in raid['tanks']):
            current_role = 'tank'
            raid['tanks'] = [u for u in raid['tanks'] if u['id'] != user_id]
        elif any(u['id'] == user_id for u in raid['supports']):
            current_role = 'support'
            raid['supports'] = [u for u in raid['supports'] if u['id'] != user_id]
        elif any(u['id'] == user_id for u in raid['dps']):
            current_role = 'dps'
            raid['dps'] = [u for u in raid['dps'] if u['id'] != user_id]
        else:
            return False, f"{username} is not in the main roster."
        
        user_data = {'id': user_id, 'name': username, 'role': current_role}
        raid['bench'].append(user_data)
        
        promoted = self.promote_from_bench(raid, current_role)
        promotion_msg = ""
        if promoted:
            promotion_msg = f"\n✅ {promoted['name']} promoted from bench to {current_role}."
        
        self.save_data()
        return True, f"Demoted {username} to bench as {current_role}.{promotion_msg}"
    
    def get_user_status(self, channel_id, user_id):
        weekly = self.get_weekly_raids(channel_id)
        if not weekly:
            return []
        
        status_list = []
        for idx, raid in enumerate(weekly['raids']):
            raid_time = datetime.fromisoformat(raid['raid_time'])
            
            if datetime.now(TIMEZONE) > raid_time:
                continue
            
            status = None
            if any(u['id'] == user_id for u in raid['tanks']):
                status = "Tank (Main Roster)"
            elif any(u['id'] == user_id for u in raid['supports']):
                status = "Support (Main Roster)"
            elif any(u['id'] == user_id for u in raid['dps']):
                status = "DPS (Main Roster)"
            elif any(u['id'] == user_id for u in raid['bench']):
                bench_user = next(u for u in raid['bench'] if u['id'] == user_id)
                status = f"{bench_user['role'].title()} (Bench)"
            elif any(u['id'] == user_id for u in raid['tentative']):
                status = "Tentative"
            
            if status:
                status_list.append({
                    'label': raid['label'],
                    'status': status,
                    'time': raid_time
                })
        
        return status_list
    
    def admin_block_user(self, channel_id, raid_index, user_id, username):
        weekly = self.get_weekly_raids(channel_id)
        if not weekly or raid_index >= len(weekly['raids']):
            return False, "Invalid raid selection."
        
        raid = weekly['raids'][raid_index]
        
        if 'blocked_users' not in raid:
            raid['blocked_users'] = []
        if 'blocked' not in raid:
            raid['blocked'] = []
        
        if user_id in raid['blocked_users'] or any(u['id'] == user_id for u in raid['blocked']):
            return False, f"{username} is already blocked."
        
        raid['blocked_users'].append(user_id)
        raid['blocked'].append({'id': user_id, 'name': username})
        self.remove_user_from_raid(raid, user_id)
        
        self.save_data()
        return True, f"🚫 **{username}** is now **blocked** from signing up for {raid['label']}."
    
    def admin_unblock_user(self, channel_id, raid_index, user_id, username):
        weekly = self.get_weekly_raids(channel_id)
        if not weekly or raid_index >= len(weekly['raids']):
            return False, "Invalid raid selection."
        
        raid = weekly['raids'][raid_index]
        initial_length = len(raid.get('blocked_users', []))
        
        if 'blocked_users' in raid:
            raid['blocked_users'] = [uid for uid in raid['blocked_users'] if uid != user_id]
        if 'blocked' in raid:
            raid['blocked'] = [u for u in raid['blocked'] if u['id'] != user_id]
        
        if len(raid.get('blocked_users', [])) < initial_length or 'blocked' in raid:
            self.save_data()
            return True, f"✅ **{username}** has been **unblocked** for {raid['label']}."
        
        return False, f"{username} is not blocked."

raid_manager = RaidManager()

def create_raid_embed(raid, raid_index):
    now = datetime.now(TIMEZONE)
    raid_time = datetime.fromisoformat(raid['raid_time'])
    time_diff = raid_time - now
    is_locked = datetime.now(TIMEZONE) >= raid_time
    
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
    
    total_signups = len(raid['tanks']) + len(raid['supports']) + len(raid['dps'])
    timestamp = int(raid_time.timestamp())
    
    lock_indicator = "🔒 **Raid Locked — In Progress**\n\n" if is_locked else ""
    
    description = lock_indicator
    description += f"**{raid['label']}**\n"
    description += f"📅 <t:{timestamp}:F> (shows your local time)\n"
    description += f"{time_str} | Signed up: **{total_signups}/20**\n\n"
    
    role_summary = (
        f"🛡️ {len(raid['tanks'])}/2 | "
        f"💚 {len(raid['supports'])}/4 | "
        f"⚔️ {len(raid['dps'])}/14"
    )
    description += f"**Role Status:** {role_summary}\n\n"
    
    description += "**Nightmares first, then if cleared, do all hard raids.**\n"
    description += "*Please mention if you have cleared the raids already!*\n\n"
    
    embed = discord.Embed(
        title=f"🗡️ Raid Signups - {raid['label']} 🗡️",
        description=description,
        color=discord.Color.red() if not is_locked else discord.Color.dark_grey()
    )
    
    tank_names = ', '.join([
        f"{u['name']} ✅" if any(c['id'] == u['id'] for c in raid['cleared']) else u['name'] 
        for u in raid['tanks']
    ]) if raid['tanks'] else "None"
    embed.add_field(name=f"🛡️ Tanks ({len(raid['tanks'])}/2)", value=tank_names, inline=True)
    
    support_names = ', '.join([
        f"{u['name']} ✅" if any(c['id'] == u['id'] for c in raid['cleared']) else u['name'] 
        for u in raid['supports']
    ]) if raid['supports'] else "None"
    embed.add_field(name=f"💚 Supports ({len(raid['supports'])}/4)", value=support_names, inline=True)
    
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    
    dps_names = ', '.join([
        f"{u['name']} ✅" if any(c['id'] == u['id'] for c in raid['cleared']) else u['name'] 
        for u in raid['dps']
    ]) if raid['dps'] else "None"
    embed.add_field(name=f"⚔️ DPS ({len(raid['dps'])}/14)", value=dps_names, inline=False)
    
    if raid['bench']:
        bench_list = "\n".join(f"{i+1}. {u['name']} ({u['role']})" for i, u in enumerate(raid['bench']))
        embed.add_field(name=f"📋 Bench ({len(raid['bench'])}) - First come, first serve", 
                       value=bench_list, inline=False)
    
    if raid['tentative']:
        tent_names = ', '.join([u['name'] for u in raid['tentative']])
        embed.add_field(name=f"❓ Tentative ({len(raid['tentative'])})", value=tent_names, inline=True)
    
    if raid['cleared']:
        cleared_names = ', '.join([u['name'] for u in raid['cleared']])
        embed.add_field(name=f"✅ Already Cleared ({len(raid['cleared'])})", value=cleared_names, inline=True)
    
    if raid.get('blocked'):
        blocked_names = ', '.join([u['name'] for u in raid['blocked']])
        embed.add_field(name=f"🚫 Blocked ({len(raid['blocked'])})", value=blocked_names, inline=False)
    
    footer_text = "Use the buttons below to sign up!" if not is_locked else "Raid has started - signups are closed"
    embed.set_footer(text=footer_text)
    return embed

class RaidSignupView(discord.ui.View):
    def __init__(self, raid_index, channel_id=None):
        super().__init__(timeout=None)
        self.raid_index = raid_index
        custom_id_base = f"raid_{raid_index}"
        
        is_locked = False
        if channel_id:
            weekly = raid_manager.get_weekly_raids(channel_id)
            if weekly and raid_index < len(weekly['raids']):
                raid = weekly['raids'][raid_index]
                raid_time = datetime.fromisoformat(raid['raid_time'])
                is_locked = datetime.now(TIMEZONE) >= raid_time
        
        self.add_item(RaidButton(label="Tank", style=discord.ButtonStyle.primary, emoji="🛡️", 
                                 custom_id=f"{custom_id_base}_tank", role="tank", raid_index=raid_index, 
                                 row=0, disabled=is_locked))
        self.add_item(RaidButton(label="Support", style=discord.ButtonStyle.success, emoji="💚", 
                                 custom_id=f"{custom_id_base}_support", role="support", raid_index=raid_index, 
                                 row=0, disabled=is_locked))
        self.add_item(RaidButton(label="DPS", style=discord.ButtonStyle.danger, emoji="⚔️", 
                                 custom_id=f"{custom_id_base}_dps", role="dps", raid_index=raid_index, 
                                 row=0, disabled=is_locked))
        
        self.add_item(TentativeButton(custom_id=f"{custom_id_base}_tentative", raid_index=raid_index, 
                                     row=1, disabled=is_locked))
        self.add_item(ClearedButton(custom_id=f"{custom_id_base}_cleared", raid_index=raid_index, row=1))
        self.add_item(LeaveButton(custom_id=f"{custom_id_base}_leave", raid_index=raid_index, row=1))

class RaidButton(discord.ui.Button):
    def __init__(self, label, style, emoji, custom_id, role, raid_index, row, disabled=False):
        super().__init__(label=label, style=style, emoji=emoji, custom_id=custom_id, row=row, disabled=disabled)
        self.role = role
        self.raid_index = raid_index
    
    async def callback(self, interaction: discord.Interaction):
        now = datetime.now(TIMEZONE).timestamp()
        user_key = f"{interaction.user.id}_{self.raid_index}"
        last_interaction = user_cooldowns[interaction.user.id][user_key]
        
        if now - last_interaction < 5.0:
            remaining = int(5.0 - (now - last_interaction))
            await interaction.response.send_message(
                f"⏱️ Please wait {remaining} seconds before making another change.", ephemeral=True
            )
            return
        
        user_cooldowns[interaction.user.id][user_key] = now
        
        channel_id, _, _ = raid_manager.get_raid_by_message(interaction.message.id)
        if not channel_id:
            await interaction.response.send_message("Error: Raid not found.", ephemeral=True)
            return
        
        weekly = raid_manager.get_weekly_raids(int(channel_id))
        if weekly and self.raid_index < len(weekly['raids']):
            raid = weekly['raids'][self.raid_index]
            raid_time = datetime.fromisoformat(raid['raid_time'])
            if datetime.now(TIMEZONE) >= raid_time:
                await interaction.response.send_message(
                    "🔒 This raid has already started. Signups are closed.", ephemeral=True
                )
                return
        
        success, message = raid_manager.signup(
            int(channel_id), self.raid_index, interaction.user.id, interaction.user.display_name, self.role
        )
        
        await interaction.response.send_message(message, ephemeral=True)
        if success:
            await update_raid_message(int(channel_id), self.raid_index)

class TentativeButton(discord.ui.Button):
    def __init__(self, custom_id, raid_index, row, disabled=False):
        super().__init__(label="Tentative", style=discord.ButtonStyle.secondary, emoji="❓", 
                        custom_id=custom_id, row=row, disabled=disabled)
        self.raid_index = raid_index
    
    async def callback(self, interaction: discord.Interaction):
        now = datetime.now(TIMEZONE).timestamp()
        user_key = f"{interaction.user.id}_{self.raid_index}"
        last_interaction = user_cooldowns[interaction.user.id][user_key]
        
        if now - last_interaction < 5.0:
            remaining = int(5.0 - (now - last_interaction))
            await interaction.response.send_message(
                f"⏱️ Please wait {remaining} seconds before making another change.", ephemeral=True
            )
            return
        
        user_cooldowns[interaction.user.id][user_key] = now
        
        channel_id, _, _ = raid_manager.get_raid_by_message(interaction.message.id)
        if not channel_id:
            await interaction.response.send_message("Error: Raid not found.", ephemeral=True)
            return
        
        weekly = raid_manager.get_weekly_raids(int(channel_id))
        if weekly and self.raid_index < len(weekly['raids']):
            raid = weekly['raids'][self.raid_index]
            raid_time = datetime.fromisoformat(raid['raid_time'])
            if datetime.now(TIMEZONE) >= raid_time:
                await interaction.response.send_message(
                    "🔒 This raid has already started. Signups are closed.", ephemeral=True
                )
                return
        
        success, message = raid_manager.signup_tentative(
            int(channel_id), self.raid_index, interaction.user.id, interaction.user.display_name
        )
        await interaction.response.send_message(message, ephemeral=True)
        if success:
            await update_raid_message(int(channel_id), self.raid_index)

class ClearedButton(discord.ui.Button):
    def __init__(self, custom_id, raid_index, row):
        super().__init__(label="Already Cleared", style=discord.ButtonStyle.secondary, emoji="✅", 
                        custom_id=custom_id, row=row)
        self.raid_index = raid_index
    
    async def callback(self, interaction: discord.Interaction):
        channel_id, _, _ = raid_manager.get_raid_by_message(interaction.message.id)
        if not channel_id:
            await interaction.response.send_message("Error: Raid not found.", ephemeral=True)
            return
        
        success, message = raid_manager.mark_cleared(
            int(channel_id), self.raid_index, interaction.user.id, interaction.user.display_name
        )
        await interaction.response.send_message(message, ephemeral=True)
        if success:
            await update_raid_message(int(channel_id), self.raid_index)

class LeaveButton(discord.ui.Button):
    def __init__(self, custom_id, raid_index, row):
        super().__init__(label="Leave My Raid", style=discord.ButtonStyle.secondary, emoji="❌", 
                        custom_id=custom_id, row=row)
        self.raid_index = raid_index
    
    async def callback(self, interaction: discord.Interaction):
        channel_id, _, _ = raid_manager.get_raid_by_message(interaction.message.id)
        if not channel_id:
            await interaction.response.send_message("Error: Raid not found.", ephemeral=True)
            return
        
        success, message = raid_manager.remove_user(int(channel_id), self.raid_index, interaction.user.id)
        await interaction.response.send_message(message, ephemeral=True)
        if success:
            await update_raid_message(int(channel_id), self.raid_index)

async def update_raid_message(channel_id, raid_index):
    weekly = raid_manager.get_weekly_raids(channel_id)
    if not weekly or raid_index >= len(weekly['raids']):
        return
    
    raid = weekly['raids'][raid_index]
    if not raid.get('message_id'):
        return
    
    channel = bot.get_channel(channel_id)
    if not channel:
        return
    
    try:
        message = await channel.fetch_message(raid['message_id'])
        embed = create_raid_embed(raid, raid_index)
        view = RaidSignupView(raid_index, channel_id)
        await message.edit(embed=embed, view=view)
    except Exception as e:
        print(f"Error updating message: {e}")

@bot.event
async def on_ready():
    print(f'{bot.user} is now online!')
    
    for channel_id_str, weekly in raid_manager.weekly_raids.items():
        for idx, raid in enumerate(weekly['raids']):
            if raid.get('message_id'):
                bot.add_view(RaidSignupView(idx, int(channel_id_str)), message_id=raid['message_id'])
    
    check_weekly_reset.start()
    update_raid_timers.start()
    check_raid_reminders.start()
    cleanup_old_raids.start()

@tasks.loop(minutes=1)
async def check_weekly_reset():
    now = datetime.now(TIMEZONE)
    
    if now.weekday() == 0 and now.hour == 0 and now.minute == 0:
        for guild in bot.guilds:
            for channel in guild.text_channels:
                if channel.name == 'guild-raid-organization':
                    await create_weekly_raid_posts(channel)
    
    for channel_id, weekly in list(raid_manager.weekly_raids.items()):
        created_at = datetime.fromisoformat(weekly['created_at'])
        days_since_creation = (now - created_at).days
        
        if days_since_creation >= 7:
            channel = bot.get_channel(int(channel_id))
            if channel and channel.name == 'guild-raid-organization':
                print(f"Detected old raids in {channel.name}, recreating...")
                await create_weekly_raid_posts(channel)

@tasks.loop(hours=24)
async def cleanup_old_raids():
    now = datetime.now(TIMEZONE)
    
    for channel_id, weekly in list(raid_manager.weekly_raids.items()):
        raids_to_keep = []
        
        for raid in weekly['raids']:
            raid_time = datetime.fromisoformat(raid['raid_time'])
            days_past = (now - raid_time).days
            
            if days_past < 7:
                raids_to_keep.append(raid)
        
        if raids_to_keep:
            weekly['raids'] = raids_to_keep
        else:
            del raid_manager.weekly_raids[channel_id]
    
    raid_manager.save_data()
    print("Cleaned up old raids")

@tasks.loop(minutes=1)
async def check_raid_reminders():
    now = datetime.now(TIMEZONE)
    
    for channel_id, weekly in raid_manager.weekly_raids.items():
        channel = bot.get_channel(int(channel_id))
        if not channel:
            continue
        
        for idx, raid in enumerate(weekly['raids']):
            raid_time = datetime.fromisoformat(raid['raid_time'])
            time_diff = raid_time - now
            minutes_until = time_diff.total_seconds() / 60
            
            signed_up_ids = []
            for user in raid['tanks'] + raid['supports'] + raid['dps']:
                signed_up_ids.append(user['id'])
            
            mentions = ' '.join([f"<@{uid}>" for uid in signed_up_ids]) if signed_up_ids else ""
            
            if 29 <= minutes_until <= 31 and not raid.get('reminder_30min_sent', False):
                embed = discord.Embed(
                    title="⚠️ Raid Starting Soon! ⚠️",
                    description=f"**{raid['label']}** starts in **30 minutes**!\n\nMake sure you're ready!",
                    color=discord.Color.orange()
                )
                
                roster_info = f"**Tanks ({len(raid['tanks'])}/2):** "
                roster_info += ', '.join([u['name'] for u in raid['tanks']]) if raid['tanks'] else "None"
                roster_info += f"\n**Supports ({len(raid['supports'])}/4):** "
                roster_info += ', '.join([u['name'] for u in raid['supports']]) if raid['supports'] else "None"
                roster_info += f"\n**DPS ({len(raid['dps'])}/14):** "
                roster_info += ', '.join([u['name'] for u in raid['dps']]) if raid['dps'] else "None"
                
                embed.add_field(name="Current Roster", value=roster_info, inline=False)
                total = len(raid['tanks']) + len(raid['supports']) + len(raid['dps'])
                embed.set_footer(text=f"Total signed up: {total}/20")
                
                if mentions:
                    await channel.send(mentions, embed=embed)
                else:
                    await channel.send(embed=embed)
                
                raid['reminder_30min_sent'] = True
                raid_manager.save_data()
            
            if -1 <= minutes_until <= 1 and not raid.get('reminder_start_sent', False):
                embed = discord.Embed(
                    title="🚨 RAID STARTING NOW! 🚨",
                    description=f"**{raid['label']}** is starting!\n\nGet in game and form up!",
                    color=discord.Color.red()
                )
                
                roster_info = f"**Tanks ({len(raid['tanks'])}/2):** "
                roster_info += ', '.join([u['name'] for u in raid['tanks']]) if raid['tanks'] else "None"
                roster_info += f"\n**Supports ({len(raid['supports'])}/4):** "
                roster_info += ', '.join([u['name'] for u in raid['supports']]) if raid['supports'] else "None"
                roster_info += f"\n**DPS ({len(raid['dps'])}/14):** "
                roster_info += ', '.join([u['name'] for u in raid['dps']]) if raid['dps'] else "None"
                
                embed.add_field(name="Raid Roster", value=roster_info, inline=False)
                
                if raid['bench']:
                    bench_info = ', '.join([f"{u['name']} ({u['role']})" for u in raid['bench']])
                    embed.add_field(name="Bench (Available for replacements)", value=bench_info, inline=False)
                
                if mentions:
                    await channel.send(mentions, embed=embed)
                else:
                    await channel.send(embed=embed)
                
                raid['reminder_start_sent'] = True
                raid_manager.save_data()
                await update_raid_message(int(channel_id), idx)

@tasks.loop(minutes=5)
async def update_raid_timers():
    for channel_id, weekly in raid_manager.weekly_raids.items():
        for idx in range(len(weekly['raids'])):
            await update_raid_message(int(channel_id), idx)

async def create_weekly_raid_posts(channel):
    weekly_raids_data = raid_manager.get_weekly_raids(channel.id)
    old_message_ids = []
    if weekly_raids_data:
        for raid in weekly_raids_data.get('raids', []):
            if raid.get('message_id'):
                old_message_ids.append(raid['message_id'])
    
    raid_manager.create_weekly_raids(channel.id)
    weekly = raid_manager.get_weekly_raids(channel.id)
    
    for msg_id in old_message_ids:
        try:
            message = await channel.fetch_message(msg_id)
            await message.delete()
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"Error deleting old message {msg_id}: {e}")
    
    member_role = discord.utils.get(channel.guild.roles, name="Member")
    ping_text = f"{member_role.mention} Raid signups for this week!" if member_role else "Raid signups for this week!"
    await channel.send(ping_text)
    
    for idx, raid in enumerate(weekly['raids']):
        embed = create_raid_embed(raid, idx)
        view = RaidSignupView(idx, channel.id)
        message = await channel.send(embed=embed, view=view)
        raid['message_id'] = message.id
        raid_manager.save_data()

@bot.command()
@commands.has_permissions(administrator=True)
async def createweeklyraids(ctx):
    await create_weekly_raid_posts(ctx.channel)
    await ctx.message.delete()

async def send_audit_log(guild, admin_name, action, details):
    try:
        channel = guild.get_channel(ROUNDTABLE_CHANNEL_ID)
        if not channel:
            channel = await bot.fetch_channel(ROUNDTABLE_CHANNEL_ID)
        
        if channel:
            embed = discord.Embed(
                title="🔧 Raid Admin Action",
                description=f"**Admin:** {admin_name}\n**Action:** {action}\n**Details:** {details}",
                color=discord.Color.blue(),
                timestamp=datetime.now(TIMEZONE)
            )
            await channel.send(embed=embed)
    except Exception as e:
        print(f"Failed to send audit log: {e}")

def has_role_permission(member, required_roles):
    member_roles = [role.name for role in member.roles]
    return any(role in member_roles for role in required_roles)

@bot.command()
async def raidblock(ctx, member: discord.Member, day: str):
    if not has_role_permission(ctx.author, ['GM']):
        await ctx.message.delete()
        try:
            await ctx.author.send("❌ You need GM role to use this command.")
        except:
            pass
        return
    
    day_map = {'wednesday': 0, 'wed': 0, 'friday': 1, 'fri': 1, 'sunday': 2, 'sun': 2}
    raid_index = day_map.get(day.lower())
    
    if raid_index is None:
        try:
            await ctx.author.send("❌ Invalid day. Use: wednesday, friday, or sunday")
        except:
            msg = await ctx.send("❌ Invalid day. Use: wednesday, friday, or sunday")
            await msg.delete(delay=5)
        await ctx.message.delete()
        return
    
    success, message = raid_manager.admin_block_user(ctx.channel.id, raid_index, member.id, member.display_name)
    
    if success:
        await update_raid_message(ctx.channel.id, raid_index)
        await send_audit_log(ctx.guild, ctx.author.display_name, "Block User", 
                           f"{member.display_name} blocked from {day} raid")
        try:
            await ctx.author.send(f"✅ {message}")
        except:
            msg = await ctx.send(f"✅ {message}")
            await msg.delete(delay=10)
    else:
        try:
            await ctx.author.send(f"❌ {message}")
        except:
            msg = await ctx.send(f"❌ {message}")
            await msg.delete(delay=5)
    
    await ctx.message.delete()

@bot.command()
async def raidunblock(ctx, member: discord.Member, day: str):
    if not has_role_permission(ctx.author, ['GM']):
        await ctx.message.delete()
        try:
            await ctx.author.send("❌ You need GM role to use this command.")
        except:
            pass
        return
    
    day_map = {'wednesday': 0, 'wed': 0, 'friday': 1, 'fri': 1, 'sunday': 2, 'sun': 2}
    raid_index = day_map.get(day.lower())
    
    if raid_index is None:
        try:
            await ctx.author.send("❌ Invalid day. Use: wednesday, friday, or sunday")
        except:
            msg = await ctx.send("❌ Invalid day. Use: wednesday, friday, or sunday")
            await msg.delete(delay=5)
        await ctx.message.delete()
        return
    
    success, message = raid_manager.admin_unblock_user(ctx.channel.id, raid_index, member.id, member.display_name)
    
    if success:
        await send_audit_log(ctx.guild, ctx.author.display_name, "Unblock User", 
                           f"{member.display_name} unblocked from {day} raid")
        try:
            await ctx.author.send(f"✅ {message}")
        except:
            msg = await ctx.send(f"✅ {message}")
            await msg.delete(delay=10)
    else:
        try:
            await ctx.author.send(f"❌ {message}")
        except:
            msg = await ctx.send(f"❌ {message}")
            await msg.delete(delay=5)
    
    await ctx.message.delete()

@bot.command()
async def raidremove(ctx, member: discord.Member, day: str):
    required_roles = ['GM', 'Vice Master', 'Administrator', 'Quartermaster']
    if not has_role_permission(ctx.author, required_roles):
        await ctx.message.delete()
        try:
            await ctx.author.send("❌ You need at least Quartermaster role to use this command.")
        except:
            pass
        return
    
    day_map = {'wednesday': 0, 'wed': 0, 'friday': 1, 'fri': 1, 'sunday': 2, 'sun': 2}
    raid_index = day_map.get(day.lower())
    
    if raid_index is None:
        msg = await ctx.send("❌ Invalid day. Use: wednesday, friday, or sunday")
        await ctx.message.delete()
        await msg.delete(delay=5)
        return
    
    success, message = raid_manager.admin_remove_user(ctx.channel.id, raid_index, member.id)
    
    if success:
        await update_raid_message(ctx.channel.id, raid_index)
        try:
            await ctx.author.send(f"✅ Removed {member.display_name} from {day} raid. {message}")
        except:
            msg = await ctx.send(f"✅ Removed {member.display_name} from {day} raid. {message}")
            await msg.delete(delay=5)
    else:
        try:
            await ctx.author.send(f"❌ {message}")
        except:
            msg = await ctx.send(f"❌ {message}")
            await msg.delete(delay=5)
    
    await ctx.message.delete()

@bot.command()
async def raidmove(ctx, member: discord.Member, day: str, role: str, bench_role: str = None):
    required_roles = ['GM', 'Vice Master', 'Administrator', 'Quartermaster', 'Strategist']
    if not has_role_permission(ctx.author, required_roles):
        await ctx.message.delete()
        try:
            await ctx.author.send("❌ You need at least Strategist role to use this command.")
        except:
            pass
        return
    
    day_map = {'wednesday': 0, 'wed': 0, 'friday': 1, 'fri': 1, 'sunday': 2, 'sun': 2}
    raid_index = day_map.get(day.lower())
    
    if raid_index is None:
        try:
            await ctx.author.send("❌ Invalid day. Use: wednesday, friday, or sunday")
        except:
            msg = await ctx.send("❌ Invalid day. Use: wednesday, friday, or sunday")
            await msg.delete(delay=5)
        await ctx.message.delete()
        return
    
    role = role.lower()
    if role not in ['tank', 'support', 'dps', 'bench']:
        try:
            await ctx.author.send("❌ Invalid role. Use: tank, support, dps, or bench")
        except:
            msg = await ctx.send("❌ Invalid role. Use: tank, support, dps, or bench")
            await msg.delete(delay=5)
        await ctx.message.delete()
        return
    
    success, message = raid_manager.admin_move_user(
        ctx.channel.id, raid_index, member.id, member.display_name, role, bench_role
    )
    
    if success:
        await update_raid_message(ctx.channel.id, raid_index)
        await send_audit_log(ctx.guild, ctx.author.display_name, "Move User", 
                           f"{member.display_name} moved to {role} for {day} raid")
        try:
            await ctx.author.send(f"✅ {message}")
        except:
            msg = await ctx.send(f"✅ {message}")
            await msg.delete(delay=10)
    else:
        try:
            await ctx.author.send(f"❌ {message}")
        except:
            msg = await ctx.send(f"❌ {message}")
            await msg.delete(delay=5)
    
    await ctx.message.delete()

@bot.command()
async def raidpromote(ctx, member: discord.Member, day: str, role: str):
    required_roles = ['GM', 'Vice Master', 'Administrator', 'Quartermaster', 'Strategist']
    if not has_role_permission(ctx.author, required_roles):
        await ctx.message.delete()
        try:
            await ctx.author.send("❌ You need at least Strategist role to use this command.")
        except:
            pass
        return
    
    day_map = {'wednesday': 0, 'wed': 0, 'friday': 1, 'fri': 1, 'sunday': 2, 'sun': 2}
    raid_index = day_map.get(day.lower())
    
    if raid_index is None:
        try:
            await ctx.author.send("❌ Invalid day. Use: wednesday, friday, or sunday")
        except:
            msg = await ctx.send("❌ Invalid day. Use: wednesday, friday, or sunday")
            await msg.delete(delay=5)
        await ctx.message.delete()
        return
    
    role = role.lower()
    if role not in ['tank', 'support', 'dps']:
        try:
            await ctx.author.send("❌ Invalid role. Use: tank, support, or dps")
        except:
            msg = await ctx.send("❌ Invalid role. Use: tank, support, or dps")
            await msg.delete(delay=5)
        await ctx.message.delete()
        return
    
    success, message = raid_manager.admin_promote_user(
        ctx.channel.id, raid_index, member.id, member.display_name, role
    )
    
    if success:
        await update_raid_message(ctx.channel.id, raid_index)
        await send_audit_log(ctx.guild, ctx.author.display_name, "Promote User", 
                           f"{member.display_name} promoted to {role} for {day} raid")
        try:
            await ctx.author.send(f"✅ {message}")
        except:
            msg = await ctx.send(f"✅ {message}")
            await msg.delete(delay=10)
    else:
        try:
            await ctx.author.send(f"❌ {message}")
        except:
            msg = await ctx.send(f"❌ {message}")
            await msg.delete(delay=5)
    
    await ctx.message.delete()

@bot.command()
async def raiddemote(ctx, member: discord.Member, day: str):
    required_roles = ['GM', 'Vice Master', 'Administrator', 'Quartermaster', 'Strategist']
    if not has_role_permission(ctx.author, required_roles):
        await ctx.message.delete()
        try:
            await ctx.author.send("❌ You need at least Strategist role to use this command.")
        except:
            pass
        return
    
    day_map = {'wednesday': 0, 'wed': 0, 'friday': 1, 'fri': 1, 'sunday': 2, 'sun': 2}
    raid_index = day_map.get(day.lower())
    
    if raid_index is None:
        try:
            await ctx.author.send("❌ Invalid day. Use: wednesday, friday, or sunday")
        except:
            msg = await ctx.send("❌ Invalid day. Use: wednesday, friday, or sunday")
            await msg.delete(delay=5)
        await ctx.message.delete()
        return
    
    success, message = raid_manager.admin_demote_user(
        ctx.channel.id, raid_index, member.id, member.display_name
    )
    
    if success:
        await update_raid_message(ctx.channel.id, raid_index)
        await send_audit_log(ctx.guild, ctx.author.display_name, "Demote User", 
                           f"{member.display_name} demoted to bench for {day} raid")
        try:
            await ctx.author.send(f"✅ {message}")
        except:
            msg = await ctx.send(f"✅ {message}")
            await msg.delete(delay=10)
    else:
        try:
            await ctx.author.send(f"❌ {message}")
        except:
            msg = await ctx.send(f"❌ {message}")
            await msg.delete(delay=5)
    
    await ctx.message.delete()

@bot.command()
@commands.has_permissions(administrator=True)
async def raidstatus(ctx, member: discord.Member):
    status_list = raid_manager.get_user_status(ctx.channel.id, member.id)
    
    if not status_list:
        try:
            await ctx.author.send(f"{member.display_name} is not signed up for any upcoming raids.")
        except:
            msg = await ctx.send(f"{member.display_name} is not signed up for any upcoming raids.")
            await msg.delete(delay=10)
    else:
        embed = discord.Embed(title=f"Raid Status for {member.display_name}", color=discord.Color.blue())
        
        for status in status_list:
            time_str = status['time'].strftime('%A, %B %d at %I:%M %p EST')
            embed.add_field(name=status['label'], value=f"{status['status']}\n{time_str}", inline=False)
        
        try:
            await ctx.author.send(embed=embed)
        except:
            msg = await ctx.send(embed=embed)
            await msg.delete(delay=30)
    
    await ctx.message.delete()

bot.run(os.getenv("BOT_2_TOKEN"))
