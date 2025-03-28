import os
import logging
import asyncio
import json
from typing import List, Optional
from contextlib import contextmanager
import enum
import pika

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
    Enum as SQLAlchemyEnum,
    UniqueConstraint,
    Float
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
# Define a Python Enum for gender values.
# We now store the gender as a string without the hyphen ("NonBinary").
class GenderEnum(enum.Enum):
    Male = "Male"
    Female = "Female"
    Trans = "Trans"
    NonBinary = "NonBinary"  # Stored without hyphen in DB

allowed_genders = (GenderEnum.Male, GenderEnum.Female, GenderEnum.Trans, GenderEnum.NonBinary)

# Helper function to convert raw string to GenderEnum.
# It accepts both "NonBinary" and "Non-Binary" as input.
def to_gender_enum(value: str) -> GenderEnum:
    if value.lower() in ("nonbinary", "non-binary"):
        return GenderEnum.NonBinary
    return GenderEnum(value)

# ─────────────────────────────────────────────
# Update the models to be guild-specific.
class UserProfile(Base):
    __tablename__ = 'user_profiles'
    id = Column(Integer, primary_key=True)
    discord_id = Column(String, nullable=False)
    guild_id = Column(String, nullable=False)
    __table_args__ = (UniqueConstraint('discord_id', 'guild_id', name='uix_discord_guild'),)
    age = Column(Integer, nullable=False)
    gender = Column(SQLAlchemyEnum(GenderEnum, name="gender_enum"), nullable=False)
    bio = Column(Text, nullable=False)
    looking_for = Column(String, nullable=False)
    attracted_genders = Column(ARRAY(SQLAlchemyEnum(GenderEnum, name="gender_enum")), nullable=False)
    preferred_min_age = Column(Integer, nullable=False, default=18)
    preferred_max_age = Column(Integer, nullable=False, default=100)
    matched_with = Column(String, nullable=True)  # This stores the discord_id of the matched profile.
    # New fields for actual location and matching preference:
    country = Column(String, nullable=True)
    state = Column(String, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    location_preference = Column(String, nullable=False, default="Anywhere")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

class Swipe(Base):
    __tablename__ = 'swipes'
    id = Column(Integer, primary_key=True)
    guild_id = Column(String, nullable=False)  # Scope swipes to a guild.
    swiper_id = Column(String, nullable=False)  # Reference to the user's discord_id.
    swiped_id = Column(String, nullable=False)
    right_swipe = Column(Boolean, nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

Base.metadata.create_all(engine)
logger.info("Database tables created.")

# ─────────────────────────────────────────────
# Synchronous Database Helper Functions
def get_user_profile(discord_id: str, guild_id: str) -> Optional[UserProfile]:
    with session_scope() as session:
        return session.query(UserProfile).filter_by(discord_id=discord_id, guild_id=guild_id).first()

def create_user_profile(discord_id: str, guild_id: str, age: int, gender: str, bio: str, looking_for: str,
                        attracted_genders: List[str], preferred_min_age: int, preferred_max_age: int) -> UserProfile:
    gender_enum_val = to_gender_enum(gender)
    attracted_enum_vals = [to_gender_enum(item) for item in attracted_genders]
    profile = UserProfile(
        discord_id=discord_id,
        guild_id=guild_id,
        age=age,
        gender=gender_enum_val,
        bio=bio,
        looking_for=looking_for,
        attracted_genders=attracted_enum_vals,
        preferred_min_age=preferred_min_age,
        preferred_max_age=preferred_max_age,
        location_preference="Anywhere"
    )
    with session_scope() as session:
        session.add(profile)
    return profile

def update_user_profile(discord_id: str, guild_id: str, **kwargs) -> bool:
    with session_scope() as session:
        profile = session.query(UserProfile).filter_by(discord_id=discord_id, guild_id=guild_id).first()
        if not profile:
            return False
        for key, value in kwargs.items():
            if key == "gender" and value:
                setattr(profile, key, to_gender_enum(value))
            elif key == "attracted_genders" and value:
                setattr(profile, key, [to_gender_enum(item) for item in value])
            else:
                setattr(profile, key, value)
        return True

def delete_user_profile(discord_id: str, guild_id: str) -> bool:
    with session_scope() as session:
        profile = session.query(UserProfile).filter_by(discord_id=discord_id, guild_id=guild_id).first()
        if not profile:
            return False
        session.delete(profile)
        return True

def record_swipe(swiper_id: str, swiped_id: str, guild_id: str, right_swipe: bool):
    swipe = Swipe(
        guild_id=guild_id,
        swiper_id=swiper_id,
        swiped_id=swiped_id,
        right_swipe=right_swipe
    )
    with session_scope() as session:
        session.add(swipe)

def has_swiped(swiper_id: str, swiped_id: str, guild_id: str) -> bool:
    with session_scope() as session:
        result = session.query(Swipe).filter_by(swiper_id=swiper_id, swiped_id=swiped_id, guild_id=guild_id).first()
        return result is not None

def has_right_swiped(swiper_id: str, swiped_id: str, guild_id: str) -> bool:
    with session_scope() as session:
        result = session.query(Swipe).filter_by(
            swiper_id=swiper_id,
            swiped_id=swiped_id,
            guild_id=guild_id,
            right_swipe=True
        ).first()
        return result is not None

def mark_as_matched(user1_id: str, user2_id: str, guild_id: str):
    with session_scope() as session:
        profile1 = session.query(UserProfile).filter_by(discord_id=user1_id, guild_id=guild_id).first()
        profile2 = session.query(UserProfile).filter_by(discord_id=user2_id, guild_id=guild_id).first()
        if profile1 and profile2:
            profile1.matched_with = profile2.discord_id
            profile2.matched_with = profile1.discord_id

def get_next_candidate(user: UserProfile) -> Optional[UserProfile]:
    user_id = user.discord_id
    guild_id = user.guild_id
    min_age = user.preferred_min_age
    max_age = user.preferred_max_age
    looking_for = user.looking_for
    attracted_genders = user.attracted_genders
    user_gender = user.gender

    with session_scope() as session:
        candidates = session.query(UserProfile).filter(
            UserProfile.discord_id != user_id,
            UserProfile.guild_id == guild_id,
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
            if has_swiped(user_id, candidate.discord_id, guild_id):
                continue
            return candidate
    return None

# ─────────────────────────────────────────────
# RabbitMQ Publisher for Location Updates
def send_location_update(discord_id: str, guild_id: str, raw_country: str, raw_state: str):
    rabbitmq_host = os.getenv("RABBITMQ_HOST", "localhost")
    rabbitmq_port = int(os.getenv("RABBITMQ_PORT", 5672))
    rabbitmq_username = os.getenv("RABBITMQ_USERNAME", "guest")
    rabbitmq_password = os.getenv("RABBITMQ_PASSWORD", "guest")
    rabbitmq_vhost = os.getenv("RABBITMQ_VHOST", "/")
    queue_name = os.getenv("RABBITMQ_QUEUE_NAME", "location_updates")
    
    credentials = pika.PlainCredentials(rabbitmq_username, rabbitmq_password)
    connection_params = pika.ConnectionParameters(
        host=rabbitmq_host,
        port=rabbitmq_port,
        virtual_host=rabbitmq_vhost,
        credentials=credentials
    )
    
    try:
        connection = pika.BlockingConnection(connection_params)
        channel = connection.channel()
        channel.queue_declare(queue=queue_name, durable=True)
        message = {
            "discord_id": discord_id,
            "guild_id": guild_id,
            "raw_country": raw_country,
            "raw_state": raw_state
        }
        channel.basic_publish(
            exchange="",
            routing_key=queue_name,
            body=json.dumps(message),
            properties=pika.BasicProperties(delivery_mode=2)  # persistent message
        )
        connection.close()
        logger.info(f"Published location update for DiscordID: {discord_id}")
    except Exception as e:
        logger.error(f"Failed to publish location update: {e}")

# ─────────────────────────────────────────────
# UI Components for Profile Creation and Update

# Note: To reduce the number of modal inputs to 5, we combine min and max age into one input.
class ProfileInfoModal(Modal, title="Enter Your Profile Information"):
    current_age = TextInput(label="Current Age", placeholder="Enter your current age", required=True)
    bio = TextInput(label="Bio", style=TextStyle.paragraph, placeholder="Write a short bio", required=True)
    preferred_age_range = TextInput(label="Preferred Age Range", placeholder="Enter your preferred age range (e.g., 18-30)", required=True)
    country = TextInput(label="Country", placeholder="Enter your country", required=True)
    state = TextInput(label="State/Province", placeholder="Enter your state/province (optional)", required=False)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            age = int(self.current_age.value)
            # Parse preferred age range
            try:
                min_age_val, max_age_val = map(lambda x: int(x.strip()), self.preferred_age_range.value.split("-"))
            except Exception:
                await interaction.response.send_message("Preferred age range must be in format 'min-max'.", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("Current age must be a valid number.", ephemeral=True)
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
        raw_country = self.country.value
        raw_state = self.state.value

        guild_id = str(interaction.guild.id) if interaction.guild else None
        
        if get_user_profile(str(interaction.user.id), guild_id):
            await interaction.response.send_message("You already have a profile. Use /update_profile to modify it.", ephemeral=True)
            return

        # Create profile with placeholder values for gender, looking_for, attracted_genders.
        create_user_profile(
            discord_id=str(interaction.user.id),
            guild_id=guild_id,
            age=age,
            gender="Male",         # Placeholder; to be updated via follow-up view.
            bio=bio_val,
            looking_for="Dating",    # Placeholder; to be updated via follow-up view.
            attracted_genders=["Female"],  # Placeholder; to be updated via follow-up view.
            preferred_min_age=min_age_val,
            preferred_max_age=max_age_val
        )
        # Publish location update so the location service can update the profile.
        send_location_update(str(interaction.user.id), guild_id, raw_country, raw_state)
        await interaction.response.send_message("Profile created successfully!", ephemeral=True)
        # Send follow-up view to let user select their gender and attraction preferences.
        await interaction.followup.send(
            "Please complete your profile by selecting your gender and the genders you're attracted to.",
            view=UpdateProfileSelectView(age, bio_val, min_age_val, max_age_val, default_looking_for="Dating", default_gender="Male", default_attracted=["Female"]),
            ephemeral=True
        )

class UpdateProfileModal(Modal, title="Update Your Profile Information"):
    current_age = TextInput(label="Current Age", placeholder="Enter your current age", required=True)
    bio = TextInput(label="Bio", style=TextStyle.paragraph, placeholder="Write a short bio", required=True)
    preferred_age_range = TextInput(label="Preferred Age Range", placeholder="Enter your preferred age range (e.g., 18-30)", required=True)
    country = TextInput(label="Country", placeholder="Enter your country", required=True)
    state = TextInput(label="State/Province", placeholder="Enter your state/province (optional)", required=False)
    
    def __init__(self, default_age: int, default_bio: str, default_min_age: int, default_max_age: int,
                 default_looking_for: str, default_gender: str, default_attracted: List[str],
                 default_country: str = "", default_state: str = ""):
        super().__init__()
        self.current_age.default = str(default_age)
        self.bio.default = default_bio
        self.preferred_age_range.default = f"{default_min_age}-{default_max_age}"
        self.country.default = default_country
        self.state.default = default_state
        self.default_looking_for = default_looking_for
        self.default_gender = default_gender
        self.default_attracted = default_attracted

    async def on_submit(self, interaction: discord.Interaction):
        try:
            age = int(self.current_age.value)
            try:
                min_age_val, max_age_val = map(lambda x: int(x.strip()), self.preferred_age_range.value.split("-"))
            except Exception:
                await interaction.response.send_message("Preferred age range must be in format 'min-max'.", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("Current age must be a valid number.", ephemeral=True)
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
        raw_country = self.country.value
        raw_state = self.state.value
        
        guild_id = str(interaction.guild.id) if interaction.guild else None
        
        # Update the user's basic profile info.
        updated = update_user_profile(
            str(interaction.user.id),
            guild_id=guild_id,
            age=age,
            bio=bio_val
            # Other fields (like looking_for, gender, attracted_genders) will be updated via follow-up view.
        )
        # Publish location update.
        send_location_update(str(interaction.user.id), guild_id, raw_country, raw_state)
        if updated:
            await interaction.response.send_message("Profile updated successfully!", ephemeral=True)
            # Show follow-up view to update gender and attraction preferences.
            await interaction.followup.send(
                "Please update your gender and attraction preferences.",
                view=UpdateProfileSelectView(age, bio_val, min_age_val, max_age_val, default_looking_for=self.default_looking_for, default_gender=self.default_gender, default_attracted=self.default_attracted),
                ephemeral=True
            )
        else:
            await interaction.response.send_message("Failed to update profile.", ephemeral=True)

# ─────────────────────────────────────────────
# New Consolidated Settings UI for future settings updates.
class LocationPreferenceSelect(Select):
    def __init__(self, default="Anywhere"):
        options = [
            discord.SelectOption(label="State/Province", value="State/Province", description="Only match within your state/province"),
            discord.SelectOption(label="Nearby", value="Nearby", description="Match with users no more than 500 miles away"),
            discord.SelectOption(label="Same Country", value="Same Country", description="Only match with users from the same country"),
            discord.SelectOption(label="Same Continent", value="Same Continent", description="Only match with users from the same continent"),
            discord.SelectOption(label="Anywhere", value="Anywhere", description="No location restrictions", default=True)
        ]
        # Do not update the database immediately.
        super().__init__(placeholder="Select your location preference", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        # Store the selection in the view for now.
        self.view.selected_preference = self.values[0]
        await interaction.response.defer()

class ConfirmSettingsButton(Button):
    def __init__(self):
        super().__init__(label="Confirm Settings", style=discord.ButtonStyle.green)

    async def callback(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id) if interaction.guild else None
        preference = self.view.selected_preference
        updated = update_user_profile(str(interaction.user.id), guild_id, location_preference=preference)
        if updated:
            await interaction.response.send_message(f"Settings updated! Location preference set to {preference}.", ephemeral=True)
        else:
            await interaction.response.send_message("Failed to update settings.", ephemeral=True)

class ConsolidatedSettingsView(View):
    def __init__(self, timeout=180):
        super().__init__(timeout=timeout)
        self.selected_preference = "Anywhere"
        self.add_item(LocationPreferenceSelect())
        self.add_item(ConfirmSettingsButton())

# ─────────────────────────────────────────────
# Standard Matching and Profile Viewing UI Components
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

# This view is used in the follow-up step after profile creation or update.
class UpdateProfileSelectView(View):
    def __init__(self, age: int, bio: str, preferred_min_age: int, preferred_max_age: int,
                 default_looking_for: str, default_gender: str, default_attracted: List[str], timeout=180):
        super().__init__(timeout=timeout)
        self.age = age
        self.bio = bio
        self.preferred_min_age = preferred_min_age
        self.preferred_max_age = preferred_max_age
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
        guild_id = str(interaction.guild.id) if interaction.guild else None
        updated = update_user_profile(
            str(interaction.user.id),
            guild_id=guild_id,
            age=self.age,
            bio=self.bio,
            looking_for=self.looking_for,
            preferred_min_age=self.preferred_min_age,
            preferred_max_age=self.preferred_max_age,
            gender=self.gender,
            attracted_genders=self.attracted
        )
        if not updated:
            await interaction.response.send_message("Failed to update profile.", ephemeral=True)
        else:
            await interaction.response.send_message("Profile updated successfully!", ephemeral=True)
        self.stop()

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
        # Normalize the default value: convert "NonBinary" from the database to "Non-Binary" for display.
        normalized_default = "Non-Binary" if default == "NonBinary" else default
        options = [
            discord.SelectOption(label="Male", value="Male", default=(normalized_default == "Male")),
            discord.SelectOption(label="Female", value="Female", default=(normalized_default == "Female")),
            discord.SelectOption(label="Trans", value="Trans", default=(normalized_default == "Trans")),
            discord.SelectOption(label="Non-Binary", value="Non-Binary", default=(normalized_default == "Non-Binary"))
        ]
        super().__init__(placeholder="Select your gender", min_values=1, max_values=1, options=options)
    
    async def callback(self, interaction: discord.Interaction):
        self.view.gender = self.values[0]
        await interaction.response.defer()

class UpdateAttractedSelect(Select):
    def __init__(self, default: List[str] = None):
        default = default or []
        normalized_default = ["Non-Binary" if g == "NonBinary" else g for g in default]
        options = [
            discord.SelectOption(label="Male", value="Male", default=("Male" in normalized_default)),
            discord.SelectOption(label="Female", value="Female", default=("Female" in normalized_default)),
            discord.SelectOption(label="Trans", value="Trans", default=("Trans" in normalized_default)),
            discord.SelectOption(label="Non-Binary", value="Non-Binary", default=("Non-Binary" in normalized_default))
        ]
        super().__init__(placeholder="Select genders you're attracted to", min_values=1, max_values=4, options=options)
    
    async def callback(self, interaction: discord.Interaction):
        self.view.attracted = self.values
        await interaction.response.defer()

# ─────────────────────────────────────────────
# A simple view for DM messages with a button to view profile.
class ProfileButtonView(View):
    def __init__(self, user_id: str):
        super().__init__(timeout=None)
        self.add_item(Button(label="View Profile", url=f"https://discord.com/users/{user_id}"))

# ─────────────────────────────────────────────
# Standard Matching View
class MatchView(View):
    def __init__(self, user_id: str, guild_id: str):
        super().__init__(timeout=180)
        self.user_id = user_id  
        self.guild_id = guild_id
        self.current_candidate: Optional[UserProfile] = None

    async def update_candidate(self, interaction: discord.Interaction):
        user = get_user_profile(self.user_id, self.guild_id)
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

        candidate_user = interaction.client.get_user(candidate.discord_id)
        if candidate_user is None:
            candidate_user = await interaction.client.fetch_user(candidate.discord_id)

        display_gender = "Non-Binary" if candidate.gender.value == "NonBinary" else candidate.gender.value
        country = candidate.country if candidate.country else "N/A"
        state = candidate.state if candidate.state else "N/A"

        embed = discord.Embed(title="Potential Match", color=discord.Color.blue())
        embed.add_field(name="Age", value=str(candidate.age))
        embed.add_field(name="Gender", value=display_gender)
        embed.add_field(name="Country", value=country, inline=True)
        embed.add_field(name="State/Province", value=state, inline=True)
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
        record_swipe(self.user_id, self.current_candidate.discord_id, self.guild_id, False)
        await self.update_candidate(interaction)

    @discord.ui.button(label="Swipe Right", style=discord.ButtonStyle.green)
    async def swipe_right(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        if not self.current_candidate:
            await interaction.followup.send("No candidate available.", ephemeral=True)
            return
        record_swipe(self.user_id, self.current_candidate.discord_id, self.guild_id, True)
        if has_right_swiped(self.current_candidate.discord_id, self.user_id, self.guild_id):
            mark_as_matched(self.user_id, self.current_candidate.discord_id, self.guild_id)
            match_message = f"It's a match with <@{self.current_candidate.discord_id}>!"
            try:
                await interaction.edit_original_response(content=match_message, embed=None, view=None)
            except discord.NotFound:
                try:
                    await interaction.followup.send(match_message, ephemeral=True)
                except discord.NotFound:
                    logger.error("Failed to send followup message: Unknown Webhook (Swipe Right match)")
            self.stop()
            try:
                user_dm = await interaction.client.fetch_user(self.user_id)
                candidate_dm = await interaction.client.fetch_user(self.current_candidate.discord_id)
                server_name = interaction.guild.name if interaction.guild else "this server"
                await user_dm.send(
                    content=f"You matched with <@{self.current_candidate.discord_id}> in {server_name}!",
                    view=ProfileButtonView(self.current_candidate.discord_id)
                )
                await candidate_dm.send(
                    content=f"You matched with <@{self.user_id}> in {server_name}!",
                    view=ProfileButtonView(self.user_id)
                )
            except Exception as e:
                logger.error(f"Failed to send DM on match: {e}")
            return
        await self.update_candidate(interaction)

# ─────────────────────────────────────────────
# Discord Bot Setup
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
# Slash Commands
@bot.tree.command(name="create_profile", description="Create your dating profile.")
async def create_profile(interaction: discord.Interaction):
    await interaction.response.send_modal(ProfileInfoModal())

@bot.tree.command(name="update_profile", description="Update your dating profile.")
async def update_profile(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id) if interaction.guild else None
    profile = get_user_profile(str(interaction.user.id), guild_id)
    if not profile:
        await interaction.response.send_message("You don't have a profile yet. Use /create_profile first.", ephemeral=True)
        return
    with session_scope() as session:
        profile = session.query(UserProfile).filter_by(discord_id=str(interaction.user.id), guild_id=guild_id).first()
        default_age = profile.age
        default_bio = profile.bio
        default_min_age = profile.preferred_min_age
        default_max_age = profile.preferred_max_age
        default_looking_for = profile.looking_for
        default_gender = profile.gender.value
        default_attracted = [x.value for x in profile.attracted_genders]
        default_country = profile.country if profile.country else ""
        default_state = profile.state if profile.state else ""
    modal = UpdateProfileModal(
        default_age=default_age,
        default_bio=default_bio,
        default_min_age=default_min_age,
        default_max_age=default_max_age,
        default_looking_for=default_looking_for,
        default_gender=default_gender,
        default_attracted=default_attracted,
        default_country=default_country,
        default_state=default_state
    )
    await interaction.response.send_modal(modal)

@bot.tree.command(name="delete_profile", description="Delete your dating profile.")
async def delete_profile(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id) if interaction.guild else None
    deleted = delete_user_profile(str(interaction.user.id), guild_id)
    if not deleted:
        await interaction.response.send_message("No profile found to delete.", ephemeral=True)
    else:
        await interaction.response.send_message("Profile deleted successfully.", ephemeral=True)

@bot.tree.command(name="start_matching", description="Start swiping for matches.")
async def start_matching(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id) if interaction.guild else None
    with session_scope() as session:
        user_instance = session.query(UserProfile).filter_by(discord_id=str(interaction.user.id), guild_id=guild_id).first()
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

    view = MatchView(user_id, guild_id)
    await interaction.response.send_message("Fetching a potential match...", view=view, ephemeral=True)
    await view.update_candidate(interaction)

@bot.tree.command(name="unmatch", description="Unmatch from your current match.")
async def unmatch(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id) if interaction.guild else None
    with session_scope() as session:
        user = session.query(UserProfile).filter_by(discord_id=str(interaction.user.id), guild_id=guild_id).first()
        if not user or not user.matched_with:
            await interaction.response.send_message("You are not currently matched with anyone.", ephemeral=True)
            return
        partner = session.query(UserProfile).filter_by(discord_id=user.matched_with, guild_id=guild_id).first()
        user.matched_with = None
        if partner:
            partner.matched_with = None
    await interaction.response.send_message("Match removed. Both users are now back in the matching pool.", ephemeral=True)

@bot.tree.command(name="settings", description="Update your personal settings, including location preferences.")
async def settings(interaction: discord.Interaction):
    await interaction.response.send_message("Select your settings:", view=ConsolidatedSettingsView(), ephemeral=True)

@bot.event
async def on_ready():
    logger.info(f"Bot is ready! Logged in as {bot.user} (ID: {bot.user.id})")
    logger.info(f"Connected to {len(bot.guilds)} guild(s): {[guild.name for guild in bot.guilds]}")

def main():
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    main()
