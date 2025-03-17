import os
import logging
import asyncio
import datetime
from typing import List, Optional

import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button

from dotenv import load_dotenv

from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy import select, and_
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker

# ───────────────────────────────────────────────────────────────────────────────
# Load environment variables and set up logging
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")  # e.g. "postgresql+asyncpg://user:password@host/dbname"
LOGGING_LEVEL = os.getenv("LOGGING_LEVEL", "INFO").upper()

logging.basicConfig(level=getattr(logging, LOGGING_LEVEL))
logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────────────────────
# SQLAlchemy setup
Base = declarative_base()

class UserProfile(Base):
    __tablename__ = 'user_profiles'
    id = Column(Integer, primary_key=True)
    discord_id = Column(String, unique=True, nullable=False)
    age = Column(Integer, nullable=False)
    gender = Column(String, nullable=False)
    bio = Column(Text, nullable=False)
    looking_for = Column(String, nullable=False)
    # Store preferred attracted genders as an array of strings (e.g. ['male','female'])
    attracted_genders = Column(ARRAY(String), nullable=False)
    # Matching preferences (age range of potential matches)
    preferred_min_age = Column(Integer, nullable=False, default=18)
    preferred_max_age = Column(Integer, nullable=False, default=100)
    # If matched, store the Discord ID of the partner
    matched_with = Column(String, nullable=True)

