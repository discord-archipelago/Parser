import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
import asyncio
import os
import tempfile
import re
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
TEMP_DIR = tempfile.gettempdir()
MAX_FILE_SIZE = 24 * 1024 * 1024  # 디코 25MB 제한보다 약간 낮게

# 봇 초기화
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# 사이트 감지

def detect_site(url: str) -> str:
    if re.search(r"(youtube\.com|youtu\.be)", url):
        return "youtube"
    elif re.search(r"(twitter\.com|x\.com)", url):
        return "twitter"
    elif re.search(r"pinterest\.(com|co\.kr)", url):
        return "pinterest"
    else:
        return "unknown"

# 다운로드
async def download_media(url: str, format: str, output_path: str) -> tuple[bool, str]:
    """
    url: 다운로드할 링크
    format: 'mp3' or 'mp4'
    output_path: 저장할 경로 (확장자 제외)
    반환: (성공여부, 파일경로 or 에러메시지)
    """

    if format == "mp3":
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": output_path + ".%(ext)s",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "quiet": True,
            "no_warnings": True,
        }
        expected_ext = ".mp3"
    else:  # mp4
        ydl_opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": output_path + ".%(ext)s",
            "quiet": True,
            "no_warnings": True,
            "merge_output_format": "mp4",
        }
        expected_ext = ".mp4"

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: _run_ydl(ydl_opts, url))

        # 결과 파일 찾기
        final_path = output_path + expected_ext
        if os.path.exists(final_path):
            return True, final_path

        # 다른 확장자로 저장됐을 경우 대비해서 TEMP_DIR에서 output_path로 시작하는 파일 검색
        for f in os.listdir(TEMP_DIR):
            full = os.path.join(TEMP_DIR, f)
            if full.startswith(output_path) and os.path.isfile(full):
                return True, full

        return False, "파일을 찾을 수 없음. 다운로드 실패했을 수 있음"

    except Exception as e:
        return False, str(e)


def _run_ydl(opts, url):
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


# 변환 (mp4 -> mp3)
async def convert_mp4_to_mp3(input_path: str, output_path: str) -> tuple[bool, str]:
    try:
        loop = asyncio.get_event_loop()
        proc = await loop.run_in_executor(
            None,
            lambda: os.system(f'ffmpeg -i "{input_path}" -q:a 0 -map a "{output_path}" -y -loglevel quiet')
        )
        if os.path.exists(output_path):
            return True, output_path
        return False, "변환 실패. ffmpeg가 설치되어 있는지 확인해봐"
    except Exception as e:
        return False, str(e)


# /download 커맨드

@bot.tree.command(name="download", description="유튜브/트위터/핀터레스트 링크를 mp3 또는 mp4로 다운로드")
@app_commands.describe(
    url="다운로드할 링크 (유튜브, 트위터, 핀터레스트)",
    format="다운로드 형식 선택"
)
@app_commands.choices(format=[
    app_commands.Choice(name="mp3 (음원만)", value="mp3"),
    app_commands.Choice(name="mp4 (영상)", value="mp4"),
])
async def download(interaction: discord.Interaction, url: str, format: app_commands.Choice[str]):
    await interaction.response.defer(thinking=True)

    site = detect_site(url)
    if site == "unknown":
        await interaction.followup.send("지원하지 않는 링크. 유튜브, 트위터(X), 핀터레스트만 됨")
        return

    fmt = format.value
    await interaction.followup.send(f" `{site}` 링크 감지! `{fmt}`로 다운로드 중... ")

    # 임시 파일 경로
    output_base = os.path.join(TEMP_DIR, f"dlbot_{interaction.id}")

    success, result = await download_media(url, fmt, output_base)

    if not success:
        await interaction.edit_original_response(content=f" 다운로드 실패 :(\n```{result}```")
        return

    file_size = os.path.getsize(result)
    if file_size > MAX_FILE_SIZE:
        os.remove(result)
        await interaction.edit_original_response(
            content=f" 파일이 너무 큼 ({file_size // (1024*1024)}MB). 디코 업로드 한계 초과 "
        )
        return

    try:
        filename = f"download.{fmt}"
        await interaction.edit_original_response(
            content=f" 다운로드 완료!",
            attachments=[discord.File(result, filename=filename)]
        )
    except Exception as e:
        await interaction.edit_original_response(content=f" 업로드 실패 :(\n```{e}```")
    finally:
        if os.path.exists(result):
            os.remove(result)


# /convert 커맨드

