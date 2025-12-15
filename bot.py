import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import pytz
import json
import os
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Timezone setup
TIMEZONE = pytz.timezone('America/New_York')

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
        self.locks = set()  # Prevent race conditions
        self.ensure_data_directory()
        self.load_data()
    
    def ensure_data_directory(self):
        """Ensure /app/data directory exists"""
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
        """Remove hunts older than 2 days"""
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
                    '8': {'tank': None, 'support': None, 'dps': []}
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
        """Get which party and role a user is currently in"""
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
    
    def is_user_signed_up(self, channel_id, hunt_index, user_id):
        """Check if user is signed up for the hunt"""
        weekly = self.get_weekly_hunts(channel_id)
        if not weekly or hunt_index >= len(weekly['hunts']):
            return False
        
        hunt = weekly['hunts'][hunt_index]
        return any(u['id'] == user_id for u in hunt['signed_up'])
    
    def change_role(self, channel_id, hunt_index, user_id, username, new_role):
        """Change a user's signed up role"""
        lock_key = f"{channel_id}_{hunt_index}_{user_id}"
        if lock_key in self.locks:
            return False, "Please wait a moment before making changes."
        
        self.locks.add(lock_key)
        try:
            weekly = self.get_weekly_hunts(channel_id)
            if not weekly or hunt_index >= len(weekly['hunts']):
                return False, "Invalid hunt selection."
            
            hunt = weekly['hunts'][hunt_index]
            
            user_signup = next((u for u in hunt['signed_up'] if u['id'] == user_id), None)
            if not user_signup:
                return False, "You must be signed up first to change roles."
            
            self.remove_user_from_hunt(hunt, user_id)
            user_data = {'id': user_id, 'name': username, 'role': new_role}
            hunt['signed_up'].append(user_data)
            
            self.save_data()
            return True, f"Role changed to {new_role}! Please select your party again."
        finally:
            self.locks.discard(lock_key)
    
    def signup(self, channel_id, hunt_index, user_id, username, role, is_replacement=False):
        lock_key = f"{channel_id}_{hunt_index}_{user_id}"
        if lock_key in self.locks:
            return False, "Please wait a moment before making changes."
        
        self.locks.add(lock_key)
        try:
            weekly = self.get_weekly_hunts(channel_id)
            if not weekly or hunt_index >= len(weekly['hunts']):
                return False, "Invalid hunt selection."
            
            hunt = weekly['hunts'][hunt_index]
            
            if not is_replacement:
                self.remove_user_from_hunt(hunt, user_id)
            
            user_data = {'id': user_id, 'name': username, 'role': role}
            
            if is_replacement:
                assigned = False
                for party_num in range(1, 9):
                    party = hunt['parties'][str(party_num)]
                    party_count = (1 if party['tank'] else 0) + (1 if party['support'] else 0) + len(party['dps'])
                    
                    if party_count > 0:
                        user_in_this_party = (party['tank'] and party['tank']['id'] == user_id) or \
                                             (party['support'] and party['support']['id'] == user_id) or \
                                             any(d['id'] == user_id for d in party['dps'])
                        
                        if user_in_this_party:
                            continue
                        
                        if role == 'tank' and not party['tank']:
                            party['tank'] = {'id': user_id, 'name': username}
                            assigned = True
                            self.save_data()
                            return True, f"Assigned as Tank replacement to Party {party_num}!"
                        elif role == 'support' and not party['support']:
                            party['support'] = {'id': user_id, 'name': username}
                            assigned = True
                            self.save_data()
                            return True, f"Assigned as Support replacement to Party {party_num}!"
                        elif role == 'dps' and len(party['dps']) < 3:
                            party['dps'].append({'id': user_id, 'name': username})
                            assigned = True
                            self.save_data()
                            return True, f"Assigned as DPS replacement to Party {party_num}!"
                
                if not assigned:
                    return False, f"No parties currently need a {role} replacement."
            else:
                hunt['signed_up'].append(user_data)
            
            self.save_data()
            return True, f"Signed up as {role} for {hunt['label']}! Now select your party."
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
            
            user_signup = next((u for u in hunt['signed_up'] if u['id'] == user_id), None)
            if not user_signup:
                return False, "You must sign up for the hunt first before joining a party!"
            
            role = user_signup['role']
            
            if party_number in [1, 2]:
                has_priority = any(role_name in PRIORITY_ROLES for role_name in user_roles)
                if not has_priority:
                    return False, f"Party {party_number} is reserved for leadership roles only."
            
            for p in hunt['parties'].values():
                if p['tank'] and p['tank']['id'] == user_id:
                    p['tank'] = None
                if p['support'] and p['support']['id'] == user_id:
                    p['support'] = None
                p['dps'] = [d for d in p['dps'] if d['id'] != user_id]
            
            user_data = {'id': user_id, 'name': username}
            
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
    
    def leave_party(self, channel_id, hunt_index, user_id):
        lock_key = f"{channel_id}_{hunt_index}_{user_id}"
        if lock_key in self.locks:
            return False, "Please wait a moment before making changes."
        
        self.locks.add(lock_key)
        try:
            weekly = self.get_weekly_hunts(channel_id)
            if not weekly or hunt_index >= len(weekly['hunts']):
                return False, "Invalid hunt selection."
            
            hunt = weekly['hunts'][hunt_index]
            
            found = False
            for p in hunt['parties'].values():
                if p['tank'] and p['tank']['id'] == user_id:
                    p['tank'] = None
                    found = True
                if p['support'] and p['support']['id'] == user_id:
                    p['support'] = None
                    found = True
                if any(d['id'] == user_id for d in p['dps']):
                    p['dps'] = [d for d in p['dps'] if d['id'] != user_id]
                    found = True
                if found:
                    break
            
            if not found:
                return False, "You are not currently in a party."
            
            self.save_data()
            return True, "Left your current party!"
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
    time_diff = hunt_time - now
    
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
    
    description = f"**{hunt['label']}**\n"
    description += f"📅 <t:{timestamp}:F>\n"
    description += f"{time_str} | Signed up: **{total_signups}**\n\n"
    description += "*Parties 1 & 2 reserved for leadership 👑 | Sign up with role, then pick party*\n\n"
    
    # Display parties in a compact 2-column format
    for row in range(4):  # 4 rows
        left_party_num = row + 1
        right_party_num = row + 5
        
        left_party = hunt['parties'][str(left_party_num)]
        right_party = hunt['parties'][str(right_party_num)]
        
        left_count = (1 if left_party['tank'] else 0) + (1 if left_party['support'] else 0) + len(left_party['dps'])
        right_count = (1 if right_party['tank'] else 0) + (1 if right_party['support'] else 0) + len(right_party['dps'])
        
        # Party headers
        left_header = f"**P{left_party_num}({left_count}/5)**"
        if left_party_num in [1, 2]:
            left_header += "👑"
        
        right_header = f"**P{right_party_num}({right_count}/5)**"
        
        description += f"{left_header} | {right_header}\n"
        
        # Tank line
        left_tank = left_party['tank']['name'] if left_party['tank'] else "--"
        right_tank = right_party['tank']['name'] if right_party['tank'] else "--"
        description += f"🛡️ {left_tank} | 🛡️ {right_tank}\n"
        
        # Support line
        left_support = left_party['support']['name'] if left_party['support'] else "--"
        right_support = right_party['support']['name'] if right_party['support'] else "--"
        description += f"💚 {left_support} | 💚 {right_support}\n"
        
        # DPS line
        left_dps = ', '.join([d['name'] for d in left_party['dps']]) if left_party['dps'] else "--"
        right_dps = ', '.join([d['name'] for d in right_party['dps']]) if right_party['dps'] else "--"
        description += f"⚔️ {left_dps} | ⚔️ {right_dps}\n"
        
        description += "\n"
    
    # Add users not in party with role icons
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
        color=discord.Color.green()
    )
    
    embed.set_footer(text="Use the buttons below to sign up!")
    return embed

