import discord
from discord import app_commands, ui, Interaction
from discord.utils import get
import requests
import datetime
from typing import Optional

from shared.thread_titles import format_thread_title

from bot_projects import format_thread_title

# --- ================================== ---
# --- 1. PLANNING BOT UI
# --- ================================== ---

class ProjectCreateModal(ui.Modal, title='Create New Project'):
    def __init__(self, api_url: str, active_category_id: int, sheet_url: str):
        super().__init__(timeout=600)
        self.API_BASE_URL = api_url
        self.ACTIVE_CATEGORY_ID = active_category_id
        self.SHEET_URL = sheet_url

        # Ensure dynamic defaults each time the modal is opened
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        self.due_date_input.default = tomorrow
        self.due_date_input.placeholder = f"e.g., {tomorrow}"
        self.kpi_input.required = False

    # --- Modal Fields ---
    title_input = ui.TextInput(
        label="Project Title",
        placeholder="e.g., New Wheelchair Grip v3"
    )
    deliverables_input = ui.TextInput(
        label="Deliverables & Why",
        style=discord.TextStyle.paragraph,
        placeholder="e.g., A 3D-printable model of a new grip, because the old one was slippery."
    )
    kpi_input = ui.TextInput(
        label="Key Performance Indicator (KPI)",
        placeholder="e.g., 3 user tests show a 50% improvement in grip satisfaction."
    )
    due_date_input = ui.TextInput(
        label="Target Due Date (YYYY-MM-DD)",
        placeholder="Select a date"
    )
    accountable_input = ui.UserSelect(
        placeholder="Who is Accountable for this project?",
        min_values=1,
        max_values=1
    )

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        # 1. Data Validation
        try:
            due_date = datetime.datetime.strptime(self.due_date_input.value, '%Y-%m-%d').date()
            if due_date <= datetime.date.today():
                raise ValueError("Due Date must be in the future.")
        except ValueError as e:
            await interaction.followup.send(f"Error: Invalid Due Date. {e}. Please try again.", ephemeral=True)
            return

        accountable_user = self.accountable_input.values[0]
        
        # 2. Create Discord Channel
        guild = interaction.guild
        category = get(guild.categories, id=self.ACTIVE_CATEGORY_ID)
        if not category:
            await interaction.followup.send(f"Error: 'Active' category not found.", ephemeral=True)
            return
            
        # Calculate days left for title
        days_left = (due_date - datetime.date.today()).days
        channel_title = f"({days_left}d) {self.title_input.value}"
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            accountable_user: discord.PermissionOverwrite(read_messages=True, manage_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, manage_messages=True, manage_threads=True)
        }
        
        try:
            new_channel = await guild.create_text_channel(
                channel_title,
                category=category,
                overwrites=overwrites
            )
        except Exception as e:
            await interaction.followup.send(f"Error creating Discord channel: {e}", ephemeral=True)
            return

        # 3. Call API to create Project
        payload = {
            "ChannelID": str(new_channel.id),
            "Title": self.title_input.value,
            "Deliverables": self.deliverables_input.value,
            "KPI": self.kpi_input.value,
            "DueDate": self.due_date_input.value,
            "AccountableID": str(accountable_user.id)
        }
        
        try:
            response = requests.post(f"{self.API_BASE_URL}/project", json=payload)
            response.raise_for_status()
            project_data = response.json().get("project", {})
            project_id = project_data.get("ProjectID")
        except Exception as e:
            await new_channel.delete(reason="API call failed")
            await interaction.followup.send(f"Error creating project in API: {e}", ephemeral=True)
            return

        # 4. Set Channel Topic
        try:
            await new_channel.edit(topic=f"ProjectID: {project_id}")
        except Exception as e:
            print(f"UI WARNING: Could not set topic for channel {new_channel.id}: {e}")

        # 5. Post the sticky message in the new channel
        embed = ProjectControlView.build_embed(project_data)
        view = ProjectControlView(api_url=self.API_BASE_URL, project_data=project_data)
        message = await new_channel.send(embed=embed, view=view)
        try:
            await message.pin()
        except discord.HTTPException as e:
            print(f"UI WARNING: Could not pin project control message in {new_channel.id}: {e}")

        await interaction.followup.send(f"Success! Project channel created: {new_channel.mention}", ephemeral=True)


