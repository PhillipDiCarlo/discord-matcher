import os
import logging
import asyncio
from typing import List, Optional
from contextlib import contextmanager
import enum

import discord
from discord import app_commands, TextStyle
from discord.ui import View, Button, Select, Modal, TextInput
from dotenv import load_dotenv

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    ForeignKey,
    func,
    Enum as SQLAlchemyEnum
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import sessionmaker, declarative_base

# ─────────────────────────────────────────────
# Load environment variables and set up logging
load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_DB = os.getenv("POSTGRES_DB")
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
LOGGING_LEVEL = os.getenv("LOGGING_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOGGING_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

if not DISCORD_TOKEN:
    logger.error("DISCORD_TOKEN not found! Please check your .env file.")
    exit(1)

# Construct the database URL (synchronous)
DATABASE_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}/{POSTGRES_DB}"

# ─────────────────────────────────────────────
# Synchronous SQLAlchemy setup
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()

@contextmanager
def session_scope():
    """Provide a transactional scope around a series of operations."""
    session = Session()
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()

# ─────────────────────────────────────────────
# Define a Python Enum for gender values
class GenderEnum(enum.Enum):
    Male = "Male"
    Female = "Female"
    Trans = "Trans"
    NonBinary = "Non-Binary"

allowed_genders = (GenderEnum.Male, GenderEnum.Female, GenderEnum.Trans, GenderEnum.NonBinary)

class UserProfile(Base):
    __tablename__ = 'user_profiles'
    id = Column(Integer, primary_key=True)
    discord_id = Column(String, unique=True, nullable=False)
    age = Column(Integer, nullable=False)
    gender = Column(SQLAlchemyEnum(GenderEnum, name="gender_enum"), nullable=False)
    bio = Column(Text, nullable=False)
    looking_for = Column(String, nullable=False)
    # Define attracted_genders as an ARRAY of our GenderEnum
    attracted_genders = Column(ARRAY(SQLAlchemyEnum(GenderEnum, name="gender_enum")), nullable=False)
    preferred_min_age = Column(Integer, nullable=False, default=18)
    preferred_max_age = Column(Integer, nullable=False, default=100)
    matched_with = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