@bot.tree.command(name="convert", description="mp4 파일을 mp3로 변환")
@app_commands.describe(file="변환할 mp4 파일을 첨부해줘")
async def convert(interaction: discord.Interaction, file: discord.Attachment):
    await interaction.response.defer(thinking=True)

    if not file.filename.lower().endswith(".mp4"):
        await interaction.followup.send(" mp4 파일만 첨부 가능함 ")
        return

    if file.size > MAX_FILE_SIZE:
        await interaction.followup.send(f" 파일이 너무 큼 ({file.size // (1024*1024)}MB) ")
        return

    await interaction.followup.send("변환 중... ")

    input_path = os.path.join(TEMP_DIR, f"convert_in_{interaction.id}.mp4")
    output_path = os.path.join(TEMP_DIR, f"convert_out_{interaction.id}.mp3")

    try:
        await file.save(input_path)
        success, result = await convert_mp4_to_mp3(input_path, output_path)

        if not success:
            await interaction.edit_original_response(content=f" 변환 실패 :(\n```{result}```")
            return

        out_size = os.path.getsize(output_path)
        if out_size > MAX_FILE_SIZE:
            await interaction.edit_original_response(content=" 변환된 파일이 너무 큼 ")
            return

        await interaction.edit_original_response(
            content=" 변환 완료!",
            attachments=[discord.File(output_path, filename="converted.mp3")]
        )

    except Exception as e:
        await interaction.edit_original_response(content=f" 오류 발생 :(\n```{e}```")
    finally:
        for path in [input_path, output_path]:
            if os.path.exists(path):
                os.remove(path)

# mp4 -> gif

async def convert_mp4_to_gif(input_path: str, output_path: str, fps: int, scale: int) -> tuple[bool, str]:
    try:
        scale_filter = f"scale=iw*{scale}/100:-1"
        cmd = (
            f'ffmpeg -i "{input_path}" '
            f'-vf "{scale_filter},fps={fps},split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" '
            f'"{output_path}" -y -loglevel quiet'
        )
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: os.system(cmd))
 
        if os.path.exists(output_path):
            return True, output_path
        return False, "변환 실패. 입력 파일을 확인해봐"
    except Exception as e:
        return False, str(e)
 
 
# /togif 커맨드
@bot.tree.command(name="togif", description="mp4 -> gif")
@app_commands.describe(
    file="변환할 mp4 파일 첨부",
    fps="GIF 프레임레이트 (기본값: 15, 권장: 10~30)",
    scale="원본 대비 크기 %"
)
async def togif(
    interaction: discord.Interaction,
    file: discord.Attachment,
    fps: app_commands.Range[int, 1, 60] = 15,
    scale: app_commands.Range[int, 1, 100] = 100
):
    await interaction.response.defer(thinking=True)
 
    if not file.filename.lower().endswith(".mp4"):
        await interaction.followup.send("mp4 파일만 첨부 가능함")
        return
 
    if file.size > MAX_FILE_SIZE:
        await interaction.followup.send(f"파일이 너무 큼 ({file.size // (1024*1024)}MB);")
        return
 
    await interaction.followup.send(f"GIF 변환 중... (fps: {fps}, scale: {scale}%)")
 
    input_path = os.path.join(TEMP_DIR, f"gif_in_{interaction.id}.mp4")
    output_path = os.path.join(TEMP_DIR, f"gif_out_{interaction.id}.gif")
 
    try:
        await file.save(input_path)
        success, result = await convert_mp4_to_gif(input_path, output_path, fps, scale)
 
        if not success:
            await interaction.edit_original_response(content=f"변환 실패 \n```{result}```")
            return
 
        out_size = os.path.getsize(output_path)
        if out_size > MAX_FILE_SIZE:
            await interaction.edit_original_response(
                content=f"변환된 GIF가 너무 큼 ({out_size // (1024*1024)}MB) scale이나 fps를 줄여봐"
            )
            return
 
        await interaction.edit_original_response(
            content=f"GIF 변환 완료! (fps: {fps}, scale: {scale}%)",
            attachments=[discord.File(output_path, filename="converted.gif")]
        )
 
    except Exception as e:
        await interaction.edit_original_response(content=f"오류 발생 \n```{e}```")
    finally:
        for path in [input_path, output_path]:
            if os.path.exists(path):
                os.remove(path)
 

# ==================== 봇 시작 =======================
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f" {bot.user} 로그인 완료!")
    print(f"슬래시 커맨드 동기화 완료")


bot.run(TOKEN)