import os
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
PING_ROLE_ID = 1497672922314313979

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

db = sqlite3.connect("atlas_bot.db")
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS boats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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

db.commit()


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
    }
}


def setup_resources():
    for category, data in DEFAULT_RESOURCES.items():
        for item in data["items"]:
            cursor.execute("""
            INSERT OR IGNORE INTO resources (name, category, goal, amount)
            VALUES (?, ?, ?, 0)
            """, (item.lower(), category, data["goal"]))
    db.commit()


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


@bot.event
async def on_ready():
    setup_resources()
    await bot.tree.sync()
    print(f"{bot.user} is online")


@bot.tree.command(name="registerboat", description="Register a boat")
async def registerboat(
    interaction: discord.Interaction,
    boat_name: str,
    claimed_by: str,
    boat_type: str,
    notes: str = "None"
):
    cursor.execute("""
    INSERT INTO boats (boat_name, claimed_by, boat_type, notes)
    VALUES (?, ?, ?, ?)
    """, (boat_name, claimed_by, boat_type, notes))

    db.commit()

    await interaction.response.send_message(
        f"Boat registered:\n"
        f"Name: {boat_name}\n"
        f"Claimed by: {claimed_by}\n"
        f"Type: {boat_type}\n"
        f"Notes: {notes}"
    )


@bot.tree.command(name="boats", description="Show all registered boats")
async def boats(interaction: discord.Interaction):
    cursor.execute("""
    SELECT boat_name, claimed_by, boat_type, notes
    FROM boats
    ORDER BY claimed_by, boat_name
    """)

    rows = cursor.fetchall()

    if not rows:
        await interaction.response.send_message("No boats registered yet.")
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

    await interaction.response.send_message(message[:2000])


@bot.tree.command(name="removeboat", description="Remove a boat by name")
async def removeboat(interaction: discord.Interaction, boat_name: str):
    cursor.execute("DELETE FROM boats WHERE lower(boat_name) = ?", (boat_name.lower(),))
    db.commit()

    if cursor.rowcount == 0:
        await interaction.response.send_message("Boat not found.")
    else:
        await interaction.response.send_message(f"Removed boat: {boat_name}")


@bot.tree.command(name="bulkupdate", description="Update many resources at once")
@app_commands.describe(
    updates="Example: Ironwood=79000, Ash=44000, Tin=16000"
)
async def bulkupdate(interaction: discord.Interaction, updates: str):
    lines = updates.replace(",", "\n").split("\n")

    updated = []
    not_found = []

    for line in lines:
        if "=" not in line:
            continue

        name, amount = line.split("=", 1)
        name = name.strip().lower()
        amount = amount.strip().replace(",", "")

        if not amount.isdigit():
            continue

        amount = int(amount)

        cursor.execute("""
        UPDATE resources
        SET amount = ?
        WHERE name = ?
        """, (amount, name))

        if cursor.rowcount == 0:
            not_found.append(name.title())
        else:
            updated.append(f"{name.title()} = {format_number(amount)}")

    db.commit()

    message = "Resource update complete.\n\n"

    if updated:
        message += "Updated:\n"
        for item in updated:
            message += f"• {item}\n"

    if not_found:
        message += "\nNot found:\n"
        for item in not_found:
            message += f"• {item}\n"

    message += "\n"
    message += build_low_resource_message(ping=False)

    await interaction.response.send_message(message[:2000])


@bot.tree.command(name="resources", description="Show all tracked resources")
async def resources(interaction: discord.Interaction):
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

    await interaction.response.send_message(message[:2000])


@bot.tree.command(name="lowresources", description="Show resources below goal")
async def lowresources(interaction: discord.Interaction):
    await interaction.response.send_message(build_low_resource_message(ping=False)[:2000])


@bot.tree.command(name="pinglowresources", description="Ping company members for low resources")
async def pinglowresources(interaction: discord.Interaction):
    await interaction.response.send_message(build_low_resource_message(ping=True)[:2000])


@bot.tree.command(name="setresourcegoal", description="Change the goal for a resource")
async def setresourcegoal(interaction: discord.Interaction, resource_name: str, goal: int):
    cursor.execute("""
    UPDATE resources
    SET goal = ?
    WHERE name = ?
    """, (goal, resource_name.lower()))

    db.commit()

    if cursor.rowcount == 0:
        await interaction.response.send_message("Resource not found.")
    else:
        await interaction.response.send_message(
            f"{resource_name.title()} goal set to {format_number(goal)}."
        )


bot.run(TOKEN)
