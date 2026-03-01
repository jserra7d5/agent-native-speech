"""Discord bot for voice channel management.

Runs in a background thread. Provides methods for the MCP server
to join/leave voice channels and access voice clients.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING, Callable

import discord
from discord import app_commands
from discord.ext import commands

from server.config import Config

if TYPE_CHECKING:
    from server.correction import CorrectionManager

log = logging.getLogger(__name__)


class VoiceBot(commands.Bot):
    """Discord bot that manages voice channel connections."""

    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

        self.config = config
        self._ready_event = asyncio.Event()
        # call_id -> voice_client mapping managed by CallManager
        self._on_user_leave: Callable[[int], None] | None = None
        self._correction_manager: CorrectionManager | None = None

        # Register slash commands on the app_commands tree
        self._register_slash_commands()

    def set_correction_manager(self, manager: CorrectionManager) -> None:
        """Wire the CorrectionManager into the bot for slash command access."""
        self._correction_manager = manager
        log.info("CorrectionManager attached to VoiceBot")

    def _register_slash_commands(self) -> None:
        """Register /correct and /corrections as application (slash) commands."""

        @self.tree.command(name="correct", description="Add an STT correction for your account")
        @app_commands.describe(
            wrong="The word/phrase as incorrectly transcribed by the STT engine",
            right="The correct word/phrase to substitute",
        )
        async def correct(interaction: discord.Interaction, wrong: str, right: str) -> None:
            if self._correction_manager is None:
                await interaction.response.send_message(
                    "Correction manager is not available yet. Please try again shortly.",
                    ephemeral=True,
                )
                return
            user_id = str(interaction.user.id)
            self._correction_manager.add_correction(user_id, wrong, right)
            log.info(
                "Slash /correct: user=%s added %r -> %r",
                user_id,
                wrong,
                right,
            )
            await interaction.response.send_message(
                f'Correction added: "{wrong}" will be replaced with "{right}".',
                ephemeral=True,
            )

        @self.tree.command(name="corrections", description="List all your STT corrections")
        async def corrections(interaction: discord.Interaction) -> None:
            if self._correction_manager is None:
                await interaction.response.send_message(
                    "Correction manager is not available yet. Please try again shortly.",
                    ephemeral=True,
                )
                return
            user_id = str(interaction.user.id)
            data = self._correction_manager.list_corrections(user_id)
            if not data:
                await interaction.response.send_message(
                    "You have no STT corrections stored.",
                    ephemeral=True,
                )
                return
            lines = [f'- "{wrong}" → "{right}"' for wrong, right in data.items()]
            body = "\n".join(lines)
            await interaction.response.send_message(
                f"Your STT corrections ({len(data)}):\n{body}",
                ephemeral=True,
            )

    async def on_ready(self) -> None:
        log.info("Discord bot ready as %s", self.user)
        # Sync slash commands with Discord so they appear in the UI
        try:
            synced = await self.tree.sync()
            log.info("Synced %d slash command(s) with Discord", len(synced))
        except Exception:
            log.exception("Failed to sync slash commands")
        self._ready_event.set()

    async def wait_until_bot_ready(self) -> None:
        await self._ready_event.wait()

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Auto-disconnect when the user leaves the voice channel.

        Also detects when the bot itself is disconnected (kicked, moved out,
        or channel deleted) and triggers the _on_user_leave callback so that
        CallManager can clean up the orphaned session.
        """
        # Handle the bot's own voice state changes first.
        if member.id == self.user.id:
            # Bot was in a channel and is now disconnected (kicked / channel deleted).
            if before.channel and not after.channel:
                log.warning(
                    "Bot was disconnected from voice channel %s (id=%d)",
                    before.channel.name,
                    before.channel.id,
                )
                if self._on_user_leave:
                    self._on_user_leave(before.channel.id)
            return

        if member.bot:
            return

        # User left a channel where the bot is connected
        if before.channel and not after.channel:
            voice_client = self._get_vc_for_channel(before.channel.id)
            if voice_client and self._channel_empty(before.channel):
                log.info(
                    "All users left channel %s, disconnecting", before.channel.name
                )
                if self._on_user_leave:
                    self._on_user_leave(before.channel.id)
                await voice_client.disconnect()

        # User moved channels
        elif before.channel and after.channel and before.channel != after.channel:
            voice_client = self._get_vc_for_channel(before.channel.id)
            if voice_client and self._channel_empty(before.channel):
                log.info(
                    "All users left channel %s, disconnecting", before.channel.name
                )
                if self._on_user_leave:
                    self._on_user_leave(before.channel.id)
                await voice_client.disconnect()

    def _get_vc_for_channel(self, channel_id: int) -> discord.VoiceClient | None:
        for vc in self.voice_clients:
            if vc.channel and vc.channel.id == channel_id:
                return vc
        return None

    def _channel_empty(self, channel: discord.abc.GuildChannel) -> bool:
        """Check if a voice channel has no non-bot members."""
        if not isinstance(channel, discord.VoiceChannel):
            return True
        return all(m.bot for m in channel.members)

    async def join_voice_channel(self, channel_id: int) -> discord.VoiceClient:
        """Join a voice channel by ID. Returns the VoiceClient."""
        await self.wait_until_bot_ready()

        channel = self.get_channel(channel_id)
        if channel is None:
            raise ValueError(f"Channel {channel_id} not found")
        if not isinstance(channel, discord.VoiceChannel):
            raise ValueError(f"Channel {channel_id} is not a voice channel")

        # Already connected to this channel?
        existing = self._get_vc_for_channel(channel_id)
        if existing and existing.is_connected():
            return existing

        try:
            # Import voice_recv for the enhanced voice client
            import discord.ext.voice_recv as voice_recv

            vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
        except ImportError:
            log.warning(
                "discord-ext-voice-recv not installed, using standard VoiceClient"
            )
            vc = await channel.connect()

        log.info("Joined voice channel: %s", channel.name)
        return vc

    async def leave_voice_channel(self, channel_id: int) -> None:
        """Leave a voice channel by ID."""
        vc = self._get_vc_for_channel(channel_id)
        if vc and vc.is_connected():
            await vc.disconnect()
            log.info("Left voice channel %d", channel_id)

    async def get_text_channel(
        self, guild_id: int, channel_id: int
    ) -> discord.TextChannel | None:
        """Get a text channel for posting transcripts."""
        guild = self.get_guild(guild_id)
        if guild is None:
            return None
        # Find the text channel associated with the voice channel's category,
        # or fall back to the first text channel
        voice_channel = self.get_channel(channel_id)
        if voice_channel and hasattr(voice_channel, "category"):
            category = voice_channel.category
            if category:
                for ch in category.text_channels:
                    return ch
        # Fall back to system channel or first text channel
        if guild.system_channel:
            return guild.system_channel
        for ch in guild.text_channels:
            return ch
        return None


class BotRunner:
    """Manages running the Discord bot in a background thread."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.bot = VoiceBot(config)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        """Start the bot in a background thread."""
        self._thread = threading.Thread(target=self._run, daemon=True, name="discord-bot")
        self._thread.start()
        log.info("Discord bot thread started")

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self.bot.start(self.config.discord_token))
        except Exception:
            log.exception("Discord bot crashed")
        finally:
            self._loop.close()

    def run_coroutine(self, coro):
        """Run an async coroutine from a sync context (the MCP thread)."""
        if self._loop is None or self._loop.is_closed():
            raise RuntimeError("Bot event loop is not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=30)

    def run_coroutine_async(self, coro):
        """Schedule a coroutine and return the future (non-blocking)."""
        if self._loop is None or self._loop.is_closed():
            raise RuntimeError("Bot event loop is not running")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def shutdown(self) -> None:
        """Gracefully shut down the bot."""
        if self._loop and not self._loop.is_closed():
            future = asyncio.run_coroutine_threadsafe(self.bot.close(), self._loop)
            try:
                future.result(timeout=10)
            except Exception:
                log.exception("Error during bot shutdown")
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Discord bot shut down")
