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
DATABASE_PATH = "spotify01.db"
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

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
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                spotify_id TEXT UNIQUE,
                custom_url TEXT UNIQUE,
                access_token TEXT,
                refresh_token TEXT,
                token_expiry TIMESTAMP,
                display_name TEXT,
                avatar_url TEXT,
                last_update TIMESTAMP
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
                FOREIGN KEY (user_id) REFERENCES users (id),
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
                FOREIGN KEY (user_id) REFERENCES users (id)
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
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)
        
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                playlist_id TEXT,
                playlist_name TEXT,
                playlist_url TEXT UNIQUE,
                cover_image TEXT,
                spotify_url TEXT,
                total_tracks INTEGER,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)

# Utility functions
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def get_spotify_data(client, endpoint: str, token: str):
    """
    Fetches data from Spotify API with retry mechanism
    """
    try:
        response = await client.get(
            f"https://api.spotify.com/v1/{endpoint}",
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Error fetching Spotify data: {str(e)}")
        raise

async def refresh_token(user_id: str, refresh_token: str):
    """
    Refreshes the Spotify access token
    """
    try:
        async with httpx.AsyncClient() as client:
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
                logger.error(f"Token refresh failed for user {user_id}")
                raise HTTPException(status_code=401, detail="Token refresh failed")
                
            token_data = response.json()
            
            query = """
                UPDATE users 
                SET access_token = :token, token_expiry = datetime('now', '+1 hour')
                WHERE spotify_id = :user_id
            """
            await database.execute(
                query=query,
                values={"token": token_data["access_token"], "user_id": user_id}
            )
                
            return token_data["access_token"]
    except Exception as e:
        logger.error(f"Error refreshing token: {str(e)}")
        raise

async def get_valid_token(user_id: str):
    """
    Gets a valid token, refreshing if necessary
    """
    query = """
        SELECT access_token, refresh_token, token_expiry 
        FROM users 
        WHERE spotify_id = :user_id
    """
    user = await database.fetch_one(query=query, values={"user_id": user_id})
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    token_expiry = datetime.strptime(user['token_expiry'], '%Y-%m-%d %H:%M:%S')
    
    if token_expiry <= datetime.now():
        return await refresh_token(user_id, user['refresh_token'])
        
    return user['access_token']

def is_valid_url(url: str):
    """
    Validates custom URL format
    """
    import re
    pattern = re.compile("^[a-zA-Z0-9_-]{3,30}$")
    return bool(pattern.match(url))

# Scheduled tasks
async def update_recent_tracks():
    """
    Updates recent tracks for all users
    """
    try:
        query = """
            SELECT spotify_id, access_token, refresh_token 
            FROM users
            WHERE datetime('now', '-15 minutes') >= COALESCE(last_update, datetime('now', '-1 day'))
        """
        users = await database.fetch_all(query=query)
        
        async with httpx.AsyncClient() as client:
            for user in users:
                try:
                    token = await get_valid_token(user['spotify_id'])
                    
                    tracks_data = await get_spotify_data(
                        client,
                        "me/player/recently-played?limit=50",
                        token
                    )
                    
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
                            SET last_update = datetime('now') 
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
    Updates top tracks and artists for all users
    """
    try:
        users = await database.fetch_all("SELECT spotify_id FROM users")
        
        async with httpx.AsyncClient() as client:
            for user in users:
                try:
                    token = await get_valid_token(user['spotify_id'])
                    
                    # Get top tracks
                    tracks_data = await get_spotify_data(
                        client,
                        "me/top/tracks?limit=50&time_range=short_term",
                        token
                    )
                    
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
                    
                    # Get top artists
                    artists_data = await get_spotify_data(
                        client,
                        "me/top/artists?limit=50&time_range=short_term",
                        token
                    )
                    
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
                    logger.error(f"Error updating top items for user {user['spotify_id']}: {str(e)}")
                    continue
    except Exception as e:
        logger.error(f"Error in update_top_items: {str(e)}")

# FastAPI startup and shutdown events
@app.on_event("startup")
async def startup_event():
    await database.connect()
    await init_db()
    scheduler.add_job(update_recent_tracks, 'interval', minutes=1)
    scheduler.add_job(update_top_items, 'interval', minutes=1)
    scheduler.add_job(update_user_playlists, 'interval', minutes=5)
    scheduler.start()

@app.on_event("shutdown")
async def shutdown_event():
    await database.disconnect()
    scheduler.shutdown()

# API endpoints
@app.get("/health")
async def health():
    return {"status": "healthy"}


def generate_playlist_url():
    import string
    import random
    chars = string.ascii_letters + string.digits
    while True:
        url = ''.join(random.choices(chars, k=5))  # 5 character random string
        return url

# Add new function to update playlists
async def update_user_playlists():
    try:
        users = await database.fetch_all("SELECT spotify_id FROM users")
        
        async with httpx.AsyncClient() as client:
            for user in users:
                try:
                    token = await get_valid_token(user['spotify_id'])
                    
                    # Get user's playlists
                    playlists_data = await get_spotify_data(
                        client,
                        "me/playlists?limit=50",
                        token
                    )
                    
                    async with database.transaction():
                        # Clear existing playlists
                        await database.execute(
                            "DELETE FROM playlists WHERE user_id = :user_id",
                            {"user_id": user['spotify_id']}
                        )
                        
                        # Insert new playlists
                        for playlist in playlists_data["items"]:
                            await database.execute(
                                """
                                INSERT INTO playlists
                                (user_id, playlist_id, playlist_name, playlist_url, cover_image, 
                                spotify_url, total_tracks)
                                VALUES (:user_id, :playlist_id, :playlist_name, :playlist_url, 
                                :cover_image, :spotify_url, :total_tracks)
                                """,
                                {
                                    "user_id": user['spotify_id'],
                                    "playlist_id": playlist["id"],
                                    "playlist_name": playlist["name"],
                                    "playlist_url": generate_playlist_url(),
                                    "cover_image": playlist["images"][0]["url"] if playlist["images"] else None,
                                    "spotify_url": playlist["external_urls"]["spotify"],
                                    "total_tracks": playlist["tracks"]["total"]
                                }
                            )
                except Exception as e:
                    logger.error(f"Error updating playlists for user {user['spotify_id']}: {str(e)}")
                    continue
    except Exception as e:
        logger.error(f"Error in update_user_playlists: {str(e)}")

# Add new endpoints
@app.get("/users/{user_id}/playlists")
async def get_user_playlists(user_id: str):
    query = """
        SELECT playlist_name, cover_image, playlist_url, total_tracks
        FROM playlists
        WHERE user_id = :user_id
        ORDER BY playlist_name
    """
    playlists = await database.fetch_all(
        query=query,
        values={"user_id": user_id}
    )
    
    return [
        {
            "name": playlist['playlist_name'],
            "cover_image": playlist['cover_image'],
            "url": playlist['playlist_url'],
            "total_tracks": playlist['total_tracks']
        }
        for playlist in playlists
    ]

@app.get("/playlists/{playlist_url}")
async def get_playlist_details(playlist_url: str):
    # First get playlist info from our database
    query = """
        SELECT p.*, u.custom_url, u.display_name
        FROM playlists p
        JOIN users u ON p.user_id = u.spotify_id
        WHERE p.playlist_url = :playlist_url
    """
    playlist = await database.fetch_one(
        query=query,
        values={"playlist_url": playlist_url}
    )
    
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
        
    # Get playlist tracks from Spotify API
    token = await get_valid_token(playlist['user_id'])
    async with httpx.AsyncClient() as client:
        tracks_data = await get_spotify_data(
            client,
            f"playlists/{playlist['playlist_id']}/tracks?limit=50",
            token
        )
    
    tracks = [
        {
            "track_name": track["track"]["name"],
            "artist_name": track["track"]["artists"][0]["name"],
            "album_name": track["track"]["album"]["name"],
            "album_art": track["track"]["album"]["images"][0]["url"] if track["track"]["album"]["images"] else None,
            "duration": track["track"]["duration_ms"]
        }
        for track in tracks_data["items"]
        if track["track"] is not None
    ]
    
    return {
        "playlist_name": playlist['playlist_name'],
        "cover_image": playlist['cover_image'],
        "total_tracks": playlist['total_tracks'],
        "spotify_url": playlist['spotify_url'],
        "owner": {
            "display_name": playlist['display_name'],
            "profile_url": playlist['custom_url']
        },
        "tracks": tracks
    }


@app.get("/check-url/{custom_url}")
async def check_url_availability(custom_url: str):
    if not is_valid_url(custom_url):
        return {"available": False, "reason": "Invalid URL format"}
    
    query = "SELECT COUNT(*) as count FROM users WHERE custom_url = :custom_url"
    result = await database.fetch_one(query=query, values={"custom_url": custom_url.lower()})
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

        async with httpx.AsyncClient() as client:
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
            
            if not custom_url or not is_valid_url(custom_url):
                custom_url = user_data["id"]
            
            # Get avatar URL
            images = user_data.get("images", [])
            avatar_url = images[0].get("url") if images else None
            
            # Store user data
            query = """
                INSERT OR REPLACE INTO users 
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
            
    except Exception as e:
        logger.error(f"Callback error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/users/{user_id}")
async def get_user(user_id: str):
    query = """
        SELECT spotify_id, custom_url, display_name, avatar_url 
        FROM users 
        WHERE custom_url = :user_id OR spotify_id = :user_id
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
    query = """
        SELECT track_name, artist_name, played_at, album_art
        FROM tracks
        WHERE user_id = :user_id
        ORDER BY played_at DESC
        LIMIT 50
    """
    tracks = await database.fetch_all(
        query=query,
        values={"user_id": user_id}
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
    query = "SELECT access_token FROM users WHERE spotify_id = :user_id"
    user = await database.fetch_one(query=query, values={"user_id": user_id})
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.spotify.com/v1/me/player/currently-playing",
            headers={"Authorization": f"Bearer {user['access_token']}"},
        )
        
        if response.status_code == 204:
            return {"is_playing": False}
        
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to get current track")
        
        data = response.json()
        return {
            "is_playing": True,
            "track_name": data["item"]["name"],
            "artist_name": data["item"]["artists"][0]["name"],
            "album_art": data["item"]["album"]["images"][0]["url"] if data["item"]["album"]["images"] else None
        }

@app.get("/users/search")
async def search_users(query: str = None):
    if not query:
        return []
        
    try:
        search_query = """
            SELECT spotify_id, display_name, avatar_url
            FROM users 
            WHERE LOWER(spotify_id) LIKE LOWER(:search_term) 
            OR LOWER(display_name) LIKE LOWER(:search_term)
            LIMIT 10
        """
        users = await database.fetch_all(
            query=search_query,
            values={"search_term": f"%{query}%"}
        )
        
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
        WHERE custom_url = :user_id OR spotify_id = :user_id
    """
    user = await database.fetch_one(
        query=user_query,
        values={"user_id": user_id.lower()}
    )
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    spotify_id = user['spotify_id']
    
    # Now fetch top tracks using the spotify_id
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
    query = """
        SELECT artist_name, artist_image, popularity
        FROM top_artists
        WHERE user_id = :user_id
        ORDER BY popularity DESC
        LIMIT 50
    """
    artists = await database.fetch_all(
        query=query,
        values={"user_id": user_id}
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