# --- ================================== ---
# --- 2. PROJECTS BOT UI
# --- ================================== ---

class ProjectControlView(ui.View):
    """Persistent view for the sticky message in a Project Channel."""
    def __init__(self, api_url: str, project_data: dict):
        super().__init__(timeout=None)
        self.API_BASE_URL = api_url
        
        # Add the ProjectID to the buttons
        project_id = project_data.get("ProjectID")
        if project_id:
            self.children[0].custom_id = f"proj_create_wo:{project_id}"
            self.children[1].custom_id = f"proj_edit:{project_id}"
            self.children[2].custom_id = f"proj_finish:{project_id}"

    @staticmethod
    def build_embed(project_data: dict) -> discord.Embed:
        """Helper to build the sticky project embed."""
        embed = discord.Embed(
            title=f"🚀 Project: {project_data.get('Title', 'N/A')}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Deliverables", value=project_data.get('Deliverables', 'N/A'), inline=False)
        kpi_value = project_data.get('KPI') or 'N/A'
        embed.add_field(name="KPI", value=kpi_value, inline=False)
        embed.add_field(name="Accountable", value=f"<@{project_data.get('AccountableID', 'N/A')}>", inline=True)
        embed.add_field(name="Due Date", value=project_data.get('DueDate', 'N/A'), inline=True)
        
        drive_url = project_data.get('DriveFolderURL')
        if drive_url:
            embed.add_field(name="G-Drive Folder", value=f"[Link to Folder]({drive_url})", inline=True)
            
        embed.set_footer(text=f"ProjectID: {project_data.get('ProjectID', 'N/A')}")
        return embed

    @ui.button(label="Create Work Order", style=discord.ButtonStyle.green, custom_id="proj_create_wo_base")
    async def create_wo(self, interaction: Interaction, button: ui.Button):
        project_id = interaction.data["custom_id"].split(":")[-1]
        
        # Fetch project data to pass AccountableID
        response = requests.get(f"{self.API_BASE_URL}/project/{project_id}")
        if response.status_code != 200:
            await interaction.response.send_message("Error: Could not find project data.", ephemeral=True)
            return

        project_data = response.json().get("project", {})

        prompt_view = WorkOrderCreatePromptView(api_url=self.API_BASE_URL, project_data=project_data)
        await interaction.response.send_message(
            "Who should this work order be pushed to?",
            ephemeral=True,
            view=prompt_view
        )

    @ui.button(label="Edit Project", style=discord.ButtonStyle.blurple, custom_id="proj_edit_base")
    async def edit_project(self, interaction: Interaction, button: ui.Button):
        project_id = interaction.data["custom_id"].split(":")[-1]
        
        # Only the accountable person can edit
        response = requests.get(f"{self.API_BASE_URL}/project/{project_id}")
        if response.status_code != 200:
            await interaction.response.send_message("Error: Could not find project data.", ephemeral=True)
            return
            
        project_data = response.json().get("project", {})
        accountable_id = str(project_data.get("AccountableID"))
        
        if str(interaction.user.id) != accountable_id:
            await interaction.response.send_message(f"Only the accountable person (<@{accountable_id}>) can edit this project.", ephemeral=True)
            return
            
        modal = ProjectEditModal(api_url=self.API_BASE_URL, project_data=project_data)
        await interaction.response.send_modal(modal)

    @ui.button(label="Finish Project", style=discord.ButtonStyle.red, custom_id="proj_finish_base")
    async def finish_project(self, interaction: Interaction, button: ui.Button):
        project_id = interaction.data["custom_id"].split(":")[-1]

        # Only the accountable person can finish
        response = requests.get(f"{self.API_BASE_URL}/project/{project_id}")
        if response.status_code != 200:
            await interaction.response.send_message("Error: Could not find project data.", ephemeral=True)
            return
            
        project_data = response.json().get("project", {})
        accountable_id = str(project_data.get("AccountableID"))
        
        if str(interaction.user.id) != accountable_id:
            await interaction.response.send_message(f"Only the accountable person (<@{accountable_id}>) can finish this project.", ephemeral=True)
            return

        confirm_view = ProjectFinishConfirmView(
            control_view=self,
            project_id=project_id,
            project_data=project_data,
            original_message=interaction.message
        )

        await interaction.response.send_message(
            "Are you sure?",
            ephemeral=True,
            view=confirm_view
        )

    async def finish_project_confirm(
        self,
        interaction: Interaction,
        project_id: str,
        project_data: dict,
        original_message: discord.Message
    ) -> bool:
        try:
            response = requests.put(f"{self.API_BASE_URL}/project/{project_id}/finish")
            response.raise_for_status()

            from config import FINISHED_CATEGORY_ID
            category = get(original_message.guild.categories, id=FINISHED_CATEGORY_ID)
            if category:
                finish_date = datetime.date.today().isoformat()
                await original_message.channel.edit(
                    name=f"({finish_date})-{project_data.get('Title')}",
                    category=category,
                    topic=f"ProjectID: {project_id} (Finished)"
                )

            for item in self.children:
                item.disabled = True
            await original_message.edit(view=self)

            await original_message.channel.send("Project has been marked as Finished and archived!")
            return True
        except Exception as e:
            await interaction.followup.send(f"An error occurred while finishing the project: {e}", ephemeral=True)
            return False

