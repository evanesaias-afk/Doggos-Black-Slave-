import os
import asyncio
import discord
import psycopg2
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

PING_ROLE_ID = 1497672922314313979
ALLOWED_ROLE_ID = 1497672922314313979

openai_client = OpenAI(api_key=OPENAI_API_KEY)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

db = psycopg2.connect(DATABASE_URL)
db.autocommit = True
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS boats (
    id SERIAL PRIMARY KEY,
    boat_name TEXT NOT NULL,
    claimed_by TEXT NOT NULL,
    boat_type TEXT NOT NULL,
    notes TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS resources (
    name TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    goal INTEGER NOT NULL,
    amount INTEGER NOT NULL DEFAULT 0
)
""")

DEFAULT_RESOURCES = {
    "Wood": {
        "goal": 100000,
        "items": ["Ash", "Cedar", "Fir", "Ironwood", "Oak", "Pine", "Poplar"]
    },
    "Thatch": {
        "goal": 100000,
        "items": ["Bark", "Fronds", "Reeds", "Roots", "Rushes", "Twigs"]
    },
    "Fiber": {
        "goal": 100000,
        "items": ["Bamboo", "Cotton", "Hemp", "Jute", "Seaweed", "Silk", "Straw"]
    },
    "Metal": {
        "goal": 100000,
        "items": ["Cobalt", "Copper", "Iridium", "Iron", "Silver", "Tin"]
    },
    "Stone": {
        "goal": 10000,
        "items": ["Coquina", "Granite", "Limestone", "Marble", "Sandstone", "Slate"]
    },
    "Hide": {
        "goal": 50000,
        "items": ["Fleece", "Fur", "Hair", "Leather", "Pelt", "Skin"]
    },
    "Gold": {
        "goal": 200000,
        "items": ["Gold"]
    }
}


def has_access(target):
    user = target.user if hasattr(target, "user") else target

    if not hasattr(user, "roles"):
        return False

    if user.guild_permissions.administrator:
        return True

    return any(role.id == ALLOWED_ROLE_ID for role in user.roles)


async def block_if_no_access(interaction):
    if has_access(interaction):
        return False

    if interaction.response.is_done():
        await interaction.followup.send(
            "You do not have permission to use this bot.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "You do not have permission to use this bot.",
            ephemeral=True
        )

    return True


def setup_resources():
    for category, data in DEFAULT_RESOURCES.items():
        for item in data["items"]:
            cursor.execute("""
            INSERT INTO resources (name, category, goal, amount)
            VALUES (%s, %s, %s, 0)
            ON CONFLICT (name) DO NOTHING
            """, (item.lower(), category, data["goal"]))

    cursor.execute("""
    UPDATE resources
    SET category = %s, goal = %s
    WHERE name = %s
    """, ("Gold", 200000, "gold"))


def format_number(num):
    return f"{num:,}"


def get_low_resources():
    cursor.execute("""
    SELECT name, category, goal, amount
    FROM resources
    WHERE amount < goal
    ORDER BY category, name
    """)

    return cursor.fetchall()


def build_low_resource_message(ping=False):
    low = get_low_resources()

    if not low:
        return "All resources are at or above goal."

    grouped = {}

    for name, category, goal, amount in low:
        grouped.setdefault(category, []).append((name.title(), goal, amount))

    message = ""

    if ping:
        message += f"<@&{PING_ROLE_ID}>\n\n"

    message += "RESOURCE FARMING NEEDED\n\n"

    for category, items in grouped.items():
        message += f"{category.upper()}\n"

        for name, goal, amount in items:
            needed = goal - amount
            message += (
                f"• {name}: {format_number(amount)} / {format_number(goal)} "
                f"Need {format_number(needed)}\n"
            )

        message += "\n"

    return message


async def make_doggo_reply(user_message):
    if not OPENAI_API_KEY:
        return "Doggo is still the best Atlas land PvPer alive, but the OpenAI key is missing."

    prompt = f"""
You are a funny Atlas game Discord bot.

Someone replied to the bot with this:
"{user_message}"

Reply based on what they said.

Rules:
- Make the reply Atlas-themed.
- Glaze Doggo as the best Atlas land PvPer.
- Mention land PvP, beds, bolas, bears, carbines, kits, islands, grids, raids, claim towers, puckles, or farming when it fits.
- Keep it 1 to 3 sentences.
- Make it funny.
- Do not be mean in a real-life way.
- Do not use slurs.
"""

    def call_openai():
        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120
        )
        return response.choices[0].message.content.strip()

    return await asyncio.to_thread(call_openai)


class BoatRegistrationModal(discord.ui.Modal):
    def __init__(self, boat_type):
        super().__init__(title="Boat Registration")
        self.boat_type = boat_type

        self.boat_name = discord.ui.TextInput(
            label="Boat Name",
            placeholder="Example: Black Pearl",
            required=True,
            max_length=100
        )

        self.claimed_by = discord.ui.TextInput(
            label="Claimed By",
            placeholder="Example: Doggo",
            required=True,
            max_length=100
        )

        self.notes = discord.ui.TextInput(
            label="Notes",
            placeholder="Optional",
            required=False,
            max_length=300
        )

        self.add_item(self.boat_name)
        self.add_item(self.claimed_by)
        self.add_item(self.notes)

    async def on_submit(self, interaction: discord.Interaction):
        if not has_access(interaction):
            await interaction.response.send_message(
                "You do not have permission to use this bot.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        notes_value = self.notes.value if self.notes.value else "None"

        cursor.execute("""
        INSERT INTO boats (boat_name, claimed_by, boat_type, notes)
        VALUES (%s, %s, %s, %s)
        """, (
            self.boat_name.value,
            self.claimed_by.value,
            self.boat_type,
            notes_value
        ))

        embed = discord.Embed(
            title="Boat Registered",
            color=discord.Color.blue()
        )

        embed.add_field(name="Name", value=self.boat_name.value, inline=False)
        embed.add_field(name="Claimed By", value=self.claimed_by.value, inline=False)
        embed.add_field(name="Type", value=self.boat_type, inline=False)

        if notes_value != "None":
            embed.add_field(name="Notes", value=notes_value, inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)


class BoatTypeSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Schooner"),
            discord.SelectOption(label="Brigantine"),
            discord.SelectOption(label="Galleon")
        ]

        super().__init__(
            placeholder="Choose boat type",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        if await block_if_no_access(interaction):
            return

        await interaction.response.send_modal(
            BoatRegistrationModal(self.values[0])
        )


class BoatTypeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(BoatTypeSelect())


class ResourceAmountModal(discord.ui.Modal):
    def __init__(self, resource_name):
        super().__init__(title=f"Update {resource_name.title()}")
        self.resource_name = resource_name

        self.amount = discord.ui.TextInput(
            label="Amount",
            placeholder="Example: 79000",
            required=True,
            max_length=20
        )

        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        if not has_access(interaction):
            await interaction.response.send_message(
                "You do not have permission to use this bot.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        amount_text = self.amount.value.replace(",", "").strip()

        if not amount_text.isdigit():
            await interaction.followup.send(
                "Enter numbers only.",
                ephemeral=True
            )
            return

        amount = int(amount_text)

        cursor.execute("""
        UPDATE resources
        SET amount = %s
        WHERE name = %s
        """, (amount, self.resource_name.lower()))

        if cursor.rowcount == 0:
            await interaction.followup.send(
                "Resource not found.",
                ephemeral=True
            )
            return

        cursor.execute("""
        SELECT goal
        FROM resources
        WHERE name = %s
        """, (self.resource_name.lower(),))

        row = cursor.fetchone()
        goal = row[0] if row else 0
        needed = max(goal - amount, 0)

        embed = discord.Embed(
            title="Resource Updated",
            color=discord.Color.green()
        )

        embed.add_field(name="Resource", value=self.resource_name.title(), inline=False)
        embed.add_field(name="Amount", value=format_number(amount), inline=False)
        embed.add_field(name="Goal", value=format_number(goal), inline=False)
        embed.add_field(name="Still Needed", value=format_number(needed), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)


class ResourceSelect(discord.ui.Select):
    def __init__(self, category):
        self.category = category

        options = [
            discord.SelectOption(label=item)
            for item in DEFAULT_RESOURCES[category]["items"]
        ]

        super().__init__(
            placeholder=f"Choose {category} resource",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        if await block_if_no_access(interaction):
            return

        await interaction.response.send_modal(
            ResourceAmountModal(self.values[0])
        )


class ResourceSelectView(discord.ui.View):
    def __init__(self, category):
        super().__init__(timeout=180)
        self.add_item(ResourceSelect(category))


class ResourceCategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=category)
            for category in DEFAULT_RESOURCES.keys()
        ]

        super().__init__(
            placeholder="Choose resource category",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        if await block_if_no_access(interaction):
            return

        category = self.values[0]

        embed = discord.Embed(
            title=f"{category} Resources",
            description=f"Choose which {category} resource to update.",
            color=discord.Color.green()
        )

        await interaction.response.send_message(
            embed=embed,
            view=ResourceSelectView(category),
            ephemeral=True
        )


class ResourceCategoryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(ResourceCategorySelect())


@bot.event
async def on_ready():
    setup_resources()

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands", flush=True)
    except Exception as error:
        print(f"Sync error: {error}", flush=True)

    print(f"{bot.user} is online", flush=True)


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.reference and message.reference.message_id:
        try:
            replied_message = await message.channel.fetch_message(
                message.reference.message_id
            )

            if replied_message.author.id == bot.user.id:
                if not has_access(message.author):
                    return

                async with message.channel.typing():
                    reply = await make_doggo_reply(message.content)

                await message.reply(reply)

        except Exception as error:
            print(f"Reply handler error: {error}", flush=True)

    await bot.process_commands(message)


@bot.tree.command(name="doggo", description="Make the bot glaze Doggo")
async def doggo(interaction: discord.Interaction, message: str):
    if await block_if_no_access(interaction):
        return

    await interaction.response.defer()
    reply = await make_doggo_reply(message)
    await interaction.followup.send(reply)


@bot.tree.command(name="registerboat", description="Register a boat")
async def registerboat(interaction: discord.Interaction):
    if await block_if_no_access(interaction):
        return

    await interaction.response.defer(ephemeral=True)

    embed = discord.Embed(
        title="Boat Registration",
        description="Select the boat type below.",
        color=discord.Color.blue()
    )

    await interaction.followup.send(
        embed=embed,
        view=BoatTypeView(),
        ephemeral=True
    )


@bot.tree.command(name="boats", description="Show all registered boats")
async def boats(interaction: discord.Interaction):
    if await block_if_no_access(interaction):
        return

    await interaction.response.defer()

    cursor.execute("""
    SELECT boat_name, claimed_by, boat_type, notes
    FROM boats
    ORDER BY claimed_by, boat_name
    """)

    rows = cursor.fetchall()

    if not rows:
        await interaction.followup.send("No boats registered yet.")
        return

    grouped = {}

    for boat_name, claimed_by, boat_type, notes in rows:
        grouped.setdefault(claimed_by, []).append((boat_name, boat_type, notes))

    message = "BOAT REGISTRY\n\n"

    for owner, boats_list in grouped.items():
        message += f"{owner}\n"

        for boat_name, boat_type, notes in boats_list:
            message += f"• {boat_name}, {boat_type}"

            if notes and notes != "None":
                message += f", {notes}"

            message += "\n"

        message += "\n"

    await interaction.followup.send(message[:2000])


@bot.tree.command(name="boatsby", description="Show boats claimed by a person")
async def boatsby(interaction: discord.Interaction, claimed_by: str):
    if await block_if_no_access(interaction):
        return

    await interaction.response.defer()

    cursor.execute("""
    SELECT boat_name, boat_type, notes
    FROM boats
    WHERE lower(claimed_by) = %s
    ORDER BY boat_name
    """, (claimed_by.lower(),))

    rows = cursor.fetchall()

    if not rows:
        await interaction.followup.send(
            f"No boats found for {claimed_by}."
        )
        return

    message = f"BOATS CLAIMED BY {claimed_by}\n\n"

    for boat_name, boat_type, notes in rows:
        message += f"• {boat_name}, {boat_type}"

        if notes and notes != "None":
            message += f", {notes}"

        message += "\n"

    await interaction.followup.send(message[:2000])


@bot.tree.command(name="removeboat", description="Remove a boat by name")
async def removeboat(interaction: discord.Interaction, boat_name: str):
    if await block_if_no_access(interaction):
        return

    await interaction.response.defer()

    cursor.execute(
        "DELETE FROM boats WHERE lower(boat_name) = %s",
        (boat_name.lower(),)
    )

    if cursor.rowcount == 0:
        await interaction.followup.send("Boat not found.")
    else:
        await interaction.followup.send(f"Removed boat: {boat_name}")


@bot.tree.command(name="bulkupdate", description="Update a resource with dropdowns")
async def bulkupdate(interaction: discord.Interaction):
    if await block_if_no_access(interaction):
        return

    await interaction.response.defer(ephemeral=True)

    embed = discord.Embed(
        title="Update Resource",
        description="Choose a category, then choose the resource. After that, enter the number.",
        color=discord.Color.green()
    )

    await interaction.followup.send(
        embed=embed,
        view=ResourceCategoryView(),
        ephemeral=True
    )


@bot.tree.command(name="resources", description="Show all tracked resources")
async def resources(interaction: discord.Interaction):
    if await block_if_no_access(interaction):
        return

    await interaction.response.defer()

    cursor.execute("""
    SELECT name, category, goal, amount
    FROM resources
    ORDER BY category, name
    """)

    rows = cursor.fetchall()
    grouped = {}

    for name, category, goal, amount in rows:
        grouped.setdefault(category, []).append((name.title(), goal, amount))

    message = "RESOURCE LIST\n\n"

    for category, items in grouped.items():
        message += f"{category.upper()}\n"

        for name, goal, amount in items:
            message += f"• {name}: {format_number(amount)} / {format_number(goal)}\n"

        message += "\n"

    await interaction.followup.send(message[:2000])


@bot.tree.command(name="lowresources", description="Show resources below goal")
async def lowresources(interaction: discord.Interaction):
    if await block_if_no_access(interaction):
        return

    await interaction.response.defer()

    await interaction.followup.send(
        build_low_resource_message(ping=False)[:2000]
    )


@bot.tree.command(name="pinglowresources", description="Ping company members for low resources")
async def pinglowresources(interaction: discord.Interaction):
    if await block_if_no_access(interaction):
        return

    await interaction.response.defer()

    await interaction.followup.send(
        build_low_resource_message(ping=True)[:2000],
        allowed_mentions=discord.AllowedMentions(roles=True)
    )


@bot.tree.command(name="setresourcegoal", description="Change the goal for a resource")
async def setresourcegoal(
    interaction: discord.Interaction,
    resource_name: str,
    goal: int
):
    if await block_if_no_access(interaction):
        return

    await interaction.response.defer()

    cursor.execute("""
    UPDATE resources
    SET goal = %s
    WHERE name = %s
    """, (goal, resource_name.lower()))

    if cursor.rowcount == 0:
        await interaction.followup.send("Resource not found.")
    else:
        await interaction.followup.send(
            f"{resource_name.title()} goal set to {format_number(goal)}."
        )


bot.run(TOKEN)