class HuntSignupView(discord.ui.View):
    def __init__(self, hunt_index):
        super().__init__(timeout=None)
        self.hunt_index = hunt_index
        custom_id_base = f"hunt_{hunt_index}"
        
        self.add_item(HuntButton(label="Tank", style=discord.ButtonStyle.primary, emoji="🛡️", 
                                 custom_id=f"{custom_id_base}_tank", role="tank", hunt_index=hunt_index, row=0))
        self.add_item(HuntButton(label="Support", style=discord.ButtonStyle.success, emoji="💚", 
                                 custom_id=f"{custom_id_base}_support", role="support", hunt_index=hunt_index, row=0))
        self.add_item(HuntButton(label="DPS", style=discord.ButtonStyle.danger, emoji="⚔️", 
                                 custom_id=f"{custom_id_base}_dps", role="dps", hunt_index=hunt_index, row=0))
        self.add_item(ChangeRoleButton(custom_id=f"{custom_id_base}_change", hunt_index=hunt_index, row=0))
        
        self.add_item(ReplacementButton(label="Replacement Tank", style=discord.ButtonStyle.primary, emoji="🔄",
                                       custom_id=f"{custom_id_base}_rep_tank", role="tank", hunt_index=hunt_index, row=1))
        self.add_item(ReplacementButton(label="Replacement Support", style=discord.ButtonStyle.success, emoji="🔄",
                                       custom_id=f"{custom_id_base}_rep_support", role="support", hunt_index=hunt_index, row=1))
        self.add_item(ReplacementButton(label="Replacement DPS", style=discord.ButtonStyle.danger, emoji="🔄",
                                       custom_id=f"{custom_id_base}_rep_dps", role="dps", hunt_index=hunt_index, row=1))
        
        self.add_item(TentativeButton(custom_id=f"{custom_id_base}_tentative", hunt_index=hunt_index, row=2))
        self.add_item(LeaveButton(custom_id=f"{custom_id_base}_leave", hunt_index=hunt_index, row=2))

