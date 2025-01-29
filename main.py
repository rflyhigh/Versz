from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2AuthorizationCodeBearer
from typing import Optional, List
from databases import Database
import httpx
import os
from datetime import datetime, timedelta
import json
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential
import logging
import asyncio
from contextlib import asynccontextmanager
import aiosqlite
import jwt
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

# Constants
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")
JWT_SECRET = os.getenv("JWT_SECRET", "your-secret-key")  # Added JWT secret
DATABASE_DIR = "data"
DATABASE_PATH = f"{DATABASE_DIR}/spotify.db"
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

# Ensure data directory exists
Path(DATABASE_DIR).mkdir(exist_ok=True)

# Initialize database
database = Database(DATABASE_URL)

# Initialize scheduler
scheduler = AsyncIOScheduler()

# Initialize FastAPI app
app = FastAPI()

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def init_db():
    async with database.connection() as connection:
        # Added indices and improved schema
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                spotify_id TEXT UNIQUE,
                custom_url TEXT UNIQUE COLLATE NOCASE,
                access_token TEXT,
                refresh_token TEXT,
                token_expiry TIMESTAMP,
                display_name TEXT,
                avatar_url TEXT,
                last_update TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                track_id TEXT,
                track_name TEXT,
                artist_name TEXT,
                album_name TEXT,
                album_art TEXT,
                played_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                UNIQUE(user_id, track_id, played_at)
            )
        """)

        await connection.execute("""
            CREATE TABLE IF NOT EXISTS top_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                track_id TEXT,
                track_name TEXT,
                artist_name TEXT,
                album_name TEXT,
                album_art TEXT,
                popularity INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
        """)

        await connection.execute("""
            CREATE TABLE IF NOT EXISTS top_artists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                artist_id TEXT,
                artist_name TEXT,
                artist_image TEXT,
                popularity INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
        """)

        # Create indices for better query performance
        await connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_users_spotify_id ON users(spotify_id);
            CREATE INDEX IF NOT EXISTS idx_users_custom_url ON users(custom_url);
            CREATE INDEX IF NOT EXISTS idx_tracks_user_id ON tracks(user_id);
            CREATE INDEX IF NOT EXISTS idx_tracks_played_at ON tracks(played_at);
            CREATE INDEX IF NOT EXISTS idx_top_tracks_user_id ON top_tracks(user_id);
            CREATE INDEX IF NOT EXISTS idx_top_artists_user_id ON top_artists(user_id);
        """)

# Utility functions
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def get_spotify_data(client, endpoint: str, token: str):
    """
    Fetches data from Spotify API with retry mechanism and proper error handling
    """
    try:
        response = await client.get(
            f"https://api.spotify.com/v1/{endpoint}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0  # Added timeout
        )
        response.raise_for_status()
        return response.json()
    except httpx.TimeoutException:
        logger.error(f"Timeout while fetching Spotify data from {endpoint}")
        raise HTTPException(status_code=504, detail="Request timeout")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            raise HTTPException(status_code=401, detail="Token expired")
        logger.error(f"HTTP error {e.response.status_code} while fetching Spotify data: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Error fetching Spotify data: {str(e)}")
        raise

