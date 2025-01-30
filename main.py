from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2AuthorizationCodeBearer
from typing import Optional, List
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import IndexModel, ASCENDING, DESCENDING
import httpx
import os
from datetime import datetime, timedelta
import json
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential
import logging
import asyncio
from bson import ObjectId
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
MONGODB_URI = os.getenv("MONGODB_URI")

# Initialize MongoDB client
client = AsyncIOMotorClient(MONGODB_URI)
db = client.spotify_db

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
    """Initialize MongoDB indexes"""
    # Users collection indexes
    await db.users.create_indexes([
        IndexModel([("spotify_id", ASCENDING)], unique=True),
        IndexModel([("custom_url", ASCENDING)], unique=True)
    ])

    # Tracks collection indexes
    await db.tracks.create_indexes([
        IndexModel([("user_id", ASCENDING), ("track_id", ASCENDING), ("played_at", ASCENDING)], unique=True),
        IndexModel([("played_at", DESCENDING)])
    ])

    # Top tracks collection indexes
    await db.top_tracks.create_indexes([
        IndexModel([("user_id", ASCENDING)]),
        IndexModel([("updated_at", ASCENDING)])
    ])

    # Top artists collection indexes
    await db.top_artists.create_indexes([
        IndexModel([("user_id", ASCENDING)]),
        IndexModel([("updated_at", ASCENDING)])
    ])

    # Playlists collection indexes
    await db.playlists.create_indexes([
        IndexModel([("user_id", ASCENDING)]),
        IndexModel([("spotify_url", ASCENDING)], unique=True)
    ])

# Utility functions
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def get_spotify_data(client, endpoint: str, token: str):
    """Fetches data from Spotify API with retry mechanism"""
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
    """Refreshes the Spotify access token"""
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
            
            await db.users.update_one(
                {"spotify_id": user_id},
                {
                    "$set": {
                        "access_token": token_data["access_token"],
                        "token_expiry": datetime.utcnow() + timedelta(hours=1)
                    }
                }
            )
                
            return token_data["access_token"]
    except Exception as e:
        logger.error(f"Error refreshing token: {str(e)}")
        raise

async def get_valid_token(user_id: str):
    """Gets a valid token, refreshing if necessary"""
    user = await db.users.find_one({"spotify_id": user_id})
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if datetime.utcnow() >= user['token_expiry']:
        return await refresh_token(user_id, user['refresh_token'])
        
    return user['access_token']

def is_valid_url(url: str):
    """Validates custom URL format"""
    import re
    pattern = re.compile("^[a-zA-Z0-9_-]{3,30}$")
    return bool(pattern.match(url))

# Scheduled tasks
async def update_recent_tracks():
    """Updates recent tracks for all users every 15 minutes"""
    try:
        users = await db.users.find({
            "$or": [
                {"last_update": {"$exists": False}},
                {"last_update": {"$lte": datetime.utcnow() - timedelta(minutes=15)}}
            ]
        }).to_list(length=None)
        
        async with httpx.AsyncClient() as client:
            for user in users:
                try:
                    token = await get_valid_token(user['spotify_id'])
                    
                    tracks_data = await get_spotify_data(
                        client,
                        "me/player/recently-played?limit=50",
                        token
                    )
                    
                    # Process tracks one by one instead of bulk operation
                    for track in tracks_data["items"]:
                        await db.tracks.update_one(
                            {
                                "user_id": user['spotify_id'],
                                "track_id": track["track"]["id"],
                                "played_at": track["played_at"]
                            },
                            {
                                "$set": {
                                    "user_id": user['spotify_id'],
                                    "track_id": track["track"]["id"],
                                    "track_name": track["track"]["name"],
                                    "artist_name": track["track"]["artists"][0]["name"],
                                    "album_name": track["track"]["album"]["name"],
                                    "album_art": track["track"]["album"]["images"][0]["url"] if track["track"]["album"]["images"] else None,
                                    "played_at": track["played_at"]
                                }
                            },
                            upsert=True
                        )
                    
                    await db.users.update_one(
                        {"spotify_id": user['spotify_id']},
                        {"$set": {"last_update": datetime.utcnow()}}
                    )
                        
                except Exception as e:
                    logger.error(f"Error updating tracks for user {user['spotify_id']}: {str(e)}")
                    continue
    except Exception as e:
        logger.error(f"Error in update_recent_tracks: {str(e)}")

