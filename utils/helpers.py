# widhvans/store/widhvans-store-a32dae6d5f5487c7bc78b13e2cdc18082aef6c58/utils/helpers.py

import re
import aiohttp
import base64
import logging
import PTN
import asyncio
from imdb import Cinemagoer
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant, ChatAdminRequired, ChannelInvalid, PeerIdInvalid, ChannelPrivate
from config import Config
from database.db import get_user, remove_from_list, update_user
from features.poster import get_poster
from features.shortener import get_shortlink
from thefuzz import fuzz

logger = logging.getLogger(__name__)

PHOTO_CAPTION_LIMIT = 1024
TEXT_MESSAGE_LIMIT = 4096

ia = Cinemagoer()

# ================= GLOBAL STABILITY LAYER =================

# IMDb Stability
imdb_cache = {}
imdb_lock = asyncio.Lock()
imdb_semaphore = asyncio.Semaphore(2)
IMDB_CACHE_TTL = 600  # 10 minutes

# TMDB Stability
tmdb_cache = {}
tmdb_lock = asyncio.Lock()
tmdb_semaphore = asyncio.Semaphore(3)
TMDB_CACHE_TTL = 600  # 10 minutes

# ===== GLOBAL FAST MOVIE CACHE (Performance Upgrade) =====
movie_lookup_cache = {}
movie_lookup_lock = asyncio.Lock()

# --- DECREED ADDITION: START ---
# A comprehensive map for detecting languages from filenames.
# This map handles various abbreviations and full names, mapping them to a standard format.
LANGUAGE_MAP = {
    'hin': 'Hindi', 'hindi': 'Hindi',
    'eng': 'English', 'english': 'English',
    'tam': 'Tamil', 'tamil': 'Tamil',
    'tel': 'Telugu', 'telugu': 'Telugu',
    'mal': 'Malayalam', 'malayalam': 'Malayalam',
    'kan': 'Kannada', 'kannada': 'Kannada',
    'pun': 'Punjabi', 'punjabi': 'Punjabi',
    'jap': 'Japanese', 'japanese': 'Japanese',
    'kor': 'Korean', 'korean': 'Korean',
    'chi': 'Chinese', 'chinese': 'Chinese',
    'fre': 'French', 'french': 'French',
    'ger': 'German', 'german': 'German',
    'spa': 'Spanish', 'spanish': 'Spanish',
    'ita': 'Italian', 'italian': 'Italian',
    'rus': 'Russian', 'russian': 'Russian',
    'ara': 'Arabic', 'arabic': 'Arabic',
    'tur': 'Turkish', 'turkish': 'Turkish',
    'ind': 'Indonesian', 'indonesian': 'Indonesian',
    'multi': 'Multi-Audio', 'dual': 'Dual-Audio'
}
# --- DECREED ADDITION: END ---

def simple_clean_filename(name: str) -> str:
    """
    A simple, synchronous function to clean a filename for display purposes.
    Removes brackets, extensions, and extra whitespace.
    """
    clean_name = ".".join(name.split('.')[:-1]) if '.' in name else name
    clean_name = re.sub(r'[\(\[\{].*?[\)\]\}]', '', clean_name)
    clean_name = clean_name.replace('.', ' ').replace('_', ' ').strip()
    clean_name = re.sub(r'\s+', ' ', clean_name).strip()
    return clean_name

