from __future__ import annotations

import re
import logging
import asyncio
from contextlib import asynccontextmanager

import aiohttp
import asyncpg
import discord
from discord.ext import commands
from discord.ext.duck import errors

import config


EXTENSIONS = [
    'jishaku',
    'cogs.notes',
    'cogs.whitelist',
    'cogs.dpy_help',
]


class BotTree(discord.app_commands.CommandTree["TagsBot"]):
    async def on_error(
        self,
        interaction: discord.Interaction[commands.Bot],
        error: discord.app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, discord.app_commands.CheckFailure):
            return
        await self.client.errors.add_error(error=error, ctx=interaction)


class TagsBot(commands.Bot):
    def __init__(self, pool: asyncpg.Pool, session: aiohttp.ClientSession):
        super().__init__(
            intents=discord.Intents.all(),
            command_prefix="hey ",
            strip_after_prefix=True,
            activity=discord.Activity(name='hey help', type=discord.ActivityType.listening),
        )
        self.errors = errors.ErrorManager(
            bot=self,
            webhook_url=config.WEBHOOK,
            session=session,
            hijack_bot_on_error=True,
            on_command_error_settings=errors.CommandErrorSettings(
                hijack=True,
            ),
        )
        self.pool = pool

    async def sync(self):
        context_types = [0, 1, 2]
        integration_types = [0, 1]

        commands = self.tree._get_all_commands(guild=None)
        default_payload = [command.to_dict() for command in commands]

        for item in default_payload:
            item["contexts"] = context_types
            item["integration_types"] = integration_types

        app_info = await self.application_info()
        data = await self.http.bulk_upsert_global_commands(app_info.id, payload=default_payload)
        print(data)

    async def setup_hook(self) -> None:
        await self.load_extension('cogs.tags')
        await self.load_extension('cogs.notes')
        await self.load_extension('cogs.whitelist')

    @asynccontextmanager
    async def safe_connection(self, *, timeout: float = 10.0):
        """A context manager to open a transaction, but shorter."""
        async with self.pool.acquire(timeout=timeout) as connection:
            async with connection.transaction():
                yield connection

    @property
    def colour(self):
        return discord.Colour.blurple()

    @classmethod
    def run(cls, log_level: int):
        discord.utils.setup_logging(level=log_level)

        async def runner():
            async with (
                asyncpg.create_pool(config.PG_DSN) as pool,
                aiohttp.ClientSession() as session,
                cls(pool, session) as bot,
            ):
                await bot.start(config.TOKEN)

        asyncio.run(runner())

    async def on_message(self, message: discord.Message) -> None:
        if not self.user:
            return
        if re.fullmatch(rf"<@!?{self.user.id}>", message.content):
            await message.channel.send(f"My prefix is `hey `. Try saying `hey help`")
            return

        await self.process_commands(message)


if __name__ == "__main__":
    import sys

    debug = sys.argv[-1] == '--debug'
    TagsBot.run(log_level=logging.DEBUG if debug else logging.INFO)
