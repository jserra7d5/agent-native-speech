"""Discord bot for voice channel management.

Runs in a background thread. Provides methods for the MCP server
to join/leave voice channels and access voice clients.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import TYPE_CHECKING, Callable

import discord
from discord import app_commands
from discord.ext import commands

from server.config import Config

if TYPE_CHECKING:
    from server.correction import CorrectionManager
    from server.session_browser import SessionBrowser
    from server.session_manager import SessionManager
    from server.spawn import SpawnManager
    from server.speech_mode import SpeechModeManager

log = logging.getLogger(__name__)


def _patch_voice_recv_router() -> None:
    """Monkey-patch PacketRouter._do_run to handle OpusError gracefully.

    The upstream discord-ext-voice-recv PacketRouter crashes the entire
    audio receive thread on a single corrupted Opus packet (e.g. the first
    packet after TTS playback ends).  This patch wraps the decode call in
    a try/except so the router continues processing subsequent packets.
    """
    try:
        from discord.ext.voice_recv.router import PacketRouter
        from discord.opus import OpusError
    except ImportError:
        return

    def _resilient_do_run(self) -> None:
        while not self._end_thread.is_set():
            self.waiter.wait()
            with self._lock:
                for decoder in self.waiter.items:
                    try:
                        data = decoder.pop_data()
                    except OpusError:
                        log.debug("Skipping corrupted Opus packet (non-fatal)")
                        continue
                    if data is not None:
                        self.sink.write(data.source, data)

    PacketRouter._do_run = _resilient_do_run
    log.info("Patched PacketRouter._do_run to handle OpusError gracefully")


def _patch_voice_recv_dave_decrypt() -> None:
    """Monkey-patch AudioReader.callback to add DAVE E2EE decryption.

    discord-ext-voice-recv only handles transport-layer decryption
    (aead_xchacha20_poly1305_rtpsize).  Discord now mandates DAVE E2EE,
    which adds a second encryption layer on the Opus payload.  Without
    this patch the Opus decoder receives still-encrypted data and produces
    garbled audio.

    This patch inserts a dave_session.decrypt() call between transport
    decryption and the rest of the packet processing pipeline.
    """
    try:
        from discord.ext.voice_recv.reader import AudioReader
        from discord.ext.voice_recv import rtp
        from discord.ext.voice_recv.rtp import ReceiverReportPacket
        from nacl.exceptions import CryptoError
        import davey
    except ImportError:
        log.warning("Cannot patch DAVE decrypt — missing imports")
        return

    def _dave_aware_callback(self, packet_data: bytes) -> None:
        packet = rtp_packet = rtcp_packet = None
        try:
            if not rtp.is_rtcp(packet_data):
                packet = rtp_packet = rtp.decode_rtp(packet_data)
                packet.decrypted_data = self.decryptor.decrypt_rtp(packet)

                # --- DAVE E2EE decryption ---
                conn = self.voice_client._connection
                ssrc = rtp_packet.ssrc
                user_id = self.voice_client._get_id_from_ssrc(ssrc)

                # Log DAVE state periodically for debugging
                if not hasattr(_dave_aware_callback, '_log_count'):
                    _dave_aware_callback._log_count = 0
                _dave_aware_callback._log_count += 1
                if _dave_aware_callback._log_count <= 3 or _dave_aware_callback._log_count % 200 == 0:
                    log.info(
                        "DAVE state [pkt#%d]: session=%s version=%d ready=%s "
                        "ssrc=%s user_id=%s passthrough=%s",
                        _dave_aware_callback._log_count,
                        conn.dave_session is not None,
                        conn.dave_protocol_version,
                        conn.dave_session.ready if conn.dave_session else "N/A",
                        ssrc, user_id,
                        conn.dave_session.can_passthrough(user_id) if (conn.dave_session and user_id) else "N/A",
                    )

                if conn.dave_session is not None and conn.dave_protocol_version > 0:
                    if user_id is not None:
                        if conn.dave_session.ready and not conn.dave_session.can_passthrough(user_id):
                            try:
                                decrypted = conn.dave_session.decrypt(
                                    user_id,
                                    davey.MediaType.audio,
                                    packet.decrypted_data,
                                )
                                packet.decrypted_data = bytes(decrypted)
                            except Exception as e:
                                if _dave_aware_callback._log_count <= 5 or _dave_aware_callback._log_count % 100 == 0:
                                    log.warning(
                                        "DAVE decrypt failed [pkt#%d] ssrc=%s user=%s: %s",
                                        _dave_aware_callback._log_count, ssrc, user_id, e,
                                    )
                                return
                        elif conn.dave_session.can_passthrough(user_id):
                            pass  # passthrough mode — no DAVE decryption needed
                        elif not conn.dave_session.ready:
                            if _dave_aware_callback._log_count <= 5:
                                log.info("DAVE session not ready yet, passing through packet")
                # --- end DAVE decryption ---

            else:
                packet = rtcp_packet = rtp.decode_rtcp(
                    self.decryptor.decrypt_rtcp(packet_data)
                )
                if not isinstance(packet, ReceiverReportPacket):
                    log.info(
                        "Received unexpected rtcp packet: type=%s, %s",
                        packet.type, type(packet),
                    )
        except CryptoError:
            log.error("CryptoError decoding packet data")
            return
        except Exception:
            if self._is_ip_discovery_packet(packet_data):
                return
            log.exception("Error unpacking packet")
        finally:
            if self.error:
                self.stop()
                return
            if not packet:
                return

        if rtcp_packet:
            self.packet_router.feed_rtcp(rtcp_packet)
        elif rtp_packet:
            ssrc = rtp_packet.ssrc
            if ssrc not in self.voice_client._ssrc_to_id:
                if rtp_packet.is_silence():
                    return
                else:
                    log.info("Received packet for unknown ssrc %s", ssrc)

            self.speaking_timer.notify(ssrc)
            try:
                self.packet_router.feed_rtp(rtp_packet)
            except Exception as e:
                log.exception("Error processing rtp packet")
                self.error = e
                self.stop()

    AudioReader.callback = _dave_aware_callback
    log.info("Patched AudioReader.callback with DAVE E2EE decryption support")


_patch_voice_recv_router()
_patch_voice_recv_dave_decrypt()


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
        self._speech_mode_manager: SpeechModeManager | None = None
        self._spawn_manager: SpawnManager | None = None
        self._session_manager: SessionManager | None = None
        self._session_browser: SessionBrowser | None = None

        # Register slash commands on the app_commands tree
        self._register_slash_commands()

    def set_correction_manager(self, manager: CorrectionManager) -> None:
        """Wire the CorrectionManager into the bot for slash command access."""
        self._correction_manager = manager
        log.info("CorrectionManager attached to VoiceBot")

    def set_speech_mode_manager(self, manager: SpeechModeManager) -> None:
        """Wire the SpeechModeManager into the bot for slash command access."""
        self._speech_mode_manager = manager
        log.info("SpeechModeManager attached to VoiceBot")

    def set_spawn_manager(self, manager: SpawnManager) -> None:
        """Wire the SpawnManager into the bot for /spawn command access."""
        self._spawn_manager = manager
        log.info("SpawnManager attached to VoiceBot")

    def set_session_manager(self, manager: SessionManager) -> None:
        """Wire the SessionManager into the bot for session commands."""
        self._session_manager = manager
        log.info("SessionManager attached to VoiceBot")

    def set_session_browser(self, browser: SessionBrowser) -> None:
        """Wire the SessionBrowser into the bot for /sessions browsing."""
        self._session_browser = browser
        log.info("SessionBrowser attached to VoiceBot")

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

        @self.tree.command(
            name="mode",
            description="Set the speech completion mode (pause or stop_token)",
        )
        @app_commands.describe(
            mode="Speech completion mode: 'pause' (silence detection) or 'stop_token' (keyword)",
            stop_word="Stop word for stop_token mode (e.g. 'over')",
        )
        @app_commands.choices(mode=[
            app_commands.Choice(name="pause", value="pause"),
            app_commands.Choice(name="stop_token", value="stop_token"),
        ])
        async def mode_cmd(
            interaction: discord.Interaction,
            mode: app_commands.Choice[str],
            stop_word: str | None = None,
        ) -> None:
            if self._speech_mode_manager is None:
                await interaction.response.send_message(
                    "Speech mode manager is not available yet. Please try again shortly.",
                    ephemeral=True,
                )
                return
            result = self._speech_mode_manager.set_mode(mode.value, stop_word=stop_word)
            log.info(
                "Slash /mode: user=%s set mode=%s stop_word=%s",
                interaction.user.id, result["mode"], result["stop_word"],
            )
            await interaction.response.send_message(
                f"Speech mode set to **{result['mode']}** (stop word: \"{result['stop_word']}\").",
                ephemeral=True,
            )

        @self.tree.command(
            name="stopword",
            description="Change the stop word for stop_token speech mode",
        )
        @app_commands.describe(word="The new stop word (e.g. 'over', 'done', 'end')")
        async def stopword_cmd(interaction: discord.Interaction, word: str) -> None:
            if self._speech_mode_manager is None:
                await interaction.response.send_message(
                    "Speech mode manager is not available yet. Please try again shortly.",
                    ephemeral=True,
                )
                return
            current_mode = self._speech_mode_manager.get_mode()
            result = self._speech_mode_manager.set_mode(current_mode, stop_word=word)
            log.info(
                "Slash /stopword: user=%s set stop_word=%s",
                interaction.user.id, result["stop_word"],
            )
            await interaction.response.send_message(
                f"Stop word updated to \"{result['stop_word']}\" (mode: {result['mode']}).",
                ephemeral=True,
            )

        @self.tree.command(
            name="spawn",
            description="Launch a coding agent CLI in a terminal on the host PC",
        )
        @app_commands.describe(
            directory="Absolute path to the project working directory",
            cli="CLI client: 'claude' or 'codex' (default: configured)",
            voice="TTS voice profile name (default: next from pool)",
            headless="Run without a terminal window (default: False)",
        )
        async def spawn_cmd(
            interaction: discord.Interaction,
            directory: str,
            cli: str | None = None,
            voice: str | None = None,
            headless: bool = False,
        ) -> None:
            if self._spawn_manager is None or self._session_manager is None:
                await interaction.response.send_message(
                    "Spawn system is not available yet. Please try again shortly.",
                    ephemeral=True,
                )
                return

            resolved_cli = cli or self._spawn_manager._config.spawn.default_cli
            await interaction.response.send_message(
                f"Spawning {resolved_cli} in {directory}...",
                ephemeral=True,
            )

            try:
                result = self._spawn_manager.spawn_session(
                    directory=directory,
                    cli=cli,
                    voice=voice,
                    headless=headless,
                    user_id=str(interaction.user.id),
                )
            except (ValueError, RuntimeError) as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return

            # Register the session in the session manager
            session = self._session_manager.register_session(
                session_name=result["session_name"],
                client_type=result["cli"],
                directory=result["directory"],
                spawn_mode="headless" if result["headless"] else "interactive",
                process_pid=result.get("process_pid"),
                terminal_pid=result.get("terminal_pid"),
                owning_user_id=result.get("user_id", ""),
                requested_voice=result.get("voice"),
            )

            mode = "headless" if headless else "terminal"
            await interaction.followup.send(
                f"Spawned **{resolved_cli}** in `{directory}` ({mode}, "
                f"voice: {session.voice_name}). Agent will call you shortly.",
                ephemeral=True,
            )
            log.info(
                "Slash /spawn: user=%s spawned %s in %s (session=%s, voice=%s)",
                interaction.user.id, resolved_cli, directory,
                session.session_id, session.voice_name,
            )

        @self.tree.command(
            name="sessions",
            description="List active voice sessions or browse session history",
        )
        @app_commands.describe(
            directory="Browse sessions from a project directory (requires SessionBrowser)",
            recent="List N most recent sessions from history",
            cli="Filter by CLI type: 'claude' or 'codex'",
        )
        async def sessions_cmd(
            interaction: discord.Interaction,
            directory: str | None = None,
            recent: int | None = None,
            cli: str | None = None,
        ) -> None:
            # If directory or recent requested, try SessionBrowser
            if directory is not None or recent is not None:
                if self._session_browser is None:
                    await interaction.response.send_message(
                        "Session browser is not available yet. Showing active sessions only.",
                        ephemeral=True,
                    )
                else:
                    try:
                        if recent is not None:
                            entries = self._session_browser.list_recent(
                                n=recent, cli_filter=cli,
                            )
                        elif directory is not None:
                            if cli == "codex":
                                entries = self._session_browser.list_codex_sessions(directory)
                            else:
                                entries = self._session_browser.list_claude_sessions(directory)
                        else:
                            entries = []

                        if not entries:
                            await interaction.response.send_message(
                                "No sessions found.", ephemeral=True,
                            )
                            return

                        lines = []
                        for idx, entry in enumerate(entries, 1):
                            summary = entry.summary or entry.session_id
                            line = f"{idx}. **{summary}**"
                            if entry.cli:
                                line += f" ({entry.cli})"
                            if entry.directory:
                                line += f" — `{entry.directory}`"
                            if entry.git_branch:
                                line += f" — branch: {entry.git_branch}"
                            lines.append(line)

                        body = "\n".join(lines)
                        await interaction.response.send_message(
                            f"Sessions ({len(entries)}):\n{body}",
                            ephemeral=True,
                        )
                        return
                    except Exception as exc:
                        log.warning("SessionBrowser error: %s", exc)
                        await interaction.response.send_message(
                            f"Session browser error: {exc}\nFalling back to active sessions.",
                            ephemeral=True,
                        )
                        return

            # Default: show active sessions from SessionManager
            if self._session_manager is None:
                await interaction.response.send_message(
                    "Session manager is not available yet. Please try again shortly.",
                    ephemeral=True,
                )
                return

            active = self._session_manager.list_active_sessions()
            if not active:
                await interaction.response.send_message(
                    "No active sessions.", ephemeral=True,
                )
                return

            lines = []
            now = time.time()
            for idx, s in enumerate(active, 1):
                # Parse started_at for relative time
                try:
                    started = time.mktime(
                        time.strptime(s["started_at"], "%Y-%m-%dT%H:%M:%SZ")
                    ) - time.timezone
                    elapsed = now - started
                    if elapsed < 60:
                        rel = f"{int(elapsed)}s ago"
                    elif elapsed < 3600:
                        rel = f"{int(elapsed // 60)}m ago"
                    else:
                        rel = f"{elapsed / 3600:.1f}h ago"
                except (KeyError, ValueError):
                    rel = "unknown"

                line = (
                    f"{idx}. **{s['session_name']}** ({s['client_type']}, "
                    f"{s['status']}) — voice: {s['voice']} — started {rel}"
                )
                count = s.get("queued_message_count", 0)
                if count:
                    line += f" — {count} queued message(s)"
                lines.append(line)

            body = "\n".join(lines)
            await interaction.response.send_message(
                f"Active sessions ({len(active)}):\n{body}",
                ephemeral=True,
            )

        @self.tree.command(
            name="kill",
            description="Terminate an active voice session",
        )
        @app_commands.describe(session="Session name or ID to terminate")
        async def kill_cmd(interaction: discord.Interaction, session: str) -> None:
            if self._session_manager is None or self._spawn_manager is None:
                await interaction.response.send_message(
                    "Session management is not available yet. Please try again shortly.",
                    ephemeral=True,
                )
                return

            active = self._session_manager.list_active_sessions()
            matched = None
            for s in active:
                if s["session_id"] == session or s["session_name"] == session:
                    matched = s
                    break

            if matched is None:
                await interaction.response.send_message(
                    f"Session not found: {session}", ephemeral=True,
                )
                return

            # Get full session object for PIDs
            try:
                full_session = self._session_manager.get_session(matched["session_id"])
            except KeyError:
                await interaction.response.send_message(
                    f"Session not found: {session}", ephemeral=True,
                )
                return

            self._spawn_manager.kill_session(
                process_pid=full_session.process_pid,
                terminal_pid=full_session.terminal_pid,
            )
            self._session_manager.unregister_session(matched["session_id"])

            name = matched["session_name"]
            log.info(
                "Slash /kill: user=%s terminated session %s (%s)",
                interaction.user.id, matched["session_id"], name,
            )
            await interaction.response.send_message(
                f"Session {name} terminated.", ephemeral=True,
            )

        @self.tree.command(
            name="resume",
            description="Resume a previous CLI session",
        )
        @app_commands.describe(
            session_id="Session ID to resume",
            voice="TTS voice profile name (default: next from pool)",
            headless="Run without a terminal window (default: False)",
        )
        async def resume_cmd(
            interaction: discord.Interaction,
            session_id: str,
            voice: str | None = None,
            headless: bool = False,
        ) -> None:
            if self._spawn_manager is None or self._session_manager is None:
                await interaction.response.send_message(
                    "Spawn system is not available yet. Please try again shortly.",
                    ephemeral=True,
                )
                return

            # Look up session metadata via SessionBrowser if available
            cli_type = None
            directory = None
            if self._session_browser is not None:
                try:
                    meta = self._session_browser.find_session(session_id)
                    if meta is not None:
                        cli_type = meta.cli
                        directory = meta.directory
                except Exception as exc:
                    log.warning("Failed to find session %s: %s", session_id, exc)

            if cli_type is None or cli_type == "unknown":
                cli_type = self._spawn_manager.default_cli

            await interaction.response.send_message(
                f"Resuming {cli_type} session `{session_id}`...",
                ephemeral=True,
            )

            try:
                # Use original project directory, fall back to home
                work_dir = directory or os.path.expanduser("~")
                result = self._spawn_manager.spawn_session(
                    directory=work_dir,
                    cli=cli_type,
                    voice=voice,
                    headless=headless,
                    user_id=str(interaction.user.id),
                    resume_session_id=session_id,
                )
            except (ValueError, RuntimeError) as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return

            session = self._session_manager.register_session(
                session_name=result["session_name"],
                client_type=result["cli"],
                directory=result["directory"],
                spawn_mode="headless" if result["headless"] else "interactive",
                process_pid=result.get("process_pid"),
                terminal_pid=result.get("terminal_pid"),
                owning_user_id=result.get("user_id", ""),
                requested_voice=result.get("voice"),
            )

            mode = "headless" if headless else "terminal"
            await interaction.followup.send(
                f"Resumed **{cli_type}** session `{session_id}` ({mode}, "
                f"voice: {session.voice_name}).",
                ephemeral=True,
            )
            log.info(
                "Slash /resume: user=%s resumed %s session %s (new_session=%s)",
                interaction.user.id, cli_type, session_id, session.session_id,
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

    async def find_user_voice_channel_any(self) -> int | None:
        """Find the first voice channel with a non-bot user.

        Iterates all guilds the bot is in, checks each voice channel's
        members for a non-bot user, and returns that channel's ID.
        Returns None if no user is found in any voice channel.
        """
        await self.wait_until_bot_ready()
        for guild in self.guilds:
            for channel in guild.voice_channels:
                for member in channel.members:
                    if not member.bot:
                        return channel.id
        return None

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
