from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2AuthorizationCodeBearer
from typing import Optional, List, Dict, Any
import aiosqlite
import httpx
import os
import asyncio
from datetime import datetime, timedelta
import json
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from contextlib import asynccontextmanager
import logging
from typing import AsyncGenerator

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# Constants
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")
DATABASE_PATH = "spotify.db"
REQUEST_TIMEOUT = 30.0  # 30 seconds timeout for external requests
MAX_RETRIES = 3
RETRY_DELAY = 1

# Database connection pool
class DatabasePool:
    def __init__(self):
        self._pool = []
        self._semaphore = asyncio.Semaphore(5)  # Limit to 5 concurrent connections

    async def get_connection(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        async with self._semaphore:
            if not self._pool:
                conn = await aiosqlite.connect(DATABASE_PATH)
                await conn.execute("PRAGMA foreign_keys = ON")
            else:
                conn = self._pool.pop()
            try:
                yield conn
            finally:
                self._pool.append(conn)

    async def close_all(self):
        while self._pool:
            conn = self._pool.pop()
            await conn.close()

db_pool = DatabasePool()

# Initialize FastAPI app with lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    scheduler.add_job(update_recent_tracks, 'interval', minutes=5)
    scheduler.add_job(update_top_items, 'interval', hours=1)
    scheduler.add_job(health_check, 'interval', minutes=10)
    scheduler.start()
    
    yield
    
    # Shutdown
    scheduler.shutdown()
    await db_pool.close_all()

app = FastAPI(lifespan=lifespan)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

scheduler = AsyncIOScheduler()

# HTTP client with connection pooling
async def get_http_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        yield client

# Database initialization
async def init_db():
    async with db_pool.get_connection() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                spotify_id TEXT UNIQUE,
                access_token TEXT,
                refresh_token TEXT,
                token_expiry TIMESTAMP,
                display_name TEXT,
                avatar_url TEXT,
                last_update TIMESTAMP
            )
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                track_id TEXT,
                track_name TEXT,
                artist_name TEXT,
                album_name TEXT,
                album_art TEXT,
                played_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS top_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                track_id TEXT,
                track_name TEXT,
                artist_name TEXT,
                album_name TEXT,
                album_art TEXT,
                popularity INTEGER,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS top_artists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                artist_id TEXT,
                artist_name TEXT,
                artist_image TEXT,
                popularity INTEGER,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS liked_songs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                track_id TEXT,
                track_name TEXT,
                artist_name TEXT,
                album_name TEXT,
                album_art TEXT,
                liked_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                UNIQUE(user_id, track_id)
            )
        """)
        
        await db.commit()

# Utility functions
async def make_spotify_request(client: httpx.AsyncClient, method: str, url: str, 
                             headers: Dict[str, str], **kwargs) -> Dict[str, Any]:
    """Make a Spotify API request with retry logic"""
    for attempt in range(MAX_RETRIES):
        try:
            response = await client.request(method, url, headers=headers, timeout=REQUEST_TIMEOUT, **kwargs)
            response.raise_for_status()
            return response.json() if response.status_code != 204 else {}
        except httpx.TimeoutException:
            if attempt == MAX_RETRIES - 1:
                raise HTTPException(status_code=504, detail="Spotify API timeout")
            await asyncio.sleep(RETRY_DELAY * (attempt + 1))
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:  # Rate limit
                retry_after = int(e.response.headers.get('Retry-After', RETRY_DELAY))
                await asyncio.sleep(retry_after)
            elif e.response.status_code == 401:  # Unauthorized
                raise HTTPException(status_code=401, detail="Token expired or invalid")
            else:
                raise HTTPException(status_code=e.response.status_code, 
                                  detail=f"Spotify API error: {e.response.text}")

async def refresh_token(user_id: str, refresh_token: str) -> str:
    """Refresh Spotify access token"""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://accounts.spotify.com/api/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": SPOTIFY_CLIENT_ID,
                    "client_secret": SPOTIFY_CLIENT_SECRET,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            response.raise_for_status()
            token_data = response.json()
            
            async with db_pool.get_connection() as db:
                await db.execute(
                    """
                    UPDATE users 
                    SET access_token = ?, token_expiry = datetime('now', '+1 hour')
                    WHERE spotify_id = ?
                    """,
                    (token_data["access_token"], user_id)
                )
                await db.commit()
                
            return token_data["access_token"]
        except Exception as e:
            logger.error(f"Token refresh failed for user {user_id}: {str(e)}")
            raise HTTPException(status_code=401, detail="Token refresh failed")

async def get_valid_token(user_id: str) -> str:
    """Get a valid access token for a user"""
    async with db_pool.get_connection() as db:
        cursor = await db.execute(
            """
            SELECT access_token, refresh_token, token_expiry 
            FROM users 
            WHERE spotify_id = ?
            """,
            (user_id,)
        )
        user = await cursor.fetchone()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
            
        token_expiry = datetime.strptime(user[2], '%Y-%m-%d %H:%M:%S')
        
        if token_expiry <= datetime.now():
            return await refresh_token(user_id, user[1])
            
        return user[0]

# Background tasks
async def update_recent_tracks():
    """Update recent tracks for all users"""
    try:
        async with db_pool.get_connection() as db:
            cursor = await db.execute(
                """
                SELECT spotify_id, access_token, refresh_token 
                FROM users
                WHERE datetime('now', '-5 minutes') >= COALESCE(last_update, datetime('now', '-1 day'))
                """
            )
            users = await cursor.fetchall()
            
        async with httpx.AsyncClient() as client:
            for user_id, access_token, refresh_token in users:
                try:
                    token = await get_valid_token(user_id)
                    tracks_data = await make_spotify_request(
                        client,
                        'GET',
                        "https://api.spotify.com/v1/me/player/recently-played?limit=50",
                        headers={"Authorization": f"Bearer {token}"}
                    )
                    
                    if not tracks_data.get('items'):
                        continue
                        
                    async with db_pool.get_connection() as db:
                        for track in tracks_data['items']:
                            await db.execute(
                                """
                                INSERT OR REPLACE INTO tracks
                                (user_id, track_id, track_name, artist_name, album_name, album_art, played_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    user_id,
                                    track["track"]["id"],
                                    track["track"]["name"],
                                    track["track"]["artists"][0]["name"],
                                    track["track"]["album"]["name"],
                                    track["track"]["album"]["images"][0]["url"] if track["track"]["album"]["images"] else None,
                                    track["played_at"],
                                )
                            )
                        
                        await db.execute(
                            "UPDATE users SET last_update = datetime('now') WHERE spotify_id = ?",
                            (user_id,)
                        )
                        await db.commit()
                        
                except Exception as e:
                    logger.error(f"Error updating tracks for user {user_id}: {str(e)}")
                    continue
    except Exception as e:
        logger.error(f"Error in update_recent_tracks: {str(e)}")

