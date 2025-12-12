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
        if os.path.exists('hunts.json'):
            with open('hunts.json', 'r') as f:
                self.weekly_hunts = json.load(f)
    
    def save_data(self):
        with open('hunts.json', 'w') as f:
            json.dump(self.weekly_hunts, f, indent=4)
    
    def create_weekly_hunts(self, channel_id):
        hunt_id = str(channel_id)
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
                'signed_up': [],  # List of users who signed up
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
                }
            })
        
        self.weekly_hunts[hunt_id] = {
            'channel_id': channel_id,
            'hunts': hunts,
            'message_id': None,
            'created_at': now.isoformat()
        }
        self.save_data()
        return hunt_id
    
    def get_weekly_hunts(self, channel_id):
        return self.weekly_hunts.get(str(channel_id))
    
    def signup(self, channel_id, hunt_index, user_id, username, role, is_replacement=False):
        weekly = self.get_weekly_hunts(channel_id)
        if not weekly or hunt_index >= len(weekly['hunts']):
            return False, "Invalid hunt selection."
        
        hunt = weekly['hunts'][hunt_index]
        
        # If not replacement, remove user from all lists in this specific hunt
        if not is_replacement:
            self.remove_user_from_hunt(hunt, user_id)
        
        user_data = {'id': user_id, 'name': username, 'role': role}
        
        # If replacement, try to auto-assign to a party that needs this role
        if is_replacement:
            assigned = False
            for party_num in range(1, 9):
                party = hunt['parties'][str(party_num)]
                # Check if party has at least 1 person and is missing this role
                party_count = (1 if party['tank'] else 0) + (1 if party['support'] else 0) + len(party['dps'])
                
                if party_count > 0:  # Party has at least one person
                    # Check if user is already in THIS specific party
                    user_in_this_party = (party['tank'] and party['tank']['id'] == user_id) or \
                                         (party['support'] and party['support']['id'] == user_id) or \
                                         any(d['id'] == user_id for d in party['dps'])
                    
                    if user_in_this_party:
                        continue  # Skip this party, user is already here
                    
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
            # Normal signup
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
    
    def join_party(self, channel_id, hunt_index, party_number, user_id, username, role, user_roles):
        weekly = self.get_weekly_hunts(channel_id)
        if not weekly or hunt_index >= len(weekly['hunts']):
            return False, "Invalid hunt selection."
        
        hunt = weekly['hunts'][hunt_index]
        party = hunt['parties'].get(str(party_number))
        
        if not party:
            return False, "Invalid party number."
        
        # Check if user is signed up first
        user_signup = next((u for u in hunt['signed_up'] if u['id'] == user_id), None)
        if not user_signup:
            return False, "You must sign up for the hunt first before joining a party!"
        
        # Check priority roles for Party 1 and 2
        if party_number in [1, 2]:
            has_priority = any(role_name in PRIORITY_ROLES for role_name in user_roles)
            if not has_priority:
                return False, f"Party {party_number} is reserved for @Frontrunner, @Envoy, @Strategist, @GM, @Quartermaster, @Administrator, and @Vice Master members only."
        
        # Remove user from all parties in this hunt
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
    
    def leave_party(self, channel_id, hunt_index, user_id):
        weekly = self.get_weekly_hunts(channel_id)
        if not weekly or hunt_index >= len(weekly['hunts']):
            return False, "Invalid hunt selection."
        
        hunt = weekly['hunts'][hunt_index]
        
        # Remove from all parties
        for p in hunt['parties'].values():
            if p['tank'] and p['tank']['id'] == user_id:
                p['tank'] = None
            if p['support'] and p['support']['id'] == user_id:
                p['support'] = None
            p['dps'] = [d for d in p['dps'] if d['id'] != user_id]
        
        self.save_data()
        return True, "Left your current party!"
    
    def remove_user_from_hunt(self, hunt, user_id):
        hunt['signed_up'] = [u for u in hunt['signed_up'] if u['id'] != user_id]
        hunt['tentative'] = [u for u in hunt['tentative'] if u['id'] != user_id]
        
        # Also remove from all parties
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