class Swipe(Base):
    __tablename__ = 'swipes'
    id = Column(Integer, primary_key=True)
    swiper_id = Column(String, ForeignKey("user_profiles.discord_id", ondelete="CASCADE"), nullable=False)
    swiped_id = Column(String, ForeignKey("user_profiles.discord_id", ondelete="CASCADE"), nullable=False)
    right_swipe = Column(Boolean, nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

Base.metadata.create_all(engine)
logger.info("Database tables created.")

# ─────────────────────────────────────────────
# Synchronous Database Helper Functions

def get_user_profile(discord_id: str) -> Optional[UserProfile]:
    with session_scope() as session:
        return session.query(UserProfile).filter_by(discord_id=discord_id).first()

def create_user_profile(discord_id: str, age: int, gender: str, bio: str, looking_for: str,
                        attracted_genders: List[str], preferred_min_age: int, preferred_max_age: int) -> UserProfile:
    gender_enum_val = GenderEnum(gender)
    attracted_enum_vals = [GenderEnum(item) for item in attracted_genders]
    profile = UserProfile(
        discord_id=discord_id,
        age=age,
        gender=gender_enum_val,
        bio=bio,
        looking_for=looking_for,
        attracted_genders=attracted_enum_vals,
        preferred_min_age=preferred_min_age,
        preferred_max_age=preferred_max_age
    )
    with session_scope() as session:
        session.add(profile)
    return profile

def update_user_profile(discord_id: str, **kwargs) -> bool:
    with session_scope() as session:
        profile = session.query(UserProfile).filter_by(discord_id=discord_id).first()
        if not profile:
            return False
        for key, value in kwargs.items():
            setattr(profile, key, value)
        return True

def delete_user_profile(discord_id: str) -> bool:
    with session_scope() as session:
        profile = session.query(UserProfile).filter_by(discord_id=discord_id).first()
        if not profile:
            return False
        session.delete(profile)
        return True

def record_swipe(swiper_id: str, swiped_id: str, right_swipe: bool):
    swipe = Swipe(
        swiper_id=swiper_id,
        swiped_id=swiped_id,
        right_swipe=right_swipe
    )
    with session_scope() as session:
        session.add(swipe)

def has_swiped(swiper_id: str, swiped_id: str) -> bool:
    with session_scope() as session:
        result = session.query(Swipe).filter_by(swiper_id=swiper_id, swiped_id=swiped_id).first()
        return result is not None

def has_right_swiped(swiper_id: str, swiped_id: str) -> bool:
    with session_scope() as session:
        result = session.query(Swipe).filter_by(
            swiper_id=swiper_id,
            swiped_id=swiped_id,
            right_swipe=True
        ).first()
        return result is not None

def mark_as_matched(user1_id: str, user2_id: str):
    with session_scope() as session:
        profile1 = session.query(UserProfile).filter_by(discord_id=user1_id).first()
        profile2 = session.query(UserProfile).filter_by(discord_id=user2_id).first()
        if profile1 and profile2:
            profile1.matched_with = profile2.discord_id
            profile2.matched_with = profile1.discord_id

def get_next_candidate(user: UserProfile) -> Optional[UserProfile]:
    # Copy needed attributes from the detached instance
    user_id = user.discord_id
    min_age = user.preferred_min_age
    max_age = user.preferred_max_age
    looking_for = user.looking_for
    attracted_genders = user.attracted_genders
    user_gender = user.gender

    with session_scope() as session:
        candidates = session.query(UserProfile).filter(
            UserProfile.discord_id != user_id,
            UserProfile.matched_with.is_(None),
            UserProfile.age >= min_age,
            UserProfile.age <= max_age,
            UserProfile.looking_for == looking_for
        ).all()
        for candidate in candidates:
            if candidate.gender not in attracted_genders:
                continue
            if user_gender not in candidate.attracted_genders:
                continue
            if has_swiped(user_id, candidate.discord_id):
                continue
            return candidate
    return None

# ─────────────────────────────────────────────
# Discord Bot Setup (Synchronous DB usage)

intents = discord.Intents.default()
intents.members = True

class MyBot(discord.Client):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
    
    async def setup_hook(self):
        await self.tree.sync()
        logger.info("Slash commands synced.")

bot = MyBot(intents=intents)

# ─────────────────────────────────────────────
# UI Components for Profile Creation and Update

class ProfileInfoModal(Modal, title="Enter Your Profile Information"):
    current_age = TextInput(label="Current Age", placeholder="Enter your current age", required=True)
    bio = TextInput(label="Bio", style=TextStyle.paragraph, placeholder="Write a short bio", required=True)
    min_age = TextInput(label="Minimum Preferred Age", placeholder="Enter minimum preferred age", required=True)
    max_age = TextInput(label="Maximum Preferred Age", placeholder="Enter maximum preferred age", required=True)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            age = int(self.current_age.value)
            min_age_val = int(self.min_age.value)
            max_age_val = int(self.max_age.value)
        except ValueError:
            await interaction.response.send_message("Age fields must be valid numbers.", ephemeral=True)
            return
        if age < 18 or age > 100:
            await interaction.response.send_message("Your age must be between 18 and 100.", ephemeral=True)
            return
        if min_age_val < 18:
            await interaction.response.send_message("Minimum preferred age must be at least 18.", ephemeral=True)
            return
        if max_age_val > 100:
            await interaction.response.send_message("Maximum preferred age must be 100 or less.", ephemeral=True)
            return
        if min_age_val > max_age_val:
            await interaction.response.send_message("Minimum preferred age cannot be greater than maximum preferred age.", ephemeral=True)
            return
        bio_val = self.bio.value
        view = ProfileSelectView(age=age, bio=bio_val, min_age=min_age_val, max_age=max_age_val)
        await interaction.response.send_message("Now please select your additional profile options:", view=view, ephemeral=True)

class LookingForSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Dating", value="Dating"),
            discord.SelectOption(label="Friends", value="Friends"),
            discord.SelectOption(label="Prom Night", value="Prom Night")
        ]
        super().__init__(placeholder="What are you looking for?", min_values=1, max_values=1, options=options)
    async def callback(self, interaction: discord.Interaction):
        self.view.looking_for = self.values[0]
        await interaction.response.defer()

class GenderSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Male", value="Male"),
            discord.SelectOption(label="Female", value="Female"),
            discord.SelectOption(label="Trans", value="Trans"),
            discord.SelectOption(label="Non-Binary", value="Non-Binary")
        ]
        super().__init__(placeholder="Select your gender", min_values=1, max_values=1, options=options)
    async def callback(self, interaction: discord.Interaction):
        self.view.gender = self.values[0]
        await interaction.response.defer()

class AttractedSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Male", value="Male"),
            discord.SelectOption(label="Female", value="Female"),
            discord.SelectOption(label="Trans", value="Trans"),
            discord.SelectOption(label="Non-Binary", value="Non-Binary")
        ]
        super().__init__(placeholder="Select genders you're attracted to", min_values=1, max_values=len(options), options=options)
    async def callback(self, interaction: discord.Interaction):
        self.view.attracted = self.values
        await interaction.response.defer()

class ProfileSelectView(View):
    def __init__(self, age: int, bio: str, min_age: int, max_age: int, timeout=180):
        super().__init__(timeout=timeout)
        self.age = age
        self.bio = bio
        self.min_age = min_age
        self.max_age = max_age
        self.looking_for: Optional[str] = None
        self.gender: Optional[str] = None
        self.attracted: Optional[List[str]] = None
        self.add_item(LookingForSelect())
        self.add_item(GenderSelect())
        self.add_item(AttractedSelect())
    
    @discord.ui.button(label="Confirm Profile", style=discord.ButtonStyle.green)
    async def confirm_profile(self, interaction: discord.Interaction, button: Button):
        if not self.looking_for or not self.gender or not self.attracted:
            await interaction.response.send_message("Please complete all selections before confirming.", ephemeral=True)
            return
        if get_user_profile(str(interaction.user.id)):
            await interaction.response.send_message("You already have a profile. Use /update_profile to modify it.", ephemeral=True)
            return
        create_user_profile(
            discord_id=str(interaction.user.id),
            age=self.age,
            gender=self.gender,
            bio=self.bio,
            looking_for=self.looking_for,
            attracted_genders=self.attracted,
            preferred_min_age=self.min_age,
            preferred_max_age=self.max_age
        )
        await interaction.response.send_message("Profile created successfully!", ephemeral=True)
        self.stop()

class UpdateProfileModal(Modal, title="Update Your Profile Information"):
    current_age = TextInput(label="Current Age", placeholder="Enter your current age", required=True)
    bio = TextInput(label="Bio", style=TextStyle.paragraph, placeholder="Write a short bio", required=True)
    min_age = TextInput(label="Minimum Preferred Age", placeholder="Enter minimum preferred age", required=True)
    max_age = TextInput(label="Maximum Preferred Age", placeholder="Enter maximum preferred age", required=True)
    
    def __init__(self, default_age: int, default_bio: str, default_min_age: int, default_max_age: int,
                 default_looking_for: str, default_gender: str, default_attracted: List[str]):
        super().__init__()
        self.current_age.default = str(default_age)
        self.bio.default = default_bio
        self.min_age.default = str(default_min_age)
        self.max_age.default = str(default_max_age)
        self.default_looking_for = default_looking_for
        self.default_gender = default_gender
        self.default_attracted = default_attracted

    async def on_submit(self, interaction: discord.Interaction):
        try:
            age = int(self.current_age.value)
            min_age_val = int(self.min_age.value)
            max_age_val = int(self.max_age.value)
        except ValueError:
            await interaction.response.send_message("Age fields must be valid numbers.", ephemeral=True)
            return
        if age < 18 or age > 100:
            await interaction.response.send_message("Your age must be between 18 and 100.", ephemeral=True)
            return
        if min_age_val < 18:
            await interaction.response.send_message("Minimum preferred age must be at least 18.", ephemeral=True)
            return
        if max_age_val > 100:
            await interaction.response.send_message("Maximum preferred age must be 100 or less.", ephemeral=True)
            return
        if min_age_val > max_age_val:
            await interaction.response.send_message("Minimum preferred age cannot be greater than maximum preferred age.", ephemeral=True)
            return
        bio_val = self.bio.value
        view = UpdateProfileSelectView(
            age=age,
            bio=bio_val,
            min_age=min_age_val,
            max_age=max_age_val,
            default_looking_for=self.default_looking_for,
            default_gender=self.default_gender,
            default_attracted=self.default_attracted
        )
        await interaction.response.send_message("Now please update your additional profile options:", view=view, ephemeral=True)