async def update_top_items():
    """Update top tracks and artists for all users"""
    try:
        async with db_pool.get_connection() as db:
            cursor = await db.execute("SELECT spotify_id FROM users")
            users = await cursor.fetchall()
        
        async with httpx.AsyncClient() as client:
            for (user_id,) in users:
                try:
                    token = await get_valid_token(user_id)
                    
                    # Get top tracks
                    tracks_data = await make_spotify_request(
                        client,
                        'GET',
                        "https://api.spotify.com/v1/me/top/tracks?limit=50&time_range=short_term",
                        headers={"Authorization": f"Bearer {token}"}
                    )
                    
                    async with db_pool.get_connection() as db:
                        if tracks_data.get('items'):
                            await db.execute("DELETE FROM top_tracks WHERE user_id = ?", (user_id,))
                            for track in tracks_data['items']:
                                await db.execute(
                                    """
                                    INSERT INTO top_tracks
                                    (user_id, track_id, track_name, artist_name, album_name, album_art, popularity)
                                    VALUES (?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (
                                        user_id,
                                        track["id"],
                                        track["name"],
                                        track["artists"][0]["name"],
                                        track["album"]["name"],
                                        track["album"]["images"][0]["url"] if track["album"]["images"] else None,
                                        track["popularity"],
                                    )
                                )
                    
                    # Get top artists
                    artists_data = await make_spotify_request(
                        client,
                        'GET',
                        "https://api.spotify.com/v1/me/top/artists?limit=50&time_range=short_term",
                        headers={"Authorization": f"Bearer {token}"}
                    )
                    
                    async with db_pool.get_connection() as db:
                        if artists_data.get('items'):
                            await db.execute("DELETE FROM top_artists WHERE user_id = ?", (user_id,))
                            for artist in artists_data['items']:
                                await db.execute(
                                    """
                                    INSERT INTO top_artists
                                    (user_id, artist_id, artist_name, artist_image, popularity)
                                    VALUES (?, ?, ?, ?, ?)
                                    """,
                                    (
                                        user_id,
                                        artist["id"],
                                        artist["name"],
                                        artist["images"][0]["url"] if artist["images"] else None,
                                        artist["popularity"],
                                    )
                                )
                        await db.commit()
                        
                except Exception as e:
                    logger.error(f"Error updating top items for user {user_id}: {str(e)}")
                    continue
    except Exception as e:
        logger.error(f"Error in update_top_items: {str(e)}")

async def health_check():
    """Periodic health check"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{os.getenv('BACKEND_URL')}/health")
            logger.info(f"Health check status: {response.status_code}")
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")

