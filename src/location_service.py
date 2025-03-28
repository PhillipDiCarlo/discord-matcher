#!/usr/bin/env python3
import os
import json
import logging
import time

import pika
import pycountry
from rapidfuzz import process, fuzz
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

from sqlalchemy import create_engine, Column, String, Float, Integer
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import SQLAlchemyError

# ---------------- Logging Setup ----------------
LOGGING_LEVEL = os.getenv("LOGGING_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOGGING_LEVEL),
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------------- Database Setup ----------------
# Load database connection details from environment variables
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_DB = os.getenv("POSTGRES_DB", "your_db")
POSTGRES_USER = os.getenv("POSTGRES_USER", "your_user")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "your_password")

DATABASE_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}/{POSTGRES_DB}"
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()

# Minimal model reflecting our user_profiles table with location fields.
class UserProfile(Base):
    __tablename__ = 'user_profiles'
    id = Column(Integer, primary_key=True)
    discord_id = Column(String, nullable=False)
    guild_id = Column(String, nullable=False)
    # ... other existing fields ...
    country = Column(String, nullable=True)
    state = Column(String, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

# ---------------- Geocoding and Normalization ----------------
# Set up the geolocator using Nominatim (OpenStreetMap)
geolocator = Nominatim(user_agent="location_service")

def normalize_country(raw_country):
    """
    Attempt to match the raw country string to a standardized country.
    If the input is 2-3 characters long, try a direct lookup.
    Otherwise, use fuzzy matching.
    Returns a pycountry Country object if found, otherwise None.
    """
    if not raw_country:
        return None
    # Direct lookup if input length is 2 or 3 (e.g. "US" or "USA")
    if len(raw_country) in (2, 3):
        try:
            country = pycountry.countries.lookup(raw_country)
            return country
        except LookupError:
            pass
    # Otherwise, use fuzzy matching
    countries = {country.name: country for country in pycountry.countries}
    choices = list(countries.keys())
    match, score, _ = process.extractOne(raw_country, choices, scorer=fuzz.WRatio)
    if score >= 80:
        return countries[match]
    else:
        return None

def normalize_subdivision(raw_state, country_code):
    """
    Attempt to match the raw state/province to a standardized subdivision
    for the given country. First, if the input is short, check if it matches
    the subdivision code (the last part of codes like 'US-NJ'). Otherwise, use fuzzy matching.
    Returns a pycountry subdivision object if found, otherwise None.
    """
    if not raw_state or not country_code:
        return None
    subdivisions = list(pycountry.subdivisions.get(country_code=country_code))
    if not subdivisions:
        return None
    raw_state_upper = raw_state.upper()
    # Check for exact match on the code suffix (e.g. "NJ" in "US-NJ")
    for subdiv in subdivisions:
        if subdiv.code.split("-")[-1] == raw_state_upper:
            return subdiv
    # Fallback to fuzzy matching by subdivision name
    choices = {subdiv.name: subdiv for subdiv in subdivisions}
    match, score, _ = process.extractOne(raw_state, list(choices.keys()), scorer=fuzz.WRatio)
    if score >= 80:
        return choices[match]
    else:
        return None

def geocode_location(country_name, state_name=None):
    """
    Uses the geolocator to convert a country (and optional state/province) into latitude and longitude.
    """
    query = country_name
    if state_name:
        query = f"{state_name}, {country_name}"
    try:
        location = geolocator.geocode(query, timeout=10)
        if location:
            return location.latitude, location.longitude
        else:
            return None, None
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        logger.error(f"Geocoding error for query '{query}': {e}")
        return None, None

# ---------------- Message Processing ----------------
def process_location_update(message_body):
    """
    Processes a location update message.
    Expected JSON keys: discord_id, guild_id, raw_country, raw_state (optional)
    """
    try:
        data = json.loads(message_body)
        discord_id = data.get("discord_id")
        guild_id = data.get("guild_id")
        raw_country = data.get("raw_country", "")
        raw_state = data.get("raw_state", "")

        logger.info(f"Processing location update for DiscordID: {discord_id} GuildID: {guild_id}")

        # Normalize the country
        country_obj = normalize_country(raw_country)
        if country_obj:
            standardized_country = country_obj.name
            country_code = country_obj.alpha_2
        else:
            standardized_country = None
            country_code = None

        # Normalize the state/province if provided
        state_obj = None
        if raw_state and country_code:
            state_obj = normalize_subdivision(raw_state, country_code)
        standardized_state = state_obj.name if state_obj else None

        # Get latitude and longitude via geocoding
        if standardized_country:
            lat, lon = geocode_location(standardized_country, standardized_state)
        else:
            lat, lon = None, None

        # Update the corresponding user profile in the database
        session = Session()
        try:
            profile = session.query(UserProfile).filter_by(discord_id=discord_id, guild_id=guild_id).first()
            if profile:
                profile.country = standardized_country
                profile.state = standardized_state
                profile.latitude = lat
                profile.longitude = lon
                session.commit()
                logger.info(f"Updated profile for DiscordID: {discord_id} with location: {standardized_country}, {standardized_state}, lat: {lat}, lon: {lon}")
            else:
                logger.warning(f"No profile found for DiscordID: {discord_id} and GuildID: {guild_id}")
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Database error: {e}")
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to process message: {e}")

# ---------------- RabbitMQ Consumer ----------------
def callback(ch, method, properties, body):
    logger.info("Received a new location update message")
    process_location_update(body)
    ch.basic_ack(delivery_tag=method.delivery_tag)

def main():
    rabbitmq_host = os.getenv("RABBITMQ_HOST", "localhost")
    rabbitmq_port = int(os.getenv("RABBITMQ_PORT", 5672))
    rabbitmq_username = os.getenv("RABBITMQ_USERNAME", "guest")
    rabbitmq_password = os.getenv("RABBITMQ_PASSWORD", "guest")
    rabbitmq_vhost = os.getenv("RABBITMQ_VHOST", "/")
    queue_name = os.getenv("RABBITMQ_QUEUE_NAME", "location_updates")
    
    # Create RabbitMQ connection parameters using provided credentials
    credentials = pika.PlainCredentials(rabbitmq_username, rabbitmq_password)
    connection_params = pika.ConnectionParameters(
        host=rabbitmq_host,
        port=rabbitmq_port,
        virtual_host=rabbitmq_vhost,
        credentials=credentials
    )

    connection = pika.BlockingConnection(connection_params)
    channel = connection.channel()
    
    # Declare the queue (durable ensures messages aren't lost)
    channel.queue_declare(queue=queue_name, durable=True)
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue_name, on_message_callback=callback)
    
    logger.info("Location service is waiting for messages...")
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        channel.stop_consuming()
        connection.close()

if __name__ == "__main__":
    main()