class HuntButton(discord.ui.Button):
    def __init__(self, label, style, emoji, custom_id, role, hunt_index, row):
        super().__init__(label=label, style=style, emoji=emoji, custom_id=custom_id, row=row)
        self.role = role
        self.hunt_index = hunt_index
    
    async def callback(self, interaction: discord.Interaction):
        if interaction.response.is_done():
            return
        
        channel_id, _, _ = hunt_manager.get_hunt_by_message(interaction.message.id)
        if not channel_id:
            await interaction.response.send_message("Error: Hunt not found.", ephemeral=True)
            return
        
        success, message = hunt_manager.signup(
            int(channel_id),
            self.hunt_index,
            interaction.user.id,
            interaction.user.display_name,
            self.role,
            is_replacement=False
        )
        
        if success:
            party_num, party_role = hunt_manager.get_user_party(int(channel_id), self.hunt_index, interaction.user.id)
            status_msg = message
            if party_num:
                status_msg += f"\n\n**Current Status:** You are in Party {party_num} as {party_role}."
            else:
                status_msg += "\n\n**Current Status:** Not in a party yet."
            
            view = PartySelectView(self.hunt_index, self.role, interaction.user, int(channel_id))
            await interaction.response.send_message(status_msg + "\nSelect your party:", view=view, ephemeral=True)
            await update_hunt_message(int(channel_id), self.hunt_index)
        else:
            await interaction.response.send_message(message, ephemeral=True)

class ChangeRoleButton(discord.ui.Button):
    def __init__(self, custom_id, hunt_index, row):
        super().__init__(label="Change Role", style=discord.ButtonStyle.secondary, emoji="🔄", custom_id=custom_id, row=row)
        self.hunt_index = hunt_index
    
    async def callback(self, interaction: discord.Interaction):
        if interaction.response.is_done():
            return
        
        channel_id, _, _ = hunt_manager.get_hunt_by_message(interaction.message.id)
        if not channel_id:
            await interaction.response.send_message("Error: Hunt not found.", ephemeral=True)
            return
        
        view = RoleChangeView(self.hunt_index, int(channel_id))
        await interaction.response.send_message("Select your new role:", view=view, ephemeral=True)

class RoleChangeView(discord.ui.View):
    def __init__(self, hunt_index, channel_id):
        super().__init__(timeout=600)
        self.hunt_index = hunt_index
        self.channel_id = channel_id
        
        select = discord.ui.Select(
            placeholder="Choose your new role...",
            options=[
                discord.SelectOption(label="Tank", value="tank", emoji="🛡️"),
                discord.SelectOption(label="Support", value="support", emoji="💚"),
                discord.SelectOption(label="DPS", value="dps", emoji="⚔️")
            ]
        )
        select.callback = self.role_select_callback
        self.add_item(select)
    
    async def role_select_callback(self, interaction: discord.Interaction):
        if interaction.response.is_done():
            return
        
        new_role = interaction.data['values'][0]
        success, message = hunt_manager.change_role(
            self.channel_id,
            self.hunt_index,
            interaction.user.id,
            interaction.user.display_name,
            new_role
        )
        
        if success:
            view = PartySelectView(self.hunt_index, new_role, interaction.user, self.channel_id)
            await interaction.response.send_message(message + "\nSelect your party:", view=view, ephemeral=True)
            await update_hunt_message(self.channel_id, self.hunt_index)
        else:
            await interaction.response.send_message(message, ephemeral=True)