async def refresh_token(user_id: str, refresh_token: str):
    """
    Refreshes the Spotify access token with improved error handling
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
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
            
            if response.status_code != 200:
                logger.error(f"Token refresh failed for user {user_id}: {response.text}")
                raise HTTPException(status_code=401, detail="Token refresh failed")
                
            token_data = response.json()
            
            # Update both token and expiry
            query = """
                UPDATE users 
                SET access_token = :token,
                    token_expiry = datetime('now', '+1 hour'),
                    updated_at = datetime('now')
                WHERE spotify_id = :user_id
            """
            await database.execute(
                query=query,
                values={"token": token_data["access_token"], "user_id": user_id}
            )
                
            return token_data["access_token"]
    except httpx.TimeoutException:
        logger.error(f"Timeout while refreshing token for user {user_id}")
        raise HTTPException(status_code=504, detail="Request timeout")
    except Exception as e:
        logger.error(f"Error refreshing token: {str(e)}")
        raise

async def get_valid_token(user_id: str):
    """
    Gets a valid token, refreshing if necessary, with proper error handling
    """
    query = """
        SELECT access_token, refresh_token, token_expiry 
        FROM users 
        WHERE spotify_id = :user_id
    """
    user = await database.fetch_one(query=query, values={"user_id": user_id})
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if not user['token_expiry']:
        return await refresh_token(user_id, user['refresh_token'])
        
    token_expiry = datetime.strptime(user['token_expiry'], '%Y-%m-%d %H:%M:%S')
    
    if token_expiry <= datetime.now():
        return await refresh_token(user_id, user['refresh_token'])
        
    return user['access_token']

def is_valid_url(url: str):
    """
    Validates custom URL format with improved regex
    """
    import re
    pattern = re.compile("^[a-zA-Z0-9][a-zA-Z0-9_-]{2,29}$")
    return bool(pattern.match(url))

# Scheduled tasks
async def update_recent_tracks():
    """
    Updates recent tracks for all users with improved error handling and batching
    """
    try:
        query = """
            SELECT spotify_id, access_token, refresh_token 
            FROM users
            WHERE datetime('now', '-15 minutes') >= COALESCE(last_update, datetime('now', '-1 day'))
            LIMIT 50  -- Process users in batches
        """
        users = await database.fetch_all(query=query)
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            for user in users:
                try:
                    token = await get_valid_token(user['spotify_id'])
                    
                    tracks_data = await get_spotify_data(
                        client,
                        "me/player/recently-played?limit=50",
                        token
                    )
                    
                    if not tracks_data.get("items"):
                        continue
                        
                    async with database.transaction():
                        for track in tracks_data["items"]:
                            try:
                                query = """
                                    INSERT INTO tracks
                                    (user_id, track_id, track_name, artist_name, album_name, album_art, played_at)
                                    VALUES (:user_id, :track_id, :track_name, :artist_name, :album_name, :album_art, :played_at)
                                    ON CONFLICT(user_id, track_id, played_at) DO NOTHING
                                """
                                await database.execute(
                                    query=query,
                                    values={
                                        "user_id": user['spotify_id'],
                                        "track_id": track["track"]["id"],
                                        "track_name": track["track"]["name"],
                                        "artist_name": track["track"]["artists"][0]["name"],
                                        "album_name": track["track"]["album"]["name"],
                                        "album_art": track["track"]["album"]["images"][0]["url"] if track["track"]["album"]["images"] else None,
                                        "played_at": track["played_at"],
                                    }
                                )
                            except Exception as e:
                                logger.error(f"Error inserting track: {str(e)}")
                                continue
                        
                        # Update last_update timestamp
                        await database.execute(
                            """
                            UPDATE users 
                            SET last_update = datetime('now'),
                                updated_at = datetime('now')
                            WHERE spotify_id = :user_id
                            """,
                            {"user_id": user['spotify_id']}
                        )
                        
                except Exception as e:
                    logger.error(f"Error updating tracks for user {user['spotify_id']}: {str(e)}")
                    continue
    except Exception as e:
        logger.error(f"Error in update_recent_tracks: {str(e)}")

async def update_top_items():
    """
    Updates top tracks and artists for all users with improved error handling
    """
    try:
        users = await database.fetch_all(
            "SELECT spotify_id FROM users LIMIT 50"  # Process users in batches
        )
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            for user in users:
                try:
                    token = await get_valid_token(user['spotify_id'])
                    
                    # Get top tracks with proper error handling
                    try:
                        tracks_data = await get_spotify_data(
                            client,
                            "me/top/tracks?limit=50&time_range=short_term",
                            token
                        )
                        
                        if tracks_data.get("items"):
                            async with database.transaction():
                                # Clear existing top tracks
                                await database.execute(
                                    "DELETE FROM top_tracks WHERE user_id = :user_id",
                                    {"user_id": user['spotify_id']}
                                )
                                
                                # Insert new top tracks
                                for track in tracks_data["items"]:
                                    await database.execute(
                                        """
                                        INSERT INTO top_tracks
                                        (user_id, track_id, track_name, artist_name, album_name, album_art, popularity)
                                        VALUES (:user_id, :track_id, :track_name, :artist_name, :album_name, :album_art, :popularity)
                                        """,
                                        {
                                            "user_id": user['spotify_id'],
                                            "track_id": track["id"],
                                            "track_name": track["name"],
                                            "artist_name": track["artists"][0]["name"],
                                            "album_name": track["album"]["name"],
                                            "album_art": track["album"]["images"][0]["url"] if track["album"]["images"] else None,
                                            "popularity": track["popularity"],
                                        }
                                    )
                    except Exception as e:
                        logger.error(f"Error updating top tracks for user {user['spotify_id']}: {str(e)}")
                    
                    # Get top artists with proper error handling
                    try:
                        artists_data = await get_spotify_data(
                            client,
                            "me/top/artists?limit=50&time_range=short_term",
                            token
                        )
                        
                        if artists_data.get("items"):
                            async with database.transaction():
                                # Clear existing top artists
                                await database.execute(
                                    "DELETE FROM top_artists WHERE user_id = :user_id",
                                    {"user_id": user['spotify_id']}
                                )
                                
                                # Insert new top artists
                                for artist in artists_data["items"]:
                                    await database.execute(
                                        """
                                        INSERT INTO top_artists
                                        (user_id, artist_id, artist_name, artist_image, popularity)
                                        VALUES (:user_id, :artist_id, :artist_name, :artist_image, :popularity)
                                        """,
                                        {
                                            "user_id": user['spotify_id'],
                                            "artist_id": artist["id"],
                                            "artist_name": artist["name"],
                                            "artist_image": artist["images"][0]["url"] if artist["images"] else None,
                                            "popularity": artist["popularity"],
                                        }
                                    )
                    except Exception as e:
                        logger.error(f"Error updating top artists for user {user['spotify_id']}: {str(e)}")
                    
                except Exception as e:
                    logger.error(f"Error updating top items for user {user['spotify_id']}: {str(e)}")
                    continue
    except Exception as e:
        logger.error(f"Error in update_top_items: {str(e)}")

# FastAPI startup and shutdown events
@app.on_event("startup")
async def startup_event():
    await database.connect()
    await init_db()
    scheduler.add_job(update_recent_tracks, 'interval', minutes=5)  # Increased interval
    scheduler.add_job(update_top_items, 'interval', minutes=30)  # Increased interval
    scheduler.start()

@app.on_event("shutdown")
async def shutdown_event():
    await database.disconnect()
    scheduler.shutdown()

# API endpoints with improved error handling
@app.get("/health")
async def health():
    try:
        await database.fetch_one("SELECT 1")
        return {"status": "healthy", "database": "connected"}
    except Exception:
        return {"status": "unhealthy", "database": "disconnected"}

@app.get("/check-url/{custom_url}")
async def check_url_availability(custom_url: str):
    if not is_valid_url(custom_url):
        return {"available": False, "reason": "Invalid URL format"}
    
    query = "SELECT COUNT(*) as count FROM users WHERE LOWER(custom_url) = LOWER(:custom_url)"
    result = await database.fetch_one(query=query, values={"custom_url": custom_url})
    return {"available": result['count'] == 0}

@app.post("/auth/callback")
async def spotify_callback(request: Request):
    try:
        request_data = await request.json()
        code = request_data.get('code')
        custom_url = request_data.get('custom_url')
        redirect_uri = request_data.get('redirect_uri', SPOTIFY_REDIRECT_URI)
        
        if not code:
            raise HTTPException(status_code=400, detail="Code parameter is required")

        if custom_url and not is_valid_url(custom_url):
            raise HTTPException(status_code=400, detail="Invalid custom URL format")

        async with httpx.AsyncClient(timeout=10.0) as client:
            # Exchange code for tokens
            token_response = await client.post(
                "https://accounts.spotify.com/api/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": SPOTIFY_CLIENT_ID,
                    "client_secret": SPOTIFY_CLIENT_SECRET,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            if token_response.status_code != 200:
                logger.error(f"Token response error: {token_response.status_code}, {token_response.text}")
                raise HTTPException(status_code=400, detail="Failed to get token")
            
            token_data = token_response.json()
            
            # Get user profile
            user_data = await get_spotify_data(client, "me", token_data["access_token"])
            
            if not custom_url:
                custom_url = user_data["id"]
            
            # Check if custom URL is already taken by another user
            if custom_url.lower() != user_data["id"].lower():
                existing_user = await database.fetch_one(
                    "SELECT spotify_id FROM users WHERE LOWER(custom_url) = LOWER(:custom_url)",
                    {"custom_url": custom_url}
                )
                if existing_user and existing_user['spotify_id'] != user_data["id"]:
                    raise HTTPException(status_code=400, detail="Custom URL already taken")
            
            # Get avatar URL
            images = user_data.get("images", [])
            avatar_url = images[0].get("url") if images else None
            
            # Store user data with proper error handling
            async with database.transaction():
                # Check if user exists
                existing_user = await database.fetch_one(
                    "SELECT spotify_id FROM users WHERE spotify_id = :spotify_id",
                    {"spotify_id": user_data["id"]}
                )
                
                if existing_user:
                    # Update existing user
                    query = """
                        UPDATE users 
                        SET custom_url = :custom_url,
                            access_token = :access_token,
                            refresh_token = COALESCE(:refresh_token, refresh_token),
                            token_expiry = datetime('now', '+1 hour'),
                            display_name = :display_name,
                            avatar_url = :avatar_url,
                            updated_at = datetime('now')
                        WHERE spotify_id = :spotify_id
                    """
                else:
                    # Insert new user
                    query = """
                        INSERT INTO users 
                        (spotify_id, custom_url, access_token, refresh_token, token_expiry, display_name, avatar_url)
                        VALUES (:spotify_id, :custom_url, :access_token, :refresh_token, datetime('now', '+1 hour'), :display_name, :avatar_url)
                    """
                
                await database.execute(
                    query=query,
                    values={
                        "spotify_id": user_data["id"],
                        "custom_url": custom_url.lower(),
                        "access_token": token_data["access_token"],
                        "refresh_token": token_data.get("refresh_token"),
                        "display_name": user_data.get("display_name", user_data["id"]),
                        "avatar_url": avatar_url
                    }
                )
            
            return {
                "success": True,
                "user_id": custom_url.lower()
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Callback error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/users/{user_id}")
async def get_user(user_id: str):
    query = """
        SELECT spotify_id, custom_url, display_name, avatar_url 
        FROM users 
        WHERE LOWER(custom_url) = LOWER(:user_id) OR LOWER(spotify_id) = LOWER(:user_id)
    """
    user = await database.fetch_one(
        query=query,
        values={"user_id": user_id.lower()}
    )
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    return {
        "id": user['spotify_id'],
        "custom_url": user['custom_url'],
        "display_name": user['display_name'],
        "avatar_url": user['avatar_url']
    }

@app.get("/users/{user_id}/recent-tracks")
async def get_recent_tracks(user_id: str):
    # First get the spotify_id from either custom_url or spotify_id
    user_query = """
        SELECT spotify_id 
        FROM users 
        WHERE LOWER(custom_url) = LOWER(:user_id) OR LOWER(spotify_id) = LOWER(:user_id)
    """
    user = await database.fetch_one(
        query=user_query,
        values={"user_id": user_id.lower()}
    )
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    query = """
        SELECT track_name, artist_name, played_at, album_art
        FROM tracks
        WHERE user_id = :user_id
        ORDER BY played_at DESC
        LIMIT 50
    """
    tracks = await database.fetch_all(
        query=query,
        values={"user_id": user['spotify_id']}
    )
    
    return [
        {
            "track_name": track['track_name'],
            "artist_name": track['artist_name'],
            "played_at": track['played_at'],
            "album_art": track['album_art']
        }
        for track in tracks
    ]

@app.get("/users/{user_id}/currently-playing")
async def get_currently_playing(user_id: str):
    # First get the spotify_id from either custom_url or spotify_id
    user_query = """
        SELECT spotify_id 
        FROM users 
        WHERE LOWER(custom_url) = LOWER(:user_id) OR LOWER(spotify_id) = LOWER(:user_id)
    """
    user = await database.fetch_one(
        query=user_query,
        values={"user_id": user_id.lower()}
    )
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    try:
        token = await get_valid_token(user['spotify_id'])
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://api.spotify.com/v1/me/player/currently-playing",
                headers={"Authorization": f"Bearer {token}"},
            )
            
            if response.status_code == 204:
                return {"is_playing": False}
            
            if response.status_code != 200:
                raise HTTPException(status_code=400, detail="Failed to get current track")
            
            data = response.json()
            if not data or not data.get("item"):
                return {"is_playing": False}
                
            return {
                "is_playing": data.get("is_playing", False),
                "track_name": data["item"]["name"],
                "artist_name": data["item"]["artists"][0]["name"],
                "album_art": data["item"]["album"]["images"][0]["url"] if data["item"]["album"]["images"] else None
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting currently playing track: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/users/search")
async def search_users(query: str = None):
    if not query:
        return []
        
    try:
        search_query = """
            SELECT spotify_id, custom_url, display_name, avatar_url
            FROM users 
            WHERE LOWER(spotify_id) LIKE LOWER(:search_term) 
            OR LOWER(display_name) LIKE LOWER(:search_term)
            OR LOWER(custom_url) LIKE LOWER(:search_term)
            LIMIT 10
        """
        users = await database.fetch_all(
            query=search_query,
            values={"search_term": f"%{query}%"}
        )
        
        return [
            {
                "id": user['spotify_id'],
                "custom_url": user['custom_url'],
                "display_name": user['display_name'],
                "avatar_url": user['avatar_url']
            }
            for user in users
        ]
        
    except Exception as e:
        logger.error(f"Search error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="An error occurred while searching for users"
        )

@app.get("/users/{user_id}/top-tracks")
async def get_top_tracks(user_id: str):
    # First get the spotify_id from either custom_url or spotify_id
    user_query = """
        SELECT spotify_id 
        FROM users 
        WHERE LOWER(custom_url) = LOWER(:user_id) OR LOWER(spotify_id) = LOWER(:user_id)
    """
    user = await database.fetch_one(
        query=user_query,
        values={"user_id": user_id.lower()}
    )
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    spotify_id = user['spotify_id']
    
    tracks_query = """
        SELECT track_name, artist_name, album_name, album_art, popularity
        FROM top_tracks
        WHERE user_id = :user_id
        ORDER BY popularity DESC
        LIMIT 50
    """
    tracks = await database.fetch_all(
        query=tracks_query,
        values={"user_id": spotify_id}
    )
    
    return [
        {
            "track_name": track['track_name'],
            "artist_name": track['artist_name'],
            "album_name": track['album_name'],
            "album_art": track['album_art'],
            "popularity": track['popularity']
        }
        for track in tracks
    ]

@app.get("/users/{user_id}/top-artists")
async def get_top_artists(user_id: str):
    # First get the spotify_id from either custom_url or spotify_id
    user_query = """
        SELECT spotify_id 
        FROM users 
        WHERE LOWER(custom_url) = LOWER(:user_id) OR LOWER(spotify_id) = LOWER(:user_id)
    """
    user = await database.fetch_one(
        query=user_query,
        values={"user_id": user_id.lower()}
    )
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    spotify_id = user['spotify_id']
    
    query = """
        SELECT artist_name, artist_image, popularity
        FROM top_artists
        WHERE user_id = :user_id
        ORDER BY popularity DESC
        LIMIT 50
    """
    artists = await database.fetch_all(
        query=query,
        values={"user_id": spotify_id}
    )
    
    return [
        {
            "artist_name": artist['artist_name'],
            "artist_image": artist['artist_image'],
            "popularity": artist['popularity']
        }
        for artist in artists
    ]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
