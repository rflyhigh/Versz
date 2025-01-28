from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2AuthorizationCodeBearer
from typing import Optional, List
import aiosqlite
import httpx
import os
from datetime import datetime, timedelta
import json
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")
DATABASE_PATH = "spotify10.db"

scheduler = AsyncIOScheduler()

async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        # First create the base users table if it doesn't exist
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                spotify_id TEXT UNIQUE,
                access_token TEXT,
                refresh_token TEXT,
                token_expiry TIMESTAMP,
                display_name TEXT,
                avatar_url TEXT,
                last_update TIMESTAMP,
                custom_url TEXT UNIQUE
            )
        """)

        # Check if custom_url column exists
        cursor = await db.execute("""
            SELECT name FROM pragma_table_info('users') WHERE name = 'custom_url'
        """)
        has_custom_url = await cursor.fetchone()

        if not has_custom_url:
            # Add custom_url column if it doesn't exist
            await db.execute("""
                ALTER TABLE users ADD COLUMN custom_url TEXT UNIQUE
            """)

        # Create other tables
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
        
        await db.commit()

@app.on_event("startup")
async def startup_event():
    await init_db()
    scheduler.add_job(update_recent_tracks, 'interval', minutes=1)
    scheduler.add_job(update_top_items, 'interval', hours=24)
    scheduler.add_job(health_check, 'interval', minutes=10)
    scheduler.start()

async def health_check():
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f"{os.getenv('BACKEND_URL')}/health")
            print(f"Health check status: {response.status_code}")
        except Exception as e:
            print(f"Health check failed: {str(e)}")

@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/auth/callback")
async def spotify_callback(request: Request):
    try:
        # Get the request body
        request_data = await request.json()
        code = request_data.get('code')
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
                print(f"Token response error: {token_response.status_code}, {token_response.text}")
                raise HTTPException(status_code=400, detail="Failed to get token")
            
            token_data = token_response.json()
            
            # Get user profile with retry logic
            for attempt in range(3):
                try:
                    user_response = await client.get(
                        "https://api.spotify.com/v1/me",
                        headers={"Authorization": f"Bearer {token_data['access_token']}"},
                        timeout=10.0
                    )
                    if user_response.status_code == 200:
                        break
                    await asyncio.sleep(1)
                except Exception:
                    if attempt == 2:
                        raise
                    continue
            
            if user_response.status_code != 200:
                print(f"User profile error: {user_response.status_code}, {user_response.text}")
                raise HTTPException(status_code=400, detail="Failed to get user profile")
            
            user_data = user_response.json()
            
            # Store user data
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("""
                    INSERT OR REPLACE INTO users 
                    (spotify_id, access_token, refresh_token, token_expiry, display_name, avatar_url)
                    VALUES (?, ?, ?, datetime('now', '+1 hour'), ?, ?)
                """, (
                    user_data["id"],
                    token_data["access_token"],
                    token_data.get("refresh_token"),
                    user_data.get("display_name", user_data["id"]),
                    user_data.get("images", [{}])[0].get("url")
                ))
                await db.commit()
            
            return {
                "success": True,
                "user_id": user_data["id"]
            }
            
    except Exception as e:
        print(f"Callback error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/users/check-url/{custom_url}")
async def check_custom_url(custom_url: str):
    if not custom_url.isalnum() or len(custom_url) < 3 or len(custom_url) > 30:
        return {"available": False, "reason": "URL must be 3-30 alphanumeric characters"}
        
    # List of reserved words that cannot be used as URLs
    reserved_words = {'login', 'admin', 'settings', 'profile', 'home', 'search', 'api'}
    if custom_url.lower() in reserved_words:
        return {"available": False, "reason": "This URL is reserved"}
    
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM users WHERE custom_url = ? OR spotify_id = ?",
            (custom_url, custom_url)
        )
        exists = await cursor.fetchone()
        
        return {
            "available": not exists,
            "reason": "URL is already taken" if exists else None
        }

# Add new endpoint to update custom URL
@app.post("/users/{user_id}/custom-url")
async def update_custom_url(user_id: str, custom_url: str):
    if not custom_url.isalnum() or len(custom_url) < 3 or len(custom_url) > 30:
        raise HTTPException(status_code=400, detail="Invalid URL format")
    
    # Check availability first
    availability = await check_custom_url(custom_url)
    if not availability["available"]:
        raise HTTPException(status_code=400, detail=availability["reason"])
    
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE users SET custom_url = ? WHERE spotify_id = ?",
            (custom_url, user_id)
        )
        await db.commit()
        
        return {"success": True, "custom_url": custom_url}

# Modify the existing get_user endpoint to handle custom URLs
@app.get("/users/{user_identifier}")
async def get_user(user_identifier: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            """
            SELECT spotify_id, display_name, avatar_url, custom_url 
            FROM users 
            WHERE spotify_id = ? OR custom_url = ?
            """,
            (user_identifier, user_identifier)
        )
        user = await cursor.fetchone()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
            
        return {
            "id": user[0],
            "display_name": user[1],
            "avatar_url": user[2],
            "custom_url": user[3]
        }
@app.get("/users/{user_id}/recent-tracks")
async def get_recent_tracks(user_id: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            """
            SELECT track_name, artist_name, played_at, album_art
            FROM tracks
            WHERE user_id = ?
            ORDER BY played_at DESC
            LIMIT 50
            """,
            (user_id,),
        )
        tracks = await cursor.fetchall()
        return [
            {
                "track_name": track[0],
                "artist_name": track[1],
                "played_at": track[2],
                "album_art": track[3]
            }
            for track in tracks
        ]

@app.get("/users/{user_id}/currently-playing")
async def get_currently_playing(user_id: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT access_token FROM users WHERE spotify_id = ?",
            (user_id,),
        )
        user = await cursor.fetchone()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.spotify.com/v1/me/player/currently-playing",
                headers={"Authorization": f"Bearer {user[0]}"},
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
        async with aiosqlite.connect(DATABASE_PATH) as db:
            # Convert the results to make them JSON serializable
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
        print(f"Search error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="An error occurred while searching for users"
        )



async def refresh_token(user_id: str, refresh_token: str):
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
            raise HTTPException(status_code=401, detail="Token refresh failed")
            
        token_data = response.json()
        
        async with aiosqlite.connect(DATABASE_PATH) as db:
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

async def get_valid_token(user_id: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
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
async def update_recent_tracks():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            """
            SELECT spotify_id, access_token, refresh_token 
            FROM users
            WHERE datetime('now', '-15 minutes') >= COALESCE(last_update, datetime('now', '-1 day'))
            """
        )
        users = await cursor.fetchall()
        
        async with httpx.AsyncClient() as client:
            for user_id, access_token, refresh_token in users:
                try:
                    # Refresh token if needed
                    token = await get_valid_token(user_id)
                    
                    response = await client.get(
                        "https://api.spotify.com/v1/me/player/recently-played?limit=50",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    
                    if response.status_code != 200:
                        continue
                    
                    tracks = response.json()["items"]
                    
                    for track in tracks:
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
                            ),
                        )
                    
                    # Update last_update timestamp
                    await db.execute(
                        "UPDATE users SET last_update = datetime('now') WHERE spotify_id = ?",
                        (user_id,)
                    )
                    
                    await db.commit()
                    
                except Exception as e:
                    print(f"Error updating tracks for user {user_id}: {str(e)}")
                    continue

async def update_top_items():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute("SELECT spotify_id FROM users")
        users = await cursor.fetchall()
        
        async with httpx.AsyncClient() as client:
            for (user_id,) in users:
                try:
                    token = await get_valid_token(user_id)
                    
                    # Get top tracks
                    tracks_response = await client.get(
                        "https://api.spotify.com/v1/me/top/tracks?limit=50&time_range=short_term",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    
                    if tracks_response.status_code == 200:
                        await db.execute("DELETE FROM top_tracks WHERE user_id = ?", (user_id,))
                        
                        for track in tracks_response.json()["items"]:
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
                                ),
                            )
                    
                    # Get top artists
                    artists_response = await client.get(
                        "https://api.spotify.com/v1/me/top/artists?limit=50&time_range=short_term",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    
                    if artists_response.status_code == 200:
                        await db.execute("DELETE FROM top_artists WHERE user_id = ?", (user_id,))
                        
                        for artist in artists_response.json()["items"]:
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
                                ),
                            )
                    
                    await db.commit()
                    
                except Exception as e:
                    print(f"Error updating top items for user {user_id}: {str(e)}")
                    continue

@app.get("/users/{user_id}/top-tracks")
async def get_top_tracks(user_id: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            """
            SELECT track_name, artist_name, album_name, album_art, popularity
            FROM top_tracks
            WHERE user_id = ?
            ORDER BY popularity DESC
            LIMIT 50
            """,
            (user_id,),
        )
        tracks = await cursor.fetchall()
        return [
            {
                "track_name": track[0],
                "artist_name": track[1],
                "album_name": track[2],
                "album_art": track[3],
                "popularity": track[4]
            }
            for track in tracks
        ]

@app.get("/users/{user_id}/top-artists")
async def get_top_artists(user_id: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            """
            SELECT artist_name, artist_image, popularity
            FROM top_artists
            WHERE user_id = ?
            ORDER BY popularity DESC
            LIMIT 50
            """,
            (user_id,),
        )
        artists = await cursor.fetchall()
        return [
            {
                "artist_name": artist[0],
                "artist_image": artist[1],
                "popularity": artist[2]
            }
            for artist in artists
        ]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
