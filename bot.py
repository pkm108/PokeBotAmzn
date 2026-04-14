import os
import json
import re
import asyncio
from typing import Optional, Dict, Any

import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv

from monitor import check_amazon_product

load_dotenv()

TOKEN = os.getenv("TOKEN")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "45"))
DEFAULT_ALERT_CHANNEL_ID = os.getenv("ALERT_CHANNEL_ID")

DATA_DIR = "data"
DB_PATH = os.path.join(DATA_DIR, "products.json")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

os.makedirs(DATA_DIR, exist_ok=True)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


def load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default


def save_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


products: Dict[str, Dict[str, Any]] = load_json(DB_PATH, {})
config: Dict[str, Any] = load_json(CONFIG_PATH, {
    "alert_channel_id": int(DEFAULT_ALERT_CHANNEL_ID) if DEFAULT_ALERT_CHANNEL_ID else None
})


def extract_asin(value: str) -> Optional[str]:
    value = value.strip()
    m = re.search(r"/dp/([A-Z0-9]{10})", value, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    m = re.search(r"/gp/product/([A-Z0-9]{10})", value, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    value = value.upper()
    if re.fullmatch(r"[A-Z0-9]{10}", value):
        return value

    return None


def get_alert_channel() -> Optional[discord.TextChannel]:
    channel_id = config.get("alert_channel_id")
    if not channel_id:
        return None
    channel = bot.get_channel(int(channel_id))
    return channel if isinstance(channel, discord.TextChannel) else None


def make_embed(result: Dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(
        title="🚨 Amazon Restock Alert",
        description=result["title"][:4096],
    )
    embed.add_field(name="ASIN", value=result["asin"], inline=True)
    embed.add_field(name="Price", value=result.get("price_text", "Unknown"), inline=True)
    embed.add_field(name="Sold by", value=result.get("sold_by", "Unknown"), inline=True)
    embed.add_field(name="Ships from", value=result.get("ships_from", "Unknown"), inline=True)
    embed.add_field(name="Link", value=result["url"], inline=False)
    return embed


@bot.event
async def on_ready():
    await bot.tree.sync()
    if not monitor_loop.is_running():
        monitor_loop.start()
    print(f"Logged in as {bot.user}")


@bot.tree.command(name="setchannel", description="Set the Discord channel for restock alerts.")
@app_commands.describe(channel="The channel that should receive alerts")
@app_commands.checks.has_permissions(administrator=True)
async def setchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    config["alert_channel_id"] = channel.id
    save_json(CONFIG_PATH, config)
    await interaction.response.send_message(f"Alerts will go to {channel.mention}.")


@bot.tree.command(name="add", description="Add an Amazon US product by ASIN or URL.")
@app_commands.describe(item="Amazon product URL or ASIN")
async def add_item(interaction: discord.Interaction, item: str):
    asin = extract_asin(item)
    if not asin:
        await interaction.response.send_message("Invalid ASIN or Amazon URL.", ephemeral=True)
        return

    products[asin] = {
        "enabled": True,
        "last_state": "unknown",
        "last_alerted_at": 0,
    }
    save_json(DB_PATH, products)
    await interaction.response.send_message(f"Tracking `{asin}`.")


@bot.tree.command(name="remove", description="Remove a tracked ASIN.")
@app_commands.describe(asin="ASIN to remove")
async def remove_item(interaction: discord.Interaction, asin: str):
    asin = asin.upper().strip()
    if asin in products:
        del products[asin]
        save_json(DB_PATH, products)
        await interaction.response.send_message(f"Removed `{asin}`.")
    else:
        await interaction.response.send_message("That ASIN is not being tracked.", ephemeral=True)


@bot.tree.command(name="list", description="List tracked ASINs.")
async def list_items(interaction: discord.Interaction):
    if not products:
        await interaction.response.send_message("No ASINs are being tracked.")
        return

    lines = [f"- `{asin}`" for asin in products.keys()]
    await interaction.response.send_message("\n".join(lines))


@bot.tree.command(name="check", description="Run an immediate check now.")
async def manual_check(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    count = await run_checks()
    await interaction.followup.send(f"Checked {count} product(s).")


async def run_checks() -> int:
    channel = get_alert_channel()
    if channel is None:
        return 0

    count = 0
    for asin, meta in list(products.items()):
        if not meta.get("enabled", True):
            continue

        try:
            result = await check_amazon_product(asin)
        except Exception as e:
            print(f"Check failed for {asin}: {e}")
            continue

        count += 1
        is_good = (
            result["in_stock"]
            and result["sold_by_amazon"]
            and result["ships_from_amazon"]
        )

        previous = meta.get("last_state", "unknown")
        current = "eligible" if is_good else "not_eligible"

        if current == "eligible" and previous != "eligible":
            await channel.send(embed=make_embed(result))

        products[asin]["last_state"] = current
        save_json(DB_PATH, products)

        await asyncio.sleep(2)

    return count


@tasks.loop(seconds=CHECK_INTERVAL)
async def monitor_loop():
    await run_checks()


if not TOKEN:
    raise RuntimeError("Missing TOKEN environment variable.")

bot.run(TOKEN)