async def update_top_items():
    """Updates top tracks and artists for all users daily"""
    try:
        users = await db.users.find({
            "$or": [
                {"top_items_update": {"$exists": False}},
                {"top_items_update": {"$lte": datetime.utcnow() - timedelta(days=1)}}
            ]
        }).to_list(length=None)
        
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
                    
                    # Replace all top tracks for user
                    await db.top_tracks.delete_many({"user_id": user['spotify_id']})
                    if tracks_data["items"]:
                        await db.top_tracks.insert_many([
                            {
                                "user_id": user['spotify_id'],
                                "track_id": track["id"],
                                "track_name": track["name"],
                                "artist_name": track["artists"][0]["name"],
                                "album_name": track["album"]["name"],
                                "album_art": track["album"]["images"][0]["url"] if track["album"]["images"] else None,
                                "updated_at": datetime.utcnow()
                            }
                            for track in tracks_data["items"]
                        ])
                    
                    # Get top artists
                    artists_data = await get_spotify_data(
                        client,
                        "me/top/artists?limit=50&time_range=short_term",
                        token
                    )
                    
                    # Replace all top artists for user
                    await db.top_artists.delete_many({"user_id": user['spotify_id']})
                    if artists_data["items"]:
                        await db.top_artists.insert_many([
                            {
                                "user_id": user['spotify_id'],
                                "artist_id": artist["id"],
                                "artist_name": artist["name"],
                                "artist_image": artist["images"][0]["url"] if artist["images"] else None,
                                "updated_at": datetime.utcnow()
                            }
                            for artist in artists_data["items"]
                        ])
                    
                    await db.users.update_one(
                        {"spotify_id": user['spotify_id']},
                        {"$set": {"top_items_update": datetime.utcnow()}}
                    )
                    
                except Exception as e:
                    logger.error(f"Error updating top items for user {user['spotify_id']}: {str(e)}")
                    continue
    except Exception as e:
        logger.error(f"Error in update_top_items: {str(e)}")


async def update_user_playlists():
    """Updates user playlists every hour, storing only public playlists"""
    try:
        users = await db.users.find({
            "$or": [
                {"playlists_update": {"$exists": False}},
                {"playlists_update": {"$lte": datetime.utcnow() - timedelta(hours=1)}}
            ]
        }).to_list(length=None)
        
        async with httpx.AsyncClient() as client:
            for user in users:
                try:
                    token = await get_valid_token(user['spotify_id'])
                    
                    playlists_data = await get_spotify_data(
                        client,
                        "me/playlists?limit=50",
                        token
                    )
                    
                    await db.playlists.delete_many({"user_id": user['spotify_id']})
                    playlists = [
                        {
                            "user_id": user['spotify_id'],
                            "playlist_id": playlist["id"],
                            "playlist_name": playlist["name"],
                            "spotify_url": playlist["external_urls"]["spotify"],
                            "cover_image": playlist["images"][0]["url"] if playlist.get("images") else None,
                            "total_tracks": playlist["tracks"]["total"],
                            "updated_at": datetime.utcnow(),
                            "is_public": playlist.get("public", True)
                        }
                        for playlist in playlists_data["items"]
                        if playlist.get("public", True)  # Only include public playlists
                    ]
                    
                    if playlists:
                        await db.playlists.insert_many(playlists)
                    
                    await db.users.update_one(
                        {"spotify_id": user['spotify_id']},
                        {"$set": {"playlists_update": datetime.utcnow()}}
                    )
                    
                except Exception as e:
                    logger.error(f"Error updating playlists for user {user['spotify_id']}: {str(e)}")
                    continue
    except Exception as e:
        logger.error(f"Error in update_user_playlists: {str(e)}")