def create_weekly_embed(weekly_data):
    now = datetime.now(TIMEZONE)
    
    description = "**Guild Hunt Signups for This Week!**\n"
    description += "Sign up first, then choose your party (5 players per party: 1 Tank, 1 Support, 3 DPS)\n"
    description += "*Parties 1 & 2 are reserved for @Frontrunner, @Envoy, @Strategist, @GM, @Quartermaster, @Administrator, and @Vice Master*\n\n"
    
    for idx, hunt in enumerate(weekly_data['hunts']):
        hunt_time = datetime.fromisoformat(hunt['hunt_time'])
        time_diff = hunt_time - now
        
        # Calculate time remaining
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
        
        description += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        description += f"**{hunt['label']}**\n"
        description += f"📅 <t:{timestamp}:F> (shows your local time)\n"
        description += f"{time_str} | Signed up: **{total_signups}**\n\n"
        
        # Show parties
        for party_num in range(1, 9):
            party = hunt['parties'][str(party_num)]
            
            # Calculate party status
            party_count = (1 if party['tank'] else 0) + (1 if party['support'] else 0) + len(party['dps'])
            
            # Determine display text for each role
            if party_count == 0:
                # Empty party - show "Empty" for all
                tank_name = "Empty"
                support_name = "Empty"
                dps_names = "Empty"
            else:
                # Party has at least one person - show "Need X to fill in" for missing roles
                tank_name = party['tank']['name'] if party['tank'] else "Need a tank to fill in"
                support_name = party['support']['name'] if party['support'] else "Need a support to fill in"
                
                if len(party['dps']) == 0:
                    dps_names = "Need DPS to fill in"
                elif len(party['dps']) < 3:
                    dps_names = ', '.join([d['name'] for d in party['dps']]) + " (Need more DPS)"
                else:
                    dps_names = ', '.join([d['name'] for d in party['dps']])
            
            dps_count = len(party['dps'])
            
            party_label = f"**Party {party_num}** ({party_count}/5)"
            if party_num in [1, 2]:
                party_label += " 👑"
            
            description += f"{party_label}\n"
            description += f"🛡️ Tank: {tank_name} | 💚 Support: {support_name} | ⚔️ DPS ({dps_count}/3): {dps_names}\n"
        
        # Not in party yet
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
            description += f"\n**Signed up but not in party:** {', '.join(not_in_party)}\n"
        
        # Tentative
        if hunt['tentative']:
            tent_names = ', '.join([u['name'] for u in hunt['tentative']])
            description += f"❓ **Tentative:** {tent_names}\n"
        
        description += "\n"
    
    embed = discord.Embed(
        title="🏹 Guild Hunt Signups 🏹",
        description=description,
        color=discord.Color.green()
    )
    
    embed.set_footer(text="Times shown in your local timezone • Select a hunt day first, then choose your role!")
    return embed

class HuntDaySelect(discord.ui.Select):
    def __init__(self, weekly_data):
        options = []
        for idx, hunt in enumerate(weekly_data['hunts']):
            total = len(hunt['signed_up'])
            options.append(
                discord.SelectOption(
                    label=hunt['label'],
                    description=f"Signups: {total}",
                    value=str(idx),
                    emoji="📅"
                )
            )
        
        super().__init__(
            placeholder="Select a hunt day...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="hunt_day_select"
        )
    
    async def callback(self, interaction: discord.Interaction):
        hunt_index = int(self.values[0])
        view = HuntRoleView(hunt_index)
        await interaction.response.send_message(
            f"Selected hunt day. Now choose your action:",
            view=view,
            ephemeral=True
        )

class HuntDayView(discord.ui.View):
    def __init__(self, weekly_data):
        super().__init__(timeout=None)
        self.add_item(HuntDaySelect(weekly_data))

