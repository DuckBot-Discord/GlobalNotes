from __future__ import annotations

import asyncio
from textwrap import indent
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import menus
from discord.ext import commands


from .utils.menus import ViewMenuPages

if TYPE_CHECKING:
    from asyncpg import Record

    from main import TagsBot


"""
CREATE TABLE IF NOT EXISTS user_notes (
    id BIGSERIAL,
    user_id BIGINT NOT NULL,
    target_id BIGINT NOT NULL,
    content VARCHAR(2000) DEFAULT 'was a retard',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id)
); 
"""

NOTIFICATIONS_EMOJI = {True: '\N{BELL}', False: '\N{BELL WITH CANCELLATION STROKE}'}
TOGGLE_TEXT = {True: "now", False: "no longer"}
GET_NOTES_FROM_USER = """
    SELECT 
        id, 
        user_id, 
        target_id, 
        content, 
        created_at,
        EXISTS (
            SELECT 1 
            FROM user_muted_notes 
            WHERE user_muted_notes.note_id = user_notes.id 
            AND user_muted_notes.user_id = $2
        ) AS muted
    FROM user_notes WHERE target_id = $1 ORDER BY created_at DESC
"""


def notify_text(text: str, value: bool):
    return NOTIFICATIONS_EMOJI[value] + (text % TOGGLE_TEXT[value])


