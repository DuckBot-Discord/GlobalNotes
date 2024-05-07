from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from main import TagsBot


async def tree_whitelist(interaction: discord.Interaction[TagsBot]) -> bool:
    # Ensure that you don't get locked out as an owner
    if await interaction.client.is_owner(interaction.user):
        return True

    query = "SELECT EXISTS(SELECT 1 FROM whitelist WHERE user_id = $1)"

    # bool result
    whitelisted = await interaction.client.pool.fetchval(query, interaction.user.id)

    if not whitelisted:
        await interaction.response.send_message("You do not have permission to use this bot.", ephemeral=True)

    return whitelisted


class WhitelistCog(commands.Cog):
    def __init__(self, bot: TagsBot):
        self.bot = bot
        self._original_interaction_check = bot.tree.interaction_check

    def cog_load(self):
        self.bot.tree.interaction_check = tree_whitelist

    def cog_unload(self):
        self.bot.tree.interaction_check = self._original_interaction_check

    @commands.group()
    @commands.is_owner()
    async def notes(self, ctx: commands.Context):
        """Manages the app command whitelist"""
        if not ctx.invoked_subcommand:
            await ctx.send_help(ctx.command)

    @notes.group(name='whitelist')
    async def notes_whitelist(self, ctx: commands.Context):
        """Manages the app command whitelist"""
        if not ctx.invoked_subcommand:
            await ctx.send_help(ctx.command)

    @notes_whitelist.command(name='add')
    async def notes_whitelist_add(self, ctx: commands.Context, user: discord.User):
        """Adds someone to the whitelist."""
        query = "INSERT INTO whitelist (user_id) VALUES ($1) ON CONFLICT DO NOTHING"
        await self.bot.pool.execute(query, user.id)
        await ctx.message.add_reaction("\N{WHITE HEAVY CHECK MARK}")

    @notes_whitelist.command(name='remove')
    async def notes_whitelist_remove(self, ctx: commands.Context, user: discord.User):
        """Removes someone from the whitelist."""
        query = "DELETE FROM whitelist WHERE user_id = $1 RETURNING TRUE"
        check = await self.bot.pool.fetchval(query, user.id)
        await ctx.message.add_reaction("\N{WHITE HEAVY CHECK MARK}" if check else "\N{BLACK QUESTION MARK ORNAMENT}")

    @notes_whitelist.command(name='list')
    async def notes_whitelist_list(self, ctx: commands.Context):
        """Shows the the whitelist."""
        data = await self.bot.pool.fetch("SELECT user_id FROM WHITELIST")
        if not data:
            await ctx.send('No records found...')

        formatted = ", ".join(str(user) for user in ((ctx.bot.get_user(r['user_id']) or r['user_id']) for r in data))
        await ctx.send(formatted)


async def setup(bot: TagsBot):
    await bot.add_cog(WhitelistCog(bot))
