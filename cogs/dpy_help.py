from __future__ import annotations

import asyncio
from re import Match
from typing import TYPE_CHECKING, TypedDict

import asyncpg
import discord
from aiohttp import web
from discord.ext.duck import webserver
from discord.ui.item import Item

from config import PORT

from .notes import notify_text

if TYPE_CHECKING:
    from .notes import Notes
    from main import TagsBot


class InHelpPayload(TypedDict):
    user_id: int
    thread_id: int
    owner_id: int


class ViewNotes(discord.ui.DynamicItem, template=r"NOTES:(?P<id>\d+)"):
    def __init__(self, user_id: int):
        self.user_id = user_id
        super().__init__(discord.ui.Button(label='View Notes', custom_id=f"NOTES:{user_id}"))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: Match[str]):
        return cls(int(match.group('id')))

    async def callback(self, interaction: discord.Interaction[TagsBot]):
        cog: Notes | None = interaction.client.get_cog('Notes')  # type: ignore
        if not cog:
            return await interaction.response.send_message("Service currently unavailable.", ephemeral=True)
        user = await interaction.client.fetch_user(self.user_id)
        await cog.get_notes_impl(interaction, user)


class ToggleNotifications(discord.ui.DynamicItem, template="NOTIFS_TOGGLE"):
    def __init__(self):
        super().__init__(discord.ui.Button(label='Toggle Notifications', custom_id='NOTIFS_TOGGLE'))

    async def callback(self, interaction: discord.Interaction[TagsBot]):
        query = """INSERT INTO user_settings (user_id, notifications_enabled) VALUES ($1, FALSE) 
                    ON CONFLICT (user_id) DO UPDATE SET notifications_enabled = NOT user_settings.notifications_enabled
                    RETURNING notifications_enabled"""
        current = await interaction.client.pool.fetchval(query, interaction.user.id)
        await interaction.response.send_message(notify_text("You are %s receiving notifications.", current), ephemeral=True)


class NotificationView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.add_item(ToggleNotifications())
        self.add_item(ViewNotes(user_id))


class DpyListener(webserver.WebserverCog, port=PORT):
    def __init__(self, bot: TagsBot):
        super().__init__()
        self.bot = bot
        self.message_processing_lock = asyncio.Lock()
        self.bot.add_dynamic_items(ToggleNotifications, ViewNotes)

    @webserver.route('post', '/inhelp')
    async def on_dpy_help_thread_interact(self, request: web.Request):
        """https://github.com/DuckBot-Discord/DuckBot/tree/master/cogs/dpy_help.py"""
        try:
            self.logger.info("Got request: %s", request)
            data: InHelpPayload = await request.json()
            self.logger.debug("payload: %s", data)
            async with self.message_processing_lock:
                async with self.bot.safe_connection() as conn:

                    query = "SELECT EXISTS(SELECT 1 FROM whitelist WHERE user_id = $1)"
                    whitelisted: bool = await conn.fetchval(query, data['user_id'])
                    is_owner = await self.bot.is_owner(discord.Object(data['user_id']))  # type: ignore
                    if not whitelisted and not is_owner:
                        return web.json_response({'error': 'user not whitelisted'})

                    query = "SELECT COALESCE((SELECT notifications_enabled FROM user_settings WHERE user_id = $1), TRUE)"
                    notifications_enabled = await conn.fetchval(query, data['user_id'])
                    if not notifications_enabled:
                        return web.json_response({'error': 'notifications disabled'})

                    query = """
                        SELECT EXISTS (
                            SELECT 1 FROM user_notes WHERE target_id = $1 AND NOT EXISTS (
                                SELECT 1 
                                FROM user_muted_notes
                                WHERE user_muted_notes.note_id = user_notes.id 
                                AND user_muted_notes.user_id = $2
                            )
                        )
                    """
                    has_notes = await conn.fetchval(query, data['owner_id'], data['user_id'])

                    if not has_notes:
                        return web.json_response({'error': 'user has no notes'})

                    try:
                        await conn.execute(
                            "INSERT INTO warned(user_id, thread_id) VALUES ($1, $2)",
                            data['user_id'],
                            data['thread_id'],
                        )

                        user = await self.bot.fetch_user(data['user_id'])
                        await user.send(
                            f"Hey! User <@{data['owner_id']}> has notes set! From https://discord.com/channels/336642139381301249/{data['thread_id']}",
                            view=NotificationView(data['owner_id']),
                        )

                    except asyncpg.UniqueViolationError:
                        pass
                    except discord.HTTPException:
                        pass
            return web.json_response({'status': 'ok'})
        except Exception as e:
            self.logger.error("Something went extremely wrong...", exc_info=e)
            return web.json_response({'error': str(e)}, status=500)


async def setup(bot: TagsBot):
    await bot.add_cog(DpyListener(bot))