class NotesMenu(ViewMenuPages):
    source: NotesFormatter

    @property
    def current_data(self):
        if self.source.per_page != 1:
            raise RuntimeError("Per page is not 1.")
        return self.source.entries[self.current_page]

    @discord.ui.button(emoji=NOTIFICATIONS_EMOJI[True])
    async def toggle_notifs_for_note(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.bot.safe_connection() as conn:
            if self.current_data['muted']:
                query = "DELETE FROM user_muted_notes WHERE note_id = $1 AND user_id = $2"
            else:
                query = "INSERT INTO user_muted_notes (note_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING"
            await conn.execute(query, self.current_data['id'], interaction.user.id)

            sql = """
                SELECT 
                    id, 
                    user_id, 
                    target_id, 
                    content, 
                    created_at,
                    EXISTS (
                        SELECT 1 
                        FROM user_muted_notes 
                        WHERE user_muted_notes.note_id = user_notes.id 
                        AND user_muted_notes.user_id = $2
                    ) AS muted
                FROM user_notes WHERE id = $1"""

            new_data = await conn.fetchrow(sql, self.current_data['id'], interaction.user.id)
            self.source.entries[self.current_page] = new_data or self.source.entries[self.current_page]
            await self.show_checked_page(interaction, self.current_page)
            await interaction.followup.send(
                notify_text("You will %s get notified for that note.", not self.current_data['muted']), ephemeral=True
            )

    @discord.ui.button(emoji='\N{WASTEBASKET}')
    async def delete_note(self, interaction: discord.Interaction, button: discord.ui.Button):
        query = "DELETE FROM user_notes WHERE id = $1 AND user_id = $2"
        await self.bot.pool.execute(query, self.current_data['id'], interaction.user.id)
        data = await self.bot.pool.fetch(GET_NOTES_FROM_USER, self.current_data['target_id'], interaction.user.id)
        self.update_source(NotesFormatter(data))
        await self.show_checked_page(interaction, self.current_page)

    def _update_labels(self, page_number: int) -> None:
        data = self.current_data
        super()._update_labels(page_number)
        self.toggle_notifs_for_note.emoji = NOTIFICATIONS_EMOJI[data['muted']]
        self.delete_note.disabled = data['user_id'] != self.owner.id

    def fill_items(self) -> None:
        super().fill_items()
        self.remove_item(self.stop_pages)
        if self.source.per_page == 1:
            if self.compact:
                self.stop_pages.row = 2
                self.toggle_notifs_for_note.row = 2
                self.delete_note.row = 2
            self.add_item(self.toggle_notifs_for_note)
            self.add_item(self.delete_note)
        self.add_item(self.stop_pages)


class NotesFormatter(menus.ListPageSource):
    def __init__(self, notes: list[Record]):
        super().__init__(notes, per_page=1)

    async def format_page(self, menu: ViewMenuPages, record: Record):
        user = await menu.bot.fetch_user(record['user_id'])
        target = await menu.bot.fetch_user(record['target_id'])
        return (
            discord.Embed(
                description=record['content'],
                color=user.accent_colour or menu.bot.colour,
                timestamp=record['created_at'],
            )
            .set_author(name=user.display_name, icon_url=user.display_avatar.url)
            .set_footer(
                text=NOTIFICATIONS_EMOJI[not record['muted']]
                + f"{target.display_name} "
                + (f"({menu.current_page+1}/{count})" if (count := self.get_max_pages()) > 1 else "")
                + f"(ID: {record['id']})",
                icon_url=target.display_avatar.url,
            )
        )


def short(text: str, length: int):
    """Shortens a bit of text with ellipses."""
    if len(text) > length:
        return text[: length - 3] + '...'
    else:
        return text


class AddNoteModal(discord.ui.Modal):
    content = discord.ui.TextInput(
        label='',
        placeholder='Any whitelisted user can see these! (You can add more than one note, if you run out of space.)',
        style=discord.TextStyle.long,
    )

    def __init__(self, owner: discord.abc.User, target: discord.abc.User):
        super().__init__(title="Adding global user note.")
        self.owner = owner
        self.target = target
        self.content.label = f"Note for {target}"

    async def on_submit(self, interaction: discord.Interaction[TagsBot]) -> None:
        async with interaction.client.safe_connection() as conn:
            query = "INSERT INTO user_notes (user_id, target_id, content, created_at) VALUES ($1, $2, $3, $4)"
            await conn.execute(query, self.owner.id, self.target.id, self.content.value, interaction.created_at)
            await interaction.response.send_message("\N{WHITE HEAVY CHECK MARK}", ephemeral=True, delete_after=1)


class Notes(commands.Cog):
    """For keeping track of the dumbasses"""

    def __init__(self, bot: TagsBot):
        super().__init__()
        self.bot = bot

        self.get_ctx_menu = app_commands.ContextMenu(
            name="Get Global Note(s)",
            callback=self.get_notes_impl,
        )

        self.add_ctx_menu = app_commands.ContextMenu(
            name="Add Global Note",
            callback=self.add_note_impl,
        )

        self.bot.tree.add_command(self.get_ctx_menu)
        self.bot.tree.add_command(self.add_ctx_menu)
        self.message_processing_lock = asyncio.Lock()

    async def cog_unload(self) -> None:
        await super().cog_unload()
        self.bot.tree.remove_command(self.get_ctx_menu.name, type=self.get_ctx_menu.type)
        self.bot.tree.remove_command(self.add_ctx_menu.name, type=self.add_ctx_menu.type)

    async def get_notes_impl(self, interaction: discord.Interaction[TagsBot], user: discord.User):
        data = await self.bot.pool.fetch(GET_NOTES_FROM_USER, user.id, interaction.user.id)
        if not data:
            return await interaction.response.send_message("No notes found...", ephemeral=True, delete_after=5)
        await NotesMenu(NotesFormatter(data), interaction=interaction, compact=True).start()

    async def add_note_impl(self, interaction: discord.Interaction, user: discord.User):
        await interaction.response.send_modal(AddNoteModal(interaction.user, user))

    notes = app_commands.Group(name='notes', description='Notes for skid shitheads.')

    @notes.command(name='get')
    async def get_notes_app_command(self, interaction: discord.Interaction[TagsBot], user: discord.User):
        """Gets all notes for a user.

        Parameters
        ----------
        user: discord.User
            The user whose notes to retrieve.
        """
        await self.get_notes_impl(interaction, user)

    @notes.command(name='add')
    async def add_note_app_command(self, interaction: discord.Interaction, user: discord.User):
        """Adds a note to a user via a modal.

        Parameters
        ----------
        user: discord.User
            The user to add the note to.
        """
        await self.add_note_impl(interaction, user)

    @notes.command(name='remove')
    @app_commands.rename(note_id='note-id')
    async def note_remove(self, interaction: discord.Interaction, user: discord.User, note_id: int):
        """Removes a note from a user.

        Parameters
        ----------
        user: discord.User
            The user to search notes for.
        note_id: int
            The note to remove. Pass a user for further filtering.
        """
        async with self.bot.safe_connection() as conn:
            query = "DELETE FROM user_notes WHERE id = $1 AND (user_id = $2 OR $3 = TRUE) returning content"
            content = await conn.fetchval(query, note_id, interaction.user.id, await self.bot.is_owner(interaction.user))
            if content is None:
                await interaction.response.send_message("Could not delete note, are you sure it exists?", ephemeral=True)
            else:
                await interaction.response.send_message(
                    "Successfully deleted the following quote:\n" + indent(content, '> '), ephemeral=True
                )

    @note_remove.autocomplete("note_id")
    async def note_id_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice]:
        if not interaction.namespace.user:
            return [app_commands.Choice(value=-1, name="No user provided...")]
        if await self.bot.is_owner(interaction.user):
            query = "SELECT id, content FROM user_notes WHERE target_id = $1"
            data = await self.bot.pool.fetch(query, interaction.namespace.user.id)
        else:
            query = "SELECT id, content FROM user_notes WHERE user_id = $1 and target_id = $2"
            data = await self.bot.pool.fetch(query, interaction.user.id, interaction.namespace.user.id)

        d = [app_commands.Choice(value=-1, name="No notes found...")]
        return [app_commands.Choice(name=short(f"({entry['id']}) {entry['content']}", 100), value=10) for entry in data] or d


async def setup(bot: TagsBot):
    await bot.add_cog(Notes(bot))