class ReplacementButton(discord.ui.Button):
    def __init__(self, label, style, emoji, custom_id, role, hunt_index, row):
        super().__init__(label=label, style=style, emoji=emoji, custom_id=custom_id, row=row)
        self.role = role
        self.hunt_index = hunt_index
    
    async def callback(self, interaction: discord.Interaction):
        if interaction.response.is_done():
            return
        
        channel_id, _, _ = hunt_manager.get_hunt_by_message(interaction.message.id)
        if not channel_id:
            await interaction.response.send_message("Error: Hunt not found.", ephemeral=True)
            return
        
        success, message = hunt_manager.signup(
            int(channel_id),
            self.hunt_index,
            interaction.user.id,
            interaction.user.display_name,
            self.role,
            is_replacement=True
        )
        await interaction.response.send_message(message, ephemeral=True)
        if success:
            await update_hunt_message(int(channel_id), self.hunt_index)

class TentativeButton(discord.ui.Button):
    def __init__(self, custom_id, hunt_index, row):
        super().__init__(label="Tentative", style=discord.ButtonStyle.secondary, emoji="❓", custom_id=custom_id, row=row)
        self.hunt_index = hunt_index
    
    async def callback(self, interaction: discord.Interaction):
        if interaction.response.is_done():
            return
        
        channel_id, _, _ = hunt_manager.get_hunt_by_message(interaction.message.id)
        if not channel_id:
            await interaction.response.send_message("Error: Hunt not found.", ephemeral=True)
            return
        
        success, message = hunt_manager.signup_tentative(
            int(channel_id),
            self.hunt_index,
            interaction.user.id,
            interaction.user.display_name
        )
        await interaction.response.send_message(message, ephemeral=True)
        if success:
            await update_hunt_message(int(channel_id), self.hunt_index)

class LeaveButton(discord.ui.Button):
    def __init__(self, custom_id, hunt_index, row):
        super().__init__(label="Leave Hunt", style=discord.ButtonStyle.secondary, emoji="❌", custom_id=custom_id, row=row)
        self.hunt_index = hunt_index
    
    async def callback(self, interaction: discord.Interaction):
        if interaction.response.is_done():
            return
        
        channel_id, _, _ = hunt_manager.get_hunt_by_message(interaction.message.id)
        if not channel_id:
            await interaction.response.send_message("Error: Hunt not found.", ephemeral=True)
            return
        
        success, message = hunt_manager.remove_user(
            int(channel_id),
            self.hunt_index,
            interaction.user.id
        )
        await interaction.response.send_message(message, ephemeral=True)
        if success:
            await update_hunt_message(int(channel_id), self.hunt_index)