def go_back_button(user_id):
    """Creates a standard 'Go Back' button to return to the main menu."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("« Go Back", callback_data=f"go_back_{user_id}")]])

def format_bytes(size):
    """Converts bytes to a human-readable format with custom rounding."""
    if not isinstance(size, (int, float)) or size == 0:
        return ""
    power = 1024
    n = 0
    power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size >= power and n < len(power_labels) - 1:
        size /= power
        n += 1
    if n >= 3: return f"{size:.1f} {power_labels[n]}"
    elif n == 2: return f"{round(size)} {power_labels[n]}"
    else: return f"{int(size)} {power_labels[n]}"

async def get_definitive_title_from_imdb(title_from_filename):
    if not title_from_filename:
        return None, None

    lookup_key = title_from_filename.lower().strip()

    # 🔥 FAST GLOBAL CACHE CHECK
    async with movie_lookup_lock:
        if lookup_key in movie_lookup_cache:
            return movie_lookup_cache[lookup_key]

    loop = asyncio.get_event_loop()
    current_time = loop.time()

    # ===== NORMAL TTL CACHE CHECK =====
    async with imdb_lock:
        if title_from_filename in imdb_cache:
            cached_data, timestamp = imdb_cache[title_from_filename]
            if current_time - timestamp < IMDB_CACHE_TTL:
                return cached_data
            else:
                del imdb_cache[title_from_filename]

    try:
        async with imdb_semaphore:

            logger.info(f"IMDb Query: '{title_from_filename}'")

            results = await loop.run_in_executor(
                None,
                lambda: ia.search_movie(title_from_filename, results=2)
            )

            if not results:
                result = (None, None)
            else:
                # Extract year from filename
                year_match = re.search(r"\b(19|20)\d{2}\b", title_from_filename)
                file_year = int(year_match.group()) if year_match else None

                # Remove year for fuzzy comparison
                clean_search_title = re.sub(
                    r"\b(19|20)\d{2}\b",
                    "",
                    title_from_filename
                ).strip()

                best_score = 0
                best_match = None

                for movie in results:
                    imdb_title_raw = movie.get("title", "")

                    similarity = fuzz.ratio(
                        clean_search_title.lower(),
                        imdb_title_raw.lower()
                    )

                    # Year boost if match
                    if file_year and movie.get("year") == file_year:
                        similarity += 15

                    if similarity > best_score:
                        best_score = similarity
                        best_match = movie

                if not best_match or best_score < 60:
                    result = (None, None)
                else:
                    await loop.run_in_executor(
                        None,
                        lambda: ia.update(best_match, info=["main"])
                    )

                    imdb_title = best_match.get("title")
                    imdb_year = best_match.get("year")

                    # Final year safety check
                    if file_year and imdb_year and file_year != imdb_year:
                        result = (None, None)
                    else:
                        result = (imdb_title, imdb_year)

    except Exception as e:
        logger.error(f"IMDb error: {e}")
        result = (None, None)

    # ===== SAVE TTL CACHE =====
    async with imdb_lock:
        imdb_cache[title_from_filename] = (result, current_time)

    # ===== SAVE FAST GLOBAL CACHE =====
    async with movie_lookup_lock:
        movie_lookup_cache[lookup_key] = result

    return result

# ---------------- NEW: EXTRA IMDb DATA (only added) ----------------

async def get_imdb_extra(title):
    if not title:
        return "", "", ""

    # 🔥 First check fast global cache
    lookup_key = title.lower().strip()

    async with movie_lookup_lock:
        if lookup_key in movie_lookup_cache:
            cached = movie_lookup_cache[lookup_key]
            if isinstance(cached, tuple):
                # Only title/year stored — so continue
                pass

    try:
        loop = asyncio.get_event_loop()

        async with imdb_semaphore:

            results = await loop.run_in_executor(
                None,
                lambda: ia.search_movie(title, results=1)
            )

            if not results:
                return "", "", ""

            movie = results[0]

            await loop.run_in_executor(
                None,
                lambda: ia.update(movie, info=["main", "plot"])
            )

            genres = ", ".join(movie.get("genres", []))
            rating = movie.get("rating", "")
            plot = ""

            plots = movie.get("plot")
            if plots:
                plot = plots[0].split("::")[0]

            return genres, rating, plot

    except Exception as e:
        logger.error(f"IMDb extra error: {e}")
        return "", "", ""

# ================= TMDB EXTRA DATA =================

async def get_tmdb_extra(title, year=None):
    api = Config.TMDB_API_KEY
    if not api:
        return "", "", ""

    cache_key = f"{title}_{year}"
    loop = asyncio.get_event_loop()
    current_time = loop.time()

    # ===== CACHE CHECK =====
    async with tmdb_lock:
        if cache_key in tmdb_cache:
            cached_data, timestamp = tmdb_cache[cache_key]
            if current_time - timestamp < TMDB_CACHE_TTL:
                return cached_data
            else:
                del tmdb_cache[cache_key]

    try:
        async with tmdb_semaphore:

            query = title.replace(" ", "%20")
            url = f"https://api.themoviedb.org/3/search/movie?api_key={api}&query={query}"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    data = await resp.json()

            results = data.get("results")
            if not results:
                result = ("", "", "")
            else:
                movie = results[0]

                overview = movie.get("overview", "")
                rating = movie.get("vote_average", "")
                genre_ids = movie.get("genre_ids", [])

                genre_map = {
                    28:"Action",12:"Adventure",16:"Animation",35:"Comedy",
                    80:"Crime",99:"Documentary",18:"Drama",10751:"Family",
                    14:"Fantasy",36:"History",27:"Horror",10402:"Music",
                    9648:"Mystery",10749:"Romance",878:"Sci-Fi",
                    53:"Thriller",10752:"War",37:"Western"
                }

                genres = ", ".join(
                    genre_map.get(i, "") for i in genre_ids if i in genre_map
                )

                result = (genres, rating, overview)

    except Exception:
        result = ("", "", "")

    # ===== SAVE CACHE =====
    async with tmdb_lock:
        tmdb_cache[cache_key] = (result, current_time)

    return result

# ================= TMDB TV EXTRA DATA =================

async def get_tmdb_tv_extra(title, year=None):
    api = Config.TMDB_API_KEY
    if not api:
        return "", "", ""

    cache_key = f"tv_{title}_{year}"
    loop = asyncio.get_event_loop()
    current_time = loop.time()

    # ===== CACHE CHECK =====
    async with tmdb_lock:
        if cache_key in tmdb_cache:
            cached_data, timestamp = tmdb_cache[cache_key]
            if current_time - timestamp < TMDB_CACHE_TTL:
                return cached_data
            else:
                del tmdb_cache[cache_key]

    try:
        async with tmdb_semaphore:

            query = title.replace(" ", "%20")
            url = f"https://api.themoviedb.org/3/search/tv?api_key={api}&query={query}"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    data = await resp.json()

            results = data.get("results")
            if not results:
                result = ("", "", "")
            else:
                show = results[0]

                overview = show.get("overview", "")
                rating = show.get("vote_average", "")
                genre_ids = show.get("genre_ids", [])

                genre_map = {
                    10759:"Action & Adventure",16:"Animation",35:"Comedy",
                    80:"Crime",99:"Documentary",18:"Drama",10751:"Family",
                    9648:"Mystery",10765:"Sci-Fi & Fantasy",
                    53:"Thriller",10768:"War & Politics"
                }

                genres = ", ".join(
                    genre_map.get(i, "") for i in genre_ids if i in genre_map
                )

                result = (genres, rating, overview)

    except Exception:
        result = ("", "", "")

    # ===== SAVE CACHE =====
    async with tmdb_lock:
        tmdb_cache[cache_key] = (result, current_time)

    return result

# ================= HYBRID IMDb + TMDB =================

async def get_movie_extra(title, year=None, is_series=False):
    """
    MOVIES → TMDB movie → IMDb fallback
    SERIES → TMDB tv → IMDb fallback
    """

    genres = rating = story = ""

    # ===== TMDB FIRST =====
    try:
        if is_series:
            t_genres, t_rating, t_story = await get_tmdb_tv_extra(title, year)
        else:
            t_genres, t_rating, t_story = await get_tmdb_extra(title, year)

        genres = t_genres or ""
        rating = t_rating or ""
        story = t_story or ""
    except:
        pass

    # ===== IMDb FALLBACK =====
    try:
        i_genres, i_rating, i_story = await get_imdb_extra(title)

        if not genres:
            genres = i_genres
        if not rating:
            rating = i_rating
        if not story:
            story = i_story
    except:
        pass

    return genres, rating, story

async def clean_and_parse_filename(name: str, cache: dict = None):
    """
    A next-gen, multi-pass robust filename parser that preserves all metadata.
    """
    original_name = name

    name_for_parsing = name.replace('_', ' ').replace('.', ' ')
    name_for_parsing = re.sub(r'(?:www\.)?[\w-]+\.(?:com|org|net|xyz|me|io|in|cc|biz|world|info|club|mobi|press|top|site|tech|online|store|live|co|shop|fun|tamilmv)\b', '', name_for_parsing, flags=re.IGNORECASE)
    name_for_parsing = re.sub(r'@[a-zA-Z0-9_]+', '', name_for_parsing).strip()


    season_info_str = ""
    episode_info_str = ""
    raw_episode_text_to_remove = ""

    search_name_for_eps = name.replace('_', '.').replace(' ', '.')
    
    range_patterns = [
        (r'(\d{1,2})\s+(?:To|-|–|—)\s+(\d{1,2})', 'no_season'),
        (r'(\d{1,2})\s+(\d{1,2})(?=\s\d{4})', 'no_season'),
        (r'S(\d{1,2}).*?EP\((\d{1,4})-(\d{1,4})\)', 'season'),
        (r'S(\d{1,2}).*?\[E?(\d{1,4})\s*-\s*E?(\d{1,4})\]', 'season'),
        (r'S(\d{1,2}).*?\[(\d{1,4})\s*To\s*(\d{1,4})\s*Eps?\]', 'season'),
        (r'S(\d{1,2}).*?\[EP\s*(\d{1,4})\s*to\s*(\d{1,4})\]', 'season'),
        (r'S(\d{1,2}).*?\[Epi\s*(\d{1,4})\s*-\s*(\d{1,4})\]', 'season'),
        (r'S(\d{1,2}).*?Ep\.?(\d{1,4})-(\d{1,4})', 'season'),
        (r'S(\d{1,2})\s*E(\d{1,4})[-\s]*E(\d{1,4})', 'season'),
        (r'\.Ep\.\[(\d{1,4})-(\d{1,4})\]', 'no_season'),
        (r'Ep\s*(\d{1,4})\s*-\s*(\d{1,4})', 'no_season'),
        (r'(?:E|Episode)s?\.?\s?(\d{1,4})\s?(?:to|-|–|—)\s?(\d{1,4})', 'no_season'),
    ]

    for pattern, p_type in range_patterns:
        match = re.search(pattern, name_for_parsing, re.IGNORECASE)
        if match:
            groups = match.groups()
            raw_episode_text_to_remove = match.group(0)
            if p_type == 'season':
                if not season_info_str: season_info_str = f"S{int(groups[0]):02d}"
                start_ep, end_ep = groups[1], groups[2]
            else:
                start_ep, end_ep = groups[0], groups[1]

            if int(start_ep) < int(end_ep):
                episode_info_str = f"E{int(start_ep):02d}-E{int(end_ep):02d}"
                name_for_parsing = name_for_parsing.replace(raw_episode_text_to_remove, ' ', 1)
                break 

    name_for_ptn = re.sub(r'\[.*?\]', '', name_for_parsing).strip()
    parsed_info = PTN.parse(name_for_ptn)
    
    initial_title = parsed_info.get('title', '').strip()
    if not season_info_str and parsed_info.get('season'):
        season_info_str = f"S{parsed_info.get('season'):02d}"
    if not episode_info_str and parsed_info.get('episode'):
        episode = parsed_info.get('episode')
        if isinstance(episode, list):
            if len(episode) > 1: episode_info_str = f"E{min(episode):02d}-E{max(episode):02d}"
            elif episode: episode_info_str = f"E{episode[0]:02d}"
        else: episode_info_str = f"E{episode:02d}"
    
    year_from_filename = parsed_info.get('year')
    
    # --- DECREED MODIFICATION: START ---
    # Hybrid language detection using PTN's output and our custom map.
    found_languages = set()
    search_string_lower = name.lower()
    
    # Also check PTN's audio tag for languages
    ptn_audio_tags = parsed_info.get('audio', '')
    if isinstance(ptn_audio_tags, list):
        ptn_audio_tags = " ".join(ptn_audio_tags)
    
    search_string_lower += " " + ptn_audio_tags.lower()
    
    for key, value in LANGUAGE_MAP.items():
        if re.search(r'\b' + key + r'\b', search_string_lower):
            found_languages.add(value)
    # --- DECREED MODIFICATION: END ---

    title_to_clean = initial_title
    if year_from_filename:
        title_to_clean = re.sub(r'\b' + str(year_from_filename) + r'\b', '', title_to_clean)
    
    if raw_episode_text_to_remove:
        title_to_clean = title_to_clean.replace(raw_episode_text_to_remove, '')
        
    title_to_clean = re.sub(r'\bS\d{1,2}\b|\bE\d{1,4}\b', '', title_to_clean, flags=re.IGNORECASE)
    
    junk_words = [
        'Ep', 'Eps', 'Episode', 'Episodes', 'Season', 'Series', 'South', 'Dubbed', 'Completed',
        'Web', r'\d+Kbps', 'UNCUT', 'ORG', 'HQ', 'ESubs', 'MSubs', 'REMASTERED', 'REPACK',
        'PROPER', 'iNTERNAL', 'Sample', 'Video', 'Dual', 'Audio', 'Multi', 'Hollywood',
        'New', 'Combined', 'Complete', 'Chapter', 'PSA', 'JC', 'DIDAR', 'StarBoy',
        'Hindi', 'English', 'Tamil', 'Telugu', 'Kannada', 'Malayalam', 'Punjabi', 'Japanese', 'Korean',
        'NF', 'AMZN', 'MAX', 'DSNP', 'ZEE5', 'WEB-DL', 'HDRip', 'WEBRip', 'HEVC', 'x265', 'x264', 'AAC',
        '1tamilmv', 'www'
    ]
    junk_pattern_re = r'\b(' + r'|'.join(junk_words) + r')\b'
    cleaned_title = re.sub(junk_pattern_re, '', title_to_clean, flags=re.IGNORECASE)
    cleaned_title = re.sub(r'[-_.]', ' ', cleaned_title)
    cleaned_title = re.sub(r'^[^\w\s]+', '', cleaned_title)
    cleaned_title = re.sub(r'\s+', ' ', cleaned_title).strip()

    if not cleaned_title:
        cleaned_title = " ".join(original_name.split('.')[:-1])

    # ---------- IMDb SAFE TITLE (FOR API ONLY) ----------
    imdb_safe_title = re.sub(
        r'\b(HDTC|HDCAM|CAM|PRE[- ]?DVD|DVDSCR|WEB[- ]?DL|WEB[- ]?RIP|HDRIP|BLURAY|BRRIP)\b',
        '',
        cleaned_title,
        flags=re.IGNORECASE
    ).strip()

    # ✅ IMDb lookup MUST use imdb_safe_title
    definitive_title, definitive_year = await get_definitive_title_from_imdb(imdb_safe_title)
    
    final_title = definitive_title if definitive_title else cleaned_title.title()
    final_title = re.sub(r'^[^\w]+', '', final_title).strip()

    final_year = definitive_year if definitive_year else year_from_filename
    is_series = bool(season_info_str or episode_info_str)
    
    display_title_main = final_title.strip()
    if is_series and season_info_str and season_info_str not in display_title_main:
        display_title_main += f" {season_info_str}"
    
    display_title_with_year = display_title_main
    if final_year:
        display_title_with_year += f" ({final_year})"
        
    return {
        "batch_title": f"{final_title} {season_info_str}".strip(),
        "display_title": display_title_with_year,
        "year": final_year,
        "is_series": is_series,
        "season_info": season_info_str, 
        "episode_info": episode_info_str,
        # --- DECREED MODIFICATION: START ---
        # Return detected languages. Audio tag is removed from quality_tags to avoid duplication.
        "languages": sorted(list(found_languages)),
        "quality_tags": " | ".join(filter(None, [parsed_info.get('resolution'), parsed_info.get('quality'), parsed_info.get('codec')]))
        # --- DECREED MODIFICATION: END ---
    }

# ---------------- CREATE POST (FINAL FIXED VERSION) ----------------

async def create_post(client, user_id, messages, cache: dict):
    user = await get_user(user_id)
    if not user:
        return []

    media_info_list = []

    # ---------- PARSE FILES ----------
    parse_tasks = [
        clean_and_parse_filename(getattr(m, m.media.value).file_name, cache)
        for m in messages
        if getattr(m, m.media.value, None)
    ]
    parsed_results = await asyncio.gather(*parse_tasks)

    for i, info in enumerate(parsed_results):
        if not info:
            continue
        media = getattr(messages[i], messages[i].media.value)
        info["file_size"] = media.file_size
        info["file_unique_id"] = media.file_unique_id
        media_info_list.append(info)

    if not media_info_list:
        return []

    # ---------- SORT (480p → 720p → 1080p) ----------
    media_info_list.sort(key=lambda x: natural_sort_key(x.get("quality_tags", "")))
    first_info = media_info_list[0]

    # ---------- TITLE ----------
    primary_display_title = first_info["display_title"]

    # Clean title ONLY for APIs (logic unchanged)
    clean_title_for_api = re.sub(r"\(\d{4}\)", "", primary_display_title).strip()

    # ---------- EXTRA INFO ----------
    genres, rating, story = await get_movie_extra(
        clean_title_for_api,
        first_info.get("year"),
        is_series=first_info.get("is_series")
    )

    # ---------- STORY TRIM (Telegram Safe) ----------
    MAX_STORY_LENGTH = 600  # Safe for 1024 caption limit
    if story and len(story) > MAX_STORY_LENGTH:
        story = story[:MAX_STORY_LENGTH].rsplit(" ", 1)[0] + "..."

    # ---------- POSTER ----------
    poster = (
        await get_poster(first_info["batch_title"], first_info["year"])
        if user.get("show_poster", True)
        else None
    )

    CAPTION_LIMIT = PHOTO_CAPTION_LIMIT if poster else TEXT_MESSAGE_LIMIT

    # ---------- FILE LINKS ----------
    all_entries = []

    for info in media_info_list:
        display_parts = []

        # 🔥 LANGUAGE FIRST
        languages = info.get("languages", [])
        if languages:
            display_parts.append(" + ".join(languages))

        # 🔥 QUALITY NEXT
        if info.get("quality_tags"):
            display_parts.append(info["quality_tags"].replace("|", "").strip())

        display_line = " ".join(display_parts).strip() or "File"

        owner_id = user_id
        file_unique_id = info["file_unique_id"]
        bot_username = client.me.username

        deep_link = f"https://t.me/{bot_username}?start=get_{owner_id}_{file_unique_id}"

        size_text = format_bytes(info["file_size"])

        all_entries.append(
            f"📁 **{display_line}**\n"
            f"📥 [Get File]({deep_link}) ({size_text})"
        )

    # ---------- BASE CAPTION ----------
    base_caption = (
        f"🎬 **{primary_display_title}**\n\n"
        f"🎭 **Genres:** {genres or 'N/A'}\n"
        f"⭐ **Rating:** {rating or 'N/A'}\n"
        f"📖 **Story:** {story or 'N/A'}\n\n"
    )

    # ---------- SPLIT HANDLING ----------
    final_posts = []
    current_block = []
    current_len = len(base_caption)

    for entry in all_entries:
        if current_len + len(entry) + 2 > CAPTION_LIMIT and current_block:
            caption = (
                base_caption
                + "\n\n".join(current_block)
                + "\n\n💪 **Powered By : [MzMoviiez](https://t.me/MzMoviiez)**"
            )

            # 🔐 FINAL SAFETY CHECK
            if len(caption) > CAPTION_LIMIT:
                caption = caption[:CAPTION_LIMIT - 3] + "..."

            final_posts.append((poster if not final_posts else None, caption, None))
            current_block = [entry]
            current_len = len(base_caption) + len(entry)
        else:
            current_block.append(entry)
            current_len += len(entry)

    if current_block:
        caption = (
            base_caption
            + "\n\n".join(current_block)
            + "\n\n💪 **Powered By : [MzMoviiez](https://t.me/MzMoviiez)**"
        )

        # 🔐 FINAL SAFETY CHECK
        if len(caption) > CAPTION_LIMIT:
            caption = caption[:CAPTION_LIMIT - 3] + "..."

        final_posts.append((poster if not final_posts else None, caption, None))

    return final_posts

def calculate_title_similarity(title1: str, title2: str) -> float:
    """Calculates similarity between two titles."""
    return fuzz.token_sort_ratio(title1.lower(), title2.lower())

async def get_title_key(filename: str) -> str:
    media_info = await clean_and_parse_filename(filename)
    return media_info['batch_title'] if media_info else None

async def get_file_raw_link(message):
    """Creates the raw 't.me/c/...' link for a message in a private channel."""
    # The message ID needs to be from the channel, not the user's private chat
    # This logic assumes the 'message' object is from a channel.
    return f"https://t.me/c/{str(message.chat.id).replace('-100', '')}/{message.id}"

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'([0-9]+)', s or '')]

async def get_main_menu(user_id):
    user_settings = await get_user(user_id) or {}
    text = "✅ **Setup Complete!**\n\nYou can now forward files to your Index Channel." if user_settings.get('index_db_channel') and user_settings.get('post_channels') else "⚙️ **Bot Settings**\n\nChoose an option below to configure the bot."
    buttons = [
        [InlineKeyboardButton("🗂️ Manage Channels", callback_data="manage_channels_menu")],
        [InlineKeyboardButton("🔗 Shortener", callback_data="shortener_menu"), InlineKeyboardButton("🔄 Backup", callback_data="backup_links")],
        [InlineKeyboardButton("✍️ Filename Link", callback_data="filename_link_menu"), InlineKeyboardButton("👣 Footer Buttons", callback_data="manage_footer")],
        [InlineKeyboardButton("🖼️ IMDb Poster", callback_data="poster_menu"), InlineKeyboardButton("📂 My Files", callback_data="my_files_1")],
        [InlineKeyboardButton("📢 FSub", callback_data="fsub_menu"), InlineKeyboardButton("📊 Daily Stats", callback_data="daily_stats_menu")], # New Button
        [InlineKeyboardButton("❓ How to Download", callback_data="how_to_download_menu")]
    ]
    return text, InlineKeyboardMarkup(buttons)

async def notify_and_remove_invalid_channel(client, user_id, channel_id, channel_type):
    try:
        await client.get_chat_member(channel_id, "me")
        return True
    except Exception:
        db_key = 'index_db_channel' if channel_type == 'Index DB' else 'post_channels'
        user_settings = await get_user(user_id)
        if isinstance(user_settings.get(db_key), list):
             await remove_from_list(user_id, db_key, channel_id)
        else:
             await update_user(user_id, db_key, None)
        await client.send_message(user_id, f"⚠️ **Channel Inaccessible**\n\nYour {channel_type} Channel (ID: `{channel_id}`) has been automatically removed because I could not access it.")
        return False