# FastAPI startup and shutdown events
@app.on_event("startup")
async def startup_event():
    await init_db()
    scheduler.add_job(update_recent_tracks, 'interval', minutes=15)
    scheduler.add_job(update_top_items, 'interval', hours=24)
    scheduler.add_job(update_user_playlists, 'interval', hours=1)
    init_keepalive_scheduler() 
    scheduler.start()

async def cleanup():
    scheduler.shutdown(wait=True)
    if client:
        await client.close()

@app.on_event("shutdown")
async def shutdown_event():
    await cleanup()
    
@app.get("/keepalive")
async def keepalive():
    return {"status": "alive"}

# Add scheduler for keep-alive
def init_keepalive_scheduler():
    async def ping_self():
        try:
            port = os.getenv("PORT", "8000")
            async with httpx.AsyncClient() as client:
                await client.get(f"http://localhost:{port}/keepalive")
                logger.info("Keep-alive ping successful")
        except Exception as e:
            logger.error(f"Keep-alive ping failed: {str(e)}")

    scheduler.add_job(ping_self, 'interval', minutes=10)

@app.get("/users/{user_id}/playlists")
async def get_user_playlists(user_id: str):
    playlists = await db.playlists.find(
        {"user_id": user_id},
        {"_id": 0, "playlist_name": 1, "cover_image": 1, "spotify_url": 1, "total_tracks": 1}
    ).sort("playlist_name", 1).to_list(length=None)
    
    return [
        {
            "name": playlist['playlist_name'],
            "cover_image": playlist['cover_image'],
            "url": playlist['spotify_url'].split('/')[-1][:5],  # Extract only first 5 characters of playlist ID
            "total_tracks": playlist['total_tracks']
        }
        for playlist in playlists
    ]

@app.get("/playlists/{playlist_id}")
async def get_playlist_details(playlist_id: str):
    # Find the playlist using just the first 5 characters of the ID
    playlist = await db.playlists.find_one(
        {"spotify_url": {"$regex": f".*{playlist_id}"}},
        {"_id": 0}
    )
    
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    # Get user info
    user = await db.users.find_one(
        {"spotify_id": playlist['user_id']},
        {"_id": 0, "custom_url": 1, "display_name": 1}
    )
    
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
            "display_name": user['display_name'],
            "profile_url": user['custom_url']
        },
        "tracks": tracks
    }