class HuntRoleView(discord.ui.View):
    def __init__(self, hunt_index):
        super().__init__(timeout=180)
        self.hunt_index = hunt_index
    
    @discord.ui.button(label="Tank", style=discord.ButtonStyle.primary, emoji="🛡️", row=0)
    async def tank_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        success, message = hunt_manager.signup(
            interaction.channel.id,
            self.hunt_index,
            interaction.user.id,
            interaction.user.display_name,
            'tank',
            is_replacement=False
        )
        
        if success:
            # Show party selection
            view = PartySelectView(self.hunt_index, 'tank', interaction.user)
            await interaction.response.send_message(message + "\nSelect your party:", view=view, ephemeral=True)
            await update_weekly_message(interaction.channel.id)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    
    @discord.ui.button(label="Support", style=discord.ButtonStyle.success, emoji="💚", row=0)
    async def support_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        success, message = hunt_manager.signup(
            interaction.channel.id,
            self.hunt_index,
            interaction.user.id,
            interaction.user.display_name,
            'support',
            is_replacement=False
        )
        
        if success:
            view = PartySelectView(self.hunt_index, 'support', interaction.user)
            await interaction.response.send_message(message + "\nSelect your party:", view=view, ephemeral=True)
            await update_weekly_message(interaction.channel.id)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    
    @discord.ui.button(label="DPS", style=discord.ButtonStyle.danger, emoji="⚔️", row=0)
    async def dps_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        success, message = hunt_manager.signup(
            interaction.channel.id,
            self.hunt_index,
            interaction.user.id,
            interaction.user.display_name,
            'dps',
            is_replacement=False
        )
        
        if success:
            view = PartySelectView(self.hunt_index, 'dps', interaction.user)
            await interaction.response.send_message(message + "\nSelect your party:", view=view, ephemeral=True)
            await update_weekly_message(interaction.channel.id)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    
    @discord.ui.button(label="Replacement Tank", style=discord.ButtonStyle.primary, emoji="🔄", row=1)
    async def replacement_tank_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        success, message = hunt_manager.signup(
            interaction.channel.id,
            self.hunt_index,
            interaction.user.id,
            interaction.user.display_name,
            'tank',
            is_replacement=True
        )
        await interaction.response.send_message(message, ephemeral=True)
        if success:
            await update_weekly_message(interaction.channel.id)
    
    @discord.ui.button(label="Replacement Support", style=discord.ButtonStyle.success, emoji="🔄", row=1)
    async def replacement_support_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        success, message = hunt_manager.signup(
            interaction.channel.id,
            self.hunt_index,
            interaction.user.id,
            interaction.user.display_name,
            'support',
            is_replacement=True
        )
        await interaction.response.send_message(message, ephemeral=True)
        if success:
            await update_weekly_message(interaction.channel.id)
    
    @discord.ui.button(label="Replacement DPS", style=discord.ButtonStyle.danger, emoji="🔄", row=1)
    async def replacement_dps_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        success, message = hunt_manager.signup(
            interaction.channel.id,
            self.hunt_index,
            interaction.user.id,
            interaction.user.display_name,
            'dps',
            is_replacement=True
        )
        await interaction.response.send_message(message, ephemeral=True)
        if success:
            await update_weekly_message(interaction.channel.id)
    
    @discord.ui.button(label="Tentative", style=discord.ButtonStyle.secondary, emoji="❓", row=2)
    async def tentative_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        success, message = hunt_manager.signup_tentative(
            interaction.channel.id,
            self.hunt_index,
            interaction.user.id,
            interaction.user.display_name
        )
        await interaction.response.send_message(message, ephemeral=True)
        if success:
            await update_weekly_message(interaction.channel.id)
    
    @discord.ui.button(label="Leave Hunt", style=discord.ButtonStyle.secondary, emoji="❌", row=2)
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        success, message = hunt_manager.remove_user(
            interaction.channel.id,
            self.hunt_index,
            interaction.user.id
        )
        await interaction.response.send_message(message, ephemeral=True)
        if success:
            await update_weekly_message(interaction.channel.id)

