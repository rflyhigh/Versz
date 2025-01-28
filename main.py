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
DATABASE_PATH = "spotify0.db"

scheduler = AsyncIOScheduler()

async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
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
        
        # Create index for custom_url lookups
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_custom_url ON users(custom_url)
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
        request_data = await request.json()
        code = request_data.get('code')
        redirect_uri = request_data.get('redirect_uri', SPOTIFY_REDIRECT_URI)
        
        if not code:
            raise HTTPException(status_code=400, detail="Authorization code is required")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Exchange code for tokens with error logging
            try:
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
                    error_data = token_response.json()
                    print(f"Token exchange failed: Status {token_response.status_code}")
                    print(f"Error details: {error_data}")
                    
                    if 'error' in error_data:
                        if error_data['error'] == 'invalid_grant':
                            raise HTTPException(
                                status_code=400,
                                detail="Invalid authorization code. Please try logging in again."
                            )
                        elif error_data['error'] == 'invalid_client':
                            raise HTTPException(
                                status_code=500,
                                detail="Invalid client credentials. Please contact support."
                            )
                    
                    raise HTTPException(
                        status_code=400,
                        detail=f"Authentication failed: {error_data.get('error_description', 'Unknown error')}"
                    )
                
                token_data = token_response.json()
                
            except httpx.RequestError as e:
                print(f"Token exchange network error: {str(e)}")
                raise HTTPException(
                    status_code=503,
                    detail="Unable to complete authentication. Please try again."
                )

            # User profile fetch with improved error handling
            max_retries = 3
            retry_delay = 1  # seconds
            
            for attempt in range(max_retries):
                try:
                    user_response = await client.get(
                        "https://api.spotify.com/v1/me",
                        headers={
                            "Authorization": f"Bearer {token_data['access_token']}",
                            "Accept": "application/json"
                        },
                        timeout=10.0
                    )
                    
                    if user_response.status_code == 403:
                        print(f"Access denied error. Response: {user_response.text}")
                        raise HTTPException(
                            status_code=403,
                            detail="Access denied. Please ensure your Spotify account is verified and try again."
                        )
                    
                    if user_response.status_code == 429:
                        retry_after = int(user_response.headers.get('Retry-After', 1))
                        await asyncio.sleep(min(retry_after, 5))
                        continue
                        
                    if user_response.status_code != 200:
                        print(f"Profile fetch error: Status {user_response.status_code}")
                        print(f"Response: {user_response.text}")
                        
                        if attempt < max_retries - 1:
                            await asyncio.sleep(retry_delay)
                            continue
                            
                        raise HTTPException(
                            status_code=500,
                            detail="Unable to fetch user profile. Please try again."
                        )
                    
                    user_data = user_response.json()
                    break
                    
                except httpx.TimeoutException:
                    if attempt == max_retries - 1:
                        raise HTTPException(
                            status_code=408,
                            detail="Request timed out. Please try again."
                        )
                    await asyncio.sleep(retry_delay)
                    continue
            
            # Database storage with transaction
            try:
                async with aiosqlite.connect(DATABASE_PATH) as db:
                    await db.execute("BEGIN TRANSACTION")
                    try:
                        await db.execute("""
                            INSERT OR REPLACE INTO users 
                            (spotify_id, custom_url, access_token, refresh_token, token_expiry, display_name, avatar_url, last_update)
                            VALUES (?, ?, ?, ?, datetime('now', '+1 hour'), ?, ?, datetime('now'))
                        """, (
                            user_data["id"],
                            user_data["id"],
                            token_data["access_token"],
                            token_data["refresh_token"],
                            user_data.get("display_name", user_data["id"]),
                            user_data.get("images", [{}])[0].get("url", "")
                        ))
                        await db.commit()
                    except Exception as db_error:
                        await db.rollback()
                        print(f"Database transaction failed: {str(db_error)}")
                        raise HTTPException(status_code=500, detail="Failed to save user data")
                        
            except Exception as db_error:
                print(f"Database connection error: {str(db_error)}")
                raise HTTPException(status_code=500, detail="Database error occurred")
            
            return {
                "success": True,
                "user_id": user_data["id"]
            }
            
    except HTTPException:
        raise
    except Exception as e:
        print(f"Unhandled callback error: {str(e)}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")

@app.get("/users/check-url/{custom_url}")
async def check_custom_url(custom_url: str):
    if not custom_url.isalnum():
        return {"available": False, "reason": "URL must contain only letters and numbers"}
    
    if len(custom_url) < 3 or len(custom_url) > 30:
        return {"available": False, "reason": "URL must be between 3 and 30 characters"}

    # List of reserved words that can't be used as URLs
    reserved_words = {'login', 'admin', 'settings', 'profile', 'callback', 'api', 'auth'}
    if custom_url.lower() in reserved_words:
        return {"available": False, "reason": "This URL is reserved"}

    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM users WHERE LOWER(custom_url) = LOWER(?)",
            (custom_url,)
        )
        exists = await cursor.fetchone()
        return {"available": not bool(exists)}

# Add new endpoint to update custom URL
@app.put("/users/{user_id}/custom-url")
async def update_custom_url(user_id: str, custom_url: str):
    if not custom_url.isalnum():
        raise HTTPException(status_code=422, detail="URL must contain only letters and numbers")
    
    if len(custom_url) < 3 or len(custom_url) > 30:
        raise HTTPException(status_code=422, detail="URL must be between 3 and 30 characters")

    reserved_words = {'login', 'admin', 'settings', 'profile', 'callback', 'api', 'auth'}
    if custom_url.lower() in reserved_words:
        raise HTTPException(status_code=422, detail="This URL is reserved")

    async with aiosqlite.connect(DATABASE_PATH) as db:
        # Check if URL is already taken
        cursor = await db.execute(
            "SELECT 1 FROM users WHERE LOWER(custom_url) = LOWER(?) AND spotify_id != ?",
            (custom_url, user_id)
        )
        exists = await cursor.fetchone()
        if exists:
            raise HTTPException(status_code=422, detail="URL is already taken")

        # Update the custom URL
        await db.execute(
            "UPDATE users SET custom_url = ? WHERE spotify_id = ?",
            (custom_url, user_id)
        )
        await db.commit()
        
        return {"success": True, "custom_url": custom_url}

# Update the existing user endpoint to include custom_url
@app.get("/users/{user_id}")
async def get_user(user_id: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        # Try to find user by custom URL first
        cursor = await db.execute(
            "SELECT spotify_id, display_name, avatar_url, custom_url FROM users WHERE LOWER(custom_url) = LOWER(?)",
            (user_id,)
        )
        user = await cursor.fetchone()
        
        if not user:
            # If not found, try spotify_id
            cursor = await db.execute(
                "SELECT spotify_id, display_name, avatar_url, custom_url FROM users WHERE spotify_id = ?",
                (user_id,)
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
                        print(f"Failed to fetch recent tracks for user {user_id}: {response.status_code}")
                        continue
                    
                    tracks = response.json().get("items", [])
                    
                    # Clear existing tracks before inserting new ones
                    await db.execute("DELETE FROM tracks WHERE user_id = ?", (user_id,))
                    
                    for track in tracks:
                        await db.execute(
                            """
                            INSERT INTO tracks
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
