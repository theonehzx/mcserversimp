import asyncio
import base64
import os
import subprocess
import tarfile
import io

import discord
from discord.ext import commands
from aiohttp import web

# ============== CONFIG ==============
DISCORD_TOKEN = os.environ.get("MTUyMDI4MjA4NjE2MDcyODIwOA.G2m3Ek.oZv_xEcUNLdFDLslgb9wVPqJ8rfia_O77J-XEE")
ALLOWED_CHANNEL_ID = None  # optional: set an int channel ID to restrict commands

TMUX_SESSION = "mc"
SERVER_DIR = "~/mcserver"
JAVA_BIN = "java"  # use full SDKMAN path if needed, e.g. ~/.sdkman/candidates/java/25-tem/bin/java
JAVA_ARGS = "-Xmx8G -Xms4G -jar server.jar --nogui"
JAVA_CMD = f"cd {SERVER_DIR} && {JAVA_BIN} {JAVA_ARGS}"
CLOUDSHELL_TIMEOUT = 120

PORT = int(os.environ.get("PORT", 10000))  # Render injects PORT automatically
# ======================================


def restore_gcloud_credentials():
    """
    Restores ~/.config/gcloud from a base64-encoded tarball stored in the
    GCLOUD_CONFIG_B64 env var. Required because Render's filesystem is ephemeral
    and gcloud cloud-shell ssh needs your personal Google login to already be set up.
    """
    b64 = os.environ.get("GCLOUD_CONFIG_B64")
    if not b64:
        print("WARNING: GCLOUD_CONFIG_B64 not set — gcloud commands will fail.")
        return
    raw = base64.b64decode(b64)
    home = os.path.expanduser("~")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        tar.extractall(path=os.path.join(home, ".config"))
    print("gcloud credentials restored.")


restore_gcloud_credentials()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def channel_allowed(ctx) -> bool:
    if ALLOWED_CHANNEL_ID is None:
        return True
    return ctx.channel.id == ALLOWED_CHANNEL_ID


async def run_cloudshell(command: str, timeout: int = CLOUDSHELL_TIMEOUT) -> str:
    proc = await asyncio.create_subprocess_exec(
        "gcloud", "cloud-shell", "ssh", "--command", command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return "__TIMEOUT__"
    return out.decode(errors="ignore")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


@bot.command()
async def startmc(ctx):
    if not channel_allowed(ctx):
        return
    await ctx.send("⏳ Booting Cloud Shell and starting the Minecraft server (can take 30-60s)...")
    cmd = (
        f"tmux has-session -t {TMUX_SESSION} 2>/dev/null && echo ALREADY_RUNNING || "
        f"tmux new-session -d -s {TMUX_SESSION} \"{JAVA_CMD}\""
    )
    result = await run_cloudshell(cmd)
    if result == "__TIMEOUT__":
        await ctx.send("⚠️ Timed out reaching Cloud Shell. It may be cold-starting — try again shortly.")
    elif "ALREADY_RUNNING" in result:
        await ctx.send("ℹ️ Server was already running.")
    else:
        await ctx.send("✅ Server process started inside Cloud Shell.")


@bot.command()
async def stopmc(ctx):
    if not channel_allowed(ctx):
        return
    await ctx.send("⏳ Sending stop command (world will save)...")
    cmd = f"tmux send-keys -t {TMUX_SESSION} 'stop' Enter"
    result = await run_cloudshell(cmd)
    if result == "__TIMEOUT__":
        await ctx.send("⚠️ Timed out reaching Cloud Shell — it may already be stopped.")
    else:
        await ctx.send("✅ Stop command sent.")


# ---------- Tiny web server so Render treats this as a Web Service ----------
# Also gives an external uptime pinger something to hit, to prevent the
# free-tier 15-minute sleep.
async def health(request):
    return web.Response(text="ok")


async def run_web_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Health server listening on port {PORT}")


async def main():
    await run_web_server()
    await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
