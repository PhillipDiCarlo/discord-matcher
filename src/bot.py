import os
import logging
import asyncio
import datetime
from typing import List, Optional

import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button, Select

from dotenv import load_dotenv

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    select,
    ForeignKey,
    func,
    Enum
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker

# ───────────────────────────────────────────────────────────────────────────────
# Load environment variables and set up logging
load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_DB = os.getenv("POSTGRES_DB")
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
LOGGING_LEVEL = os.getenv("LOGGING_LEVEL", "INFO").upper()

logging.basicConfig(level=getattr(logging, LOGGING_LEVEL))
logger = logging.getLogger(__name__)

# Construct the database URL with asyncpg as the driver.
DATABASE_URL = f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}/{POSTGRES_DB}"

# ───────────────────────────────────────────────────────────────────────────────
# Bot initialization

# Define intents (enabling members if needed for guild.get_member() functionality)
intents = discord.Intents.default()
intents.members = True

class MyBot(discord.Client):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # Sync slash commands on startup
        await self.tree.sync()
        logger.info("Slash commands synced.")

# Initialize the bot
bot = MyBot(intents=intents)

# ───────────────────────────────────────────────────────────────────────────────
# SQLAlchemy setup
Base = declarative_base()

# Allowed genders for ENUM (should match the DB ENUM 'gender_enum')
allowed_genders = ("Male", "Female", "Trans", "Non-Binary")

class UserProfile(Base):
    __tablename__ = 'user_profiles'
    id = Column(Integer, primary_key=True)
    discord_id = Column(String, unique=True, nullable=False)
    age = Column(Integer, nullable=False)
    # Use ENUM for gender (PostgreSQL will use the pre-created gender_enum type)
    gender = Column(Enum(*allowed_genders, name="gender_enum"), nullable=False)
    bio = Column(Text, nullable=False)
    looking_for = Column(String, nullable=False)
    # Store attracted genders as an array of the ENUM
    attracted_genders = Column(ARRAY(Enum(*allowed_genders, name="gender_enum")), nullable=False)
    preferred_min_age = Column(Integer, nullable=False, default=18)
    preferred_max_age = Column(Integer, nullable=False, default=100)
    matched_with = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

class Swipe(Base):
    __tablename__ = 'swipes'
    id = Column(Integer, primary_key=True)
    # Foreign keys reference user_profiles(discord_id)
    swiper_id = Column(String, ForeignKey("user_profiles.discord_id", ondelete="CASCADE"), nullable=False)
    swiped_id = Column(String, ForeignKey("user_profiles.discord_id", ondelete="CASCADE"), nullable=False)
    right_swipe = Column(Boolean, nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created.")

# ───────────────────────────────────────────────────────────────────────────────
# Database helper functions

async def get_user_profile(session: AsyncSession, discord_id: str) -> Optional[UserProfile]:
    result = await session.execute(select(UserProfile).where(UserProfile.discord_id == discord_id))
    return result.scalars().first()

async def create_user_profile(
    session: AsyncSession,
    discord_id: str,
    age: int,
    gender: str,
    bio: str,
    looking_for: str,
    attracted_genders: List[str],
    preferred_min_age: int,
    preferred_max_age: int
) -> UserProfile:
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
        UserProfile.age >= user.preferred_min_age,
        UserProfile.age <= user.preferred_max_age,
        user.age >= UserProfile.preferred_min_age,
        user.age <= UserProfile.preferred_max_age,
        UserProfile.looking_for == user.looking_for,
    )
    result = await session.execute(stmt)
    candidates = result.scalars().all()

    for candidate in candidates:
        if candidate.gender not in user.attracted_genders:
            continue
        if user.gender not in candidate.attracted_genders:
            continue
        if await has_swiped(session, user.discord_id, candidate.discord_id):
            continue
        return candidate

    return None

# ───────────────────────────────────────────────────────────────────────────────
# Discord UI: Matching View with Buttons

class MatchView(View):
    def __init__(self, user_profile: UserProfile):
        super().__init__(timeout=180)
        self.user_profile = user_profile
        self.current_candidate: Optional[UserProfile] = None
        self.session = None

    async def update_candidate(self, interaction: discord.Interaction):
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
            embed.set_author(name=f"{candidate.discord_id}")
            await interaction.response.edit_message(content="Swipe right or left:", embed=embed, view=self)

    @discord.ui.button(label="Swipe Right", style=discord.ButtonStyle.green)
    async def swipe_right(self, interaction: discord.Interaction, button: Button):
        if self.current_candidate is None:
            await interaction.response.send_message("No candidate available.", ephemeral=True)
            return

        async with AsyncSessionLocal() as session:
            await record_swipe(session, self.user_profile.discord_id, self.current_candidate.discord_id, True)
            if await check_for_reciprocal_swipe(session, self.user_profile.discord_id, self.current_candidate.discord_id):
                candidate_profile = await get_user_profile(session, self.current_candidate.discord_id)
                if candidate_profile:
                    await mark_as_matched(session, self.user_profile, candidate_profile)
                    await interaction.response.edit_message(content=f"It's a match with {self.current_candidate.discord_id}!", embed=None, view=None)
                    self.stop()
                    return
            await self.update_candidate(interaction)

    @discord.ui.button(label="Swipe Left", style=discord.ButtonStyle.red)
    async def swipe_left(self, interaction: discord.Interaction, button: Button):
        if self.current_candidate is None:
            await interaction.response.send_message("No candidate available.", ephemeral=True)
            return

        async with AsyncSessionLocal() as session:
            await record_swipe(session, self.user_profile.discord_id, self.current_candidate.discord_id, False)
            await self.update_candidate(interaction)