class PartySelectView(discord.ui.View):
    def __init__(self, hunt_index, role, user):
        super().__init__(timeout=180)
        self.hunt_index = hunt_index
        self.role = role
        self.user = user
        
        # Add party buttons
        for i in range(1, 9):
            button = discord.ui.Button(
                label=f"Party {i}",
                style=discord.ButtonStyle.primary if i not in [1, 2] else discord.ButtonStyle.secondary,
                custom_id=f"party_{i}",
                emoji="👑" if i in [1, 2] else "🎯"
            )
            button.callback = self.create_party_callback(i)
            self.add_item(button)
        
        # Add leave party button
        leave_button = discord.ui.Button(
            label="Leave Party",
            style=discord.ButtonStyle.danger,
            emoji="❌"
        )
        leave_button.callback = self.leave_party_callback
        self.add_item(leave_button)
    
    def create_party_callback(self, party_num):
        async def callback(interaction: discord.Interaction):
            user_role_names = [role.name for role in interaction.user.roles]
            success, message = hunt_manager.join_party(
                interaction.channel.id,
                self.hunt_index,
                party_num,
                interaction.user.id,
                interaction.user.display_name,
                self.role,
                user_role_names
            )
            await interaction.response.send_message(message, ephemeral=True)
            if success:
                await update_weekly_message(interaction.channel.id)
        return callback
    
    async def leave_party_callback(self, interaction: discord.Interaction):
        success, message = hunt_manager.leave_party(
            interaction.channel.id,
            self.hunt_index,
            interaction.user.id
        )
        await interaction.response.send_message(message, ephemeral=True)
        if success:
            await update_weekly_message(interaction.channel.id)

async def update_weekly_message(channel_id):
    weekly = hunt_manager.get_weekly_hunts(channel_id)
    if not weekly or not weekly.get('message_id'):
        return
    
    channel = bot.get_channel(weekly['channel_id'])
    if not channel:
        return
    
    try:
        message = await channel.fetch_message(weekly['message_id'])
        embed = create_weekly_embed(weekly)
        view = HuntDayView(weekly)
        await message.edit(embed=embed, view=view)
    except Exception as e:
        print(f"Error updating message: {e}")

@bot.event
async def on_ready():
    print(f'{bot.user} is now online!')
    check_weekly_reset.start()
    update_hunt_timers.start()

@tasks.loop(minutes=1)
async def check_weekly_reset():
    now = datetime.now(TIMEZONE)
    
    # Check if it's Monday at 00:00
    if now.weekday() == 0 and now.hour == 0 and now.minute == 0:
        for guild in bot.guilds:
            for channel in guild.text_channels:
                if channel.name == 'guild-hunt-organization':
                    await create_weekly_hunt_post(channel)

@tasks.loop(minutes=5)
async def update_hunt_timers():
    for hunt_id, weekly in hunt_manager.weekly_hunts.items():
        await update_weekly_message(weekly['channel_id'])

async def create_weekly_hunt_post(channel):
    hunt_id = hunt_manager.create_weekly_hunts(channel.id)
    weekly = hunt_manager.get_weekly_hunts(channel.id)
    
    embed = create_weekly_embed(weekly)
    view = HuntDayView(weekly)
    message = await channel.send(embed=embed, view=view)
    
    weekly['message_id'] = message.id
    hunt_manager.save_data()

@bot.command()
@commands.has_permissions(administrator=True)
async def createweeklyhunts(ctx):
    """Create weekly hunt signups (Admin only)"""
    hunt_id = hunt_manager.create_weekly_hunts(ctx.channel.id)
    weekly = hunt_manager.get_weekly_hunts(ctx.channel.id)
    
    embed = create_weekly_embed(weekly)
    view = HuntDayView(weekly)
    message = await ctx.send(embed=embed, view=view)
    
    weekly['message_id'] = message.id
    hunt_manager.save_data()
    
    await ctx.send("✅ Weekly hunts created!", delete_after=5)

# Run the bot
bot.run(os.getenv('BOT_TOKEN'))