# API Routes
@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.post("/auth/callback")
async def spotify_callback(request: Request, background_tasks: BackgroundTasks):
    try:
        request_data = await request.json()
        code = request_data.get('code')
        redirect_uri = request_data.get('redirect_uri', SPOTIFY_REDIRECT_URI)
        
        if not code:
            raise HTTPException(status_code=400, detail="Code parameter is required")
        
        async with httpx.AsyncClient() as client:
            # Exchange code for tokens
            token_response = await make_spotify_request(
                client,
                'POST',
                "https://accounts.spotify.com/api/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": SPOTIFY_CLIENT_ID,
                    "client_secret": SPOTIFY_CLIENT_SECRET,
                }
            )
            
            # Get user profile
            user_response = await make_spotify_request(
                client,
                'GET',
                "https://api.spotify.com/v1/me",
                headers={"Authorization": f"Bearer {token_response['access_token']}"}
            )
            
            # Store user data
            async with db_pool.get_connection() as db:
                await db.execute("""
                    INSERT OR REPLACE INTO users 
                    (spotify_id, access_token, refresh_token, token_expiry, display_name, avatar_url)
                    VALUES (?, ?, ?, datetime('now', '+1 hour'), ?, ?)
                """, (
                    user_response["id"],
                    token_response["access_token"],
                    token_response.get("refresh_token"),
                    user_response.get("display_name", user_response["id"]),
                    user_response.get("images", [{}])[0].get("url")
                ))
                await db.commit()
            
            # Schedule immediate update of user data
            background_tasks.add_task(update_recent_tracks)
            background_tasks.add_task(update_top_items)
            
            return {
                "success": True,
                "user_id": user_response["id"]
            }
            
    except Exception as e:
        logger.error(f"Callback error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/users/{user_id}")
async def get_user(user_id: str):
    async with db_pool.get_connection() as db:
        cursor = await db.execute(
            "SELECT spotify_id, display_name, avatar_url FROM users WHERE spotify_id = ?",
            (user_id,)
        )
        user = await cursor.fetchone()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
            
        return {
            "id": user[0],
            "display_name": user[1],
            "avatar_url": user[2]
        }

@app.get("/users/{user_id}/recent-tracks")
async def get_recent_tracks(user_id: str):
    async with db_pool.get_connection() as db:
        cursor = await db.execute(
            """
            SELECT track_id, track_name, artist_name, played_at, album_art
            FROM tracks
            WHERE user_id = ?
            ORDER BY played_at DESC
            LIMIT 50
            """,
            (user_id,)
        )
        tracks = await cursor.fetchall()
        return [
            {
                "track_id": track[0],
                "track_name": track[1],
                "artist_name": track[2],
                "played_at": track[3],
                "album_art": track[4]
            }
            for track in tracks
        ]

@app.get("/users/{user_id}/currently-playing")
async def get_currently_playing(user_id: str):
    token = await get_valid_token(user_id)
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                "https://api.spotify.com/v1/me/player/currently-playing",
                headers={"Authorization": f"Bearer {token}"},
                timeout=REQUEST_TIMEOUT
            )
            
            if response.status_code == 204:
                return {"is_playing": False}
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "is_playing": True,
                    "track_name": data["item"]["name"],
                    "artist_name": data["item"]["artists"][0]["name"],
                    "album_art": data["item"]["album"]["images"][0]["url"] if data["item"]["album"]["images"] else None
                }
                
            raise HTTPException(status_code=400, detail="Failed to get current track")
            
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Request timeout")
        except Exception as e:
            logger.error(f"Error getting currently playing: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/users/search")