class ProjectFinishConfirmView(ui.View):
    def __init__(
        self,
        control_view: ProjectControlView,
        project_id: str,
        project_data: dict,
        original_message: discord.Message
    ):
        super().__init__(timeout=60)
        self.control_view = control_view
        self.project_id = project_id
        self.project_data = project_data
        self.original_message = original_message

    @ui.button(label="Yes", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer(thinking=True, ephemeral=True)
        success = await self.control_view.finish_project_confirm(
            interaction,
            self.project_id,
            self.project_data,
            self.original_message
        )

        if success:
            await interaction.edit_original_response(content="Project marked as finished.", view=None)
        else:
            await interaction.edit_original_response(content="Could not finish the project.", view=None)

        self.stop()

    @ui.button(label="No", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: Interaction, button: ui.Button):
        await interaction.response.edit_message(content="Okay, keeping the project active.", view=None)
        self.stop()

class ProjectEditModal(ui.Modal, title='Edit Project Details'):
    def __init__(self, api_url: str, project_data: dict):
        super().__init__(timeout=600)
        self.API_BASE_URL = api_url
        self.project_id = project_data.get("ProjectID")
        self.project_data = project_data
        
        self.title_input = ui.TextInput(
            label="Project Title",
            default=project_data.get("Title")
        )
        self.deliverables_input = ui.TextInput(
            label="Deliverables & Why",
            style=discord.TextStyle.paragraph,
            default=project_data.get("Deliverables")
        )
        self.kpi_input = ui.TextInput(
            label="Key Performance Indicator (KPI)",
            default=project_data.get("KPI")
        )
        self.due_date_input = ui.TextInput(
            label="Target Due Date (YYYY-MM-DD)",
            default=project_data.get("DueDate")
        )
        
        self.add_item(self.title_input)
        self.add_item(self.deliverables_input)
        self.add_item(self.kpi_input)
        self.add_item(self.due_date_input)
        # Accountable person is a dropdown, so we can't add it here.
        # We'll make a more complex view for this later if needed.

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)

        payload = {
            "Title": self.title_input.value,
            "Deliverables": self.deliverables_input.value,
            "KPI": self.kpi_input.value,
            "DueDate": self.due_date_input.value
        }
        
        try:
            response = requests.put(f"{self.API_BASE_URL}/project/{self.project_id}", json=payload)
            response.raise_for_status()
            new_data = response.json().get("project")
            
            # Edit the sticky message
            embed = ProjectControlView.build_embed(new_data)
            await interaction.message.edit(embed=embed) # Assumes this is the sticky msg
            
            # Edit the channel title
            due_date = datetime.datetime.strptime(new_data.get("DueDate"), '%Y-%m-%d').date()
            days_left = (due_date - datetime.date.today()).days
            await interaction.channel.edit(name=f"({days_left}d) {new_data.get('Title')}")
            
            await interaction.followup.send("Project details updated!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Error updating project: {e}", ephemeral=True)


class _WorkOrderPushUserSelect(ui.UserSelect):
    def __init__(self, placeholder: str):
        super().__init__(placeholder=placeholder, min_values=0, max_values=1)

    async def callback(self, interaction: Interaction):
        selected = self.values[0] if self.values else None
        self.view.selected_user_id = str(selected.id) if selected else None
        if hasattr(self.view, "explicit_change"):
            self.view.explicit_change = True
        await interaction.response.defer(ephemeral=True)


