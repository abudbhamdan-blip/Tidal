import discord
from discord import app_commands, ui, Interaction
from discord.utils import get
import requests
import datetime
import asyncio
from discord.ext import tasks

from shared.thread_titles import format_thread_title

# --- Import secrets ---
try:
    from config import (
        PROJECTS_BOT_TOKEN, 
        GUILD_ID, 
        ACTIVE_CATEGORY_ID,
        FINISHED_CATEGORY_ID,
        API_BASE_URL
    )
except ImportError:
    print("FATAL ERROR: config.py not found.")
    print("Please create config.py and add your secrets.")
    exit()

# --- Import UI ---
from bot_ui import (
    ProjectControlView, 
    WorkOrderCreateModal, 
    ProjectEditModal,
    WorkOrderControlView
)

# --- BOT SETUP ---
intents = discord.Intents.default()
intents.members = True # For user lookups
intents.message_content = True # For sticky messages
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# --- ================================== ---
# --- HELPER FUNCTIONS
# --- ================================== ---

def get_project_id_from_channel(channel: discord.TextChannel) -> str:
    """Extracts the ProjectID from a channel's topic."""
    if not channel.topic or not channel.topic.startswith("ProjectID:"):
        return None
    return channel.topic.split("ProjectID:")[1].strip()

def get_wo_id_from_thread(thread: discord.Thread) -> str:
    """Extracts the WorkOrderID from a thread's topic."""
    if not thread.topic or not thread.topic.startswith("WorkOrderID:"):
        return None
    return thread.topic.split("WorkOrderID:")[1].strip()

# --- ================================== ---
# --- TIMER & SCHEDULER LOOP
# --- ================================== ---

@tasks.loop(seconds=60)
async def timer_loop():
    """Updates all active work order timers every minute."""
    await client.wait_until_ready()
    
    try:
        response = requests.get(f"{API_BASE_URL}/workorders/inprogress")
        response.raise_for_status()
        active_wos = response.json().get("workorders", [])
    except Exception as e:
        print(f"TIMER LOOP ERROR: Could not fetch active WOs: {e}")
        return

    guild = client.get_guild(GUILD_ID)
    if not guild:
        return

    for wo_data in active_wos:
        try:
            thread_id = int(wo_data.get("ThreadID"))
            thread = guild.get_thread(thread_id)
            if not thread:
                print(f"TIMER LOOP: Thread {thread_id} not found, skipping.")
                continue
            
            # 1. Find the sticky message
            async for msg in thread.history(limit=5):
                if msg.author == client.user and msg.embeds and "Work Order:" in msg.embeds[0].title:
                    # 2. Get updated WO data (to refresh timer)
                    wo_id = wo_data.get("WorkOrderID")
                    response = requests.get(f"{API_BASE_URL}/workorder/{wo_id}")
                    if response.status_code != 200:
                        continue # Skip this one
                    
                    updated_wo_data = response.json().get("workorder", {})
                    
                    # 3. Re-build embed and view
                    embed = WorkOrderControlView.build_embed(updated_wo_data)
                    view = WorkOrderControlView(api_url=API_BASE_URL, project_data={}, wo_data=updated_wo_data)
                    
                    # 4. Edit the message
                    await msg.edit(embed=embed, view=view)
                    break # Move to next WO
                    
        except Exception as e:
            print(f"TIMER LOOP ERROR: Failed to update timer for WO {wo_data.get('WorkOrderID')}: {e}")

@tasks.loop(hours=24)
async def update_project_titles_loop():
    """Updates all project channel titles with (Days Left) every 24 hours."""
    await client.wait_until_ready()
    print("SCHEDULER: Running daily project title update...")
    
    try:
        response = requests.get(f"{API_BASE_URL}/projects/active")
        response.raise_for_status()
        active_projects = response.json().get("projects", [])
    except Exception as e:
        print(f"SCHEDULER ERROR: Could not fetch active projects: {e}")
        return
        
    guild = client.get_guild(GUILD_ID)
    if not guild:
        return

    today = datetime.date.today()
    for proj in active_projects:
        try:
            channel = guild.get_channel(int(proj.get("ChannelID")))
            if not channel:
                continue
                
            due_date = datetime.datetime.strptime(proj.get("DueDate"), '%Y-%m-%d').date()
            days_left = (due_date - today).days
            new_title = f"({days_left}d) {proj.get('Title')}"
            
            if channel.name != new_title:
                await channel.edit(name=new_title)
                
        except Exception as e:
            print(f"SCHEDULER ERROR: Failed to update title for {proj.get('Title')}: {e}")
    
    print("SCHEDULER: Daily project title update complete.")

# --- ================================== ---
# --- BOT EVENTS
# --- ================================== ---

@client.event
async def on_ready():
    await client.wait_until_ready()

    # Load active projects so each persistent view is registered with its ProjectID
    project_lookup = {}
    try:
        response = requests.get(f"{API_BASE_URL}/projects/active")
        response.raise_for_status()
        active_projects = response.json().get("projects", [])
    except Exception as e:
        print(f"ON_READY ERROR: Could not load active projects: {e}")
        active_projects = []

    for project in active_projects:
        project_id = project.get("ProjectID")
        if not project_id:
            continue
        project_lookup[project_id] = project
        client.add_view(ProjectControlView(api_url=API_BASE_URL, project_data=project))

    # Load actionable work orders so their persistent views have the proper IDs
    try:
        response = requests.get(f"{API_BASE_URL}/workorders/active")
        response.raise_for_status()
        active_workorders = response.json().get("workorders", [])
    except Exception as e:
        print(f"ON_READY ERROR: Could not load active work orders: {e}")
        active_workorders = []

    for wo in active_workorders:
        wo_id = wo.get("WorkOrderID")
        if not wo_id:
            continue

        project_data = project_lookup.get(wo.get("ProjectID"), {})
        if not project_data and wo.get("ProjectID"):
            try:
                proj_resp = requests.get(f"{API_BASE_URL}/project/{wo.get('ProjectID')}")
                if proj_resp.status_code == 200:
                    project_data = proj_resp.json().get("project", {})
                    if project_data:
                        project_lookup[wo.get("ProjectID")] = project_data
            except Exception as e:
                print(f"ON_READY WARNING: Could not fetch project {wo.get('ProjectID')} for WO {wo_id}: {e}")
                project_data = {}

        client.add_view(WorkOrderControlView(api_url=API_BASE_URL, project_data=project_data, wo_data=wo))

    await tree.sync(guild=discord.Object(id=GUILD_ID))

    # Start background loops
    timer_loop.start()
    update_project_titles_loop.start()
    
    print(f'Logged in as {client.user} (Projects Bot)')
    print('Bot is running and loops have started.')

client.run(PROJECTS_BOT_TOKEN)