async def search_users(query: str = None):
    if not query:
        return []
        
    try:
        async with db_pool.get_connection() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT spotify_id, display_name, avatar_url
                FROM users 
                WHERE LOWER(spotify_id) LIKE LOWER(?) OR LOWER(display_name) LIKE LOWER(?)
                LIMIT 10
                """,
                (f"%{query}%", f"%{query}%")
            )
            users = await cursor.fetchall()
            
            return [
                {
                    "id": user['spotify_id'],
                    "display_name": user['display_name'],
                    "avatar_url": user['avatar_url']
                }
                for user in users
            ]
            
    except Exception as e:
        logger.error(f"Search error: {str(e)}")
        raise HTTPException(status_code=500, detail="Search failed")

@app.get("/users/{user_id}/liked-songs")
async def get_liked_songs(user_id: str):
    async with db_pool.get_connection() as db:
        cursor = await db.execute(
            """
            SELECT track_id, track_name, artist_name, album_name, album_art, liked_at
            FROM liked_songs
            WHERE user_id = ?
            ORDER BY liked_at DESC
            """,
            (user_id,)
        )
        songs = await cursor.fetchall()
        return [
            {
                "track_id": song[0],
                "track_name": song[1],
                "artist_name": song[2],
                "album_name": song[3],
                "album_art": song[4],
                "liked_at": song[5]
            }
            for song in songs
        ]

@app.post("/users/{user_id}/liked-songs/{track_id}")
async def toggle_like_song(user_id: str, track_id: str):
    async with db_pool.get_connection() as db:
        try:
            # Check if song is already liked
            cursor = await db.execute(
                "SELECT id FROM liked_songs WHERE user_id = ? AND track_id = ?",
                (user_id, track_id)
            )
            existing = await cursor.fetchone()
            
            if existing:
                await db.execute(
                    "DELETE FROM liked_songs WHERE user_id = ? AND track_id = ?",
                    (user_id, track_id)
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT track_name, artist_name, album_name, album_art
                    FROM tracks 
                    WHERE user_id = ? AND track_id = ?
                    UNION
                    SELECT track_name, artist_name, album_name, album_art
                    FROM top_tracks
                    WHERE user_id = ? AND track_id = ?
                    LIMIT 1
                    """,
                    (user_id, track_id, user_id, track_id)
                )
                track = await cursor.fetchone()
                
                if track:
                    await db.execute(
                        """
                        INSERT INTO liked_songs 
                        (user_id, track_id, track_name, artist_name, album_name, album_art, liked_at)
                        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                        """,
                        (user_id, track_id, track[0], track[1], track[2], track[3])
                    )
            
            await db.commit()
            return {"success": True}
            
        except Exception as e:
            logger.error(f"Error toggling like for track {track_id}: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to toggle like status")

@app.get("/users/{user_id}/top-tracks")
async def get_top_tracks(user_id: str):
    async with db_pool.get_connection() as db:
        cursor = await db.execute(
            """
            SELECT track_id, track_name, artist_name, album_name, album_art, popularity
            FROM top_tracks
            WHERE user_id = ?
            ORDER BY popularity DESC
            LIMIT 50
            """,
            (user_id,)
        )
        tracks = await cursor.fetchall()
        return [
            {
                "track_id": track[0],
                "track_name": track[1],
                "artist_name": track[2],
                "album_name": track[3],
                "album_art": track[4],
                "popularity": track[5]
            }
            for track in tracks
        ]

@app.get("/users/{user_id}/top-artists")
async def get_top_artists(user_id: str):
    async with db_pool.get_connection() as db:
        cursor = await db.execute(
            """
            SELECT artist_id, artist_name, artist_image, popularity
            FROM top_artists
            WHERE user_id = ?
            ORDER BY popularity DESC
            LIMIT 50
            """,
            (user_id,)
        )
        artists = await cursor.fetchall()
        return [
            {
                "artist_id": artist[0],
                "artist_name": artist[1],
                "artist_image": artist[2],
                "popularity": artist[3]
            }
            for artist in artists
        ]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