class WorkOrderCreatePromptView(ui.View):
    def __init__(self, api_url: str, project_data: dict):
        super().__init__(timeout=120)
        self.API_BASE_URL = api_url
        self.project_data = project_data
        self.selected_user_id: Optional[str] = None

        placeholder = "Select a user to push this work order to (optional)."
        self.add_item(_WorkOrderPushUserSelect(placeholder=placeholder))

    async def _open_modal(self, interaction: Interaction, user_id: Optional[str]):
        modal = WorkOrderCreateModal(
            api_url=self.API_BASE_URL,
            project_data=self.project_data,
            pushed_to_user_id=user_id
        )
        await interaction.response.send_modal(modal)
        self.stop()

    @ui.button(label="Skip", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: Interaction, button: ui.Button):
        await self._open_modal(interaction, None)

    @ui.button(label="Continue", style=discord.ButtonStyle.green)
    async def continue_button(self, interaction: Interaction, button: ui.Button):
        await self._open_modal(interaction, self.selected_user_id)


class WorkOrderEditPromptView(ui.View):
    def __init__(self, control_view: 'WorkOrderControlView', original_message: discord.Message):
        super().__init__(timeout=120)
        self.control_view = control_view
        self.original_message = original_message
        self.selected_user_id: Optional[str] = control_view.wo_data.get("PushedToUserID") or None
        self.explicit_change = False

        if self.selected_user_id:
            placeholder = f"Currently pushed to <@{self.selected_user_id}>. Select a user to change."
        else:
            placeholder = "Select a user to push this work order to (optional)."

        self.add_item(_WorkOrderPushUserSelect(placeholder=placeholder))

    async def _open_modal(self, interaction: Interaction, user_id: Optional[str], update_push: bool):
        modal = WorkOrderEditModal(
            api_url=self.control_view.API_BASE_URL,
            wo_data=self.control_view.wo_data,
            project_data=self.control_view.project_data,
            control_view=self.control_view,
            original_message=self.original_message,
            pushed_to_user_id=user_id,
            update_push=update_push
        )
        await interaction.response.send_modal(modal)
        self.stop()

    @ui.button(label="Keep Current", style=discord.ButtonStyle.secondary)
    async def keep_current(self, interaction: Interaction, button: ui.Button):
        await self._open_modal(interaction, self.selected_user_id, update_push=False)

    @ui.button(label="Clear Assignment", style=discord.ButtonStyle.danger)
    async def clear_assignment(self, interaction: Interaction, button: ui.Button):
        self.selected_user_id = ""
        self.explicit_change = True
        await self._open_modal(interaction, "", update_push=True)

    @ui.button(label="Continue", style=discord.ButtonStyle.green)
    async def continue_button(self, interaction: Interaction, button: ui.Button):
        if not self.explicit_change:
            await interaction.response.send_message("Please select a user or choose 'Keep Current'.", ephemeral=True)
            return
        await self._open_modal(interaction, self.selected_user_id or "", update_push=True)