class UpdateLookingForSelect(Select):
    def __init__(self, default: str = None):
        options = [
            discord.SelectOption(label="Dating", value="Dating", default=(default=="Dating")),
            discord.SelectOption(label="Friends", value="Friends", default=(default=="Friends")),
            discord.SelectOption(label="Prom Night", value="Prom Night", default=(default=="Prom Night"))
        ]
        super().__init__(placeholder="What are you looking for?", min_values=1, max_values=1, options=options)
    async def callback(self, interaction: discord.Interaction):
        self.view.looking_for = self.values[0]
        await interaction.response.defer()

class UpdateGenderSelect(Select):
    def __init__(self, default: str = None):
        options = [
            discord.SelectOption(label="Male", value="Male", default=(default=="Male")),
            discord.SelectOption(label="Female", value="Female", default=(default=="Female")),
            discord.SelectOption(label="Trans", value="Trans", default=(default=="Trans")),
            discord.SelectOption(label="Non-Binary", value="Non-Binary", default=(default=="Non-Binary"))
        ]
        super().__init__(placeholder="Select your gender", min_values=1, max_values=1, options=options)
    async def callback(self, interaction: discord.Interaction):
        self.view.gender = self.values[0]
        await interaction.response.defer()

class UpdateAttractedSelect(Select):
    def __init__(self, default: List[str] = None):
        default = default or []
        options = [
            discord.SelectOption(label="Male", value="Male", default=("Male" in default)),
            discord.SelectOption(label="Female", value="Female", default=("Female" in default)),
            discord.SelectOption(label="Trans", value="Trans", default=("Trans" in default)),
            discord.SelectOption(label="Non-Binary", value="Non-Binary", default=("Non-Binary" in default))
        ]
        super().__init__(placeholder="Select genders you're attracted to", min_values=1, max_values=4, options=options)
    async def callback(self, interaction: discord.Interaction):
        self.view.attracted = self.values
        await interaction.response.defer()

class UpdateProfileSelectView(View):
    def __init__(self, age: int, bio: str, min_age: int, max_age: int,
                 default_looking_for: str, default_gender: str, default_attracted: List[str], timeout=180):
        super().__init__(timeout=timeout)
        self.age = age
        self.bio = bio
        self.min_age = min_age
        self.max_age = max_age
        self.looking_for: Optional[str] = default_looking_for
        self.gender: Optional[str] = default_gender
        self.attracted: Optional[List[str]] = default_attracted
        self.add_item(UpdateLookingForSelect(default_looking_for))
        self.add_item(UpdateGenderSelect(default_gender))
        self.add_item(UpdateAttractedSelect(default_attracted))
    
    @discord.ui.button(label="Confirm Profile Update", style=discord.ButtonStyle.green)
    async def confirm_profile(self, interaction: discord.Interaction, button: Button):
        if not self.looking_for or not self.gender or not self.attracted:
            await interaction.response.send_message("Please complete all selections before confirming.", ephemeral=True)
            return
        updated = update_user_profile(
            str(interaction.user.id),
            age=self.age,
            bio=self.bio,
            looking_for=self.looking_for,
            preferred_min_age=self.min_age,
            preferred_max_age=self.max_age,
            gender=self.gender,
            attracted_genders=self.attracted
        )
        if not updated:
            await interaction.response.send_message("Failed to update profile.", ephemeral=True)
        else:
            await interaction.response.send_message("Profile updated successfully!", ephemeral=True)
        self.stop()