# ───────────────────────────────────────────────────────────────────────────────
# UI Components for Profile Setup

class ProfileGenderSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Male", value="Male"),
            discord.SelectOption(label="Female", value="Female"),
            discord.SelectOption(label="Trans", value="Trans"),
            discord.SelectOption(label="Non-Binary", value="Non-Binary")
        ]
        super().__init__(placeholder="Select your gender", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        self.view.selected_gender = self.values[0]
        await interaction.response.defer()

class ProfileAttractedSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Male", value="Male"),
            discord.SelectOption(label="Female", value="Female"),
            discord.SelectOption(label="Trans", value="Trans"),
            discord.SelectOption(label="Non-Binary", value="Non-Binary")
        ]
        super().__init__(placeholder="Select the genders you're attracted to", min_values=1, max_values=len(options), options=options)

    async def callback(self, interaction: discord.Interaction):
        self.view.selected_attracted = self.values
        await interaction.response.defer()

class ProfileSetupView(View):
    def __init__(self, age: int, bio: str, looking_for: str, preferred_min_age: int, preferred_max_age: int, timeout=180):
        super().__init__(timeout=timeout)
        self.age = age
        self.bio = bio
        self.looking_for = looking_for
        self.preferred_min_age = preferred_min_age
        self.preferred_max_age = preferred_max_age
        self.selected_gender: Optional[str] = None
        self.selected_attracted: Optional[List[str]] = None

        self.add_item(ProfileGenderSelect())
        self.add_item(ProfileAttractedSelect())
        self.add_item(Button(label="Confirm", style=discord.ButtonStyle.green, custom_id="confirm_profile"))

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm_button(self, interaction: discord.Interaction, button: Button):
        if not self.selected_gender or not self.selected_attracted:
            await interaction.response.send_message("Please make sure to select both your gender and the genders you're attracted to.", ephemeral=True)
            return

        async with AsyncSessionLocal() as session:
            existing = await get_user_profile(session, str(interaction.user.id))
            if existing:
                await interaction.response.send_message("You already have a profile. Use /update_profile to modify it.", ephemeral=True)
                return

            await create_user_profile(
                session,
                discord_id=str(interaction.user.id),
                age=self.age,
                gender=self.selected_gender,
                bio=self.bio,
                looking_for=self.looking_for,
                attracted_genders=self.selected_attracted,
                preferred_min_age=self.preferred_min_age,
                preferred_max_age=self.preferred_max_age
            )
        await interaction.response.send_message("Profile created successfully!", ephemeral=True)
        self.stop()

# ───────────────────────────────────────────────────────────────────────────────
# Slash Commands (Only / Commands are implemented; no prefix commands)

@bot.tree.command(name="create_profile", description="Create your dating profile.")
@app_commands.describe(
    age="Your age (must be between 18 and 100)",
    bio="A short bio about yourself",
    looking_for="What you're looking for (e.g., dating, friends)",
    preferred_min_age="Preferred minimum age for matches",
    preferred_max_age="Preferred maximum age for matches"
)
async def create_profile(interaction: discord.Interaction, age: int, bio: str, looking_for: str, preferred_min_age: int = 18, preferred_max_age: int = 100):
    if age < 18 or age > 100:
        await interaction.response.send_message("Age must be between 18 and 100.", ephemeral=True)
        return

    view = ProfileSetupView(age=age, bio=bio, looking_for=looking_for, preferred_min_age=preferred_min_age, preferred_max_age=preferred_max_age)
    await interaction.response.send_message("Please select your gender and the genders you're attracted to:", view=view, ephemeral=True)

@bot.tree.command(name="update_profile", description="Update your dating profile.")
@app_commands.describe(
    age="Your age (must be between 18 and 100)",
    bio="A short bio about yourself",
    looking_for="What you're looking for (e.g., dating, friends)",
    preferred_min_age="Preferred minimum age for matches",
    preferred_max_age="Preferred maximum age for matches"
)
async def update_profile(interaction: discord.Interaction, age: int, bio: str, looking_for: str, preferred_min_age: int, preferred_max_age: int):
    if age < 18 or age > 100:
        await interaction.response.send_message("Age must be between 18 and 100.", ephemeral=True)
        return

    async with AsyncSessionLocal() as session:
        profile = await get_user_profile(session, str(interaction.user.id))
        if not profile:
            await interaction.response.send_message("You don't have a profile yet. Use /create_profile first.", ephemeral=True)
            return
        await update_user_profile(session, profile,
                                  age=age,
                                  bio=bio,
                                  looking_for=looking_for,
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
    await view.update_candidate(interaction)

@bot.tree.command(name="unmatch", description="Unmatch from your current match.")
async def unmatch(interaction: discord.Interaction):
    async with AsyncSessionLocal() as session:
        profile = await get_user_profile(session, str(interaction.user.id))
        if not profile or not profile.matched_with:
            await interaction.response.send_message("You are not currently matched with anyone.", ephemeral=True)
            return
        other_profile = await get_user_profile(session, profile.matched_with)
        profile.matched_with = None
        if other_profile:
            other_profile.matched_with = None
        await session.commit()
    await interaction.response.send_message("Match removed. You are now back in the matching pool.", ephemeral=True)

@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
        logger.info("Slash commands synced.")
    except Exception as e:
        logger.error("Error syncing commands: %s", e)
    logger.info("Bot is ready.")

async def main():
    await init_db()
    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot shutdown requested.")