class WorkOrderCreateModal(ui.Modal, title='Create New Work Order'):
    def __init__(self, api_url: str, project_data: dict, pushed_to_user_id: Optional[str] = None):
        super().__init__(timeout=600)
        self.API_BASE_URL = api_url
        self.project_data = project_data
        self.pushed_to_user_id = pushed_to_user_id or ""

    title_input = ui.TextInput(
        label="Work Order Title",
        placeholder="e.g., 3D Print Grip v3.1"
    )
    deliverables_input = ui.TextInput(
        label="Deliverables & Why",
        style=discord.TextStyle.paragraph,
        placeholder="e.g., 1x 3D print in PETG, because we need to test the new ergonomics."
    )

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)

        pushed_to_user_id = self.pushed_to_user_id

        # 1. Create the Thread
        try:
            thread_title = f"({self.title_input.value})" # Initial title, no time
            thread = await interaction.channel.create_thread(
                name=thread_title,
                type=discord.ChannelType.public_thread
            )
        except Exception as e:
            await interaction.followup.send(f"Error creating thread: {e}", ephemeral=True)
            return

        # 2. Call API to create Work Order
        payload = {
            "ProjectID": self.project_data.get("ProjectID"),
            "ThreadID": str(thread.id),
            "Title": self.title_input.value,
            "Deliverables": self.deliverables_input.value,
            "PushedToUserID": pushed_to_user_id
        }
        
        try:
            response = requests.post(f"{self.API_BASE_URL}/workorder", json=payload)
            response.raise_for_status()
            wo_data = response.json().get("workorder", {})
            wo_id = wo_data.get("WorkOrderID")
        except Exception as e:
            await thread.delete(reason="API call failed")
            await interaction.followup.send(f"Error creating work order in API: {e}", ephemeral=True)
            return

        # 3. Set Thread Topic
        try:
            await thread.edit(topic=f"WorkOrderID: {wo_id}")
        except Exception as e:
            print(f"UI WARNING: Could not set topic for thread {thread.id}: {e}")

        # 4. Post the sticky message in the new thread
        embed = WorkOrderControlView.build_embed(wo_data)
        view = WorkOrderControlView(api_url=self.API_BASE_URL, project_data=self.project_data, wo_data=wo_data)
        control_message = await thread.send(embed=embed, view=view)
        try:
            await control_message.pin()
        except discord.HTTPException as e:
            print(f"UI WARNING: Could not pin work order control message in thread {thread.id}: {e}")
        
        # 5. Send confirmation
        await interaction.followup.send(f"Success! Work order thread created: {thread.mention}", ephemeral=True)
        await thread.send(f"Work order created by {interaction.user.mention}.")


# --- ================================== ---
# --- 3. WORK ORDER UI (INSIDE THREADS)
# --- ================================== ---

