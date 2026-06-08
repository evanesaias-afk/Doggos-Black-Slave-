import os
import asyncio
import base64
import json
import re

import discord
import psycopg2
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
        "items": [
            "Cobalt", "Copper", "Iridium", "Iron", "Silver", "Tin",
            "Cobalt Ingot", "Copper Ingot", "Iridium Ingot",
            "Iron Ingot", "Silver Ingot", "Tin Ingot"
        ]
    },
    "Stone": {
        "goal": 10000,
        "items": ["Coquina", "Granite", "Limestone", "Marble", "Sandstone", "Slate"]
    },
    "Hide": {
        "goal": 50000,
        "items": ["Fleece", "Fur", "Hair", "Leather", "Pelt", "Skin"]
    },
    "Keratin": {
        "goal": 50000,
        "items": ["Bone", "Carapace", "Chitin", "Scale", "Turtle Shell", "Residue"]
    },
    "Gold": {
        "goal": 200000,
        "items": ["Gold"]
    }
}

INGOT_RESOURCES = {
    "cobalt ingot",
    "copper ingot",
    "iridium ingot",
    "iron ingot",
    "silver ingot",
    "tin ingot"
}


async def delete_after_two_minutes(message):
    await asyncio.sleep(120)

    try:
        await message.delete()
    except Exception:
        pass


async def send_temp_followup(interaction, content=None, **kwargs):
    message = await interaction.followup.send(content, **kwargs)

    if not kwargs.get("ephemeral", False):
        asyncio.create_task(delete_after_two_minutes(message))

    return message


async def send_temp_reply(message, content):
    sent = await message.reply(content)
    asyncio.create_task(delete_after_two_minutes(sent))
    return sent


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


def clean_key(value):
    value = value.lower().strip()
    value = value.replace("_", " ")
    value = re.sub(r"[^a-z0-9 ]+", "", value)
    value = re.sub(r"\s+", " ", value)
    return value


def build_resource_lookup():
    lookup = {}

    for data in DEFAULT_RESOURCES.values():
        for item in data["items"]:
            lookup[clean_key(item)] = item.lower()

    aliases = {
        "bones": "bone",
        "carapaces": "carapace",
        "chitins": "chitin",
        "scales": "scale",
        "turtle shells": "turtle shell",
        "residues": "residue",
        "cobalt ingots": "cobalt ingot",
        "copper ingots": "copper ingot",
        "iridium ingots": "iridium ingot",
        "iron ingots": "iron ingot",
        "silver ingots": "silver ingot",
        "tin ingots": "tin ingot"
    }

    for alias, real_name in aliases.items():
        lookup[clean_key(alias)] = real_name

    return lookup


def get_all_tracked_resource_names():
    names = []

    for data in DEFAULT_RESOURCES.values():
        names.extend(data["items"])

    return names


def setup_resources():
    for category, data in DEFAULT_RESOURCES.items():
        for item in data["items"]:
            name = item.lower()
            goal = 50000 if name in INGOT_RESOURCES else data["goal"]

            cursor.execute("""
            INSERT INTO resources (name, category, goal, amount)
            VALUES (%s, %s, %s, 0)
            ON CONFLICT (name) DO UPDATE
            SET category = EXCLUDED.category,
                goal = EXCLUDED.goal
            """, (name, category, goal))

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
                f"â¢ {name}: {format_number(amount)} / {format_number(goal)} "
                f"Need {format_number(needed)}\n"
            )

        message += "\n"

    return message


def split_message(message, limit=1900):
    chunks = []

    while len(message) > limit:
        split_at = message.rfind("\n", 0, limit)

        if split_at == -1:
            split_at = limit

        chunks.append(message[:split_at])
        message = message[split_at:].lstrip()

    if message:
        chunks.append(message)

    return chunks


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


def parse_json_from_ai(text):
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise ValueError("No JSON object found in AI response.")

    return json.loads(text[start:end + 1])


async def scan_resource_image(image_bytes, content_type):
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is missing.")

    encoded_image = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{content_type};base64,{encoded_image}"

    tracked_names = get_all_tracked_resource_names()

    prompt = f"""
You are reading an Atlas game warehouse resource screenshot.

Extract only resource names and amounts visible in the image.

Tracked resource names:
{", ".join(tracked_names)}

Rules:
- Return JSON only.
- Use this exact shape:
{{
  "resources": [
    {{"name": "Hemp", "amount": 349750}},
    {{"name": "Tin Ingot", "amount": 12345}}
  ]
}}
- Amounts must be integers.
- Remove commas from numbers.
- Do not invent missing resources.
- Do not include item weights, stacks, levels, or anything that is not a resource amount.
- Match resource names as closely as possible to the tracked names.
- The category named Keratin contains Bone, Carapace, Chitin, Scale, Turtle Shell, and Residue.
- Do not include a resource named Keratin unless it is visibly listed in the screenshot as an item.
"""

    def call_openai():
        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url
                            }
                        }
                    ]
                }
            ],
            max_tokens=1200
        )
        return response.choices[0].message.content.strip()

    raw_response = await asyncio.to_thread(call_openai)
    return parse_json_from_ai(raw_response)


