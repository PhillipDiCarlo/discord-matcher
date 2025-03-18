# Discord Matcher Bot

Discord Matcher Bot is a Discord bot designed to help users connect by matching them based on preferences and profiles. With multi-guild support, users can have a unique profile in each server (guild) they join, and matching data is kept separate per guild.

## Features

- **Multi-Guild Profiles:**  
  Each user can create a separate profile for every server they join. Profiles are uniquely identified by the combination of the user's Discord ID and the guild ID.

- **Profile Creation & Update:**  
  Users can create and update profiles with details such as age, gender, bio, and preferences (including which genders they are attracted to).

- **Matching System:**  
  The bot suggests potential matches based on compatibility criteria (age, preferences, etc.) and ensures that swipes and matches are guild-specific.

- **Interactive Swiping:**  
  Users can swipe right or left on suggested profiles. When both parties swipe right, they receive a match notification along with a DM containing a button to view the matched profile.

## Setup and Installation

### Prerequisites

- Python 3.8 or higher
- PostgreSQL database
- (Optional) Docker & Docker Compose for containerized deployment

### Environment Variables

Create a `.env` file in the root directory and add the following variables:

```dotenv
DISCORD_TOKEN=your_discord_bot_token
POSTGRES_HOST=your_postgres_host
POSTGRES_DB=your_postgres_db
POSTGRES_USER=your_postgres_user
POSTGRES_PASSWORD=your_postgres_password
```

### Database Schema

Since the bot now supports multi-guild profiles, the database tables have been updated to include a `guild_id` column and a composite unique constraint on `(discord_id, guild_id)`. If you are starting fresh (i.e., deleting all profiles), run the following PostgreSQL commands to drop existing tables and recreate them:

```sql
-- Drop the tables and enum type if they exist
DROP TABLE IF EXISTS swipes CASCADE;
DROP TABLE IF EXISTS user_profiles CASCADE;
DROP TYPE IF EXISTS gender_enum;

-- Create the enum type with the desired values.
-- In the new schema, the gender is stored as "NonBinary" (without a hyphen).
CREATE TYPE gender_enum AS ENUM ('Male', 'Female', 'Trans', 'NonBinary');

-- Create the user_profiles table with guild-specific columns.
CREATE TABLE user_profiles (
    id SERIAL PRIMARY KEY,
    discord_id VARCHAR NOT NULL,
    guild_id VARCHAR NOT NULL,
    age INTEGER NOT NULL,
    gender gender_enum NOT NULL,
    bio TEXT NOT NULL,
    looking_for VARCHAR NOT NULL,
    attracted_genders gender_enum[] NOT NULL,
    preferred_min_age INTEGER NOT NULL DEFAULT 18,
    preferred_max_age INTEGER NOT NULL DEFAULT 100,
    matched_with VARCHAR,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uix_discord_guild UNIQUE (discord_id, guild_id)
);

-- Create the swipes table with guild_id.
CREATE TABLE swipes (
    id SERIAL PRIMARY KEY,
    guild_id VARCHAR NOT NULL,
    swiper_id VARCHAR NOT NULL,
    swiped_id VARCHAR NOT NULL,
    right_swipe BOOLEAN NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### Installation Steps

1. **Clone the Repository:**

   ```sh
   git clone https://github.com/YourUsername/discord-matcher.git
   cd discord-matcher
   ```

2. **Install Dependencies:**

   If you are using a virtual environment:

   ```sh
   python -m venv .venv
   source .venv/bin/activate   # On Windows use: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Configure the Environment:**

   Make sure your `.env` file is configured as described above.

4. **Run the Bot:**

   ```sh
   python src/bot.py
   ```

   Or, if you are using Docker:

   ```sh
   docker-compose up --build
   ```

## Usage

Once the bot is running and added to your Discord server, users can interact with it using the following slash commands:

- **`/create_profile`**:  
  Create a new profile for the current guild. The profile will include details such as age, gender, bio, and preferences.  
- **`/update_profile`**:  
  Update your existing profile for the current guild.
- **`/delete_profile`**:  
  Delete your profile for the current guild.
- **`/start_matching`**:  
  Begin the matching process. The bot will only show potential matches for the current guild.
- **`/unmatch`**:  
  Unmatch from your current match in the current guild. This will clear the match for both users.

## Contributing

Contributions are welcome! Please fork the repository and create a pull request with your changes. Ensure that your code follows the project's coding standards and includes appropriate tests.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