# ─────────────────────────────────────────────
# A simple view for DM messages with a button to view profile.
class ProfileButtonView(View):
    def __init__(self, user_id: str):
        super().__init__(timeout=None)
        self.add_item(Button(label="View Profile", url=f"https://discord.com/users/{user_id}"))

# ─────────────────────────────────────────────
# Standard Matching View

class MatchView(View):
    def __init__(self, user_id: str):
        super().__init__(timeout=180)
        self.user_id = user_id  
        self.current_candidate: Optional[UserProfile] = None

    async def update_candidate(self, interaction: discord.Interaction):
        user = get_user_profile(self.user_id)
        if not user:
            try:
                await interaction.edit_original_response(content="User profile not found.", embed=None, view=None)
            except discord.NotFound:
                try:
                    await interaction.followup.send("User profile not found.", ephemeral=True)
                except discord.NotFound:
                    logger.error("Failed to send followup message: Unknown Webhook (User profile not found)")
            self.stop()
            return

        candidate = get_next_candidate(user)
        if candidate is None:
            try:
                await interaction.edit_original_response(
                    content="You've run out of swipes for now. Check back tomorrow!",
                    embed=None,
                    view=None
                )
            except discord.NotFound:
                try:
                    await interaction.followup.send("You've run out of swipes for now. Check back tomorrow!", ephemeral=True)
                except discord.NotFound:
                    logger.error("Failed to send followup message: Unknown Webhook (No candidates)")
            self.stop()
            return

        self.current_candidate = candidate

        # Fetch the candidate's Discord User object
        candidate_user = interaction.client.get_user(candidate.discord_id)
        if candidate_user is None:
            candidate_user = await interaction.client.fetch_user(candidate.discord_id)

        embed = discord.Embed(title="Potential Match", color=discord.Color.blue())
        embed.add_field(name="Age", value=str(candidate.age))
        embed.add_field(name="Gender", value=candidate.gender.value)
        embed.add_field(name="Looking for", value=candidate.looking_for)
        embed.add_field(name="Bio", value=candidate.bio, inline=False)
        embed.set_author(
            name=candidate_user.display_name,
            icon_url=candidate_user.avatar.url if candidate_user.avatar else candidate_user.default_avatar.url,
            url=f"https://discord.com/users/{candidate.discord_id}"
        )
        embed.set_thumbnail(url=candidate_user.avatar.url if candidate_user.avatar else candidate_user.default_avatar.url)

        try:
            await interaction.edit_original_response(content="Swipe right or left:", embed=embed, view=self)
        except discord.NotFound:
            try:
                await interaction.followup.send(content="Swipe right or left:", embed=embed, view=self, ephemeral=True)
            except discord.NotFound:
                logger.error("Failed to send followup message: Unknown Webhook (Editing response)")

    @discord.ui.button(label="Swipe Left", style=discord.ButtonStyle.red)
    async def swipe_left(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        if not self.current_candidate:
            await interaction.followup.send("No candidate available.", ephemeral=True)
            return
        record_swipe(self.user_id, self.current_candidate.discord_id, False)
        await self.update_candidate(interaction)

    @discord.ui.button(label="Swipe Right", style=discord.ButtonStyle.green)
    async def swipe_right(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        if not self.current_candidate:
            await interaction.followup.send("No candidate available.", ephemeral=True)
            return
        record_swipe(self.user_id, self.current_candidate.discord_id, True)
        # Check if candidate swiped right on you.
        if has_right_swiped(self.current_candidate.discord_id, self.user_id):
            mark_as_matched(self.user_id, self.current_candidate.discord_id)
            match_message = f"It's a match with <@{self.current_candidate.discord_id}>!"
            try:
                await interaction.edit_original_response(content=match_message, embed=None, view=None)
            except discord.NotFound:
                try:
                    await interaction.followup.send(match_message, ephemeral=True)
                except discord.NotFound:
                    logger.error("Failed to send followup message: Unknown Webhook (Swipe Right match)")
            self.stop()

            # Send DM to both users with a clickable button.
            try:
                user_dm = await interaction.client.fetch_user(self.user_id)
                candidate_dm = await interaction.client.fetch_user(self.current_candidate.discord_id)
                server_name = interaction.guild.name if interaction.guild else "this server"
                await user_dm.send(
                    content=f"You matched with <@{self.current_candidate.discord_id}> in {server_name}!",
                    view=ProfileButtonView(self.current_candidate.discord_id)
                )
                # await candidate_dm.send(
                #     content=f"You matched with <@{self.user_id}> in {server_name}!",
                #     view=ProfileButtonView(self.user_id)
                # )
            except Exception as e:
                logger.error(f"Failed to send DM on match: {e}")
            return
        await self.update_candidate(interaction)

# ─────────────────────────────────────────────
# Slash Commands

@bot.tree.command(name="create_profile", description="Create your dating profile.")
async def create_profile(interaction: discord.Interaction):
    await interaction.response.send_modal(ProfileInfoModal())

@bot.tree.command(name="update_profile", description="Update your dating profile.")
async def update_profile(interaction: discord.Interaction):
    profile = get_user_profile(str(interaction.user.id))
    if not profile:
        await interaction.response.send_message("You don't have a profile yet. Use /create_profile first.", ephemeral=True)
        return
    with session_scope() as session:
        profile = session.query(UserProfile).filter_by(discord_id=str(interaction.user.id)).first()
        default_age = profile.age
        default_bio = profile.bio
        default_min_age = profile.preferred_min_age
        default_max_age = profile.preferred_max_age
        default_looking_for = profile.looking_for
        default_gender = profile.gender.value
        default_attracted = [x.value for x in profile.attracted_genders]
    modal = UpdateProfileModal(
        default_age=default_age,
        default_bio=default_bio,
        default_min_age=default_min_age,
        default_max_age=default_max_age,
        default_looking_for=default_looking_for,
        default_gender=default_gender,
        default_attracted=default_attracted
    )
    await interaction.response.send_modal(modal)

@bot.tree.command(name="delete_profile", description="Delete your dating profile.")
async def delete_profile(interaction: discord.Interaction):
    deleted = delete_user_profile(str(interaction.user.id))
    if not deleted:
        await interaction.response.send_message("No profile found to delete.", ephemeral=True)
    else:
        await interaction.response.send_message("Profile deleted successfully.", ephemeral=True)

@bot.tree.command(name="start_matching", description="Start swiping for matches.")
async def start_matching(interaction: discord.Interaction):
    with session_scope() as session:
        user_instance = session.query(UserProfile).filter_by(discord_id=str(interaction.user.id)).first()
        if not user_instance:
            await interaction.response.send_message(
                "You must create a profile first using /create_profile.",
                ephemeral=True
            )
            return
        if user_instance.matched_with:
            await interaction.response.send_message(
                "You are already matched. Unmatch first to start swiping.",
                ephemeral=True
            )
            return

        candidate = get_next_candidate(user_instance)
        if candidate is None:
            await interaction.response.send_message(
                "No new candidates found right now. Check back later!",
                ephemeral=True
            )
            return

        user_id = user_instance.discord_id

    view = MatchView(user_id)
    await interaction.response.send_message("Fetching a potential match...", view=view, ephemeral=True)
    await view.update_candidate(interaction)

@bot.tree.command(name="unmatch", description="Unmatch from your current match.")
async def unmatch(interaction: discord.Interaction):
    with session_scope() as session:
        user = session.query(UserProfile).filter_by(discord_id=str(interaction.user.id)).first()
        if not user or not user.matched_with:
            await interaction.response.send_message("You are not currently matched with anyone.", ephemeral=True)
            return
        # Query for the partner user
        partner = session.query(UserProfile).filter_by(discord_id=user.matched_with).first()
        # Clear matches for both users
        user.matched_with = None
        if partner:
            partner.matched_with = None
    await interaction.response.send_message("Match removed. You are now back in the matching pool.", ephemeral=True)

@bot.event
async def on_ready():
    logger.info(f"Bot is ready! Logged in as {bot.user} (ID: {bot.user.id})")
    logger.info(f"Connected to {len(bot.guilds)} guild(s): {[guild.name for guild in bot.guilds]}")

def main():
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    main()