def update_resources_from_scan(scan_data):
    lookup = build_resource_lookup()
    updated = []
    not_tracked = []
    invalid = []

    for item in scan_data.get("resources", []):
        raw_name = str(item.get("name", "")).strip()
        raw_amount = item.get("amount")

        if not raw_name:
            continue

        try:
            amount = int(str(raw_amount).replace(",", "").strip())
        except Exception:
            invalid.append(raw_name)
            continue

        db_name = lookup.get(clean_key(raw_name))

        if not db_name:
            not_tracked.append(raw_name)
            continue

        cursor.execute("""
        UPDATE resources
        SET amount = %s
        WHERE name = %s
        """, (amount, db_name))

        if cursor.rowcount == 0:
            not_tracked.append(raw_name)
        else:
            updated.append((db_name.title(), amount))

    return updated, not_tracked, invalid


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


@bot.event
async def on_ready():
    setup_resources()

    try:
        for guild in bot.guilds:
            bot.tree.copy_global_to(guild=guild)
            guild_synced = await bot.tree.sync(guild=guild)
            print(
                f"Guild synced {len(guild_synced)} commands to {guild.name} ({guild.id})",
                flush=True
            )

        bot.tree.clear_commands(guild=None)
        cleared_global = await bot.tree.sync()
        print(f"Cleared global commands. Global count: {len(cleared_global)}", flush=True)

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

                await send_temp_reply(message, reply)

        except Exception as error:
            print(f"Reply handler error: {error}", flush=True)

    await bot.process_commands(message)


@bot.tree.command(name="doggo", description="Make the bot glaze Doggo")
async def doggo(interaction: discord.Interaction, message: str):
    if await block_if_no_access(interaction):
        return

    await interaction.response.defer()
    reply = await make_doggo_reply(message)
    await send_temp_followup(interaction, reply)


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
        await send_temp_followup(interaction, "No boats registered yet.")
        return

    grouped = {}

    for boat_name, claimed_by, boat_type, notes in rows:
        grouped.setdefault(claimed_by, []).append((boat_name, boat_type, notes))

    message = "BOAT REGISTRY\n\n"

    for owner, boats_list in grouped.items():
        message += f"{owner}\n"

        for boat_name, boat_type, notes in boats_list:
            message += f"â¢ {boat_name}, {boat_type}"

            if notes and notes != "None":
                message += f", {notes}"

            message += "\n"

        message += "\n"

    for chunk in split_message(message):
        await send_temp_followup(interaction, chunk)


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
        await send_temp_followup(interaction, f"No boats found for {claimed_by}.")
        return

    message = f"BOATS CLAIMED BY {claimed_by}\n\n"

    for boat_name, boat_type, notes in rows:
        message += f"â¢ {boat_name}, {boat_type}"

        if notes and notes != "None":
            message += f", {notes}"

        message += "\n"

    for chunk in split_message(message):
        await send_temp_followup(interaction, chunk)


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
        await send_temp_followup(interaction, "Boat not found.")
    else:
        await send_temp_followup(interaction, f"Removed boat: {boat_name}")


@bot.tree.command(name="scanresources", description="Update resources from a screenshot")
async def scanresources(interaction: discord.Interaction, image: discord.Attachment):
    if await block_if_no_access(interaction):
        return

    if not image.content_type or not image.content_type.startswith("image/"):
        await interaction.response.send_message(
            "Upload an image file.",
            ephemeral=True
        )
        return

    await interaction.response.defer()

    try:
        image_bytes = await image.read()
        scan_data = await scan_resource_image(image_bytes, image.content_type)
        updated, not_tracked, invalid = update_resources_from_scan(scan_data)

        if not updated and not not_tracked and not invalid:
            await send_temp_followup(
                interaction,
                "I could not read any tracked resources from that screenshot."
            )
            return

        message = "RESOURCE SCREENSHOT SCAN COMPLETE\n\n"

        if updated:
            message += f"Updated {len(updated)} resources:\n"

            for name, amount in sorted(updated):
                message += f"â¢ {name}: {format_number(amount)}\n"

        if not_tracked:
            message += "\nNot tracked or not matched:\n"

            for name in sorted(set(not_tracked)):
                message += f"â¢ {name}\n"

        if invalid:
            message += "\nCould not read amount for:\n"

            for name in sorted(set(invalid)):
                message += f"â¢ {name}\n"

        message += "\n"
        message += build_low_resource_message(ping=False)

        for chunk in split_message(message):
            await send_temp_followup(interaction, chunk)

    except Exception as error:
        print(f"Scan resource error: {error}", flush=True)
        await send_temp_followup(
            interaction,
            "I could not scan that screenshot. Try a clearer image with the resource names and amounts visible."
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
            message += f"â¢ {name}: {format_number(amount)} / {format_number(goal)}\n"

        message += "\n"

    for chunk in split_message(message):
        await send_temp_followup(interaction, chunk)


@bot.tree.command(name="lowresources", description="Show resources below goal")
async def lowresources(interaction: discord.Interaction):
    if await block_if_no_access(interaction):
        return

    await interaction.response.defer()
    message = build_low_resource_message(ping=False)

    for chunk in split_message(message):
        await send_temp_followup(interaction, chunk)


@bot.tree.command(name="pinglowresources", description="Ping company members for low resources")
async def pinglowresources(interaction: discord.Interaction):
    if await block_if_no_access(interaction):
        return

    await interaction.response.defer()

    message = build_low_resource_message(ping=True)
    chunks = split_message(message)

    for index, chunk in enumerate(chunks):
        sent = await interaction.followup.send(
            chunk,
            allowed_mentions=discord.AllowedMentions(roles=True if index == 0 else False)
        )
        asyncio.create_task(delete_after_two_minutes(sent))


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
        await send_temp_followup(interaction, "Resource not found.")
    else:
        await send_temp_followup(
            interaction,
            f"{resource_name.title()} goal set to {format_number(goal)}."
        )


bot.run(TOKEN)
