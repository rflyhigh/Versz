from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
import asyncio
import os
from dotenv import load_load_dotenv

# Load environment variables
load_dotenv()

# Initialize FastAPI app
app = FastAPI()

# CORS middleware configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://versz.fun", "https://www.versz.fun"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security configuration
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

# Database connection
MONGODB_URL = os.getenv("MONGODB_URL")
client = AsyncIOMotorClient(MONGODB_URL)
db = client.versz

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# Models
class User(BaseModel):
    username: str
    name: str
    password: str

class LyricsContent(BaseModel):
    title: str
    subtitle: str
    lyrics: str
    fontSize: str
    textColor: str
    textFormat: str
    theme: str

class LyricsShare(BaseModel):
    extension: str
    content: LyricsContent

# Authentication functions
async def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

async def get_password_hash(password):
    return pwd_context.hash(password)

async def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    user = await db.users.find_one({"username": username})
    if user is None:
        raise credentials_exception
    return user

# Self-ping mechanism
async def keep_alive():
    while True:
        try:
            requests.get('https://tiffintreats.onrender.com/ping')
            time.sleep(600)  # 10 minutes
        except:
            continue

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(keep_alive())

# Endpoints
@app.post("/register")
async def register(user: User):
    if await db.users.find_one({"username": user.username}):
        raise HTTPException(status_code=400, detail="Username already registered")
    
    hashed_password = await get_password_hash(user.password)
    user_dict = user.dict()
    user_dict["password"] = hashed_password
    
    await db.users.insert_one(user_dict)
    return {"message": "User registered successfully"}

@app.post("/token")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = await db.users.find_one({"username": form_data.username})
    if not user or not await verify_password(form_data.password, user["password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = await create_access_token({"sub": user["username"]})
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/lyrics")
async def get_user_lyrics(current_user: dict = Depends(get_current_user)):
    lyrics = await db.lyrics.find({"username": current_user["username"]}).to_list(length=None)
    return lyrics

@app.post("/lyrics")
async def create_lyrics(content: LyricsContent, current_user: dict = Depends(get_current_user)):
    lyrics_doc = {
        "username": current_user["username"],
        "content": content.dict(),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    result = await db.lyrics.insert_one(lyrics_doc)
    return {"id": str(result.inserted_id)}

@app.put("/lyrics/{lyrics_id}")
async def update_lyrics(lyrics_id: str, content: LyricsContent, current_user: dict = Depends(get_current_user)):
    result = await db.lyrics.update_one(
        {"_id": lyrics_id, "username": current_user["username"]},
        {
            "$set": {
                "content": content.dict(),
                "updated_at": datetime.utcnow()
            }
        }
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Lyrics not found or unauthorized")
    return {"message": "Lyrics updated successfully"}

@app.post("/share")
async def share_lyrics(share: LyricsShare, current_user: dict = Depends(get_current_user)):
    if await db.shares.find_one({"extension": share.extension}):
        raise HTTPException(status_code=400, detail="Extension already in use")
    
    share_doc = {
        "username": current_user["username"],
        "extension": share.extension,
        "content": share.content.dict(),
        "created_at": datetime.utcnow()
    }
    await db.shares.insert_one(share_doc)
    return {"url": f"https://versz.fun/{share.extension}"}

@app.get("/share/{extension}")
async def get_shared_lyrics(extension: str):
    share = await db.shares.find_one({"extension": extension})
    if not share:
        raise HTTPException(status_code=404, detail="Shared lyrics not found")
    return share["content"]
