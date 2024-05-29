import asyncio
import logging
import os
import re
import tempfile
from genericpath import exists
from time import time

import discord
from discord import Message
from discord.ext import tasks
from discord.file import File
from dotenv import load_dotenv

from nfcli import determine_output_png, init_logger, load_path
from nfcli.models import Lobbies
from nfcli.parsers import parse_any, parse_mods
from nfcli.printers import Printer
from nfcli.sqlite import create_connection, fetch_usage_servers, insert_usage_data
from nfcli.steam import get_player_count, get_workshop_files, get_workshop_id
from nfcli.wiki import Wiki

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_CHANNEL = int(os.getenv("DISCORD_CHANNEL"))
MAX_UPLOAD = 0.5 * 1024 * 1024
lobbies = Lobbies(time(), None)

wiki_db = Wiki()
connection = create_connection()
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Bot(intents=intents)
init_logger("../bot.log", logging.INFO)


def get_temp_filename(ext: str) -> str:
    return tempfile.mktemp() + ext


def is_supported(filename: str) -> bool:
    extensions = ["fleet", "missile", "ship"]
    return any(filename.endswith(extension) for extension in extensions)


async def process_file(message: Message, xml_data: str, filename: str, with_fleet_file: bool):
    """Process one file content."""
    async with message.channel.typing():
        logging.info(f"Converting file {filename}")
        png_file = determine_output_png(filename)
        tmp_file = get_temp_filename(".png")
        entity = parse_any(filename, xml_data)
        entity.write(tmp_file)
        all_files = []
        if not exists(tmp_file):
            raise RuntimeError(f"Failed to write to {tmp_file}")
        converted_file = File(tmp_file, filename=png_file)
        all_files.append(converted_file)
        if with_fleet_file:
            all_files.append(File(filename, filename=os.path.basename(filename)))
        mod_deps = parse_mods(xml_data)
        mods = Printer.get_mods(mod_deps, "<", ">")
        await message.reply(f"{entity.text}{mods}", files=all_files)
        converted_file.close()
        os.unlink(tmp_file)


async def process_uploads(message: Message):
    """Process uploaded files."""
    files = {file for file in message.attachments if is_supported(file.filename) and not file.is_spoiler()}
    valid_files = {file for file in files if file.size < MAX_UPLOAD}
    invalid_files = files - valid_files
    if invalid_files:
        await message.reply(
            "Some files could not be parsed as they were too big to fit in my small brain."
            f"\nPlease upload files no larger than {round(MAX_UPLOAD/1024,0)}KB."
        )
    if valid_files:
        insert_usage_data(connection, message.guild.id, message.author.id, files)
    for file in valid_files:
        xml_data = await file.read()
        await process_file(message, xml_data, file.filename, with_fleet_file=False)


async def process_workshop(message: Message, workshop_id: int):
    """Process one workshop id."""
    logging.info(f"Processing workshop item {workshop_id}")
    try:
        input_files = get_workshop_files(workshop_id, throw_if_not_found=True)
        for input_file in input_files:
            xml_data = load_path(input_file)
            await process_file(message, xml_data, input_file, with_fleet_file=True)
    except RuntimeError as exception:
        logging.error(exception)
        await message.reply(exception)


async def process_old_wiki(message: Message):
    """Adds a friendly reminded to use slash command instead."""
    is_wiki = message.content[1:5].lower() == "wiki"
    if is_wiki:
        reply = await message.reply(
            "Hey dummy, stop spamming the channel and use `/wiki` command instead!\n"
            "In case you missed the tutorial: type `/wiki`, press enter, type keywords, press enter again.\n"
            "This message will self destruct in few seconds. I hope yours too!"
        )
        await asyncio.sleep(9)
        await reply.delete()


async def process_workshops(message: Message):
    """Extract and process workshop links."""
    link_regex = re.compile(r"https?:\/\/steamcommunity\.com\/sharedfiles\/filedetails\S+id=\d+")
    links = re.findall(link_regex, message.content)
    workshop_ids = set()
    for link_data in links:
        workshop_ids.add(get_workshop_id(link_data))

    for workshop_id in workshop_ids:
        await process_workshop(message, workshop_id)


async def process_lobby_data(message: Message):
    """Extract and process lobby data from subscribed channel."""
    logging.debug("Checking incoming message")
    if not len(message.content):
        return
    lobbies_temp = Lobbies(time(), message.content)
    logging.debug("Storing new lobby data")
    global lobbies
    lobbies = lobbies_temp


async def process_interaction(ctx: discord.ApplicationContext, reply: str, timeout: int = 30):
    new_lines = "\n" if reply.endswith("```") else "\n\n"
    await ctx.respond(reply + f"{new_lines}*This message will be deleted in {timeout}s.*")
    for i in range(timeout):
        await ctx.edit(content=reply + f"{new_lines}*This message will be deleted in {timeout - i}s.*")
        await asyncio.sleep(1)
    await ctx.delete()


@bot.event
async def on_ready():
    logging.info("Discord bot initialized")
    for guild in bot.guilds:
        logging.info(f"Connected to the guild: {guild.name} (id: {guild.id})")
    status_changer.start()


@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return
    if message.channel.id == DISCORD_CHANNEL and message.author.bot:
        await process_lobby_data(message)
    else:
        await process_old_wiki(message)
        await process_workshops(message)
        await process_uploads(message)


@bot.slash_command(name="wiki")
async def wiki_action(ctx: discord.ApplicationContext, *, keywords: str):
    """Search N:FC wiki data dumps provided by @Alexbay218#0295"""
    entity = wiki_db.get(keywords)
    await process_interaction(ctx, entity.text)


@bot.slash_command(name="lobbies")
async def lobbies_action(ctx: discord.ApplicationContext):
    """Report number of lobbies in the game (semi-live data provided by volunteers)."""
    global lobbies
    await process_interaction(ctx, str(lobbies))


@bot.slash_command(name="stats")
async def stats_action(ctx: discord.ApplicationContext, last_days: int):
    """Show basic usage statics."""
    last_days = max(1, min(last_days, 30))
    guilds_stats = fetch_usage_servers(connection, last_days)
    await process_interaction(ctx, str(guilds_stats))


@tasks.loop(seconds=60.0)
async def status_changer():
    player_count = get_player_count()
    name = f"{player_count!s} fleets"
    if player_count == -1:
        name = "undisclosed number of fleets"
    elif player_count == 0:
        name = "an empty shipyard"
    elif player_count == 1:
        name = "just one lonely fleet"
    activity = discord.Activity(type=discord.ActivityType.watching, name=name)
    await bot.change_presence(activity=activity)


def start():
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    start()
