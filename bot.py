import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import pytz
import json
import os

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Timezone setup
TIMEZONE = pytz.timezone('America/New_York')

# Weekly guild hunt schedule
HUNT_SCHEDULE = [
    {'day': 4, 'hour': 21, 'minute': 0, 'label': 'Friday 21:00'},    # Friday
    {'day': 5, 'hour': 21, 'minute': 0, 'label': 'Saturday 21:00'},  # Saturday
    {'day': 6, 'hour': 21, 'minute': 0, 'label': 'Sunday 21:00'}     # Sunday
]

# Priority roles that can access Party 1 and 2
PRIORITY_ROLES = ['Frontrunner', 'Envoy', 'Strategist', 'GM', 'Quartermaster', 'Administrator', 'Vice Master']

# Guild Hunt data structure
class HuntManager:
    def __init__(self):
        self.weekly_hunts = {}
        self.load_data()
    
    def load_data(self):
        if os.path.exists('/app/data/hunts.json'):
            try:
                with open('/app/data/hunts.json', 'r') as f:
                    self.weekly_hunts = json.load(f)
            except json.JSONDecodeError:
                print("hunts.json is corrupted or empty — resetting data")
                self.weekly_hunts = {}
            except Exception as e:
                print(f"Error loading hunts.json: {e} — resetting data")
                self.weekly_hunts = {}
    
    def save_data(self):
        with open('/app/data/hunts.json', 'w') as f:
            json.dump(self.weekly_hunts, f, indent=4)
    
    def create_weekly_hunts(self, channel_id):
        hunt_id = str(channel_id)
        
        # 🔒 Prevent duplicate weekly hunts for the same channel
        self.weekly_hunts.pop(hunt_id, None)
        
        now = datetime.now(TIMEZONE)
        
        hunts = []
        for schedule in HUNT_SCHEDULE:
            # Calculate next occurrence of this day
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
                'reminder_sent': False
            })
        
        self.weekly_hunts[hunt_id] = {
            'channel_id': channel_id,
            'hunts': hunts,
            'created_at': now.isoformat()
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
    
    def change_role(self, channel_id, hunt_index, user_id, username, new_role):
        """Change a user's signed up role"""
        weekly = self.get_weekly_hunts(channel_id)
        if not weekly or hunt_index >= len(weekly['hunts']):
            return False, "Invalid hunt selection."
        
        hunt = weekly['hunts'][hunt_index]
        
        # Check if user is signed up
        user_signup = next((u for u in hunt['signed_up'] if u['id'] == user_id), None)
        if not user_signup:
            return False, "You must be signed up first to change roles."
        
        # Remove from current party
        self.remove_user_from_hunt(hunt, user_id)
        
        # Update role in signed_up list
        user_data = {'id': user_id, 'name': username, 'role': new_role}
        hunt['signed_up'].append(user_data)
        
        self.save_data()
        return True, f"Role changed to {new_role}! Please select your party again."
    
    def signup(self, channel_id, hunt_index, user_id, username, role, is_replacement=False):
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
    
    def signup_tentative(self, channel_id, hunt_index, user_id, username):
        weekly = self.get_weekly_hunts(channel_id)
        if not weekly or hunt_index >= len(weekly['hunts']):
            return False, "Invalid hunt selection."
        
        hunt = weekly['hunts'][hunt_index]
        self.remove_user_from_hunt(hunt, user_id)
        hunt['tentative'].append({'id': user_id, 'name': username})
        self.save_data()
        return True, f"Added to tentative for {hunt['label']}!"
    
    def join_party(self, channel_id, hunt_index, party_number, user_id, username, user_roles):
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
                return False, f"Party {party_number} is reserved for @Frontrunner, @Envoy, @Strategist, @GM, @Quartermaster, @Administrator, and @Vice Master members only."
        
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
    
    def leave_party(self, channel_id, hunt_index, user_id):
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
            p['dps'] = [d for d in p['dps'] if d['id'] != user_id]
            if found:
                break
        
        if not found:
            return False, "You are not currently in a party."
        
        self.save_data()
        return True, "Left your current party!"
    
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
        weekly = self.get_weekly_hunts(channel_id)
        if not weekly or hunt_index >= len(weekly['hunts']):
            return False, "Invalid hunt selection."
        
        hunt = weekly['hunts'][hunt_index]
        self.remove_user_from_hunt(hunt, user_id)
        self.save_data()
        return True, "Removed from hunt!"

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
    description += f"📅 <t:{timestamp}:F> (shows your local time)\n"
    description += f"{time_str} | Signed up: **{total_signups}**\n\n"
    description += "*Parties 1 & 2 reserved for leadership roles 👑*\n"
    description += "*Sign up with your role, then choose your party*\n\n"
    
    for party_num in range(1, 9):
        party = hunt['parties'][str(party_num)]
        party_count = (1 if party['tank'] else 0) + (1 if party['support'] else 0) + len(party['dps'])
        
        if party_count == 0:
            tank_name = "Empty"
            support_name = "Empty"
            dps_names = "Empty"
        else:
            tank_name = party['tank']['name'] if party['tank'] else "⚠️ Need a tank to fill in"
            support_name = party['support']['name'] if party['support'] else "⚠️ Need a support to fill in"
            
            if len(party['dps']) == 0:
                dps_names = "⚠️ Need DPS to fill in"
            elif len(party['dps']) < 3:
                dps_names = ', '.join([d['name'] for d in party['dps']]) + " (Need more DPS)"
            else:
                dps_names = ', '.join([d['name'] for d in party['dps']])
        
        dps_count = len(party['dps'])
        party_label = f"## Party {party_num} ({party_count}/5)"
        if party_num in [1, 2]:
            party_label += " 👑"
        
        description += f"{party_label}\n"
        
        # Tank
        description += f"🛡️ **Tank:**\n"
        if party_count == 0 or not party['tank']:
            if party_count == 0:
                description += f"• Empty\n"
            else:
                description += f"• ⚠️ Need a tank to fill in\n"
        else:
            description += f"• {party['tank']['name']}\n"
        
        # Support
        description += f"💚 **Support:**\n"
        if party_count == 0 or not party['support']:
            if party_count == 0:
                description += f"• Empty\n"
            else:
                description += f"• ⚠️ Need a support to fill in\n"
        else:
            description += f"• {party['support']['name']}\n"
        
        # DPS
        description += f"⚔️ **DPS ({dps_count}/3):**\n"
        if party_count == 0:
            description += f"• Empty\n"
        elif len(party['dps']) == 0:
            description += f"• ⚠️ Need DPS to fill in\n"
        else:
            dps_list = ', '.join([d['name'] for d in party['dps']])
            description += f"• {dps_list}"
            if len(party['dps']) < 3:
                description += " (Need more DPS)"
            description += "\n"
        
        description += "\n"
    
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
            not_in_party.append(f"{user['name']} ({user['role']})")
    
    if not_in_party:
        description += f"**📝 Signed up but not in party:** {', '.join(not_in_party)}\n"
    
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
            # Check current party status
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
        channel_id, _, _ = hunt_manager.get_hunt_by_message(interaction.message.id)
        if not channel_id:
            await interaction.response.send_message("Error: Hunt not found.", ephemeral=True)
            return
        
        view = RoleChangeView(self.hunt_index, int(channel_id))
        await interaction.response.send_message("Select your new role:", view=view, ephemeral=True)

class RoleChangeView(discord.ui.View):
    def __init__(self, hunt_index, channel_id):
        super().__init__(timeout=600)  # 10 minutes instead of 180 seconds
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
        super().__init__(timeout=600)  # 10 minutes instead of 180 seconds
        self.hunt_index = hunt_index
        self.role = role
        self.user = user
        self.channel_id = channel_id
        
        # Get current hunt data to check party status
        weekly = hunt_manager.get_weekly_hunts(channel_id)
        hunt = weekly['hunts'][hunt_index] if weekly and hunt_index < len(weekly['hunts']) else None
        
        for i in range(1, 9):
            # Check if this party slot is full for this role
            is_full = False
            if hunt:
                party = hunt['parties'].get(str(i))
                if role == 'tank' and party['tank']:
                    is_full = True
                elif role == 'support' and party['support']:
                    is_full = True
                elif role == 'dps' and len(party['dps']) >= 3:
                    is_full = True
            
            button = discord.ui.Button(
                label=f"Party {i}",
                style=discord.ButtonStyle.primary if i not in [1, 2] else discord.ButtonStyle.secondary,
                custom_id=f"party_{i}_{hunt_index}",
                emoji="👑" if i in [1, 2] else "🎯",
                disabled=is_full
            )
            button.callback = self.create_party_callback(i)
            self.add_item(button)
        
        # Add Un-Sign Up button
        unsignup_button = discord.ui.Button(
            label="Un-Sign Up (Remove Role)",
            style=discord.ButtonStyle.danger,
            emoji="🚫"
        )
        unsignup_button.callback = self.unsignup_callback
        self.add_item(unsignup_button)
        
        # Add leave party button
        leave_button = discord.ui.Button(
            label="Leave My Party",
            style=discord.ButtonStyle.secondary,
            emoji="❌"
        )
        leave_button.callback = self.leave_party_callback
        self.add_item(leave_button)
    
    def create_party_callback(self, party_num):
        async def callback(interaction: discord.Interaction):
            user_role_names = [role.name for role in interaction.user.roles]
            success, message = hunt_manager.join_party(
                self.channel_id,
                self.hunt_index,
                party_num,
                interaction.user.id,
                interaction.user.display_name,
                user_role_names
            )
            
            # Add current status to message
            if success:
                party_num_current, role_current = hunt_manager.get_user_party(self.channel_id, self.hunt_index, interaction.user.id)
                if party_num_current:
                    message += f"\n\n**Current Status:** You are now in Party {party_num_current} as {role_current}."
            
            await interaction.response.send_message(message, ephemeral=True)
            if success:
                await update_hunt_message(self.channel_id, self.hunt_index)
        return callback
    
    async def unsignup_callback(self, interaction: discord.Interaction):
        success, message = hunt_manager.remove_user(
            self.channel_id,
            self.hunt_index,
            interaction.user.id
        )
        await interaction.response.send_message(message, ephemeral=True)
        if success:
            await update_hunt_message(self.channel_id, self.hunt_index)
    
    async def leave_party_callback(self, interaction: discord.Interaction):
        success, message = hunt_manager.leave_party(
            self.channel_id,
            self.hunt_index,
            interaction.user.id
        )
        await interaction.response.send_message(message, ephemeral=True)
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
    except Exception as e:
        print(f"Error updating message: {e}")

@bot.event
async def on_ready():
    print(f'{bot.user} is now online!')
    
    for weekly in hunt_manager.weekly_hunts.values():
        for idx, hunt in enumerate(weekly['hunts']):
            if hunt.get('message_id'):
                bot.add_view(HuntSignupView(idx), message_id=hunt['message_id'])
    
    check_weekly_reset.start()
    update_hunt_timers.start()
    check_hunt_reminders.start()

@tasks.loop(minutes=1)
async def check_weekly_reset():
    now = datetime.now(TIMEZONE)
    
    if now.weekday() == 0 and now.hour == 0 and now.minute == 0:
        for guild in bot.guilds:
            for channel in guild.text_channels:
                if channel.name == 'guild-hunt-organization':
                    await create_weekly_hunt_posts(channel)

@tasks.loop(minutes=1)
async def check_hunt_reminders():
    """Check if any hunts are starting in 30 minutes and send reminders"""
    now = datetime.now(TIMEZONE)
    
    for channel_id, weekly in hunt_manager.weekly_hunts.items():
        channel = bot.get_channel(int(channel_id))
        if not channel:
            continue
        
        for idx, hunt in enumerate(weekly['hunts']):
            # Skip if reminder already sent
            if hunt.get('reminder_sent', False):
                continue
            
            hunt_time = datetime.fromisoformat(hunt['hunt_time'])
            time_diff = hunt_time - now
            
            # Check if hunt is starting in 30 minutes (with 1 minute buffer)
            minutes_until = time_diff.total_seconds() / 60
            if 29 <= minutes_until <= 31:
                # Send reminder
                member_role = discord.utils.get(channel.guild.roles, name="Member")
                ping_text = f"{member_role.mention}" if member_role else "@everyone"
                
                embed = discord.Embed(
                    title="⚠️ Guild Hunt Starting Soon! ⚠️",
                    description=f"**{hunt['label']}** starts in **30 minutes**!\n\nMake sure you're in your party and ready to go!",
                    color=discord.Color.orange()
                )
                
                # List parties with members
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
                
                # Mark reminder as sent
                hunt['reminder_sent'] = True
                hunt_manager.save_data()

@tasks.loop(minutes=5)
async def update_hunt_timers():
    for channel_id, weekly in hunt_manager.weekly_hunts.items():
        for idx in range(len(weekly['hunts'])):
            await update_hunt_message(int(channel_id), idx)

async def create_weekly_hunt_posts(channel):
    # Retrieve old message IDs before creating new hunts
    weekly_hunts_data = hunt_manager.get_weekly_hunts(channel.id)
    old_message_ids = []
    if weekly_hunts_data:
        for hunt in weekly_hunts_data.get('hunts', []):
            if hunt.get('message_id'):
                old_message_ids.append(hunt['message_id'])
    
    hunt_id = hunt_manager.create_weekly_hunts(channel.id)
    weekly = hunt_manager.get_weekly_hunts(channel.id)
    
    # 1. Delete Old Messages
    for msg_id in old_message_ids:
        try:
            message = await channel.fetch_message(msg_id)
            await message.delete()
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"Error deleting old message {msg_id}: {e}")
    
    # 2. Post New Messages
    member_role = discord.utils.get(channel.guild.roles, name="Member")
    ping_text = f"{member_role.mention} Guild Hunts for this week!" if member_role else "Guild Hunts for this week!"
    
    await channel.send(ping_text)
    
    for idx, hunt in enumerate(weekly['hunts']):
        embed = create_hunt_embed(hunt, idx)
        view = HuntSignupView(idx)
        message = await channel.send(embed=embed, view=view)
        
        hunt['message_id'] = message.id
        hunt_manager.save_data()