class Swipe(Base):
    __tablename__ = 'swipes'
    id = Column(Integer, primary_key=True)
    swiper_id = Column(String, nullable=False)  # who swiped
    swiped_id = Column(String, nullable=False)  # profile on which swipe was made
    right_swipe = Column(Boolean, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

# Create an async engine and session maker
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Utility: create tables (you might want to run this separately)
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created.")

# ───────────────────────────────────────────────────────────────────────────────
# Discord bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# A helper function to get a DB session
async def get_db_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session

# ───────────────────────────────────────────────────────────────────────────────
# Database helper functions

async def get_user_profile(session: AsyncSession, discord_id: str) -> Optional[UserProfile]:
    result = await session.execute(select(UserProfile).where(UserProfile.discord_id == discord_id))
    return result.scalars().first()

async def create_user_profile(session: AsyncSession, discord_id: str, age: int, gender: str,
                              bio: str, looking_for: str, attracted_genders: List[str],
                              preferred_min_age: int, preferred_max_age: int) -> UserProfile:
    profile = UserProfile(
        discord_id=discord_id,
        age=age,
        gender=gender,
        bio=bio,
        looking_for=looking_for,
        attracted_genders=attracted_genders,
        preferred_min_age=preferred_min_age,
        preferred_max_age=preferred_max_age,
    )
    session.add(profile)
    await session.commit()
    return profile

async def update_user_profile(session: AsyncSession, profile: UserProfile, **kwargs):
    for key, value in kwargs.items():
        setattr(profile, key, value)
    await session.commit()
    return profile

async def delete_user_profile(session: AsyncSession, profile: UserProfile):
    await session.delete(profile)
    await session.commit()

async def record_swipe(session: AsyncSession, swiper_id: str, swiped_id: str, right_swipe: bool):
    swipe = Swipe(
        swiper_id=swiper_id,
        swiped_id=swiped_id,
        right_swipe=right_swipe
    )
    session.add(swipe)
    await session.commit()
    return swipe

async def has_swiped(session: AsyncSession, swiper_id: str, swiped_id: str) -> bool:
    result = await session.execute(
        select(Swipe).where(
            Swipe.swiper_id == swiper_id,
            Swipe.swiped_id == swiped_id
        )
    )
    return result.scalars().first() is not None

async def check_for_reciprocal_swipe(session: AsyncSession, user_id: str, candidate_id: str) -> bool:
    result = await session.execute(
        select(Swipe).where(
            Swipe.swiper_id == candidate_id,
            Swipe.swiped_id == user_id,
            Swipe.right_swipe == True
        )
    )
    return result.scalars().first() is not None

async def mark_as_matched(session: AsyncSession, user1: UserProfile, user2: UserProfile):
    user1.matched_with = user2.discord_id
    user2.matched_with = user1.discord_id
    await session.commit()

# ───────────────────────────────────────────────────────────────────────────────
# Matching Logic
async def get_next_candidate(session: AsyncSession, user: UserProfile) -> Optional[UserProfile]:
    """
    Return the next eligible candidate for matching based on:
      - Not the same user.
      - Both users are not already matched.
      - Candidate’s age is within the swiping user's preferred age range.
      - Swiping user's age is within candidate's preferred age range.
      - Candidate's gender is in user's attracted_genders and vice versa.
      - Their "looking_for" fields match.
      - The swiping user has not already swiped on this candidate.
    """
    stmt = select(UserProfile).where(
        UserProfile.discord_id != user.discord_id,
        UserProfile.matched_with.is_(None),
        # candidate age filter
        UserProfile.age >= user.preferred_min_age,
        UserProfile.age <= user.preferred_max_age,
        # reciprocal age filter: user's age should be within candidate's preference (for simplicity assume stored in profile)
        user.age >= UserProfile.preferred_min_age,
        user.age <= UserProfile.preferred_max_age,
        # looking_for must match
        UserProfile.looking_for == user.looking_for,
    )
    result = await session.execute(stmt)
    candidates = result.scalars().all()
    
    # Filter based on gender preferences and if already swiped on
    for candidate in candidates:
        # Check if candidate's gender is in user's attracted_genders
        if candidate.gender.lower() not in [g.lower() for g in user.attracted_genders]:
            continue
        # Also check if user's gender is in candidate's attracted_genders
        if user.gender.lower() not in [g.lower() for g in candidate.attracted_genders]:
            continue
        # Check if the user already swiped on candidate
        if await has_swiped(session, user.discord_id, candidate.discord_id):
            continue

        return candidate

    return None

# ───────────────────────────────────────────────────────────────────────────────
# Discord UI: Matching View with Buttons

class MatchView(View):
    def __init__(self, user_profile: UserProfile):
        super().__init__(timeout=180)  # session timeout of 3 minutes
        self.user_profile = user_profile
        self.current_candidate: Optional[UserProfile] = None
        self.session = None  # will set the DB session in the callback context

    async def update_candidate(self, interaction: discord.Interaction):
        # Create a new session for each candidate query
        async with AsyncSessionLocal() as session:
            self.session = session
            candidate = await get_next_candidate(session, self.user_profile)
            if candidate is None:
                await interaction.response.edit_message(content="You've run out of swipes for now. Check back tomorrow!", embed=None, view=None)
                self.stop()
                return
            self.current_candidate = candidate
            embed = discord.Embed(title="Potential Match", color=discord.Color.blue())
            embed.add_field(name="Age", value=str(candidate.age))
            embed.add_field(name="Gender", value=candidate.gender)
            embed.add_field(name="Looking for", value=candidate.looking_for)
            embed.add_field(name="Bio", value=candidate.bio, inline=False)
            embed.set_author(name=f"{candidate.discord_id}")  # using discord_id; in practice, you might store username separately
            # In a real bot, you might fetch the discord.Member to get profile pic
            await interaction.response.edit_message(content="Swipe right or left:", embed=embed, view=self)

    @discord.ui.button(label="Swipe Right", style=discord.ButtonStyle.green)
    async def swipe_right(self, interaction: discord.Interaction, button: Button):
        if self.current_candidate is None:
            await interaction.response.send_message("No candidate available.", ephemeral=True)
            return

        async with AsyncSessionLocal() as session:
            # Record the right swipe
            await record_swipe(session, self.user_profile.discord_id, self.current_candidate.discord_id, True)
            # Check for reciprocal right swipe
            if await check_for_reciprocal_swipe(session, self.user_profile.discord_id, self.current_candidate.discord_id):
                # Mark both users as matched
                # Fetch candidate profile object
                candidate_profile = await get_user_profile(session, self.current_candidate.discord_id)
                if candidate_profile:
                    await mark_as_matched(session, self.user_profile, candidate_profile)
                    await interaction.response.edit_message(content=f"It's a match with {self.current_candidate.discord_id}!", embed=None, view=None)
                    # In a full implementation, you would notify the candidate as well (e.g. via DM)
                    self.stop()
                    return
            # Otherwise, load the next candidate
            await self.update_candidate(interaction)

    @discord.ui.button(label="Swipe Left", style=discord.ButtonStyle.red)
    async def swipe_left(self, interaction: discord.Interaction, button: Button):
        if self.current_candidate is None:
            await interaction.response.send_message("No candidate available.", ephemeral=True)
            return

        async with AsyncSessionLocal() as session:
            # Record the left swipe (if you want to keep track)
            await record_swipe(session, self.user_profile.discord_id, self.current_candidate.discord_id, False)
            # Load next candidate
            await self.update_candidate(interaction)

# ───────────────────────────────────────────────────────────────────────────────
# Slash Commands for Profile and Matching Management

@bot.tree.command(name="create_profile", description="Create your dating profile.")
@app_commands.describe(
    age="Your age (must be between 18 and 100)",
    gender="Your gender",
    bio="A short bio about yourself",
    looking_for="What you're looking for (e.g., dating, friends)",
    attracted_genders="Comma-separated genders you're attracted to (e.g., male,female)",
    preferred_min_age="Preferred minimum age for matches",
    preferred_max_age="Preferred maximum age for matches"
)
async def create_profile(interaction: discord.Interaction, age: int, gender: str, bio: str, looking_for: str,
                         attracted_genders: str, preferred_min_age: int = 18, preferred_max_age: int = 100):
    if age < 18 or age > 100:
        await interaction.response.send_message("Age must be between 18 and 100.", ephemeral=True)
        return

    # Convert comma-separated string into list
    attracted_list = [s.strip() for s in attracted_genders.split(",") if s.strip()]

    async with AsyncSessionLocal() as session:
        existing = await get_user_profile(session, str(interaction.user.id))
        if existing:
            await interaction.response.send_message("You already have a profile. Use /update_profile to modify it.", ephemeral=True)
            return
        profile = await create_user_profile(
            session,
            discord_id=str(interaction.user.id),
            age=age,
            gender=gender,
            bio=bio,
            looking_for=looking_for,
            attracted_genders=attracted_list,
            preferred_min_age=preferred_min_age,
            preferred_max_age=preferred_max_age
        )
    await interaction.response.send_message("Profile created successfully!", ephemeral=True)

@bot.tree.command(name="update_profile", description="Update your dating profile.")
@app_commands.describe(
    age="Your age (must be between 18 and 100)",
    gender="Your gender",
    bio="A short bio about yourself",
    looking_for="What you're looking for (e.g., dating, friends)",
    attracted_genders="Comma-separated genders you're attracted to (e.g., male,female)",
    preferred_min_age="Preferred minimum age for matches",
    preferred_max_age="Preferred maximum age for matches"
)
async def update_profile(interaction: discord.Interaction, age: int, gender: str, bio: str, looking_for: str,
                         attracted_genders: str, preferred_min_age: int, preferred_max_age: int):
    if age < 18 or age > 100:
        await interaction.response.send_message("Age must be between 18 and 100.", ephemeral=True)
        return

    attracted_list = [s.strip() for s in attracted_genders.split(",") if s.strip()]

    async with AsyncSessionLocal() as session:
        profile = await get_user_profile(session, str(interaction.user.id))
        if not profile:
            await interaction.response.send_message("You don't have a profile yet. Use /create_profile first.", ephemeral=True)
            return
        await update_user_profile(session, profile,
                                  age=age,
                                  gender=gender,
                                  bio=bio,
                                  looking_for=looking_for,
                                  attracted_genders=attracted_list,
                                  preferred_min_age=preferred_min_age,
                                  preferred_max_age=preferred_max_age)
    await interaction.response.send_message("Profile updated successfully!", ephemeral=True)

@bot.tree.command(name="delete_profile", description="Delete your dating profile.")
async def delete_profile(interaction: discord.Interaction):
    async with AsyncSessionLocal() as session:
        profile = await get_user_profile(session, str(interaction.user.id))
        if not profile:
            await interaction.response.send_message("No profile found to delete.", ephemeral=True)
            return
        await delete_user_profile(session, profile)
    await interaction.response.send_message("Profile deleted successfully.", ephemeral=True)

@bot.tree.command(name="start_matching", description="Start swiping for matches.")
async def start_matching(interaction: discord.Interaction):
    async with AsyncSessionLocal() as session:
        profile = await get_user_profile(session, str(interaction.user.id))
        if not profile:
            await interaction.response.send_message("You must create a profile first using /create_profile.", ephemeral=True)
            return
        if profile.matched_with:
            await interaction.response.send_message("You are already matched. Unmatch first to start swiping.", ephemeral=True)
            return

    view = MatchView(user_profile=profile)
    await interaction.response.send_message("Fetching a potential match...", ephemeral=True, view=view)
    # Initialize first candidate
    await view.update_candidate(interaction)

@bot.tree.command(name="unmatch", description="Unmatch from your current match.")
async def unmatch(interaction: discord.Interaction):
    async with AsyncSessionLocal() as session:
        profile = await get_user_profile(session, str(interaction.user.id))
        if not profile or not profile.matched_with:
            await interaction.response.send_message("You are not currently matched with anyone.", ephemeral=True)
            return
        # Also remove the match from the other user's profile
        other_profile = await get_user_profile(session, profile.matched_with)
        profile.matched_with = None
        if other_profile:
            other_profile.matched_with = None
        await session.commit()
    await interaction.response.send_message("Match removed. You are now back in the matching pool.", ephemeral=True)

# ───────────────────────────────────────────────────────────────────────────────
# Bot startup events

@bot.event
async def on_ready():
    # Sync slash commands with Discord
    try:
        await bot.tree.sync()
        logger.info("Slash commands synced.")
    except Exception as e:
        logger.error("Error syncing commands: %s", e)
    logger.info("Bot is ready.")

async def main():
    # Initialize the database tables (only if not already created)
    await init_db()
    await bot.start(BOT_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot shutdown requested.")