class WorkOrderControlView(ui.View):
    """Persistent view for the sticky message in a Work Order Thread."""
    def __init__(self, api_url: str, project_data: dict, wo_data: dict):
        super().__init__(timeout=None)
        self.API_BASE_URL = api_url
        self.project_data = project_data
        self.wo_data = wo_data
        self.wo_id = wo_data.get("WorkOrderID")
        
        # Set all custom IDs
        self.children[0].custom_id = f"wo_start:{self.wo_id}"
        self.children[1].custom_id = f"wo_edit:{self.wo_id}"
        self.children[2].custom_id = f"wo_cancel:{self.wo_id}"
        self.children[3].custom_id = f"wo_pause:{self.wo_id}"
        self.children[4].custom_id = f"wo_finish:{self.wo_id}"
        self.children[5].custom_id = f"wo_approve:{self.wo_id}"
        self.children[6].custom_id = f"wo_rework:{self.wo_id}"
        
        # Show/Hide buttons based on status
        status = wo_data.get("Status")
        self.toggle_buttons(status)

    def toggle_buttons(self, status: str):
        """Shows/hides buttons based on WO status."""
        all_buttons = (
            self.start_button,
            self.edit_button,
            self.cancel_button,
            self.pause_button,
            self.finish_button,
            self.approve_button,
            self.rework_button
        )

        for button in all_buttons:
            button.disabled = True

        self.clear_items()

        if status in {"Open", "Rework"}:
            for button in all_buttons[:3]:
                button.disabled = False
                self.add_item(button)
        elif status == "InProgress":
            for button in all_buttons[3:5]:
                button.disabled = False
                self.add_item(button)
        elif status == "InQA":
            for button in all_buttons[5:]:
                button.disabled = False
                self.add_item(button)

    @staticmethod
    def build_embed(wo_data: dict) -> discord.Embed:
        """Helper to build the sticky work order embed."""
        status = wo_data.get("Status", "N/A")
        embed = discord.Embed(
            title=f"Work Order: {wo_data.get('Title', 'N/A')}",
            description=f"**Status: {status}**\n\n{wo_data.get('Deliverables', 'N/A')}",
            color=discord.Color.orange()
        )
        
        if wo_data.get("SubfolderURL"):
            embed.add_field(name="G-Drive Subfolder", value=f"[Link to Folder]({wo_data.get('SubfolderURL')})")
            
        pushed_to = wo_data.get("PushedToUserID")
        if pushed_to:
            embed.add_field(name="Assigned To", value=f"<@{pushed_to}> (Training)")
            
        # Add Timer
        total_sec = int(float(wo_data.get('TotalTimeSeconds', 0)))
        if status == "InProgress":
            start_time_str = wo_data.get('CurrentStartTime')
            if start_time_str:
                start_time = datetime.datetime.fromisoformat(start_time_str)
                time_spent = (datetime.datetime.now(datetime.timezone.utc) - start_time).total_seconds()
                total_sec += int(time_spent)
        
        hours, remainder = divmod(total_sec, 3600)
        minutes, seconds = divmod(remainder, 60)
        timer_str = f"{hours:02}:{minutes:02}:{seconds:02}"
        embed.add_field(name="Total Time Logged", value=timer_str)

        embed.set_footer(text=f"WorkOrderID: {wo_data.get('WorkOrderID', 'N/A')}")
        return embed

    # --- ROW 1: OPEN ---
    @ui.button(label="Start", style=discord.ButtonStyle.green, custom_id="wo_start_base", row=0)
    async def start_button(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        
        # 1. Check if this is a Pushed WO
        pushed_to = self.wo_data.get("PushedToUserID")
        if pushed_to and str(interaction.user.id) != str(pushed_to):
            await interaction.followup.send(f"This is a training WO assigned to <@{pushed_to}>. Only they can start it.", ephemeral=True)
            return

        try:
            # 2. Call API
            payload = {"UserID": str(interaction.user.id)}
            requests.put(f"{self.API_BASE_URL}/workorder/{self.wo_id}/start", json=payload).raise_for_status()

            # 3. Update local cache & thread title
            response = requests.get(f"{self.API_BASE_URL}/workorder/{self.wo_id}")
            response.raise_for_status()
            new_data = response.json().get("workorder", {})

            self.wo_data = new_data
            self.wo_id = self.wo_data.get("WorkOrderID", self.wo_id)

            new_title = format_thread_title(self.wo_data, worker=interaction.user)
            await interaction.channel.edit(name=new_title[:100])

            # 4. Update Message (will be done by loop, but we do it once for responsiveness)

            embed = self.build_embed(self.wo_data)
            self.toggle_buttons(self.wo_data.get("Status"))
            await interaction.edit_original_response(embed=embed, view=self)

        except Exception as e:
            await interaction.followup.send(f"Error starting task: {e}", ephemeral=True)

    @ui.button(label="Edit Work Order", style=discord.ButtonStyle.grey, custom_id="wo_edit_base", row=0)
    async def edit_button(self, interaction: Interaction, button: ui.Button):
        # TODO: Add permissions check (creator or accountable)
        prompt_view = WorkOrderEditPromptView(control_view=self, original_message=interaction.message)
        await interaction.response.send_message(
            "Update the assignee before editing the work order details.",
            ephemeral=True,
            view=prompt_view
        )

    @ui.button(label="Cancel", style=discord.ButtonStyle.red, custom_id="wo_cancel_base", row=0)
    async def cancel_button(self, interaction: Interaction, button: ui.Button):
        confirm_view = WorkOrderCancelConfirmView(
            control_view=self,
            original_message=interaction.message
        )

        await interaction.response.send_message(
            "Are you sure?",
            ephemeral=True,
            view=confirm_view
        )

    async def cancel_work_order_confirm(self, interaction: Interaction, original_message: discord.Message) -> bool:
        try:
            # Update backend status first to ensure all clients see the cancellation.
            response = requests.put(f"{self.API_BASE_URL}/workorder/{self.wo_id}/cancel")
            response.raise_for_status()

            # Refresh local data from the source of truth.
            wo_response = requests.get(f"{self.API_BASE_URL}/workorder/{self.wo_id}")
            wo_response.raise_for_status()
            new_data = wo_response.json().get("workorder", {})
            if new_data:
                self.wo_data = new_data
                self.wo_id = self.wo_data.get("WorkOrderID", self.wo_id)

            await interaction.channel.edit(name=f"❌ (Cancelled) {self.wo_data.get('Title')}"[:100])
            await interaction.channel.send(f"Work order cancelled by {interaction.user.mention}.")

            embed = self.build_embed(self.wo_data)
            self.toggle_buttons(self.wo_data.get("Status"))

            await original_message.edit(embed=embed, view=self)
            return True
        except Exception as e:
            await interaction.followup.send(f"Error cancelling work order: {e}", ephemeral=True)
            return False

    # --- ROW 2: IN-PROGRESS ---
    @ui.button(label="Pause", style=discord.ButtonStyle.secondary, custom_id="wo_pause_base", row=1)
    async def pause_button(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        
        # 1. Check if user is the one working
        if str(interaction.user.id) != str(self.wo_data.get("InProgressUserID")):
            await interaction.followup.send(f"Only the user working on this task can pause it.", ephemeral=True)
            return
            
        try:
            # 2. Call API
            requests.put(f"{self.API_BASE_URL}/workorder/{self.wo_id}/pause").raise_for_status()

            # 3. Update Thread Title
            response = requests.get(f"{self.API_BASE_URL}/workorder/{self.wo_id}")
            new_data = response.json().get("workorder", {})
            self.wo_data = new_data
            self.wo_id = self.wo_data.get("WorkOrderID", self.wo_id)

            new_title = format_thread_title(self.wo_data)
            await interaction.channel.edit(name=new_title[:100])

            # 4. Update Message
            embed = self.build_embed(self.wo_data)
            self.toggle_buttons(self.wo_data.get("Status"))
            await interaction.edit_original_response(embed=embed, view=self)

        except Exception as e:
            await interaction.followup.send(f"Error pausing task: {e}", ephemeral=True)

    @ui.button(label="Finish", style=discord.ButtonStyle.green, custom_id="wo_finish_base", row=1)
    async def finish_button(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        
        # 1. Check if user is the one working
        if str(interaction.user.id) != str(self.wo_data.get("InProgressUserID")):
            await interaction.followup.send(f"Only the user working on this task can finish it.", ephemeral=True)
            return
            
        try:
            # 2. Call API
            payload = {"UserID": str(interaction.user.id)}
            requests.put(f"{self.API_BASE_URL}/workorder/{self.wo_id}/finish", json=payload).raise_for_status()

            # 3. Update Thread Title & Message
            response = requests.get(f"{self.API_BASE_URL}/workorder/{self.wo_id}")
            new_data = response.json().get("workorder", {})
            self.wo_data = new_data
            self.wo_id = self.wo_data.get("WorkOrderID", self.wo_id)

            new_title = format_thread_title(self.wo_data)
            await interaction.channel.edit(name=new_title[:100])

            embed = self.build_embed(self.wo_data)
            self.toggle_buttons(self.wo_data.get("Status"))
            await interaction.edit_original_response(embed=embed, view=self)

            # 4. Ping Accountable Person
            accountable_id = self.wo_data.get("AccountableID") or self.project_data.get("AccountableID")
            await interaction.followup.send(f"<@{accountable_id}>, this work order is finished and ready for your approval.")

        except Exception as e:
            await interaction.followup.send(f"Error finishing task: {e}", ephemeral=True)

    # --- ROW 3: IN-QA ---
    @ui.button(label="Approve", style=discord.ButtonStyle.green, custom_id="wo_approve_base", row=2)
    async def approve_button(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        
        # 1. Check if user is Accountable
        if str(interaction.user.id) != str(self.project_data.get("AccountableID")):
            await interaction.followup.send(f"Only the Project Accountable (<@{self.project_data.get('AccountableID')}>) can approve this.", ephemeral=True)
            return

        try:
            # 2. Call API
            requests.put(f"{self.API_BASE_URL}/workorder/{self.wo_id}/approve").raise_for_status()

            # 3. Update Thread Title & Message
            response = requests.get(f"{self.API_BASE_URL}/workorder/{self.wo_id}")
            new_data = response.json().get("workorder", {})
            self.wo_data = new_data
            self.wo_id = self.wo_data.get("WorkOrderID", self.wo_id)

            new_title = format_thread_title(self.wo_data)
            await interaction.channel.edit(name=new_title[:100])

            embed = self.build_embed(self.wo_data)
            self.toggle_buttons(self.wo_data.get("Status"))
            for item in self.children: item.disabled = True # Disable all
            await interaction.edit_original_response(embed=embed, view=self)

            # 4. Post confirmation
            submitter_id = self.wo_data.get("QA_SubmittedByID")
            await interaction.followup.send(f"Work order approved! Great job <@{submitter_id}>.")

        except Exception as e:
            await interaction.followup.send(f"Error approving task: {e}", ephemeral=True)

    @ui.button(label="Rework", style=discord.ButtonStyle.red, custom_id="wo_rework_base", row=2)
    async def rework_button(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        
        # 1. Check if user is Accountable
        if str(interaction.user.id) != str(self.project_data.get("AccountableID")):
            await interaction.followup.send(f"Only the Project Accountable (<@{self.project_data.get('AccountableID')}>) can send this for rework.", ephemeral=True)
            return

        try:
            # 2. Call API
            requests.put(f"{self.API_BASE_URL}/workorder/{self.wo_id}/rework").raise_for_status()

            # 3. Update Thread Title & Message
            response = requests.get(f"{self.API_BASE_URL}/workorder/{self.wo_id}")
            new_data = response.json().get("workorder", {})
            self.wo_data = new_data
            self.wo_id = self.wo_data.get("WorkOrderID", self.wo_id)

            new_title = format_thread_title(self.wo_data)
            await interaction.channel.edit(name=new_title[:100])

            embed = self.build_embed(self.wo_data)
            self.toggle_buttons(self.wo_data.get("Status"))
            await interaction.edit_original_response(embed=embed, view=self)

            # 4. Post confirmation
            submitter_id = self.wo_data.get("QA_SubmittedByID")
            await interaction.followup.send(f"This work order has been sent back for rework. <@{submitter_id}>, please review.")

        except Exception as e:
            await interaction.followup.send(f"Error sending for rework: {e}", ephemeral=True)


class WorkOrderCancelConfirmView(ui.View):
    def __init__(self, control_view: WorkOrderControlView, original_message: discord.Message):
        super().__init__(timeout=60)
        self.control_view = control_view
        self.original_message = original_message

    @ui.button(label="Yes", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer(thinking=True, ephemeral=True)
        success = await self.control_view.cancel_work_order_confirm(interaction, self.original_message)

        if success:
            await interaction.edit_original_response(content="Work order cancelled.", view=None)
        else:
            await interaction.edit_original_response(content="Could not cancel the work order.", view=None)

        self.stop()

    @ui.button(label="No", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: Interaction, button: ui.Button):
        await interaction.response.edit_message(content="Cancellation aborted.", view=None)
        self.stop()

class WorkOrderEditModal(ui.Modal, title='Edit Work Order'):
    def __init__(
        self,
        api_url: str,
        wo_data: dict,
        project_data: dict,
        control_view: WorkOrderControlView,
        original_message: discord.Message,
        pushed_to_user_id: Optional[str],
        update_push: bool
    ):
        super().__init__(timeout=600)
        self.API_BASE_URL = api_url
        self.wo_id = wo_data.get("WorkOrderID")
        self.wo_data = wo_data
        self.project_data = project_data
        self.control_view = control_view
        self.original_message = original_message
        self.pushed_to_user_id = pushed_to_user_id
        self.update_push = update_push

        self.title_input = ui.TextInput(
            label="Work Order Title",
            default=wo_data.get("Title")
        )
        self.deliverables_input = ui.TextInput(
            label="Deliverables & Why",
            style=discord.TextStyle.paragraph,
            default=wo_data.get("Deliverables")
        )
        self.add_item(self.title_input)
        self.add_item(self.deliverables_input)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        payload = {
            "Title": self.title_input.value,
            "Deliverables": self.deliverables_input.value
        }
        if self.update_push:
            payload["PushedToUserID"] = self.pushed_to_user_id or ""
        try:
            response = requests.put(f"{self.API_BASE_URL}/workorder/{self.wo_id}", json=payload)
            response.raise_for_status()
            new_data = response.json().get("workorder", {})

            combined_data = dict(self.wo_data)
            combined_data.update(new_data)
            self.wo_data = combined_data
            self.control_view.wo_data = combined_data
            self.control_view.wo_id = combined_data.get("WorkOrderID", self.control_view.wo_id)

            embed = WorkOrderControlView.build_embed(self.control_view.wo_data)
            self.control_view.toggle_buttons(self.control_view.wo_data.get("Status"))
            await self.original_message.edit(embed=embed, view=self.control_view)

            new_title = format_thread_title(self.control_view.wo_data)
            await self.original_message.channel.edit(name=new_title[:100])

            await interaction.followup.send("Work order details updated!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Error updating work order: {e}", ephemeral=True)
