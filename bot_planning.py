import discord
from discord import app_commands, ui, Interaction
from discord.utils import get
import requests
import datetime
import asyncio

# --- Import secrets ---
try:
    from config import (
        PLANNING_BOT_TOKEN, 
        GUILD_ID, 
        PLANNING_CHANNEL_ID,
        ACTIVE_CATEGORY_ID,
        API_BASE_URL
    )
except ImportError:
    print("FATAL ERROR: config.py not found.")
    print("Please create config.py and add your secrets.")
    exit()

# --- Import UI ---
from bot_ui import ProjectCreateModal

# --- BOT SETUP ---
intents = discord.Intents.default()
intents.members = True # For user dropdowns
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# --- ================================== ---
# --- PLANNING DASHBOARD & BUTTONS
# --- ================================== ---

class PlanningDashboardView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(
        label="🚀 Create New Project", 
        style=discord.ButtonStyle.green, 
        custom_id="persistent_create_project"
    )
    async def create_project_button(self, interaction: Interaction, button: ui.Button):
        # 1. Get the Google Sheet URL from the API (or config)
        # For now, we'll just link to the main sheet
        try:
            from config import SHEET_URL
            sheet_url = SHEET_URL
        except ImportError:
            sheet_url = "https://docs.google.com/spreadsheets/"

        # 2. Open the Project Creation Modal
        modal = ProjectCreateModal(
            api_url=API_BASE_URL,
            active_category_id=ACTIVE_CATEGORY_ID,
            sheet_url=sheet_url
        )
        await interaction.response.send_modal(modal)

# --- ================================== ---
# --- BOT COMMANDS & EVENTS
# --- ================================== ---

@tree.command(
    name="setup-planning-dashboard", 
    description="[Admin] Posts the main planning dashboard.",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.checks.has_permissions(administrator=True)
async def setup_dashboard(interaction: Interaction):
    if interaction.channel_id != PLANNING_CHANNEL_ID:
        await interaction.response.send_message(f"This command can only be used in the <#{PLANNING_CHANNEL_ID}> channel.", ephemeral=True)
        return

    await interaction.response.defer()
    
    # 0. Clear old messages from this bot
    await interaction.channel.purge(limit=10, check=lambda m: m.author == client.user)
    
    # 1. Get G-Sheet URL
    try:
        from config import SHEET_URL
        sheet_url = SHEET_URL
    except ImportError:
        sheet_url = "https://docs.google.com/spreadsheets/"

    # 2. Post the new sticky message
    embed = discord.Embed(
        title="Project Planning Hub", 
        description="Use the button below to start a new project. All active projects are tracked in the Google Sheet.",
        color=discord.Color.dark_green()
    )
    embed.add_field(name="Project G-Sheet Link", value=f"[Open Sheet]({sheet_url})", inline=False)
    
    view = PlanningDashboardView()
    await interaction.channel.send(embed=embed, view=view)
    await interaction.delete_original_response() # Delete the "..." message


@client.event
async def on_message(message: discord.Message):
    # This bot has a simple sticky message logic
    
    # Ignore bots, DMs, and non-planning channels
    if message.author.bot or not message.guild or message.channel_id != PLANNING_CHANNEL_ID:
        return
        
    # Wait a sec for the user's message to send
    await asyncio.sleep(1)
    
    # 1. Find the bot's last message
    last_bot_message = None
    async for msg in message.channel.history(limit=10):
        if msg.author == client.user:
            last_bot_message = msg
            break
            
    # 2. If it exists, delete it
    if last_bot_message:
        try:
            await last_bot_message.delete()
        except discord.NotFound:
            pass # Already gone
        except Exception as e:
            print(f"PLANNING BOT ERROR (on_message): Could not delete old sticky: {e}")

    # 3. Post a new one
    try:
        from config import SHEET_URL
        sheet_url = SHEET_URL
    except ImportError:
        sheet_url = "https://docs.google.com/spreadsheets/"

    embed = discord.Embed(
        title="Project Planning Hub", 
        description="Use the button below to start a new project. All active projects are tracked in the Google Sheet.",
        color=discord.Color.dark_green()
    )
    embed.add_field(name="Project G-Sheet Link", value=f"[Open Sheet]({sheet_url})", inline=False)
    
    view = PlanningDashboardView()
    await message.channel.send(embed=embed, view=view)


@client.event
async def on_ready():
    # Register the persistent view
    client.add_view(PlanningDashboardView())
    
    await tree.sync(guild=discord.Object(id=GUILD_ID))
    print(f'Logged in as {client.user} (Planning Bot)')
    
    # Ensure sticky message is present on startup
    channel = client.get_channel(PLANNING_CHANNEL_ID)
    if channel:
        await on_message(await channel.fetch_message(channel.last_message_id))

client.run(PLANNING_BOT_TOKEN)