@app.get("/check-url/{custom_url}")
async def check_url_availability(custom_url: str):
    if not is_valid_url(custom_url):
        return {"available": False, "reason": "Invalid URL format"}
    
    count = await db.users.count_documents({"custom_url": custom_url.lower()})
    return {"available": count == 0}

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
            await db.users.update_one(
                {"spotify_id": user_data["id"]},
                {
                    "$set": {
                        "spotify_id": user_data["id"],
                        "custom_url": custom_url.lower(),
                        "access_token": token_data["access_token"],
                        "refresh_token": token_data.get("refresh_token"),
                        "token_expiry": datetime.utcnow() + timedelta(hours=1),
                        "display_name": user_data.get("display_name", user_data["id"]),
                        "avatar_url": avatar_url
                    }
                },
                upsert=True
            )

            # Immediately collect initial data for the new user
            async def collect_initial_data():
                try:
                    # Create a context for the user
                    user_context = {"spotify_id": user_data["id"]}
                    
                    # Run all data collection functions for this user
                    await update_recent_tracks_for_user(user_context)
                    await update_top_items_for_user(user_context)
                    await update_user_playlists_for_user(user_context)
                except Exception as e:
                    logger.error(f"Error collecting initial data for user {user_data['id']}: {str(e)}")

            async def update_recent_tracks_for_user(user):
                async with httpx.AsyncClient() as client:
                    token = await get_valid_token(user['spotify_id'])
                    tracks_data = await get_spotify_data(
                        client,
                        "me/player/recently-played?limit=50",
                        token
                    )
                    
                    # Insert tracks individually instead of using bulk write
                    for track in tracks_data["items"]:
                        await db.tracks.update_one(
                            {
                                "user_id": user['spotify_id'],
                                "track_id": track["track"]["id"],
                                "played_at": track["played_at"]
                            },
                            {
                                "$set": {
                                    "user_id": user['spotify_id'],
                                    "track_id": track["track"]["id"],
                                    "track_name": track["track"]["name"],
                                    "artist_name": track["track"]["artists"][0]["name"],
                                    "album_name": track["track"]["album"]["name"],
                                    "album_art": track["track"]["album"]["images"][0]["url"] if track["track"]["album"]["images"] else None,
                                    "played_at": track["played_at"]
                                }
                            },
                            upsert=True
                        )

                    # Update the last update timestamp
                    await db.users.update_one(
                        {"spotify_id": user['spotify_id']},
                        {"$set": {"last_update": datetime.utcnow()}}
                    )

            async def update_top_items_for_user(user):
                async with httpx.AsyncClient() as client:
                    token = await get_valid_token(user['spotify_id'])
                    
                    # Get top tracks
                    tracks_data = await get_spotify_data(
                        client,
                        "me/top/tracks?limit=50&time_range=short_term",
                        token
                    )
                    
                    # Clear existing top tracks
                    await db.top_tracks.delete_many({"user_id": user['spotify_id']})
                    if tracks_data["items"]:
                        await db.top_tracks.insert_many([{
                            "user_id": user['spotify_id'],
                            "track_id": track["id"],
                            "track_name": track["name"],
                            "artist_name": track["artists"][0]["name"],
                            "album_name": track["album"]["name"],
                            "album_art": track["album"]["images"][0]["url"] if track["album"]["images"] else None,
                            "popularity": track["popularity"],
                            "updated_at": datetime.utcnow()
                        } for track in tracks_data["items"]])
                    
                    # Get top artists
                    artists_data = await get_spotify_data(
                        client,
                        "me/top/artists?limit=50&time_range=short_term",
                        token
                    )
                    
                    # Clear existing top artists
                    await db.top_artists.delete_many({"user_id": user['spotify_id']})
                    if artists_data["items"]:
                        await db.top_artists.insert_many([{
                            "user_id": user['spotify_id'],
                            "artist_id": artist["id"],
                            "artist_name": artist["name"],
                            "artist_image": artist["images"][0]["url"] if artist["images"] else None,
                            "popularity": artist["popularity"],
                            "updated_at": datetime.utcnow()
                        } for artist in artists_data["items"]])

                    # Update the last update timestamp
                    await db.users.update_one(
                        {"spotify_id": user['spotify_id']},
                        {"$set": {"top_items_update": datetime.utcnow()}}
                    )

            async def update_user_playlists_for_user(user):
                """Individual user playlist update function for public playlists only"""
                async with httpx.AsyncClient() as client:
                    try:
                        token = await get_valid_token(user['spotify_id'])
                        
                        playlists_data = await get_spotify_data(
                            client,
                            "me/playlists?limit=50",
                            token
                        )
                        
                        await db.playlists.delete_many({"user_id": user['spotify_id']})
                        
                        playlists = [
                            {
                                "user_id": user['spotify_id'],
                                "playlist_id": playlist["id"],
                                "playlist_name": playlist["name"],
                                "spotify_url": playlist["external_urls"]["spotify"],
                                "cover_image": playlist["images"][0]["url"] if playlist.get("images") else None,
                                "total_tracks": playlist["tracks"]["total"],
                                "updated_at": datetime.utcnow(),
                                "is_public": playlist.get("public", True)
                            }
                            for playlist in playlists_data["items"]
                            if playlist.get("public", True)  # Only include public playlists
                        ]
                        
                        if playlists:
                            await db.playlists.insert_many(playlists)
                    
                        await db.users.update_one(
                            {"spotify_id": user['spotify_id']},
                            {"$set": {"playlists_update": datetime.utcnow()}}
                        )
                        
                    except Exception as e:
                        logger.error(f"Error updating playlists for user {user['spotify_id']}: {str(e)}")
                        raise
            # Start the initial data collection in the background
            asyncio.create_task(collect_initial_data())
            
            return {
                "success": True,
                "user_id": custom_url.lower()
            }
            
    except Exception as e:
        logger.error(f"Callback error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/users/{user_id}")