@bot.command()
@commands.has_permissions(administrator=True)
async def createweeklyhunts(ctx):
    """Create weekly hunt signups (Admin only)"""
    # Retrieve old message IDs before creating new hunts
    weekly_hunts_data = hunt_manager.get_weekly_hunts(ctx.channel.id)
    old_message_ids = []
    if weekly_hunts_data:
        for hunt in weekly_hunts_data.get('hunts', []):
            if hunt.get('message_id'):
                old_message_ids.append(hunt['message_id'])
    
    hunt_id = hunt_manager.create_weekly_hunts(ctx.channel.id)
    weekly = hunt_manager.get_weekly_hunts(ctx.channel.id)
    
    # 1. Delete Old Messages
    for msg_id in old_message_ids:
        try:
            message = await ctx.channel.fetch_message(msg_id)
            await message.delete()
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"Error deleting old message {msg_id}: {e}")
    
    # 2. Post New Messages
    member_role = discord.utils.get(ctx.guild.roles, name="Member")
    ping_text = f"{member_role.mention} Guild Hunts for this week!" if member_role else "Guild Hunts for this week!"
    
    await ctx.send(ping_text)
    
    for idx, hunt in enumerate(weekly['hunts']):
        embed = create_hunt_embed(hunt, idx)
        view = HuntSignupView(idx)
        message = await ctx.send(embed=embed, view=view)
        
        hunt['message_id'] = message.id
        hunt_manager.save_data()
    
    await ctx.message.delete()

bot.run(os.getenv("BOT_TOKEN"))