class PartySelectView(discord.ui.View):
    def __init__(self, hunt_index, role, user, channel_id):
        super().__init__(timeout=600)
        self.hunt_index = hunt_index
        self.role = role
        self.user = user
        self.channel_id = channel_id
        
        weekly = hunt_manager.get_weekly_hunts(channel_id)
        hunt = weekly['hunts'][hunt_index] if weekly and hunt_index < len(weekly['hunts']) else None
        
        # Check if user is signed up
        is_signed_up = hunt_manager.is_user_signed_up(channel_id, hunt_index, user.id)
        
        for i in range(1, 9):
            is_full = False
            if hunt:
                party = hunt['parties'].get(str(i))
                if party:
                    if role == 'tank' and party.get('tank'):
                        is_full = True
                    elif role == 'support' and party.get('support'):
                        is_full = True
                    elif role == 'dps' and len(party.get('dps', [])) >= 3:
                        is_full = True
            
            button = discord.ui.Button(
                label=f"Party {i}",
                style=discord.ButtonStyle.primary if i not in [1, 2] else discord.ButtonStyle.secondary,
                custom_id=f"party_{i}_{hunt_index}",
                emoji="👑" if i in [1, 2] else "🎯",
                disabled=is_full or not is_signed_up
            )
            button.callback = self.create_party_callback(i)
            self.add_item(button)
        
        unsignup_button = discord.ui.Button(
            label="Un-Sign Up (Remove Role)",
            style=discord.ButtonStyle.danger,
            emoji="🚫"
        )
        unsignup_button.callback = self.unsignup_callback
        self.add_item(unsignup_button)
        
        leave_button = discord.ui.Button(
            label="Leave My Party",
            style=discord.ButtonStyle.secondary,
            emoji="❌"
        )
        leave_button.callback = self.leave_party_callback
        self.add_item(leave_button)
    
    def create_party_callback(self, party_num):
        async def callback(interaction: discord.Interaction):
            if interaction.response.is_done():
                return
            
            await interaction.response.defer(ephemeral=True)
            
            user_role_names = []
            if isinstance(interaction.user, discord.Member):
                user_role_names = [role.name for role in interaction.user.roles]
            
            success, message = hunt_manager.join_party(
                self.channel_id,
                self.hunt_index,
                party_num,
                interaction.user.id,
                interaction.user.display_name,
                user_role_names
            )
            
            if success:
                party_num_current, role_current = hunt_manager.get_user_party(self.channel_id, self.hunt_index, interaction.user.id)
                if party_num_current:
                    message += f"\n\n**Current Status:** You are now in Party {party_num_current} as {role_current}."
            
            await interaction.followup.send(message, ephemeral=True)
            if success:
                await update_hunt_message(self.channel_id, self.hunt_index)
        return callback
    
    async def unsignup_callback(self, interaction: discord.Interaction):
        if interaction.response.is_done():
            return
        
        await interaction.response.defer(ephemeral=True)
        
        success, message = hunt_manager.remove_user(
            self.channel_id,
            self.hunt_index,
            interaction.user.id
        )
        
        await interaction.followup.send(message, ephemeral=True)
        if success:
            await update_hunt_message(self.channel_id, self.hunt_index)
    
    async def leave_party_callback(self, interaction: discord.Interaction):
        if interaction.response.is_done():
            return
        
        await interaction.response.defer(ephemeral=True)
        
        party_num, _ = hunt_manager.get_user_party(self.channel_id, self.hunt_index, interaction.user.id)
        
        if not party_num:
            await interaction.followup.send("You are not currently assigned to any party.", ephemeral=True)
            return
        
        success, message = hunt_manager.leave_party(
            self.channel_id,
            self.hunt_index,
            interaction.user.id
        )
        
        await interaction.followup.send(message, ephemeral=True)
        if success:
            await update_hunt_message(self.channel_id, self.hunt_index)

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
        view = HuntSignupView(hunt_index)
        await message.edit(embed=embed, view=view)
    except discord.NotFound:
        hunt['message_id'] = None
        hunt_manager.save_data()
        logging.warning(f"Message not found for hunt {hunt_index} in channel {channel_id}. ID cleared.")
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
                bot.add_view(HuntSignupView(idx), message_id=hunt['message_id'])
    
    check_weekly_reset.start()
    update_hunt_timers.start()
    check_hunt_reminders.start()
    cleanup_old_hunts_task.start()

@tasks.loop(minutes=1)
async def check_weekly_reset():
    now = datetime.now(TIMEZONE)
    
    if now.weekday() == 0 and now.hour == 0 and now.minute == 0:
        logging.info("Weekly reset trigger time reached. Running reset.")
        for guild in bot.guilds:
            for channel in guild.text_channels:
                if channel.name == 'guild-hunt-organization':
                    await post_new_hunts_to_channel(channel)

@tasks.loop(minutes=1)
async def check_hunt_reminders():
    """Check if any hunts are starting in 30 minutes and send reminders"""
    now = datetime.now(TIMEZONE)
    
    for channel_id, weekly in hunt_manager.weekly_hunts.items():
        channel = bot.get_channel(int(channel_id))
        if not channel:
            continue
        
        for idx, hunt in enumerate(weekly['hunts']):
            if hunt.get('reminder_sent_at') is not None:
                continue
            
            hunt_time = datetime.fromisoformat(hunt['hunt_time'])
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
    """Clean up hunts older than 2 days"""
    logging.info("Running cleanup of old hunts...")
    hunt_manager.cleanup_old_hunts()

@tasks.loop(minutes=5)
async def update_hunt_timers():
    for channel_id, weekly in hunt_manager.weekly_hunts.items():
        for idx in range(len(weekly['hunts'])):
            await update_hunt_message(int(channel_id), idx)

async def post_new_hunts_to_channel(channel):
    """Handles the logic of deleting old messages, creating new hunts, and posting them."""
    try:
        weekly_hunts_data = hunt_manager.get_weekly_hunts(channel.id)
        old_message_ids = []
        if weekly_hunts_data:
            for hunt in weekly_hunts_data.get('hunts', []):
                if hunt.get('message_id'):
                    old_message_ids.append(hunt['message_id'])
        
        hunt_manager.create_weekly_hunts(channel.id)
        weekly = hunt_manager.get_weekly_hunts(channel.id)
        
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
            view = HuntSignupView(idx)
            message = await channel.send(embed=embed, view=view)
            
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