async def get_user(user_id: str):
    user = await db.users.find_one(
        {
            "$or": [
                {"custom_url": user_id.lower()},
                {"spotify_id": user_id}
            ]
        },
        {"_id": 0, "spotify_id": 1, "custom_url": 1, "display_name": 1, "avatar_url": 1}
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
    tracks = await db.tracks.find(
        {"user_id": user_id},
        {"_id": 0, "track_name": 1, "artist_name": 1, "played_at": 1, "album_art": 1}
    ).sort("played_at", -1).limit(50).to_list(length=None)
    
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
    try:
        # First, get the user and ensure they exist
        user = await db.users.find_one(
            {"spotify_id": user_id},
            {"_id": 0, "access_token": 1}
        )
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Get a valid token
        token = await get_valid_token(user_id)
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.spotify.com/v1/me/player/currently-playing",
                headers={"Authorization": f"Bearer {token}"},
            )
            
            # Handle different response scenarios
            if response.status_code == 204:
                return {"is_playing": False, "message": "No track currently playing"}
                
            if response.status_code == 401:
                # Token might be invalid despite our refresh attempt
                raise HTTPException(status_code=401, detail="Authentication failed")
                
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail="Failed to get current track"
                )
            
            try:
                data = response.json()
                
                # Check if we have valid data
                if not data or "item" not in data:
                    return {"is_playing": False, "message": "No track information available"}
                
                # Extract track information with safe fallbacks
                track_item = data.get("item", {})
                if not track_item:
                    return {"is_playing": False, "message": "No track details available"}
                
                artists = track_item.get("artists", [])
                artist_name = artists[0].get("name", "Unknown Artist") if artists else "Unknown Artist"
                
                album = track_item.get("album", {})
                images = album.get("images", [])
                album_art = images[0].get("url") if images else None
                
                return {
                    "is_playing": data.get("is_playing", False),
                    "track_name": track_item.get("name", "Unknown Track"),
                    "artist_name": artist_name,
                    "album_art": album_art,
                    "progress_ms": data.get("progress_ms"),
                    "duration_ms": track_item.get("duration_ms")
                }
                
            except (KeyError, IndexError, TypeError) as e:
                logger.error(f"Error parsing currently playing response: {str(e)}")
                return {
                    "is_playing": False,
                    "message": "Error parsing track information"
                }
                
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_currently_playing: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="An error occurred while fetching currently playing track"
        )
        
@app.get("/users/search")
async def search_users(query: str = None):
    if not query:
        return []
        
    try:
        users = await db.users.find(
            {
                "$or": [
                    {"spotify_id": {"$regex": query, "$options": "i"}},
                    {"display_name": {"$regex": query, "$options": "i"}}
                ]
            },
            {"_id": 0, "spotify_id": 1, "display_name": 1, "avatar_url": 1}
        ).limit(10).to_list(length=None)
        
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
    user = await db.users.find_one(
        {
            "$or": [
                {"custom_url": user_id.lower()},
                {"spotify_id": user_id}
            ]
        },
        {"_id": 0, "spotify_id": 1}
    )
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    tracks = await db.top_tracks.find(
        {"user_id": user['spotify_id']},
        {
            "_id": 0,
            "track_name": 1,
            "artist_name": 1,
            "album_name": 1,
            "album_art": 1
        }
    ).limit(50).to_list(length=None)
    
    return [
        {
            "track_name": track['track_name'],
            "artist_name": track['artist_name'],
            "album_name": track['album_name'],
            "album_art": track['album_art']
        }
        for track in tracks
    ]


@app.get("/users/{user_id}/top-artists")
async def get_top_artists(user_id: str):
    artists = await db.top_artists.find(
        {"user_id": user_id},
        {"_id": 0, "artist_name": 1, "artist_image": 1}
    ).limit(50).to_list(length=None)
    
    return [
        {
            "artist_name": artist['artist_name'],
            "artist_image": artist['artist_image']
        }
        for artist in artists
    ]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
